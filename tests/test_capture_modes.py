import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2

from schlieren.cameras import open_camera
from schlieren.capture_modes import CaptureSettings, capture_report, initial_settings, parse_dshow_modes, preset_settings, resolve_auto


DRIVER_LIST = """
[dshow] pixel_format=yuyv422 min s=1920x1080 fps=5 max s=1920x1080 fps=5
[dshow] vcodec=mjpeg min s=1920x1080 fps=5 max s=1920x1080 fps=30
[dshow] vcodec=mjpeg min s=1280x720 fps=5 max s=1280x720 fps=60.0002
[dshow] vcodec=mjpeg min s=1280x720 fps=5 max s=1280x720 fps=60.0002 (pc, bt709)
[dshow] unknown compression type 0x42474752 min s=640x480 fps=1 max s=640x480 fps=800
"""


class CaptureModeTests(unittest.TestCase):
    def test_presets_use_real_driver_limits_and_exclude_unsupported_raw_formats(self):
        modes = parse_dshow_modes(DRIVER_LIST)
        self.assertEqual(len(modes), 4)
        fastest = preset_settings("max_fps", modes, CaptureSettings())
        detail = preset_settings("max_resolution", modes, CaptureSettings())
        self.assertEqual((fastest.width, fastest.height, fastest.fps, fastest.fourcc), (1280, 720, 60, "MJPG"))
        self.assertEqual((detail.width, detail.height, detail.fps), (1920, 1080, 30))
        self.assertEqual(modes[-1]["format"], "RGGB")
        self.assertFalse(modes[-1]["selectable"])

    def test_auto_avoids_slow_uncompressed_brio_format(self):
        requested = resolve_auto(CaptureSettings(1920, 1080, 30), parse_dshow_modes(DRIVER_LIST))
        self.assertEqual(requested.fourcc, "MJPG")

    def test_no_detected_modes_does_not_claim_a_maximum(self):
        with self.assertRaisesRegex(ValueError, "No compatible"):
            preset_settings("max_fps", [], CaptureSettings())

    def test_initial_camera_size_uses_an_existing_sensor_mode(self):
        modes = parse_dshow_modes("pixel_format=yuyv422 min s=1280x1024 fps=1 max s=1280x1024 fps=120")
        settings = initial_settings(CaptureSettings(), modes)
        self.assertEqual((settings.width, settings.height, settings.fps), (1280, 1024, 30))

    def test_invalid_capture_requests(self):
        for changes in ({"fps": float("nan")}, {"fps": True}, {"fps": 0}, {"width": 8192, "height": 8192},
                        {"width": 1280.5}, {"fourcc": "RGGB"}, {"processing_width": -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                CaptureSettings(**changes)

    def test_frame_shape_and_driver_values_report_a_negotiation_mismatch(self):
        cap = Mock()
        cap.get.side_effect = lambda prop: {cv2.CAP_PROP_FPS: 30, cv2.CAP_PROP_FOURCC: cv2.VideoWriter_fourcc(*"YUY2")}[prop]
        actual = capture_report(cap, CaptureSettings(3840, 2160, 60, "MJPG"), (1080, 1920, 3))
        self.assertEqual((actual["width"], actual["height"], actual["fps"]), (1920, 1080, 30))
        self.assertIn("camera delivered 1920", actual["warning"])
        self.assertIn("driver reports 30", actual["warning"])
        self.assertIn("driver reports YUY2", actual["warning"])

    def test_format_is_set_after_fps_so_directshow_does_not_reset_it(self):
        camera = SimpleNamespace(index=2, name="BRIO", backend=cv2.CAP_DSHOW)
        with patch("schlieren.cameras.cv2.VideoCapture") as factory:
            open_camera(camera, 1280, 720, 60, fourcc="MJPG")
        properties = [call.args[0] for call in factory.return_value.set.call_args_list]
        self.assertEqual(properties, [cv2.CAP_PROP_FPS, cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_FOURCC])


if __name__ == "__main__":
    unittest.main()
