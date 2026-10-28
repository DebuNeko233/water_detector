"""Conservative wafer-POSITION recognition using the three calibrated ROI circles.

No reference photographs and no whole-frame appearance matching. For each of
the three already-calibrated wafer positions, seek a circular edge near its
expected centre and radius. Choose the geometrically supported position only
when it is unambiguous; otherwise UNKNOWN/PAUSED (never fabricate an OK).

This is experimental vision: rim occlusion, defocus, glare, an absent wafer or
background circular fixtures can still cause failures. Validate with production
videos before treating the outcome as a reliable equipment-state signal.
"""
from __future__ import annotations

from math import hypot

import cv2
import numpy as np

from water_detector.config.schema import Calibration


class PositionRecognizer:
    def __init__(
        self,
        calibrations: dict[str, Calibration],
        *,
        min_support: float = 0.32,
        min_margin: float = 0.09,
    ):
        self.calibrations = {
            stage: calibrations[stage]
            for stage in ("P1", "P2", "P3")
            if stage in calibrations
        }
        self.min_support = min_support
        self.min_margin = min_margin
        theta = np.linspace(0.0, 2.0 * np.pi, 128, endpoint=False)
        self._cos = np.cos(theta)
        self._sin = np.sin(theta)
        self.last_center: tuple[float, float] | None = None
        self.last_radius: float | None = None

    def classify(self, frame: np.ndarray) -> tuple[str, float, dict[str, float]]:
        """Return (ROI name, circular-edge support, per-ROI support).

        Support is a geometry score in [0, 1], NOT an ML probability.
        Invalid/incomplete/non-distinct three-position calibrations fail closed.
        """
        self.last_center = None
        self.last_radius = None
        if len(self.calibrations) != 3 or frame.ndim != 3:
            return "UNKNOWN", 0.0, {}
        h, w = frame.shape[:2]
        for calibration in self.calibrations.values():
            if (calibration.reference_width, calibration.reference_height) != (w, h):
                return "UNKNOWN", 0.0, {}

        items = list(self.calibrations.items())
        for i, (_, left) in enumerate(items):
            for _, right in items[i + 1:]:
                # All three ROI centres must represent distinguishable positions.
                if hypot(left.cx - right.cx, left.cy - right.cy) < 0.5 * min(
                    left.radius, right.radius
                ):
                    return "UNKNOWN", 0.0, {}

        # Bound CPU cost for large industrial videos, preserving the ROI geometry.
        scale = min(1.0, 960.0 / max(w, h))
        image = (
            cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if scale < 1.0 else frame
        )
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 1.3), 45, 115)
        # Tolerate small rim thickness/blur but not large diffuse boundaries.
        edges = cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8))
        height, width = edges.shape

        scores: dict[str, float] = {}
        circles: dict[str, tuple[float, float, float]] = {}
        for stage, cal in items:
            x0, y0, radius = cal.cx * scale, cal.cy * scale, cal.radius * scale
            best_score = 0.0
            best_circle = (x0, y0, radius)
            if radius < 8:
                scores[stage] = 0.0
                continue
            # Local center search identifies which known wafer POSITION contains
            # a rim. Radius variation allows slightly imperfect manual ROI setup.
            for offset_x in (-0.16, -0.08, 0.0, 0.08, 0.16):
                for offset_y in (-0.16, -0.08, 0.0, 0.08, 0.16):
                    cx = x0 + offset_x * radius
                    cy = y0 + offset_y * radius
                    for radius_factor in (0.88, 0.94, 1.0, 1.06, 1.12):
                        r = radius * radius_factor
                        xs = np.rint(cx + r * self._cos).astype(np.int32)
                        ys = np.rint(cy + r * self._sin).astype(np.int32)
                        inside = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
                        if np.mean(inside) < 0.75:
                            continue
                        samples = np.zeros(len(xs), dtype=np.uint8)
                        samples[inside] = edges[ys[inside], xs[inside]] > 0
                        # Require a widely distributed circular edge rather than
                        # one noisy line/nozzle segment in the predicted region.
                        covered_quadrants = sum(
                            np.mean(samples[q * 32:(q + 1) * 32]) > 0.14
                            for q in range(4)
                        )
                        if covered_quadrants < 3:
                            continue
                        support = float(np.mean(samples))
                        if support > best_score:
                            best_score = support
                            best_circle = (cx, cy, r)
            scores[stage] = round(best_score, 4)
            circles[stage] = best_circle

        ranked = sorted(scores, key=scores.__getitem__, reverse=True)
        leader, runner_up = ranked[:2]
        confidence = scores[leader]
        if confidence < self.min_support or confidence - scores[runner_up] < self.min_margin:
            return "UNKNOWN", confidence, scores

        cx, cy, radius = circles[leader]
        self.last_center = (round(cx / scale, 2), round(cy / scale, 2))
        self.last_radius = round(radius / scale, 2)
        return leader, confidence, scores
