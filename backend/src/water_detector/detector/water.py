"""Faithful adaptation of the user's main.py WaterDetector.

This module intentionally keeps the original calculation sequence and defaults.
Do not add nozzle masks or brightness normalization here: those are separate
future pipeline changes and must not silently change regression results.
"""
from __future__ import annotations
from collections import deque
import numpy as np
import cv2
from water_detector.config.schema import DetectorConfig, Calibration


class WaterDetector:
    def __init__(self, shape_hw: tuple[int, int], fps: float, calib: Calibration, cfg: DetectorConfig):
        self.h, self.w = shape_hw
        self.fps = fps if fps > 1e-6 else 30.0
        self.calib, self.cfg = calib, cfg
        self.history_frames = max(2, int(round(cfg.history_seconds * self.fps)))
        self.activity_history = deque(maxlen=self.history_frames)
        self.score_history = deque(maxlen=max(10, int(round(5.0 * self.fps))))
        self.prev_gray = None
        self.abnormal_run_s = 0.0
        self.recover_run_s = 0.0
        self.status = "WARMUP"
        self._build_geometry()

    def _build_geometry(self):
        yy, xx = np.mgrid[0:self.h, 0:self.w]
        dx = xx.astype(np.float32) - float(self.calib.cx)
        dy = yy.astype(np.float32) - float(self.calib.cy)
        r = np.sqrt(dx * dx + dy * dy)
        self.phi = (np.arctan2(dy, dx) + 2.0 * np.pi) % (2.0 * np.pi)
        inner = self.cfg.inner_radius_ratio * self.calib.radius
        outer = self.cfg.outer_radius_ratio * self.calib.radius
        self.roi = (r >= inner) & (r <= outer)
        self.roi_count = int(np.count_nonzero(self.roi))
        if self.roi_count < 100:
            raise ValueError("ROI too small; check center and radius calibration")
        self.sector_masks = []
        self.sector_counts = []
        for k in range(self.cfg.sector_count):
            a0 = 2 * np.pi * k / self.cfg.sector_count
            a1 = 2 * np.pi * (k + 1) / self.cfg.sector_count
            mask = self.roi & (self.phi >= a0) & (self.phi < a1)
            self.sector_masks.append(mask)
            self.sector_counts.append(max(1, int(np.count_nonzero(mask))))

    def _prep_gray(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        k = int(self.cfg.blur_ksize)
        if k >= 3:
            if k % 2 == 0:
                k += 1
            gray = cv2.GaussianBlur(gray, (k, k), 0)
        return gray

    def reset(self):
        self.activity_history.clear()
        self.prev_gray = None
        self.abnormal_run_s = self.recover_run_s = 0.0
        self.status = "WARMUP"

    def process(self, frame):
        gray = self._prep_gray(frame)
        dt = 1.0 / self.fps
        if self.prev_gray is None:
            self.prev_gray = gray
            self.score_history.append(0.0)
            return self._empty_result()
        cur = gray.astype(np.float32)
        pre = self.prev_gray.astype(np.float32)
        signed_diff = cur - pre
        global_change = float(np.median(signed_diff[self.roi]))
        diff = np.abs(signed_diff - global_change)
        active = (diff > self.cfg.diff_threshold) & self.roi
        self.activity_history.append(active)
        self.prev_gray = gray
        if len(self.activity_history) < self.history_frames:
            self.score_history.append(0.0)
            return self._empty_result(active=active, diff=diff)
        stack = np.stack(self.activity_history, axis=0)
        persistence = stack.mean(axis=0)
        persistent = (persistence >= self.cfg.persistence_threshold) & self.roi
        s_water = float(np.count_nonzero(persistent) / self.roi_count)
        sector_scores = [float(np.count_nonzero(persistent & mask) / count)
                         for mask, count in zip(self.sector_masks, self.sector_counts)]
        max_sector_idx = int(np.argmax(sector_scores)) if sector_scores else -1
        max_sector_score = sector_scores[max_sector_idx] if max_sector_idx >= 0 else 0.0
        instant_abnormal = s_water >= self.cfg.s_water_threshold
        if instant_abnormal:
            self.abnormal_run_s += dt
            self.recover_run_s = 0.0
        else:
            self.recover_run_s += dt
            if self.status != "NG":
                self.abnormal_run_s = 0.0
        if self.status in ("WARMUP", "OK"):
            if self.abnormal_run_s >= self.cfg.ng_hold_seconds:
                self.status = "NG"
            else:
                self.status = "OK"
        elif self.status == "NG":
            if instant_abnormal:
                self.recover_run_s = 0.0
            elif self.recover_run_s >= self.cfg.recover_hold_seconds:
                self.status = "OK"
                self.abnormal_run_s = 0.0
                self.recover_run_s = 0.0
        self.score_history.append(s_water)
        return dict(status=self.status, s_water=s_water, active=active,
                    persistent=persistent, persistence=persistence, diff=diff,
                    global_change=global_change, sector_scores=sector_scores,
                    max_sector_idx=max_sector_idx, max_sector_score=max_sector_score,
                    abnormal_run_s=self.abnormal_run_s, history_ready=True)

    def _empty_result(self, active=None, diff=None):
        if self.status == "WARMUP" and len(self.activity_history) >= self.history_frames:
            self.status = "OK"
        return dict(status="WARMUP" if len(self.activity_history) < self.history_frames else self.status,
                    s_water=0.0, active=active, persistent=None, persistence=None,
                    diff=diff, global_change=0.0, sector_scores=[0.0] * self.cfg.sector_count,
                    max_sector_idx=-1, max_sector_score=0.0,
                    abnormal_run_s=self.abnormal_run_s, history_ready=False)
