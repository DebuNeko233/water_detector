"""Find vertical, moving-tool/nozzle occlusions without frame-to-frame state.

Black-hat vertical structure detection is used rather than a fixed nozzle
position, so lateral nozzle motion does not change the geometric ROI rules.
This returns a CONSERVATIVE candidate mask: it is not a guaranteed object
segmentation. It is applied only where supported and never alters source video.
"""
from __future__ import annotations

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
    radii = [float(c.radius) for c in calibrations.values()]
    centers_x = [float(c.cx) for c in calibrations.values()]
    centers_y = [float(c.cy) for c in calibrations.values()]
    radius = max(radii)
    if radius < 15:
        return empty

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    # A horizontal closing highlights dark, vertically elongated metal tools
    # even when the arm moves sideways. A circular wafer edge has different
    # orientation and is not by itself sufficient to form a long component.
    bar_width = max(21, int(round(min(radius * 0.5, 101)))) | 1
    blackhat = cv2.morphologyEx(
        gray, cv2.MORPH_BLACKHAT,
        cv2.getStructuringElement(cv2.MORPH_RECT, (bar_width, 3)),
    )
    binary = (blackhat > 22).astype(np.uint8) * 255
    length = max(20, int(round(radius * 0.32)))
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, max(11, length // 2))),
    )
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, length)),
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    candidates = []
    upper = min(centers_y) - 1.25 * radius
    for label in range(1, count):
        x, y, w, h, _ = [int(value) for value in stats[label]]
        if (h < max(length, 65) or h < 1.65 * w or w > 0.48 * radius
                or x + w < min(centers_x) - 0.85 * radius
                or x > max(centers_x) + 0.85 * radius
                or y + h < upper or y > min(centers_y) - 0.1 * radius):
            continue
        candidates.append((h * min(w, 25), label))
    # Limit masking to the two dominant vertical shafts, not the entire
    # machinery: broad black metal rings must not be mistaken for a nozzle.
    mask = np.zeros((height, width), dtype=np.uint8)
    for _, label in sorted(candidates, reverse=True)[:2]:
        mask[labels == label] = 255
    dilation = max(5, int(round(radius * 0.09))) | 1
    mask = cv2.dilate(
        mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation, dilation)),
    )
    return mask > 0
