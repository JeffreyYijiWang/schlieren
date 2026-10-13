"""Capture requests and optional DirectShow format discovery through FFmpeg."""

from dataclasses import asdict, dataclass
import math
import re
import shutil
import subprocess
import sys

import cv2


FORMATS = ("auto", "MJPG", "YUY2", "UYVY", "I420")


@dataclass
class CaptureSettings:
    width: int = 1280
    height: int = 720
    fps: float = 30
    fourcc: str = "auto"
    processing_width: int = 0

    def __post_init__(self):
        if any(type(v) is not int or not 16 <= v <= 8192 for v in (self.width, self.height)):
            raise ValueError("Capture dimensions must be integers from 16 to 8192.")
        if self.width * self.height > 16_777_216:
            raise ValueError("Capture size must be at most 16 megapixels.")
        if type(self.fps) not in (int, float) or not math.isfinite(self.fps) or not 1 <= self.fps <= 1000:
            raise ValueError("Capture FPS must be between 1 and 1000.")
        if self.fourcc not in FORMATS:
            raise ValueError("Unsupported capture pixel format.")
        if type(self.processing_width) is not int or self.processing_width not in (0, 640, 1280, 1920):
            raise ValueError("Processing width must be 0 (native), 640, 1280, or 1920.")


def parse_dshow_modes(output):
    """Keep driver endpoints, not invented intermediate resolutions or FPS values.

    Raw Bayer/packed formats are retained for diagnostics but cannot be selected:
    OpenCV's DirectShow converter does not support those camera-specific formats.
    """
    modes = {}
    pattern = r"min s=(\d+)x(\d+) fps=([\d.]+) max s=(\d+)x(\d+) fps=([\d.]+)"
    for line in output.splitlines():
        match = re.search(pattern, line)
        if not match:
            continue
        kind = re.search(r"(?:pixel_format|vcodec)=(\w+)|unknown compression type (0x[\da-fA-F]+)", line)
        if not kind:
            continue
        name = kind[1] or kind[2]
        fourcc = {"mjpeg": "MJPG", "yuyv422": "YUY2", "uyvy422": "UYVY", "yuv420p": "I420"}.get(name)
        if name.lower() == "0xe436eb7d":
            name = "RGB24"
        elif name.startswith("0x"):
            raw = int(name, 16).to_bytes(4, "little")
            if all(32 <= byte < 127 for byte in raw):
                name = raw.decode("ascii")
        w0, h0, low, w1, h1, high = match.groups()
        # A variable-size capability does not imply every size supports its peak FPS.
        endpoints = [(int(w0), int(h0), float(low)), (int(w1), int(h1), float(high))]
        for width, height, fps in endpoints:
            fps = round(fps, 2)
            if not 1 <= fps <= 1000 or width * height > 16_777_216:
                continue
            key = width, height, name
            existing = modes.get(key)
            mode = {"width": width, "height": height, "fps": fps,
                    "fourcc": fourcc, "format": fourcc or name, "selectable": fourcc is not None}
            if existing is None or fps > existing["fps"]:
                modes[key] = mode
    return sorted(modes.values(), key=lambda m: (m["width"] * m["height"], m["fps"]), reverse=True)


def discover_modes(camera, duplicate_number=0):
    executable = shutil.which("ffmpeg")
    if sys.platform != "win32" or camera.backend != cv2.CAP_DSHOW or not executable:
        return {"modes": [], "note": "Automatic mode discovery needs FFmpeg on PATH and the Windows dshow backend. Custom requests remain available."}
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-nostdin", "-f", "dshow", "-list_options", "true",
             "-video_device_number", str(duplicate_number), "-i", f"video={camera.name}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # FFmpeg may exit nonzero after successfully listing formats (not a capture).
        modes = parse_dshow_modes(result.stderr)
    except (OSError, subprocess.TimeoutExpired):
        modes = []
    if not modes:
        return {"modes": [], "note": "The driver did not provide a format list. Custom requests remain available."}
    note = "Detected driver modes. Max presets use formats supported by this OpenCV viewer."
    if any(not mode["selectable"] for mode in modes):
        note += " Additional raw/packed camera formats require a different capture implementation."
    if "brio" in camera.name.lower() and max(m["width"] for m in modes) < 3000:
        note += " 4K is not exposed on this connection; check the USB 3 port/cable and camera configuration."
    return {"modes": modes, "note": note}


def preset_settings(preset, modes, current):
    available = [mode for mode in modes if mode["selectable"]]
    if not available:
        raise ValueError("No compatible driver modes were detected. Set resolution and FPS manually.")
    if preset == "max_fps":
        key = lambda m: (m["fps"], m["width"] * m["height"], m["fourcc"] == "MJPG")
    elif preset == "max_resolution":
        key = lambda m: (m["width"] * m["height"], m["fps"], m["fourcc"] == "MJPG")
    else:
        raise ValueError("Unknown capture preset.")
    mode = max(available, key=key)
    return CaptureSettings(mode["width"], mode["height"], mode["fps"], mode["fourcc"], current.processing_width)


def resolve_auto(settings, modes):
    if settings.fourcc != "auto":
        return settings
    matches = [m for m in modes if m["selectable"] and
               (m["width"], m["height"]) == (settings.width, settings.height)]
    if not matches:
        return settings
    best = max(matches, key=lambda m: (m["fps"] >= settings.fps - 0.1, m["fourcc"] == "MJPG", m["fps"]))
    return CaptureSettings(**{**asdict(settings), "fourcc": best["fourcc"]})


def initial_settings(settings, modes):
    """Fit the viewer's default 720p request to a camera's actual sensor sizes."""
    available = [mode for mode in modes if mode["selectable"]]
    if not available or any((m["width"], m["height"]) == (settings.width, settings.height) for m in available):
        return settings
    closest = min(available, key=lambda m: (abs(m["width"] * m["height"] - settings.width * settings.height), -m["fps"]))
    return CaptureSettings(closest["width"], closest["height"], min(settings.fps, closest["fps"]),
                           "auto", settings.processing_width)


def capture_report(cap, request, shape):
    """Frame dimensions are authoritative; FPS below is a driver report, not timing."""
    fps = cap.get(cv2.CAP_PROP_FPS)
    fps = round(fps, 2) if math.isfinite(fps) and fps > 0 else None
    code = int(cap.get(cv2.CAP_PROP_FOURCC)) & 0xffffffff
    fourcc = "".join(chr((code >> (8 * i)) & 255) for i in range(4))
    if not all(32 <= ord(char) < 127 for char in fourcc):
        fourcc = "driver default"
    height, width = shape[:2]
    warning = []
    if (width, height) != (request.width, request.height):
        warning.append(f"Requested {request.width} × {request.height}; camera delivered {width} × {height}.")
    if fps is not None and abs(fps - request.fps) > max(0.5, request.fps * 0.02):
        warning.append(f"Requested {request.fps:g} FPS; driver reports {fps:g} FPS.")
    if request.fourcc != "auto" and fourcc != request.fourcc:
        warning.append(f"Requested {request.fourcc}; driver reports {fourcc}.")
    return {"width": width, "height": height, "fps": fps, "fourcc": fourcc, "warning": " ".join(warning)}
