"""Loopback-only API for the p5.js viewer. One worker owns every camera/model."""

import argparse
from dataclasses import asdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time
from urllib.parse import urlparse

import cv2
import numpy as np

from .cameras import BACKENDS, default_backend, list_cameras, open_camera
from .capture_modes import CaptureSettings, capture_report, discover_modes, initial_settings, preset_settings, resolve_auto
from .demo import DemoCamera
from .processing import MODES, Processor, Settings

WEB_ROOT = Path(__file__).resolve().parent.parent / "p5-demo"


class CameraEngine:
    def __init__(self, backend, width=1280, height=720, fps=30):
        self.backend, self.width, self.height, self.fps = backend, width, height, fps
        self.lock = Lock()
        self.commands = Queue(maxsize=64)
        self.stopping = Event()
        self.devices = {}
        self.catalog = []
        self.processor = Processor(Settings())
        self.cap = None
        self.capture = CaptureSettings(width, height, fps)
        self.camera_settings = {}
        self.format_cache = {}
        self.formats = {"modes": [], "note": "Select a camera to detect its capture modes."}
        self.actual_capture = None
        self.frame_shape = self.processing_shape = None
        self.last_preview = 0.0
        self.source_id, self.source_name = "stop", "No camera open"
        self.error = ""
        self.sequence = self.generation = self.applied_command = self.next_command = 0
        self.jpeg = None
        self.state = {}
        self.worker = Thread(target=self._loop, daemon=True, name="schlieren-capture")
        self._publish()

    def refresh_cameras(self):
        cameras = list_cameras(self.backend)
        records, devices = [], {}
        for number, camera in enumerate(cameras, 1):
            identity = f"{self.backend}|{getattr(camera, 'path', '') or str(camera.index) + '|' + camera.name}"
            key = hashlib.sha256(identity.encode()).hexdigest()[:20]
            records.append({"number": number, "id": key, "name": camera.name,
                            "opencv_index": camera.index, "backend": self.backend})
            devices[key] = camera
        with self.lock:
            self.devices, self.catalog = devices, records
        return records

    def start(self):
        self.worker.start()
        self.submit({"action": "source", "id": "demo"})

    def close(self):
        self.stopping.set()
        self.worker.join(timeout=3)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def frame(self):
        with self.lock:
            return self.jpeg, self.generation, self.sequence

    def submit(self, command):
        if not isinstance(command, dict):
            raise ValueError("Command must be a JSON object.")
        action = command.get("action")
        if action == "source":
            with self.lock:
                valid_ids = {"demo", "stop", *self.devices.keys()}
            if command.get("id") not in valid_ids:
                raise ValueError("Camera is no longer in the list. Refresh cameras and select it again.")
        elif action == "mode":
            if command.get("value") not in MODES:
                raise ValueError("Unknown background-subtraction mode.")
        elif action == "capture":
            with self.lock:
                valid_ids = {"demo", *self.devices.keys()}
            if command.get("id") not in valid_ids:
                raise ValueError("Select a camera before changing its capture settings.")
            if command.get("preset") not in (None, "max_fps", "max_resolution"):
                raise ValueError("Unknown capture preset.")
            if command.get("preset") and command.get("settings"):
                raise ValueError("Choose a preset or custom capture settings.")
            if "settings" in command:
                if not isinstance(command["settings"], dict):
                    raise ValueError("Capture settings must be an object.")
                try:
                    CaptureSettings(**command["settings"])
                except TypeError as exc:
                    raise ValueError("Unsupported capture setting.") from exc
            if "rescan" in command and type(command["rescan"]) is not bool:
                raise ValueError("rescan must be true or false.")
            if not any(command.get(key) for key in ("settings", "preset", "rescan")):
                raise ValueError("Choose capture settings or rescan the camera modes.")
        elif action == "settings":
            allowed = {"gain", "threshold", "cleanup", "signed", "compensate_light", "mog2_threshold"}
            changes = {key: value for key, value in command.items() if key != "action"}
            if not changes or not changes.keys() <= allowed:
                raise ValueError("Unsupported setting.")
            for key, value in changes.items():
                if key in {"cleanup", "signed", "compensate_light"}:
                    if not isinstance(value, bool):
                        raise ValueError(f"{key} must be true or false.")
                elif key in {"gain", "threshold"}:
                    if type(value) is not int:
                        raise ValueError(f"{key} must be an integer.")
                elif type(value) not in (int, float):
                    raise ValueError(f"{key} must be a number.")
            Settings(**changes)
        elif action not in {"train", "learning"}:
            raise ValueError("Unknown command.")
        with self.lock:
            self.next_command += 1
            ticket = self.next_command
        try:
            self.commands.put_nowait((ticket, command.copy()))
        except Full as exc:
            raise ValueError("Too many pending commands. Wait a moment and try again.") from exc
        return ticket

    def _publish(self, fps=0.0, frame_shape=None, status=None):
        state = {"source_id": self.source_id, "source_name": self.source_name,
                 "settings": asdict(self.processor.settings), "ready": self.processor.ready,
                 "training": self.processor.capturing, "training_count": self.processor.count,
                 "training_total": self.processor.training_frames, "adaptive": self.processor.adaptive,
                 "status": status or self.error or (self.processor.status if self.cap is not None else "Select a camera or Synthetic demo"),
                 "error": self.error, "fps": round(fps, 1), "sequence": self.sequence,
                 "generation": self.generation, "applied_command": self.applied_command,
                 "frame_shape": frame_shape or self.frame_shape, "connected": self.cap is not None,
                 "capture": asdict(self.capture), "actual_capture": self.actual_capture,
                 "processing_shape": self.processing_shape, "formats": self.formats}
        with self.lock:
            self.state = state

    def _clear_frames(self):
        self.last_preview = 0.0
        with self.lock:
            self.generation += 1
            self.jpeg = None

    def _release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def _open_source(self, source_id, rescan=False):
        self._release()
        self._clear_frames()
        self.processor.reset()
        self.actual_capture = None
        self.frame_shape = self.processing_shape = None
        self.source_id = source_id
        self.source_name = "No camera open"
        self.error = ""
        self.capture = self.camera_settings.get(source_id, CaptureSettings(self.width, self.height, self.fps))
        self.formats = {"modes": [], "note": "Select a camera to detect its capture modes."}
        if source_id == "demo":
            self.source_name = "Synthetic demo"
            self.formats = {"modes": [], "note": "Synthetic demo: choose a custom size and FPS."}
            self.cap = DemoCamera(self.capture.width, self.capture.height, self.processor.training_frames + 10)
            self.processor.reset(capture=True)
        elif source_id != "stop":
            with self.lock:
                selected = self.devices.get(source_id)
            if selected is None:
                raise RuntimeError("Camera was disconnected. Refresh the camera list.")
            self.source_name = selected.name
            # Re-resolve the current index; unplugging a camera can renumber devices.
            current = list_cameras(self.backend)
            path = getattr(selected, "path", None)
            matches = [c for c in current if (getattr(c, "path", None) == path if path else
                       (c.index, c.name) == (selected.index, selected.name))]
            if len(matches) != 1:
                raise RuntimeError("Selected camera is no longer connected. Refresh cameras and select it again.")
            camera = matches[0]
            if rescan or source_id not in self.format_cache:
                self._publish(status=f"Reading supported modes for {camera.name}...")
                duplicate = sum(c.name == camera.name for c in current[:current.index(camera)])
                self.format_cache[source_id] = discover_modes(camera, duplicate)
            self.formats = self.format_cache[source_id]
            if source_id not in self.camera_settings and (self.width, self.height, self.fps) == (1280, 720, 30):
                self.capture = initial_settings(self.capture, self.formats["modes"])
                self.camera_settings[source_id] = self.capture
            self._publish(status=f"Opening {camera.name} at {self.capture.width} × {self.capture.height}, {self.capture.fps:g} FPS...")
            requested = resolve_auto(self.capture, self.formats["modes"])
            self.cap = open_camera(camera, requested.width, requested.height, requested.fps, fourcc=requested.fourcc)

    def _handle(self, command):
        action = command["action"]
        if action == "source":
            self._open_source(command["id"])
        elif action == "capture":
            if command["id"] != self.source_id:
                raise ValueError("Camera changed before settings were applied. Select it and try again.")
            if command.get("preset"):
                settings = preset_settings(command["preset"], self.formats["modes"], self.capture)
            else:
                settings = CaptureSettings(**command.get("settings", asdict(self.capture)))
            self.camera_settings[self.source_id] = settings
            self._open_source(self.source_id, rescan=command.get("rescan", False))
        elif action == "train":
            if self.cap is None:
                raise RuntimeError("Select a working camera or Synthetic demo before retraining.")
            self._clear_frames()
            self.processor.reset(capture=True)
            if self.source_id == "demo":
                self.cap.count = 0
                self.cap.quiet_frames = self.processor.training_frames + 10
        elif action == "mode":
            self.processor.settings.mode = command["value"]
            self.processor.reset()
            self._clear_frames()
        elif action == "learning":
            self.processor.toggle_learning()
        elif action == "settings":
            for key, value in command.items():
                if key != "action":
                    setattr(self.processor.settings, key, value)
            if "mog2_threshold" in command and self.processor.settings.mode == "mog2":
                self.processor.model.setVarThreshold(command["mog2_threshold"])
        self.error = ""

    def _loop(self):
        rate_started = time.perf_counter()
        rate_count = 0
        measured = 0.0
        last_generation = -1
        try:
            while not self.stopping.is_set():
                started = time.perf_counter()
                for _ in range(16):
                    try:
                        ticket, command = self.commands.get_nowait()
                    except Empty:
                        break
                    try:
                        self._handle(command)
                    except Exception as exc:
                        self.error = str(exc)
                    self.applied_command = ticket
                    self._publish()
                if self.cap is None:
                    self.stopping.wait(0.04)
                    continue
                try:
                    if last_generation != self.generation:
                        rate_started = time.perf_counter()
                        rate_count = 0
                        measured = 0.0
                        last_generation = self.generation
                    ok, frame = self.cap.read()
                    if not ok or frame is None or frame.size == 0:
                        raise RuntimeError("Camera stopped delivering frames. Reconnect it and select it again.")
                    self.frame_shape = list(frame.shape[:2])
                    if self.actual_capture is None and self.source_id != "demo":
                        self.actual_capture = capture_report(self.cap, self.capture, frame.shape)
                    processing = frame
                    limit = self.capture.processing_width
                    if limit and frame.shape[1] > limit:
                        processing = cv2.resize(frame, (limit, max(1, round(frame.shape[0] * limit / frame.shape[1]))),
                                                interpolation=cv2.INTER_AREA)
                    self.processing_shape = list(processing.shape[:2])
                    result = self.processor.process(processing)
                    now = time.perf_counter()
                    rate_count += 1
                    if now - rate_started >= 0.5:
                        measured = rate_count / (now - rate_started)
                        rate_started, rate_count = now, 0
                    self._publish(measured, self.frame_shape)
                    # Models run on every acquired frame; JPEG work is limited to 60 Hz.
                    if now - self.last_preview < 1 / 60:
                        if self.source_id == "demo":
                            self.stopping.wait(max(0, 1 / self.capture.fps - (now - started)))
                        continue
                    self.last_preview = now
                    images = [frame, result.reference, result.enhanced, result.mask]
                    h, w = frame.shape[:2]
                    size = (min(640, w), max(1, round(h * min(640, w) / w)))
                    tiles = []
                    for image in images:
                        if image.dtype != np.uint8:
                            image = np.clip(image, 0, 255).astype(np.uint8)
                        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
                        if image.ndim == 2:
                            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                        tiles.append(image)
                    atlas = np.vstack((np.hstack(tiles[:2]), np.hstack(tiles[2:])))
                    ok, encoded = cv2.imencode(".jpg", atlas, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    if not ok:
                        raise RuntimeError("Could not encode camera preview.")
                    with self.lock:
                        self.jpeg = encoded.tobytes()
                        self.sequence += 1
                    self._publish(measured, self.frame_shape)
                except Exception as exc:
                    self.error = str(exc)
                    self._release()
                    self.actual_capture = None
                    self.frame_shape = self.processing_shape = None
                    self._clear_frames()
                    self.processor.reset()
                    self._publish()
                # A real camera paces read(); a fixed 30 Hz sleep would cap high-FPS modes.
                if self.source_id == "demo":
                    self.stopping.wait(max(0, 1 / self.capture.fps - (time.perf_counter() - started)))
        finally:
            self._release()


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body=b"", content_type="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (extra or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def json_reply(self, code, value):
            self.reply(code, json.dumps(value).encode("utf-8"))

        def valid_host(self):
            port = self.server.server_port
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):
            if not self.valid_host():
                return self.json_reply(403, {"error": "Use the localhost address printed by the server."})
            path = urlparse(self.path).path
            if path == "/api/state":
                return self.json_reply(200, engine.snapshot())
            if path == "/api/cameras":
                try:
                    return self.json_reply(200, {"cameras": engine.refresh_cameras(), "backend": engine.backend})
                except RuntimeError as exc:
                    return self.json_reply(503, {"error": str(exc)})
            if path == "/api/frame.jpg":
                data, generation, sequence = engine.frame()
                return self.reply(200 if data else 204, data or b"", "image/jpeg",
                                  {"X-Generation": generation, "X-Sequence": sequence})
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/style.css": ("style.css", "text/css"),
                      "/sketch.js": ("sketch.js", "text/javascript"),
                      "/vendor/p5.min.js": ("vendor/p5.min.js", "text/javascript")}
            if path not in static:
                return self.json_reply(404, {"error": "Not found"})
            filename, mime = static[path]
            try:
                self.reply(200, (WEB_ROOT / filename).read_bytes(), mime)
            except FileNotFoundError:
                self.json_reply(404, {"error": f"Missing viewer file: {filename}"})

        def do_POST(self):
            # Drain bounded request bodies before early rejection. Closing a socket
            # with unread bytes can reset it on Windows before the client sees 403.
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid command size.")
                body = self.rfile.read(length)
            except ValueError as exc:
                return self.json_reply(400, {"error": str(exc)})
            origin = self.headers.get("Origin")
            if (not self.valid_host() or self.headers.get("X-Schlieren") != "1"
                    or (origin and origin != f"http://{self.headers.get('Host')}")):
                return self.json_reply(403, {"error": "Commands must come from this local viewer."})
            if self.path != "/api/command":
                return self.json_reply(404, {"error": "Not found"})
            try:
                command = json.loads(body)
                ticket = engine.submit(command)
                self.json_reply(202, {"command": ticket})
            except (ValueError, TypeError) as exc:
                self.json_reply(400, {"error": str(exc)})
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--backend", choices=BACKENDS, default=default_backend())
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Use port 1024..65535.")
    try:
        CaptureSettings(args.width, args.height, args.fps)
    except ValueError as exc:
        parser.error(str(exc))
    engine = CameraEngine(args.backend, args.width, args.height, args.fps)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(engine))
    except OSError as exc:
        print(f"Cannot start viewer: {exc}. Try a different --port.")
        return 1
    engine.start()
    print(f"p5.js viewer: http://127.0.0.1:{args.port}", flush=True)
    print("Choose Camera 1, 2, 3, ... in the browser. Space retrains; Ctrl+C stops the server.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()
    return 0
