import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from schlieren.app import main
from schlieren.cameras import open_camera, select_camera


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.cameras = [SimpleNamespace(index=0, name="USB Webcam", backend=cv2.CAP_DSHOW),
                        SimpleNamespace(index=1, name="Integrated Camera", backend=cv2.CAP_DSHOW)]

    def test_selects_external_by_name_even_at_index_zero(self):
        self.assertIs(select_camera(self.cameras, name="usb"), self.cameras[0])

    def test_human_camera_numbers_start_at_one(self):
        self.assertIs(select_camera(self.cameras, number=1), self.cameras[0])
        self.assertIs(select_camera(self.cameras, number=2), self.cameras[1])
        for number in (0, -1, 3):
            with self.assertRaises(RuntimeError):
                select_camera(self.cameras, number=number)

    def test_missing_selection_does_not_fall_back(self):
        with self.assertRaises(RuntimeError):
            select_camera(self.cameras, index=2)
        with self.assertRaises(RuntimeError):
            select_camera(self.cameras, name="Missing Webcam")

    def test_ambiguous_name_requires_exact_index(self):
        with self.assertRaises(RuntimeError):
            select_camera(self.cameras, name="cam")

    def test_noninteractive_run_requires_explicit_selection(self):
        with patch("schlieren.cameras.sys.stdin.isatty", return_value=False):
            with self.assertRaises(RuntimeError):
                select_camera(self.cameras)

    def test_failed_open_releases_device_and_never_opens_another(self):
        with patch("schlieren.cameras.cv2.VideoCapture") as factory:
            factory.return_value.isOpened.return_value = False
            with self.assertRaises(RuntimeError):
                open_camera(self.cameras[0], 640, 480, 30)
            factory.assert_called_once_with(0, cv2.CAP_DSHOW)
            factory.return_value.release.assert_called_once()


class AppTests(unittest.TestCase):
    def test_video_to_recording_and_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="schlieren-test-") as directory:
            root = Path(directory)
            source = root / "input.avi"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 20, (160, 120))
            self.assertTrue(writer.isOpened())
            try:
                for index in range(12):
                    frame = np.full((120, 160, 3), 100, np.uint8)
                    if index >= 4:
                        frame[20:60, 30:70] = 140
                    writer.write(frame)
            finally:
                writer.release()
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["--input", str(source), "--auto-reference", "--headless",
                             "--reference-frames", "4", "--record", "--save-last",
                             "--output-dir", str(root / "output")])
            self.assertEqual(code, 0)
            snapshot = next((root / "output").glob("snapshot_*"))
            metadata = json.loads((snapshot / "settings.json").read_text())
            self.assertTrue(metadata["ready"])
            mask = cv2.imread(str(snapshot / "mask.png"), cv2.IMREAD_GRAYSCALE)
            self.assertGreater(np.count_nonzero(mask), 1400)
            reference = np.load(snapshot / "reference_float32.npy")
            self.assertEqual(reference.dtype, np.float32)
            recording = next((root / "output").glob("recording_*.avi"))
            cap = cv2.VideoCapture(str(recording))
            try:
                self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 12)
                self.assertAlmostEqual(cap.get(cv2.CAP_PROP_FPS), 20)
                ok, frame = cap.read()
                self.assertTrue(ok)
                self.assertEqual(frame.shape, (860, 1120, 3))
            finally:
                cap.release()

    def test_headless_live_run_must_be_bounded(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--demo", "--headless"])
        self.assertEqual(error.exception.code, 2)

    def test_missing_input_reports_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--input", "no-such-schlieren-video.avi"]), 1)


if __name__ == "__main__":
    unittest.main()
