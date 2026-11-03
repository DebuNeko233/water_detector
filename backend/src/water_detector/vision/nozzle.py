"""Conservative per-frame segmentation of movable cleaning nozzles.

A narrow, mostly vertical dark shaft entering from above the wafer is
segmented by horizontal black-hat filtering and long-component selection.
Camera/nozzle movement is accommodated by searching anew on each frame.
A short temporal hold prevents departing tool edges from becoming water.
This is an opt-in visualization but an enabled, bounded exclusion in scoring.
"""
from __future__ import annotations

from collections import deque

import cv2
import numpy as np

from water_detector.config.schema import Calibration


def nozzle_occlusion_mask(
    frame: np.ndarray, calibrations: dict[str, Calibration],
) -> np.ndarray:
    height, width = frame.shape[:2]
    empty = np.zeros((height, width), dtype=bool)
    if not calibrations:
        return empty
    radius = float(np.median([c.radius for c in calibrations.values()]))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bar_width = int(np.clip(radius * 0.60, 31, 85)) | 1
    blackhat = cv2.morphologyEx(
        gray, cv2.MORPH_BLACKHAT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (bar_width, 9)),
    )
    foreground = np.uint8(blackhat > 16) * 255
    long_h = max(17, int(radius * 0.28))
    foreground = cv2.morphologyEx(
        foreground, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, long_h)),
    )
    num, labels, stats, _ = cv2.connectedComponentsWithStats(foreground)
    selected: set[int] = set()
    for calibration in calibrations.values():
        r = calibration.radius
        best_index = 0
        best_area = 0
        for index in range(1, num):
            x, y, cw, ch, area = map(int, stats[index])
            if (area < max(80, int(r * 0.7))
                    or ch < 0.60 * r or cw > 1.23 * r or ch < 1.2 * cw
                    or y > calibration.cy - 0.30 * r
                    or y + ch < calibration.cy - 0.55 * r
                    or abs(x + cw / 2 - calibration.cx) > 0.82 * r
                    or x < 0.04 * width or x + cw > 0.96 * width):
                continue
            if area > best_area:
                best_area, best_index = area, index
        if best_index:
            selected.add(best_index)
    if not selected:
        return empty
    mask = np.isin(labels, list(selected)).astype(np.uint8) * 255
    mask = cv2.dilate(
        mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)),
    )
    yy, xx = np.ogrid[:height, :width]
    for c in calibrations.values():
        rr = np.hypot(xx - c.cx, yy - c.cy)
        annulus = (rr >= 0.65 * c.radius) & (rr <= 0.95 * c.radius)
        # Never remove more than 30% of an ROI; fail open instead of
        # suppressing significant water evidence.
        if np.count_nonzero(annulus) and float(np.mean(mask[annulus] > 0)) > 0.30:
            return empty
    return mask > 0


class DynamicNozzleFilter:
    """Short moving exclusion trail plus per-frame nozzle detection."""

    def __init__(self, shape_hw: tuple[int, int], fps: float,
                 calibrations: dict[str, Calibration]):
        self.shape = shape_hw
        self.calibrations = calibrations
        self.recent: deque[np.ndarray] = deque(
            maxlen=max(2, min(5, round(max(fps, 1) * 0.12)))
        )

    def reset(self) -> None:
        self.recent.clear()

    def detect(self, frame: np.ndarray) -> np.ndarray:
        now = nozzle_occlusion_mask(frame, self.calibrations)
        self.recent.append(now)
        return np.logical_or.reduce(list(self.recent))
