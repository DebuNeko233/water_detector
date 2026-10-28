"""Wafer material-and-edge localization, then P1/P2/P3 calibrated ROI choice.

Uses only the three manually calibrated locations; never compares against
reference photographs. A detected circular edge alone is NOT wafer evidence:
the region inside the candidate must also have an appearance distribution
different from the surrounding equipment. No confident candidate => UNKNOWN.

Material means locally measured Lab colour/luminance distribution (including
specular illumination differences), not a hard-coded wafer colour or a neural
network material classifier. This is conservative experimental machine vision;
a persistent circular fixture can still mimic a wafer, so evaluate on real
videos (including wafer-absent footage) before using for production interlocks.
"""
from __future__ import annotations

from math import hypot, exp

import cv2
import numpy as np

from water_detector.config.schema import Calibration
from water_detector.vision.nozzle import nozzle_occlusion_mask


class PositionRecognizer:
    """Locate wafer rim + material boundary near each of three configured ROIs.

    Scores are normalized geometric/photometric evidence, NOT probabilities.
    Rejection requires sufficient edge coverage, inside/outside material
    difference, and separation from the second-best position.
    """

    def __init__(
        self, calibrations: dict[str, Calibration], *,
        min_support: float = 0.32,
        min_material: float = 0.16,
        min_margin: float = 0.045,
    ):
        self.calibrations = {
            key: calibrations[key]
            for key in ("P1", "P2", "P3") if key in calibrations
        }
        self.min_support = min_support
        self.min_material = min_material
        self.min_margin = min_margin
        angle = np.linspace(0.0, 2.0 * np.pi, 128, endpoint=False)
        self._cos = np.cos(angle)
        self._sin = np.sin(angle)
        self.last_center: tuple[float, float] | None = None
        self.last_radius: float | None = None
        self.last_features: dict[str, dict[str, float]] = {}
        self.last_nozzle_fraction: float = 0.0

    def _material_score(self, lab: np.ndarray, texture: np.ndarray,
                        cx: float, cy: float, radius: float,
                        occluded: np.ndarray | None = None) -> float:
        """Robust colour/reflectance distribution separation across the rim.

        Several concentric samples tolerate spray, a nozzle, and highlights.
        There is intentionally no saved surface template or assumed wafer hue.
        """
        height, width = lab.shape[:2]

        def samples(factors: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
            xs = np.concatenate([
                np.rint(cx + radius * factor * self._cos).astype(np.int32)
                for factor in factors
            ])
            ys = np.concatenate([
                np.rint(cy + radius * factor * self._sin).astype(np.int32)
                for factor in factors
            ])
            valid = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
            if occluded is not None:
                valid &= ~occluded[np.clip(ys, 0, height - 1), np.clip(xs, 0, width - 1)]
            return ys[valid], xs[valid]

        # Compare wafer SURFACE with the outer equipment, not the thin
        # bright rim itself: otherwise an empty metal circle can look like a
        # filled wafer when the radius search lands just inside its outline.
        inner_y, inner_x = samples((0.35, 0.48, 0.60, 0.72))
        outer_y, outer_x = samples((1.20, 1.32, 1.43))
        inside = lab[inner_y, inner_x]
        outside = lab[outer_y, outer_x]
        if min(len(inside), len(outside)) < 120:
            return 0.0
        # Lab channel scale differences: L has a much larger physical span
        # than a/b, which describe chromatic wafer-vs-background differences.
        color_difference = np.linalg.norm(
            (np.median(inside, axis=0) - np.median(outside, axis=0))
            / np.array([80.0, 32.0, 32.0])
        )
        # Polished/metallic wafers have specular reflectance. Comparing robust
        # brightness quantiles captures a different distribution even when the
        # two regions have nearly the same median intensity.
        iq = np.percentile(inside[:, 0], [15, 50, 85])
        oq = np.percentile(outside[:, 0], [15, 50, 85])
        distribution_difference = np.linalg.norm((iq - oq) / 90.0) / np.sqrt(3.0)
        # Fine surface texture (water/specular micro-structure) can differ
        # even if the wafer and surrounding metal have similar median colour.
        # This is deliberately an inside-vs-outside statistic, not a fixed
        # texture template, so changing illumination is more tolerable.
        inner_texture = texture[inner_y, inner_x]
        outer_texture = texture[outer_y, outer_x]
        texture_difference = abs(
            float(np.median(inner_texture) - np.median(outer_texture))
        ) / 70.0
        return float(np.clip(
            max(color_difference, distribution_difference, texture_difference) / 1.2,
            0, 1,
        ))

    def _direct_roi_enclosure(self, lab: np.ndarray, cx: float, cy: float,
                              radius: float,
                              occluded: np.ndarray | None = None) -> float:
        """Compare HOW MUCH wafer material each SAVED ROI actually encloses.

        An adaptive surface threshold is found between the central wafer
        surface and the outside background. Count surface coverage inside the
        ROI and penalize any surface spilling beyond its circular boundary.
        This resolves overlapping presets (P1/P2/P3), unlike mere brightness
        or nearest-circle-centre tests, even for an obscured circular edge.
        """
        height, width = lab.shape[:2]
        pad = 1.45 * radius
        left, right = max(0, int(cx - pad)), min(width, int(cx + pad) + 1)
        top, bottom = max(0, int(cy - pad)), min(height, int(cy + pad) + 1)
        if right <= left or bottom <= top:
            return 0.0
        yy, xx = np.ogrid[top:bottom, left:right]
        normalized_radius = np.hypot(xx - cx, yy - cy) / radius
        brightness = lab[top:bottom, left:right, 0]
        allowed = (
            ~occluded[top:bottom, left:right] if occluded is not None
            else np.ones(brightness.shape, dtype=bool)
        )
        core = brightness[(normalized_radius < 0.70) & allowed]
        background = brightness[
            (normalized_radius > 1.18) & (normalized_radius < 1.42) & allowed
        ]
        if min(core.size, background.size) < 100:
            return 0.0
        center_l = float(np.median(core))
        background_l = float(np.median(background))
        # A thin metal machine ring is NOT a filled wafer: both regions then
        # have essentially the same underlying brightness.
        if center_l - background_l < 12.0:
            return 0.0
        wafer_surface = brightness > 0.5 * (center_l + background_l)
        inside = (normalized_radius < 1.0) & allowed
        outside = (normalized_radius > 1.02) & (normalized_radius < 1.42) & allowed
        if min(int(inside.sum()), int(outside.sum())) < 100:
            return 0.0
        coverage = float(np.mean(wafer_surface[inside]))
        spill = float(np.mean(wafer_surface[outside]))
        # A ROI which cuts the wafer off at its perimeter receives a lower
        # score than one containing the whole disk with minimal background.
        return float(np.clip(coverage - 1.3 * spill, 0, 1))

    def classify(self, frame: np.ndarray, nozzle_mask: np.ndarray | None = None) -> tuple[str, float, dict[str, float]]:
        """(stage or UNKNOWN, best combined support, per-stage combined support)."""
        self.last_center = None
        self.last_radius = None
        self.last_features = {}
        self.last_nozzle_fraction = 0.0
        if frame.ndim != 3 or len(self.calibrations) != 3:
            return "UNKNOWN", 0.0, {}

        height, width = frame.shape[:2]
        items = list(self.calibrations.items())
        if any(
            (cal.reference_width, cal.reference_height) != (width, height)
            for cal in self.calibrations.values()
        ):
            return "UNKNOWN", 0.0, {}
        # P1/P2/P3 often overlap heavily (e.g. concentric-looking discs at
        # different heights in the same camera view). Overlap is ALLOWED.
        # Reject only duplicate ROI definitions, where selection is impossible.
        for index, (_, left) in enumerate(items):
            for _, right in items[index + 1:]:
                if (hypot(left.cx - right.cx, left.cy - right.cy) < 2
                        and abs(left.radius - right.radius) < 2):
                    return "UNKNOWN", 0.0, {}

        # Processing downsample keeps high-resolution playback responsive.
        scale = min(1.0, 960.0 / max(height, width))
        image = (
            cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if scale < 1.0 else frame
        )
        nozzle = nozzle_mask if nozzle_mask is not None else nozzle_occlusion_mask(frame, self.calibrations)
        if nozzle.shape != frame.shape[:2]:
            raise ValueError("Nozzle mask shape does not match frame")
        self.last_nozzle_fraction = round(float(nozzle.mean()), 5)
        if scale < 1.0:
            nozzle = cv2.resize(nozzle.astype(np.uint8),
                                (image.shape[1], image.shape[0]),
                                interpolation=cv2.INTER_NEAREST).astype(bool)
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        texture = cv2.magnitude(
            cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3),
            cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3),
        )
        edges = cv2.dilate(
            cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 1.3), 45, 115),
            np.ones((3, 3), dtype=np.uint8),
        )
        h, w = edges.shape
        geometric: dict[str, float] = {}
        circles: dict[str, tuple[float, float, float]] = {}
        scores: dict[str, float] = {}

        for stage, cal in items:
            expected_x = cal.cx * scale
            expected_y = cal.cy * scale
            expected_r = cal.radius * scale
            best_edge = 0.0
            best_fit = 0.0
            best_ranking = 0.0
            best_circle = None
            if expected_r >= 8:
                # Search only near the manually established wafer position.
                for dx in (-0.16, -0.08, 0.0, 0.08, 0.16):
                    for dy in (-0.16, -0.08, 0.0, 0.08, 0.16):
                        cx = expected_x + dx * expected_r
                        cy = expected_y + dy * expected_r
                        for radius_factor in (0.88, 0.94, 1.0, 1.06, 1.12):
                            radius = expected_r * radius_factor
                            xs = np.rint(cx + radius * self._cos).astype(np.int32)
                            ys = np.rint(cy + radius * self._sin).astype(np.int32)
                            valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
                            if valid.mean() < 0.75:
                                continue
                            # Exclude only actually occluded angular samples;
                            # an unseen section is not evidence that the wafer
                            # has no rim. Do not erase entire image sectors.
                            valid &= ~nozzle[
                                np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)
                            ]
                            if valid.sum() < 72:
                                continue
                            ring = np.zeros(128, dtype=np.uint8)
                            ring[valid] = edges[ys[valid], xs[valid]] > 0
                            quadrants = sum(
                                valid[q * 32:(q + 1) * 32].sum() >= 12 and
                                ring[q * 32:(q + 1) * 32].sum() /
                                valid[q * 32:(q + 1) * 32].sum() > 0.14
                                for q in range(4)
                            )
                            if quadrants < 3:
                                continue
                            support = float(ring.sum() / valid.sum())
                            # Which of the THREE PRESET ROIs actually frames
                            # this wafer? Reward the candidate whose observed
                            # rim agrees with this ROI's centre AND radius.
                            # This breaks the old tie between highly overlapping
                            # P2/P3 without requiring non-overlapping centres.
                            distance = hypot(cx - expected_x, cy - expected_y)
                            normalized_distance = distance / max(expected_r, 1.0)
                            normalized_radius = abs(radius - expected_r) / max(expected_r, 1.0)
                            fit = exp(-0.5 * (
                                (normalized_distance / 0.13) ** 2 +
                                (normalized_radius / 0.11) ** 2
                            ))
                            ranking = support * (0.35 + 0.65 * fit)
                            if ranking > best_ranking:
                                best_ranking = ranking
                                best_edge = support
                                best_fit = fit
                                best_circle = (cx, cy, radius)

            # Compare WHICH ROI contains the actual wafer surface, using
            # its exact saved circle rather than requiring ROI separation.
            enclosure = self._direct_roi_enclosure(
                lab, expected_x, expected_y, expected_r, nozzle
            )
            geometric[stage] = best_edge
            material = (
                self._material_score(lab, texture, *best_circle, nozzle)
                if best_circle is not None else 0.0
            )
            # A circle with no inside/outside material difference is likely
            # just a machine ring: it must not be classified as a wafer.
            score = (
                (0.57 * best_edge + 0.43 * material) * (0.2 + 0.8 * best_fit)
                if material >= self.min_material else 0.0
            )
            scores[stage] = round(float(score), 4)
            self.last_features[stage] = {
                "edge": round(best_edge, 4),
                "material": round(material, 4),
                "fit": round(best_fit, 4),
                "enclosure": round(enclosure, 4),
            }
            if best_circle is not None:
                circles[stage] = best_circle

        ranked = sorted(scores, key=scores.__getitem__, reverse=True)
        leader, runner = ranked[:2]
        best = scores[leader]
        if (
            geometric[leader] < self.min_support
            or self.last_features[leader]["material"] < self.min_material
            or best < 0.28
            or best - scores[runner] < self.min_margin
        ):
            # Direct ROI occupancy fallback: the bright polished wafer may
            # fill the centre but have no usable circular rim (first frames,
            # water spray, partial tool occlusion). Only decide if one saved
            # circle encloses substantially more of that material than either
            # alternative. An empty rim alone has near-zero enclosure.
            enclosed = {
                stage: feature["enclosure"]
                for stage, feature in self.last_features.items()
            }
            order = sorted(enclosed, key=enclosed.__getitem__, reverse=True)
            first, second = order[:2]
            if enclosed[first] >= 0.30 and enclosed[first] - enclosed[second] >= 0.045:
                # Do not fabricate a measured circle centre from the
                # calibration when only surface occupancy is available.
                return first, enclosed[first], enclosed
            return "UNKNOWN", best, scores

        cx, cy, radius = circles[leader]
        self.last_center = (round(cx / scale, 2), round(cy / scale, 2))
        self.last_radius = round(radius / scale, 2)
        return leader, best, scores
