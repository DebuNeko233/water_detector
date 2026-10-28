"""Conservative appearance-based position recognition (EXPERIMENTAL).

No factory transition state machine. Returns UNKNOWN whenever references cannot
be distinguished confidently. Do not use as a production interlock yet.
"""
from __future__ import annotations
from pathlib import Path
import cv2
import numpy as np


def edge_descriptor(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (128, 96), interpolation=cv2.INTER_AREA)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge = cv2.magnitude(gx, gy)
    # Reduce sensitivity to absolute brightness and dynamic fine spray.
    return cv2.GaussianBlur(edge, (5, 5), 0)


class PositionRecognizer:
    def __init__(self, paths: dict[str, str], threshold: float = 0.78, margin: float = 0.035):
        self.reference = {}
        self.threshold, self.margin = threshold, margin
        for key in ("P1", "P2", "P3"):
            path = paths.get(key)
            if path and Path(path).is_file():
                # cv2.imread(path) can fail for non-ASCII Windows user/profile paths.
                # Read raw bytes first, then decode without filesystem encoding assumptions.
                image = cv2.imdecode(np.frombuffer(Path(path).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is not None:
                    self.reference[key] = edge_descriptor(image)

    def classify(self, frame: np.ndarray) -> tuple[str, float, dict[str, float]]:
        if len(self.reference) != 3:
            return "UNKNOWN", 0.0, {}
        current = edge_descriptor(frame)
        scores = {}
        for key, ref in self.reference.items():
            val = cv2.matchTemplate(current, ref, cv2.TM_CCOEFF_NORMED)[0, 0]
            scores[key] = float(val) if np.isfinite(val) else -1.0
        ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best, runner = ordered[0], ordered[1]
        if best[1] < self.threshold or best[1] - runner[1] < self.margin:
            return "UNKNOWN", best[1], scores
        return best[0], best[1], scores
