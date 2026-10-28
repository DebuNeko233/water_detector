"""One-frame orchestration; detector is isolated from acquisition and API."""
from __future__ import annotations
import numpy as np
from water_detector.config.schema import AppConfig
from water_detector.detector.water import WaterDetector
from water_detector.models.frames import FramePacket
from water_detector.render.overlay import render_overlay
from water_detector.vision.position import PositionRecognizer
from water_detector.vision.nozzle import DynamicNozzleFilter


class InspectionPipeline:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg.model_copy(deep=True)
        self.detectors: dict[str, WaterDetector] = {}
        self.last_stage = None
        self.recognizer = PositionRecognizer(cfg.calibrations)
        self.nozzle_filter: DynamicNozzleFilter | None = None

    def reset(self):
        for det in self.detectors.values():
            det.reset()
        self.last_stage = None
        if self.nozzle_filter is not None:
            self.nozzle_filter.reset()

    def process(self, packet: FramePacket, *, show_roi: bool = True,
                show_nozzle_mask: bool = False) -> tuple[dict, np.ndarray]:
        nozzle_mask = None
        if self.cfg.detector.nozzle_filter_enabled:
            shape = packet.image.shape[:2]
            if self.nozzle_filter is None or self.nozzle_filter.shape != shape:
                self.nozzle_filter = DynamicNozzleFilter(shape, packet.fps, self.cfg.calibrations)
            nozzle_mask = self.nozzle_filter.detect(packet.image)
        if self.cfg.stage_mode == "single":
            stage, conf, scores = self.cfg.selected_stage, 1.0, {}
            center, detected_radius = None, None
        else:
            stage, conf, scores = self.recognizer.classify(packet.image, nozzle_mask=nozzle_mask if nozzle_mask is not None else np.zeros(packet.image.shape[:2], dtype=bool))
            center = self.recognizer.last_center
            detected_radius = self.recognizer.last_radius
        position_data = {
            "wafer_center": list(center) if center is not None else None,
            "wafer_radius": detected_radius,
            "position_features": (
                dict(self.recognizer.last_features) if self.cfg.stage_mode == "auto" else {}
            ),
        }
        calib = self.cfg.calibrations.get(stage)
        if stage == "UNKNOWN" or calib is None:
            self.reset()
            result = dict(status="PAUSED", reason="POSITION_UNKNOWN" if stage == "UNKNOWN" else "CALIBRATION_MISSING",
                          stage=stage, position_confidence=conf, position_scores=scores, **position_data, s_water=0.0, sector_scores=[],
                          max_sector_idx=-1, abnormal_run_s=0.0, timestamp_s=packet.timestamp_s,
                          frame_id=packet.frame_id, history=[])
            return result, packet.image.copy()
        h, w = packet.image.shape[:2]
        if (w, h) != (calib.reference_width, calib.reference_height):
            self.reset()
            result = dict(status="PAUSED", reason="RESOLUTION_MISMATCH", stage=stage,
                          position_confidence=conf, position_scores=scores, **position_data,
                          s_water=0.0, sector_scores=[], max_sector_idx=-1,
                          abnormal_run_s=0.0, timestamp_s=packet.timestamp_s, frame_id=packet.frame_id,
                          history=[])
            return result, packet.image.copy()
        if stage != self.last_stage:
            self.reset()
            self.last_stage = stage
        det = self.detectors.get(stage)
        if det is None or abs(det.fps - packet.fps) > 0.01:
            det = WaterDetector((h, w), packet.fps, calib, self.cfg.detector)
            self.detectors[stage] = det
        raw = det.process(packet.image, exclusion_mask=nozzle_mask)
        result = dict(status=raw["status"], reason="", stage=stage,
                      position_confidence=conf, position_scores=scores, **position_data, s_water=raw["s_water"],
                      nozzle_excluded_pixels=int(np.count_nonzero(nozzle_mask & det.roi)) if nozzle_mask is not None else 0,
                      nozzle_excluded_fraction=(float(np.count_nonzero(nozzle_mask & det.roi)) / det.roi_count) if nozzle_mask is not None else 0.0,
                      sector_scores=raw["sector_scores"], max_sector_idx=raw["max_sector_idx"],
                      abnormal_run_s=raw["abnormal_run_s"], timestamp_s=packet.timestamp_s,
                      frame_id=packet.frame_id,
                      history=[round(v, 6) for v in det.score_history])
        return result, render_overlay(packet.image, calib, self.cfg.detector, raw,
                                      show_roi=show_roi,
                                      nozzle_mask=nozzle_mask if show_nozzle_mask else None)
