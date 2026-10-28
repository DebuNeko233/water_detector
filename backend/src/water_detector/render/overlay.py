"""Presentation only: no scoring logic belongs here."""
from __future__ import annotations
import math
import cv2
import numpy as np
from water_detector.config.schema import Calibration, DetectorConfig


def render_overlay(frame: np.ndarray, calib: Calibration, cfg: DetectorConfig, result: dict,
                   *, show_roi: bool = True,
                   nozzle_mask: np.ndarray | None = None) -> np.ndarray:
    out = frame.copy()
    c = (int(round(calib.cx)), int(round(calib.cy)))
    r_in = int(round(cfg.inner_radius_ratio * calib.radius))
    r_out = int(round(cfg.outer_radius_ratio * calib.radius))
    persistent = result.get("persistent")
    if persistent is not None:
        tinted = out.copy()
        tinted[persistent] = (55, 40, 255)
        out = cv2.addWeighted(tinted, 0.45, out, 0.55, 0)
    if nozzle_mask is not None and np.any(nozzle_mask):
        # Optional visual confirmation of excluded pixels (cyan). These
        # pixels do not contribute to S_water and are not abnormal water.
        out[nozzle_mask.astype(bool)] = (
            0.45 * out[nozzle_mask.astype(bool)] + 0.55 * np.array([255, 205, 40])
        ).astype(np.uint8)
    if not show_roi:
        # Keep only actual detected persistent-water pixels unless the
        # operator explicitly requested nozzle exclusion visualization.
        return out
    cv2.circle(out, c, r_in, (255, 180, 0), 2)
    cv2.circle(out, c, r_out, (70, 235, 100), 2)
    n = cfg.sector_count
    for k in range(n):
        a = 2 * math.pi * k / n
        p1 = (int(round(c[0] + r_in * math.cos(a))), int(round(c[1] + r_in * math.sin(a))))
        p2 = (int(round(c[0] + r_out * math.cos(a))), int(round(c[1] + r_out * math.sin(a))))
        cv2.line(out, p1, p2, (100, 100, 100), 1, cv2.LINE_AA)
        if result.get("sector_scores", [0] * n)[k] >= cfg.sector_threshold:
            cv2.ellipse(out, c, (r_out, r_out), 0, 360 * k / n, 360 * (k + 1) / n, (0, 0, 255), 4, cv2.LINE_AA)
    return out


def jpeg_bytes(frame: np.ndarray, quality: int = 82) -> bytes:
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return data.tobytes()
