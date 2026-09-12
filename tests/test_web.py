from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
        with patch("schlieren.web.open_camera", side_effect=RuntimeError("Camera busy")) as op:
            with self.assertRaisesRegex(RuntimeError, "Camera busy"):
                engine._handle({"action": "source", "id": record["id"]})
            op.assert_called_once_with(camera, 160, 90, 30)
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
                        {"action": "settings", "mog2_threshold": float("nan")}):
            with self.subTest(command=command), self.assertRaises(ValueError):
                engine.submit(command)


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
