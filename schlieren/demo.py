"""Synthetic intensity changes for checking the app without camera hardware."""

import cv2
import numpy as np


class DemoCamera:
    def __init__(self, width, height, quiet_frames):
        self.width, self.height = width, height
        self.quiet_frames = quiet_frames
        self.count = 0
        self.y, self.x = np.mgrid[0:height, 0:width].astype(np.float32)
        self.x /= width
        self.y /= height
        self.background = 120 + 25 * np.sin(self.x * 80) * np.cos(self.y * 65)
        self.rng = np.random.default_rng(7)

    def read(self):
        self.count += 1
        gray = self.background.copy()
        if self.count > self.quiet_frames:
            phase = (self.count - self.quiet_frames) / 18
            center = 0.5 + 0.07 * np.sin(self.y * 13 + phase)
            distance = (self.x - center) / 0.055
            # A subtle signed intensity change, not a physical flow simulation.
            gray += 10 * distance * np.exp(-distance**2) * np.sin(self.y * 3.14)
        gray += self.rng.normal(0, 0.35, gray.shape)
        frame = cv2.cvtColor(np.clip(gray, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        return True, frame

    def release(self):
        pass
