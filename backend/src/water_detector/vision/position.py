"""Select P1/P2/P3 using five or more saved screenshots per stage.

No material segmentation, circle fitting, rim edge scoring, enclosure heuristic,
or position guessing. At least five snapshots per stage are captured by the user
at known P1/P2/P3 positions. Matching compares normalized grayscale thumbnail
pixels near the union of their calibrated ROIs, excluding an optional moving
nozzle mask. A missing, incompatible, low-quality, or ambiguous screenshot
comparison must return UNKNOWN, never an assumed OK stage.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from water_detector.config.schema import Calibration

MIN_REFERENCES_PER_STAGE = 5

STAGES = ("P1", "P2", "P3")
THUMB_SIZE = (128, 96)


class PositionRecognizer:
    """Cached multi-screenshot recognizer with conservative rejection."""

    def __init__(
        self,
        references: dict[str, list[str]],
        calibrations: dict[str, Calibration] | None = None,
        *,
        min_similarity: float = 0.78,
        min_margin: float = 0.025,
    ):
        self.calibrations = calibrations or {}
        self.min_similarity = min_similarity
        self.min_margin = min_margin
        # Load, validate and downsample references ONCE per pipeline, not per
        # video frame. All 15+ screenshots use one shared image geometry.
        self.reference_thumbnails: dict[str, list[np.ndarray]] = {}
        self.reference_shape: tuple[int, int] | None = None
        self.last_scores: dict[str, float] = {}
        for stage in STAGES:
            thumbnails: list[np.ndarray] = []
            for filename in references.get(stage, []):
                try:
                    encoded = np.fromfile(Path(filename), dtype=np.uint8)
                    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                except (OSError, ValueError, cv2.error):
                    image = None
                if image is None:
                    continue
                shape = image.shape[:2]
                if self.reference_shape is None:
                    self.reference_shape = shape
                if shape != self.reference_shape:
                    continue
                thumbnails.append(self._thumbnail(image))
            self.reference_thumbnails[stage] = thumbnails

    @staticmethod
    def _thumbnail(frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (7, 7), 1.6)
        return cv2.resize(gray, THUMB_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)

    def _focus_mask(self, shape: tuple[int, int]) -> np.ndarray | None:
        """Common region of interest for all references, never stage-specific."""
        if len(self.calibrations) != 3:
            return None
        height, width = shape
        focus = np.zeros((height, width), dtype=np.uint8)
        for stage in STAGES:
            cal = self.calibrations.get(stage)
            if cal is None or (cal.reference_width, cal.reference_height) != (width, height):
                return None
            cv2.circle(focus, (round(cal.cx), round(cal.cy)),
                       round(1.20 * cal.radius), 255, -1)
        return cv2.resize(focus, THUMB_SIZE, interpolation=cv2.INTER_NEAREST) > 0

    @staticmethod
    def _similarity(source: np.ndarray, reference: np.ndarray, valid: np.ndarray) -> float:
        """Brightness-normalized screenshot similarity (no wafer edge detector)."""
        src = source[valid]
        ref = reference[valid]
        if src.size < 500:
            return 0.0
        src_std = float(src.std())
        ref_std = float(ref.std())
        if min(src_std, ref_std) < 3.0:
            return 0.0
        src = (src - src.mean()) / src_std
        ref = (ref - ref.mean()) / ref_std
        correlation = float(np.clip(np.mean(src * ref), -1.0, 1.0))
        error = float(np.mean(np.abs(src - ref)))
        return float(np.clip(
            0.65 * (correlation + 1.0) * 0.5 + 0.35 * np.exp(-error),
            0.0, 1.0
        ))

    def classify(
        self,
        frame: np.ndarray,
        nozzle_mask: np.ndarray | None = None,
    ) -> tuple[str, float, dict[str, float]]:
        self.last_scores = {}
        if (frame.ndim != 3 or self.reference_shape != frame.shape[:2] or
                any(len(self.reference_thumbnails[stage]) < MIN_REFERENCES_PER_STAGE
                    for stage in STAGES)):
            return "UNKNOWN", 0.0, {}
        shape = frame.shape[:2]
        focus = self._focus_mask(shape)
        if focus is None:
            return "UNKNOWN", 0.0, {}
        if nozzle_mask is not None:
            if nozzle_mask.shape != shape:
                raise ValueError("Nozzle mask shape does not match frame")
            mask_small = cv2.resize(
                nozzle_mask.astype(np.uint8), THUMB_SIZE,
                interpolation=cv2.INTER_NEAREST
            ).astype(bool)
            focus &= ~mask_small
        if focus.sum() < 500:
            return "UNKNOWN", 0.0, {}

        current = self._thumbnail(frame)
        # One stage is represented by a pool, not by a single idealized
        # frame. Best match tolerates changing spray, lighting and nozzle
        # position; the mask excludes the detected nozzle in the live frame.
        scores = {
            stage: round(
                max(self._similarity(current, thumb, focus)
                    for thumb in self.reference_thumbnails[stage]),
                5,
            )
            for stage in STAGES
        }
        self.last_scores = scores
        ranked = sorted(scores, key=scores.__getitem__, reverse=True)
        winner, runner_up = ranked[:2]
        best = scores[winner]
        if best < self.min_similarity or best - scores[runner_up] < self.min_margin:
            return "UNKNOWN", best, scores
        return winner, best, scores
