from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from schlieren.web import CameraEngine, make_handler


def wait_for(engine, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = engine.snapshot()
        if predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError(f"Engine did not reach expected state: {engine.snapshot()}")


class WebEngineTests(unittest.TestCase):
    def test_numbered_catalog_does_not_open_cameras(self):
        cameras = [SimpleNamespace(index=3, name="DFK 33UP1300", backend=700),
                   SimpleNamespace(index=7, name="BRIO 4K Stream Edition", backend=700)]
        engine = CameraEngine("dshow", 160, 90)
        with patch("schlieren.web.list_cameras", return_value=cameras), patch("schlieren.web.open_camera") as op:
            records = engine.refresh_cameras()
            self.assertEqual([r["number"] for r in records], [1, 2])
            self.assertEqual([r["opencv_index"] for r in records], [3, 7])
            op.assert_not_called()

    def test_switch_releases_previous_source_and_failed_camera_clears_reference(self):
        camera = SimpleNamespace(index=4, name="DFK test", backend=700)
        engine = CameraEngine("dshow", 160, 90)
        with patch("schlieren.web.list_cameras", return_value=[camera]):
            record = engine.refresh_cameras()[0]
        previous = Mock()
        engine.cap = previous
        with patch("schlieren.web.open_camera", side_effect=RuntimeError("Camera busy")) as op, \
                patch("schlieren.web.list_cameras", return_value=[camera]), \
                patch("schlieren.web.discover_modes", return_value={"modes": [], "note": "test"}):
            with self.assertRaisesRegex(RuntimeError, "Camera busy"):
                engine._handle({"action": "source", "id": record["id"]})
            op.assert_called_once_with(camera, 160, 90, 30, fourcc="auto")
        previous.release.assert_called_once()
        self.assertIsNone(engine.cap)
        self.assertIsNone(engine.processor.reference)
        self.assertIsNone(engine.frame()[0])

    def test_demo_training_switching_modes_and_stop(self):
        engine = CameraEngine("dshow", 160, 90, fps=240)
        engine.start()
        try:
            first = wait_for(engine, lambda s: s["ready"] and s["sequence"] > 32)
            self.assertIsNotNone(engine.frame()[0])
            ticket = engine.submit({"action": "mode", "value": "gmg"})
            changed = wait_for(engine, lambda s: s["applied_command"] >= ticket)
            self.assertFalse(changed["ready"])
            self.assertGreater(changed["generation"], first["generation"])
            engine.submit({"action": "train"})
            trained = wait_for(engine, lambda s: s["ready"])
            self.assertEqual(trained["training_count"], 121)
            ticket = engine.submit({"action": "source", "id": "stop"})
            stopped = wait_for(engine, lambda s: s["applied_command"] >= ticket)
            self.assertFalse(stopped["connected"])
            self.assertIsNone(engine.frame()[0])
        finally:
            engine.close()
        self.assertFalse(engine.worker.is_alive())

    def test_settings_validation_and_missing_devices(self):
        engine = CameraEngine("dshow", 160, 90)
        for command in ({"action": "mode", "value": "fake"},
                        {"action": "source", "id": "missing"},
                        {"action": "settings", "gain": 0},
                        {"action": "settings", "threshold": 1.5},
                        {"action": "settings", "cleanup": "false"},
                        {"action": "capture", "id": "demo", "settings": {"fps": -1}},
                        {"action": "capture", "id": "demo", "settings": {"unknown": True}},
                        {"action": "settings", "mog2_threshold": float("nan")}):
            with self.subTest(command=command), self.assertRaises(ValueError):
                engine.submit(command)

    def test_capture_resize_clears_background_and_uses_requested_processing_size(self):
        engine = CameraEngine("dshow", 160, 90, fps=240)
        engine.start()
        try:
            first = wait_for(engine, lambda s: s["ready"])
            ticket = engine.submit({"action": "capture", "id": "demo", "settings":
                                    {"width": 800, "height": 600, "fps": 120, "processing_width": 640}})
            changed = wait_for(engine, lambda s: s["applied_command"] >= ticket and s["processing_shape"] == [480, 640])
            self.assertGreater(changed["generation"], first["generation"])
            self.assertEqual(changed["frame_shape"], [600, 800])
            self.assertEqual(changed["capture"]["fps"], 120)
            self.assertFalse(changed["ready"])
        finally:
            engine.close()

    def test_camera_capture_settings_are_remembered_separately_and_never_reuse_background(self):
        cameras = [SimpleNamespace(index=i, name=f"Camera {i}", backend=700) for i in (1, 2)]
        engine = CameraEngine("dshow", 160, 90)
        with patch("schlieren.web.list_cameras", return_value=cameras), \
                patch("schlieren.web.discover_modes", return_value={"modes": [], "note": "test"}), \
                patch("schlieren.web.open_camera") as op:
            first, second = engine.refresh_cameras()
            engine._handle({"action": "source", "id": first["id"]})
            old_cap = engine.cap
            engine.processor.reference = np.zeros((90, 160), np.float32)
            engine.processor.ready = True
            engine._handle({"action": "capture", "id": first["id"], "settings": {"width": 640, "height": 480, "fps": 90}})
            old_cap.release.assert_called()
            self.assertFalse(engine.processor.ready)
            engine._handle({"action": "source", "id": second["id"]})
            self.assertEqual(engine.capture.fps, 30)
            engine._handle({"action": "source", "id": first["id"]})
            self.assertEqual(engine.capture.fps, 90)
            op.assert_called_with(cameras[0], 640, 480, 90, fourcc="auto")
            with self.assertRaisesRegex(ValueError, "Camera changed"):
                engine._handle({"action": "capture", "id": second["id"], "settings": {"fps": 60}})

    def test_unplugged_camera_index_never_opens_another_device(self):
        camera = SimpleNamespace(index=1, name="DFK", path="usb-dfk", backend=700)
        engine = CameraEngine("dshow", 160, 90)
        with patch("schlieren.web.list_cameras", return_value=[camera]):
            record = engine.refresh_cameras()[0]
        replacement = SimpleNamespace(index=1, name="OBS", path="virtual-obs", backend=700)
        with patch("schlieren.web.list_cameras", return_value=[replacement]), patch("schlieren.web.open_camera") as op:
            with self.assertRaisesRegex(RuntimeError, "no longer connected"):
                engine._handle({"action": "source", "id": record["id"]})
            op.assert_not_called()


class WebHTTPTests(unittest.TestCase):
    def setUp(self):
        self.engine = CameraEngine("dshow", 160, 90)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.engine))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, value=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, json.dumps(value) if value is not None else None, headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_viewer_and_no_frame_response(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"sketch.js", body)
        self.assertEqual(self.request("GET", "/api/frame.jpg")[0], 204)
        self.assertEqual(self.request("GET", "/../requirements.txt")[0], 404)

    def test_commands_require_local_origin_and_are_validated(self):
        command = {"action": "mode", "value": "mog2"}
        self.assertEqual(self.request("POST", "/api/command", command)[0], 403)
        self.assertEqual(self.request("POST", "/api/command", command,
                                     {"X-Schlieren": "1", "Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("POST", "/api/command", command, {"X-Schlieren": "1"})[0], 202)
        self.assertEqual(self.request("POST", "/api/command", {"action": "settings", "gain": -1},
                                     {"X-Schlieren": "1"})[0], 400)


if __name__ == "__main__":
    unittest.main()
