import unittest

import numpy as np

from schlieren.processing import MODES, Processor, Settings


def frame(value=100, shape=(48, 64, 3)):
    return np.full(shape, value, np.uint8)


def calibrated(mode="reference", **kwargs):
    processor = Processor(Settings(mode=mode, blur=1, **kwargs))
    processor.reset(capture=True)
    for _ in range(processor.training_frames):
        result = processor.process(frame())
        assert not result.ready
        assert not result.mask.any()
    return processor


class ProcessingTests(unittest.TestCase):
    def test_waits_for_explicit_capture(self):
        processor = Processor(Settings())
        for _ in range(40):
            self.assertFalse(processor.process(frame()).ready)
        self.assertIsNone(processor.reference)

    def test_averages_reference_without_integer_rounding(self):
        processor = Processor(Settings(blur=1, reference_frames=2))
        processor.reset(capture=True)
        processor.process(frame(100))
        processor.process(frame(101))
        np.testing.assert_array_equal(processor.reference, np.full((48, 64), 100.5))

    def test_signed_difference_and_mask_preserve_both_polarities(self):
        processor = calibrated(gain=10, threshold=3)
        changed = frame()
        changed[5:15, 5:15] = 105
        changed[25:35, 25:35] = 95
        result = processor.process(changed)
        self.assertEqual(int(result.enhanced[10, 10]), 178)
        self.assertEqual(int(result.enhanced[30, 30]), 78)
        self.assertEqual(int(result.enhanced[0, 0]), 128)
        self.assertEqual(np.count_nonzero(result.mask), 200)
        processor.settings.gain = 60
        np.testing.assert_array_equal(processor.process(changed).mask, result.mask)

    def test_absolute_difference_does_not_wrap_or_lose_darkening(self):
        processor = calibrated(gain=64, signed=False)
        result = processor.process(frame(0))
        self.assertTrue(np.all(result.enhanced == 255))

    def test_global_light_compensation_retains_local_change(self):
        processor = calibrated(compensate_light=True)
        changed = frame(110)
        changed[10:20, 10:20] = 120
        result = processor.process(changed)
        self.assertEqual(np.count_nonzero(result.mask), 100)
        self.assertEqual(int(result.enhanced[0, 0]), 128)

    def test_reset_requires_new_calibration(self):
        processor = calibrated()
        processor.reset()
        self.assertFalse(processor.process(frame(220)).ready)
        self.assertIsNone(processor.reference)

    def test_resolution_change_invalidates_reference(self):
        processor = calibrated()
        self.assertFalse(processor.process(frame(shape=(24, 32, 3))).ready)
        self.assertIsNone(processor.reference)

    def test_all_modes_suppress_training_and_keep_persistent_foreground_when_frozen(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                processor = calibrated(mode)
                baseline = processor.process(frame())
                self.assertTrue(baseline.ready)
                self.assertLess(np.count_nonzero(baseline.mask), 10)
                changed = frame()
                changed[12:36, 20:44] = 220
                for _ in range(70):
                    result = processor.process(changed)
                self.assertGreater(np.count_nonzero(result.mask[12:36, 20:44]), 500)
                np.testing.assert_array_equal(processor.reference, np.full((48, 64), 100))

    def test_adaptive_models_learn_a_new_stable_background(self):
        for mode in MODES[1:]:
            with self.subTest(mode=mode):
                processor = calibrated(mode, learning_rate=0.2)
                processor.toggle_learning()
                for _ in range(100):
                    result = processor.process(frame(220))
                self.assertLess(np.count_nonzero(result.mask), 10)
                np.testing.assert_array_equal(processor.reference, np.full((48, 64), 100))

    def test_gmg_has_a_complete_initialization_even_for_short_reference_request(self):
        processor = Processor(Settings(mode="gmg", reference_frames=2, gmg_frames=120))
        self.assertEqual(processor.training_frames, 121)

    def test_invalid_settings(self):
        for kwargs in ({"blur": 2}, {"reference_frames": 0}, {"gain": 0},
                       {"learning_rate": float("nan")}, {"mog2_threshold": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)


if __name__ == "__main__":
    unittest.main()
