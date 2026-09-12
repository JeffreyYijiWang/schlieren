"""Camera-independent processing, including an explicitly captured reference."""

from dataclasses import dataclass

import cv2
import numpy as np

MODES = ("reference", "mog", "mog2", "gmg")


@dataclass
class Settings:
    mode: str = "reference"
    reference_frames: int = 30
    gmg_frames: int = 120
    gain: int = 8
    threshold: int = 3
    blur: int = 3
    cleanup: bool = False
    signed: bool = True
    compensate_light: bool = False
    learning_rate: float = 0.001
    mog2_threshold: float = 16.0
    shadows: bool = False

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"Unknown mode: {self.mode}")
        if self.reference_frames < 1 or self.gmg_frames < 1:
            raise ValueError("Calibration frame counts must be positive.")
        if self.blur < 1 or self.blur % 2 == 0:
            raise ValueError("Blur must be a positive odd number (1 disables it).")
        if not 1 <= self.gain <= 64 or not 0 <= self.threshold <= 255:
            raise ValueError("Gain must be 1..64 and threshold must be 0..255.")
        if not 0 < self.learning_rate <= 1:
            raise ValueError("Learning rate must be greater than 0 and at most 1.")
        if not np.isfinite(self.mog2_threshold) or self.mog2_threshold <= 0:
            raise ValueError("MOG2 variance threshold must be positive and finite.")


@dataclass
class Result:
    reference: np.ndarray
    enhanced: np.ndarray
    mask: np.ndarray
    ready: bool


class Processor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.reset()

    def reset(self, capture: bool = False):
        self.reference = None
        self.count = 0
        self.capturing = capture
        self.ready = False
        self.adaptive = False
        self.shape = None
        self.model = self._make_model()

    def _make_model(self):
        s = self.settings
        if s.mode == "reference":
            return None
        if s.mode == "mog2":
            return cv2.createBackgroundSubtractorMOG2(
                history=500, varThreshold=s.mog2_threshold, detectShadows=s.shadows
            )
        if not hasattr(cv2, "bgsegm"):
            raise RuntimeError("MOG and GMG require opencv-contrib-python. Install requirements.txt.")
        if s.mode == "mog":
            return cv2.bgsegm.createBackgroundSubtractorMOG(history=500)
        model = cv2.bgsegm.createBackgroundSubtractorGMG(
            initializationFrames=s.gmg_frames, decisionThreshold=0.8
        )
        # Keep GMG's implicit smoothing from erasing fine structure. Cleanup is optional.
        model.setSmoothingRadius(0)
        return model

    @property
    def training_frames(self):
        # GMG needs a post-initialization frame before its model can be frozen.
        return max(self.settings.reference_frames, self.settings.gmg_frames + 1) \
            if self.settings.mode == "gmg" else self.settings.reference_frames

    @property
    def status(self):
        if self.capturing:
            return f"Capturing background {self.count}/{self.training_frames} - keep scene still"
        if not self.ready:
            return "Set up the camera, then press Space to capture a quiet background"
        return "Ready - model adapting" if self.adaptive else "Ready - background frozen"

    def toggle_learning(self):
        if self.ready and self.model is not None:
            self.adaptive = not self.adaptive

    def process(self, frame: np.ndarray) -> Result:
        s = self.settings
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if s.blur > 1:
            gray = cv2.GaussianBlur(gray, (s.blur, s.blur), 0)
        if self.shape is not None and self.shape != gray.shape:
            # A resolution change invalidates both the reference and pixelwise models.
            self.reset()
        self.shape = gray.shape
        image = gray.astype(np.float32)
        zero = np.zeros_like(gray)
        neutral = np.full_like(gray, 128 if s.signed else 0)

        if self.capturing:
            self.count += 1
            if self.reference is None:
                self.reference = image.copy()
            else:
                self.reference += (image - self.reference) / self.count
            if self.model is not None:
                self.model.apply(gray, learningRate=-1)
            if self.count >= self.training_frames:
                self.capturing = False
                self.ready = True
            return Result(self.reference.copy(), neutral, zero, False)

        if not self.ready:
            return Result(zero.astype(np.float32), neutral, zero, False)

        delta = image - self.reference
        offset = float(np.median(delta)) if s.compensate_light else 0.0
        delta -= offset
        enhanced = np.clip(
            (delta if s.signed else np.abs(delta)) * s.gain + (128 if s.signed else 0),
            0, 255,
        ).astype(np.uint8)

        if self.model is None:
            # Threshold raw intensity differences, independently of display gain.
            mask = (np.abs(delta) > s.threshold).astype(np.uint8) * 255
        else:
            corrected = np.clip(image - offset, 0, 255).astype(np.uint8)
            if s.mode == "gmg":
                self.model.setUpdateBackgroundModel(self.adaptive)
            mask = self.model.apply(
                corrected, learningRate=s.learning_rate if self.adaptive else 0
            )
        if s.cleanup:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        return Result(self.reference.copy(), enhanced, mask, True)
