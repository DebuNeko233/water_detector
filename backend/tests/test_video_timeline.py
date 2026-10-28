"""File timeline metadata and arbitrary-frame seek regression tests (no private videos needed)."""
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from water_detector.camera.metadata import probe_file_video
from water_detector.config.manager import ConfigManager
from water_detector.config.schema import SourceConfig
from water_detector.service.api import create_app


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    path = tmp_path / "timeline.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 12.0, (160, 120))
    assert writer.isOpened(), "MJPG test codec unavailable"
    for i in range(48):
        frame = np.full((120, 160, 3), 50 + (i % 30), dtype=np.uint8)
        cv2.circle(frame, (80, 60), 34, (0, 200, 100), 2)
        writer.write(frame)
    writer.release()
    return path


def test_video_metadata_and_seek(synthetic_video: Path, tmp_path: Path) -> None:
    source = {"kind": "file", "address": str(synthetic_video), "device_index": 0}
    metadata = probe_file_video(SourceConfig.model_validate(source))
    assert metadata["frame_count"] == 48
    assert metadata["last_frame_index"] == 47
    assert metadata["width"] == 160
    assert metadata["height"] == 120
    assert metadata["fps"] == pytest.approx(12.0)
    assert metadata["duration_seconds"] == pytest.approx(4.0)
    with TestClient(create_app(ConfigManager(tmp_path / "app-data"))) as client:
        response = client.post("/api/camera/metadata", json={"source": source})
        assert response.status_code == 200
        assert response.json() == metadata
        first = client.post("/api/camera/frame", json={"source": source, "frame_index": 0})
        middle = client.post("/api/camera/frame", json={"source": source, "frame_index": 27})
        last = client.post("/api/camera/frame", json={"source": source, "frame_index": 47})
        assert all(item.status_code == 200 for item in (first, middle, last))
        assert first.json()["jpeg_base64"] != middle.json()["jpeg_base64"]


def test_timeline_not_available_for_live_sources(tmp_path: Path) -> None:
    with TestClient(create_app(ConfigManager(tmp_path))) as client:
        response = client.post("/api/camera/metadata", json={
            "source": {"kind": "usb", "address": "", "device_index": 0}
        })
        assert response.status_code == 400


def test_monitor_page_can_preview_arbitrary_file_frames_without_starting_monitor(
    synthetic_video: Path, tmp_path: Path,
) -> None:
    """The monitor timeline uses the existing *isolated* frame/debug preview API.

    Seek must recompute S_water history at the target frame without changing the
    active monitoring task, writing records, or carrying previous frame state.
    """
    source = {"kind": "file", "address": str(synthetic_video), "device_index": 0}
    with TestClient(create_app(ConfigManager(tmp_path / "app-data"))) as client:
        cfg = client.get("/api/config/active").json()
        calibration = {
            "cx": 80, "cy": 60, "radius": 34,
            "reference_width": 160, "reference_height": 120,
        }
        previews = []
        for frame_index in (0, 27, 47, 3, 27):
            result = client.post("/api/debug/preview", json={
                "source": source, "frame_index": frame_index, "stage": "P1",
                "calibration": calibration, "detector": cfg["detector"],
            })
            assert result.status_code == 200, result.text
            payload = result.json()
            assert payload["jpeg_base64"]
            assert len(payload["samples"]) > 0
            assert payload["samples"][-1]["time_s"] == pytest.approx(frame_index / 12, abs=.001)
            previews.append(payload)
        assert previews[0]["status"] == "WARMUP"
        assert previews[1]["jpeg_base64"] != previews[2]["jpeg_base64"]
        # Repeated seeks to the same frame must give the same isolated result.
        assert previews[1]["status"] == previews[4]["status"]
        assert previews[1]["s_water"] == pytest.approx(previews[4]["s_water"])
        assert client.get("/api/monitor/status").json()["state"] == "IDLE"
        assert not (tmp_path / "app-data" / "results").exists()
