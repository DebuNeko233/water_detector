"""Per-frame exclusion mask for elongated movable cleaning nozzles.

Distinguishes a narrow, top-entering, mostly vertical dark nozzle from
water motion. Supports a configurable short hold so a nozzle's arrival/departure
does not create temporal frame-difference artifacts in the water detector.

The mask is conservative (max 30% of any ROI's annulus); false negatives are
preferable to hiding substantial real water. A mask of zero means uncertain.
"""
from __future__ import annotations

from collections import deque
import cv2
import numpy as np

from water_detector.config.schema import Calibration


class DynamicNozzleFilter:
    def __init__(self, shape_hw: tuple[int, int], fps: float,
                 calibrations: dict[str, Calibration]):
        self.shape = shape_hw
        self.calibrations = list(calibrations.values())
        self.recent: deque[np.ndarray] = deque(
            maxlen=max(2, min(5, round(max(1.0, fps) * 0.12)))
        )
        yy, xx = np.ogrid[:shape_hw[0], :shape_hw[1]]
        self.annuli: list[np.ndarray] = []
        for c in self.calibrations:
            radius = np.hypot(xx - c.cx, yy - c.cy)
            self.annuli.append((radius >= 0.65 * c.radius) &
                               (radius <= 0.95 * c.radius))

    def reset(self) -> None:
        self.recent.clear()

    def detect(self, frame: np.ndarray) -> np.ndarray:
        h, w = self.shape
        if frame.shape[:2] != self.shape or not self.calibrations:
            return np.zeros(frame.shape[:2], dtype=bool)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        representative = float(np.median([c.radius for c in self.calibrations]))
        width = int(np.clip(0.60 * representative, 31, 85))
        if width % 2 == 0:
            width += 1
        blackhat = cv2.morphologyEx(
            gray, cv2.MORPH_BLACKHAT,
            cv2.getStructuringElement(cv2.MORPH_RECT, (width, 9)),
        )
        dark = np.uint8(blackhat > 16) * 255
        length = max(17, round(0.28 * representative))
        dark = cv2.morphologyEx(
            dark, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (3, length)),
        )
        num, labels, components, _ = cv2.connectedComponentsWithStats(dark)
        best: dict[int, float] = {}
        for index in range(1, num):
            x, y, cw, ch, area = map(int, components[index])
            if area < max(80, int(representative * 0.7)):
                continue
            for k, cal in enumerate(self.calibrations):
                r = float(cal.radius)
                if (ch < 0.6 * r or cw > 1.23 * r or ch < 1.2 * cw
                        or y > cal.cy - 0.30 * r
                        or y + ch < cal.cy - 0.55 * r
                        or abs(x + cw / 2 - cal.cx) > 0.82 * r
                        or x < 0.04 * w or x + cw > 0.96 * w):
                    continue
                # One long connected segment per ROI. Excluding every dark
                # component would erase water and fixed equipment boundaries.
                old = best.get(k)
                if old is None or area > components[int(old), cv2.CC_STAT_AREA]:
                    best[k] = float(index)
        candidate = np.zeros((h, w), dtype=np.uint8)
        for index in set(int(value) for value in best.values()):
            candidate[labels == index] = 255
        if np.any(candidate):
            candidate = cv2.dilate(
                candidate, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
            )
            for annulus in self.annuli:
                if (np.count_nonzero(annulus) > 0 and
                        np.mean(candidate[annulus] > 0) > 0.30):
                    # Never erase too much of the wafer's detection area.
                    candidate.fill(0)
                    break
        self.recent.append(candidate > 0)
        # Hold a few frames of previous nozzle positions so the moving edge
        # does not become a persistent water anomaly as the nozzle leaves.
        return np.logical_or.reduce(list(self.recent))
