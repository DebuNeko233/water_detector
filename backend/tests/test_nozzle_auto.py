"""End-to-end dynamic nozzle and full-video independent algorithm testing."""
from __future__ import annotations

import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from water_detector.config.manager import ConfigManager
from water_detector.config.schema import Calibration, DetectorConfig
from water_detector.detector.water import WaterDetector
from water_detector.service.api import create_app
from water_detector.vision.nozzle import DynamicNozzleFilter


def _calibration() -> Calibration:
    return Calibration(cx=80, cy=70, radius=48,
                       reference_width=160, reference_height=120)


def _nozzle_frame(x: int, *, water: bool = False) -> np.ndarray:
    frame = np.full((120, 160, 3), 56, dtype=np.uint8)
    cv2.circle(frame, (80, 70), 48, (165, 175, 185), -1)
    # The elongated tool arrives from above and moves laterally.
    cv2.rectangle(frame, (x, 0), (x + 13, 92), (18, 18, 18), -1)
    if water:
        cv2.circle(frame, (111, 77), 8, (235, 235, 235), -1)
    return frame


def test_dynamic_nozzle_mask_moves_with_shaft_and_does_not_erase_water_region():
    cal = _calibration()
    tracker = DynamicNozzleFilter((120, 160), 30.0, {"P1": cal})
    for x in (69, 75, 81):
        mask = tracker.detect(_nozzle_frame(x, water=True))
        assert mask.dtype == bool
        assert mask.shape == (120, 160)
        assert np.any(mask[:, x + 6]), x
        assert not mask[77, 111]  # A water patch apart from the moving nozzle.
        yy, xx = np.ogrid[:120, :160]
        rr = np.hypot(xx - 80, yy - 70)
        roi = (rr >= 0.65 * 48) & (rr <= 0.95 * 48)
        assert mask[roi].mean() < 0.30
    tracker.reset()
    assert not tracker.recent


def test_masked_persistent_water_uses_remaining_valid_roi_and_history():
    cal = _calibration()
    settings = DetectorConfig(
        diff_threshold=5, history_seconds=0.1,
        persistence_threshold=0.3, s_water_threshold=0.01,
    )
    base = WaterDetector((120, 160), 20.0, cal, settings)
    masked = WaterDetector((120, 160), 20.0, cal, settings)
    yy, xx = np.ogrid[:120, :160]
    # Nozzle travels around the upper arc of the ROI.
    mask = np.zeros((120, 160), dtype=bool)
    mask[20:90, 60:82] = True
    for index in range(12):
        frame = _nozzle_frame(60 + (index % 3) * 3)
        old = base.process(frame)
        new = masked.process(frame, exclusion_mask=mask)
    assert new["s_water"] <= old["s_water"], (old, new)
    assert new["excluded_pixels"] > 0
    assert new["s_water"] >= 0


def test_full_video_auto_test_runs_independently_with_nozzle_statistics(tmp_path):
    filename = tmp_path / "moving-nozzle.avi"
    writer = cv2.VideoWriter(str(filename), cv2.VideoWriter_fourcc(*"MJPG"),
                             15.0, (160, 120))
    assert writer.isOpened()
    for i in range(24):
        writer.write(_nozzle_frame(65 + i % 8))
    writer.release()

    root = tmp_path / "data"
    with TestClient(create_app(ConfigManager(root))) as client:
        saved = client.get("/api/config/active").json()
        request = {
            "source": {"kind": "file", "address": str(filename), "device_index": 0},
            "stage_mode": "single", "stage": "P1", "frame_index": 0,
            "calibration": _calibration().model_dump(),
            "calibrations": {"P1": _calibration().model_dump()},
            "detector": {**saved["detector"], "nozzle_filter_enabled": True},
        }
        first = client.post("/api/debug/auto-test/start", json=request)
        assert first.status_code == 200, first.text
        deadline = time.monotonic() + 12
        result = None
        while time.monotonic() < deadline:
            result = client.get("/api/debug/auto-test/status").json()
            if result["state"] in ("COMPLETED", "ERROR"):
                break
            time.sleep(.025)
        assert result is not None and result["state"] == "COMPLETED", result
        assert result["processed_frames"] == 24
        assert result["total_frames"] == 24
        assert result["progress"] == 1.0
        assert result["stage_counts"]["P1"] == 24
        assert sum(result["status_counts"].values()) == 24
        assert result["nozzle_excluded_frames"] > 0
        assert result["average_nozzle_excluded_fraction"] > 0
        assert result["events"]
        assert client.get("/api/config/active").json() == saved
        assert client.get("/api/monitor/status").json()["state"] == "IDLE"
        assert not (root / "results").exists()
