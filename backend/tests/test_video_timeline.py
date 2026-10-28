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



def test_file_playback_seek_resumes_continuously_without_monitor_side_effects(
    synthetic_video: Path, tmp_path: Path,
) -> None:
    """A jump must restart sequential playback and rebuild detector history.

    Replaying must never create monitoring exports. Pausing freezes the cursor.
    """
    import time

    home = tmp_path / "isolated-player"
    source = {"kind": "file", "address": str(synthetic_video), "device_index": 0}
    with TestClient(create_app(ConfigManager(home))) as client:
        cfg = client.get("/api/config/active").json()
        request = {
            "source": source, "stage": "P1", "frame_index": 0,
            "calibration": {
                "cx": 80, "cy": 60, "radius": 34,
                "reference_width": 160, "reference_height": 120,
            },
            "detector": cfg["detector"],
        }
        started = client.post("/api/playback/start", json=request)
        assert started.status_code == 200, started.text
        assert started.json()["state"] in ("SEEKING", "PLAYING")
        deadline = time.monotonic() + 8
        state = None
        while time.monotonic() < deadline:
            state = client.get("/api/playback/state").json()
            if state["frame_index"] >= 3 and state["state"] == "PLAYING":
                break
            time.sleep(.03)
        assert state is not None and state["frame_index"] >= 3, state
        old_index = state["frame_index"]

        # Seeking to frame 25 must not stay on that single still frame.
        seek = client.post("/api/playback/seek", json={"frame_index": 25})
        assert seek.status_code == 200, seek.text
        deadline = time.monotonic() + 8
        first_target = None
        while time.monotonic() < deadline:
            state = client.get("/api/playback/state").json()
            if state["frame_index"] >= 25 and state["state"] == "PLAYING":
                first_target = state
                break
            time.sleep(.03)
        assert first_target is not None, state
        assert first_target["frame_index"] >= 25
        assert first_target["jpeg_base64"]
        assert isinstance(first_target["history"], list)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get("/api/playback/state").json()
            if state["frame_index"] > first_target["frame_index"]:
                break
            time.sleep(.03)
        assert state["frame_index"] > first_target["frame_index"], state

        paused = client.post("/api/playback/pause").json()
        assert paused["state"] == "PAUSED"
        time.sleep(.2)
        stable = client.get("/api/playback/state").json()
        time.sleep(.2)
        stable_again = client.get("/api/playback/state").json()
        assert stable_again["frame_index"] == stable["frame_index"]
        rev = stable_again["revision"]
        assert client.get("/api/playback/state", params={"after_revision": rev}).json()["jpeg_base64"] is None

        resumed = client.post("/api/playback/resume")
        assert resumed.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get("/api/playback/state").json()
            if state["frame_index"] > stable["frame_index"]:
                break
            time.sleep(.03)
        assert state["frame_index"] > stable["frame_index"], state

        # A seek while paused STILL resumes, including backward jumps.
        client.post("/api/playback/pause")
        backward = client.post("/api/playback/seek", json={"frame_index": 5})
        assert backward.status_code == 200
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            state = client.get("/api/playback/state").json()
            if 5 <= state["frame_index"] < 18 and state["state"] == "PLAYING":
                break
            time.sleep(.03)
        assert 5 <= state["frame_index"] < 18, state
        assert state["state"] == "PLAYING"
        assert client.get("/api/monitor/status").json()["state"] == "IDLE"
        assert not (home / "results").exists()

        stopped = client.post("/api/playback/stop")
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "IDLE"


def test_file_playback_rejects_live_sources(tmp_path: Path) -> None:
    with TestClient(create_app(ConfigManager(tmp_path))) as client:
        response = client.post("/api/playback/start", json={
            "source": {"kind": "usb", "address": "", "device_index": 0},
        })
        assert response.status_code == 400



def test_live_draft_update_recalculates_paused_frame_without_saving(
    synthetic_video: Path, tmp_path: Path,
) -> None:
    """Unsaved threshold/ROI changes take effect at the same paused frame."""
    import time

    home = tmp_path / "live-calibration"
    source = {"kind": "file", "address": str(synthetic_video), "device_index": 0}
    with TestClient(create_app(ConfigManager(home))) as client:
        original = client.get("/api/config/active").json()
        request = {
            "source": source, "stage": "P1", "frame_index": 12,
            "calibration": {
                "cx": 80, "cy": 60, "radius": 34,
                "reference_width": 160, "reference_height": 120,
            },
            "detector": original["detector"],
        }
        assert client.post("/api/playback/start", json=request).status_code == 200
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            state = client.get("/api/playback/state").json()
            if state["frame_index"] >= 12 and state["revision"] > 0:
                break
            time.sleep(.02)
        assert state["frame_index"] >= 12
        client.post("/api/playback/pause")
        time.sleep(.15)
        before = client.get("/api/playback/state").json()
        assert before["state"] == "PAUSED"
        paused_frame = before["frame_index"]
        previous_revision = before["revision"]

        modified = {**request, "frame_index": 0}
        modified["calibration"] = {**request["calibration"], "radius": 31}
        modified["detector"] = {**request["detector"], "diff_threshold": 255.0}
        resp = client.post("/api/playback/update", json=modified)
        assert resp.status_code == 200, resp.text
        deadline = time.monotonic() + 6
        after = None
        while time.monotonic() < deadline:
            after = client.get("/api/playback/state").json()
            if after["revision"] > previous_revision and after["state"] == "PAUSED":
                break
            time.sleep(.02)
        assert after is not None and after["revision"] > previous_revision, after
        assert after["state"] == "PAUSED"
        assert after["frame_index"] == paused_frame
        assert after["jpeg_base64"] is not None
        assert after["s_water"] == 0.0

        # No client parameter or calibration edit reaches the committed config.
        assert client.get("/api/config/active").json() == original
        assert client.get("/api/monitor/status").json()["state"] == "IDLE"
        assert not (home / "results").exists()

        invalid = client.post("/api/playback/update", json={
            **modified,
            "source": {"kind": "usb", "address": "", "device_index": 0},
        })
        assert invalid.status_code == 409
        assert client.get("/api/playback/state").json()["state"] == "PAUSED"



def test_auto_roi_selects_by_wafer_circle_position_without_reference_images(tmp_path: Path) -> None:
    """Position-only auto chooses the correct calibrated circle as wafer moves."""
    import time

    file = tmp_path / "three_positions.avi"
    writer = cv2.VideoWriter(str(file), cv2.VideoWriter_fourcc(*"MJPG"), 12.0, (160, 120))
    assert writer.isOpened()
    for index in range(36):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.circle(frame, ((36, 80, 120)[index // 12], 60), 25,
                   (225, 225, 225), 2, cv2.LINE_AA)
        writer.write(frame)
    writer.release()
    source = {"kind": "file", "address": str(file), "device_index": 0}
    calibrations = {
        stage: {"cx": x, "cy": 60, "radius": 25,
                "reference_width": 160, "reference_height": 120}
        for stage, x in (("P1", 36), ("P2", 80), ("P3", 120))
    }

    with TestClient(create_app(ConfigManager(tmp_path / "auto-data"))) as client:
        saved = client.get("/api/config/active").json()
        assert saved["position_references"] == {}
        request = {
            "source": source, "frame_index": 0, "stage": "P3",
            "stage_mode": "auto", "calibration": calibrations["P3"],
            "calibrations": calibrations,
            "detector": saved["detector"], "show_algorithm_roi": False,
        }
        assert client.post("/api/playback/start", json=request).status_code == 200
        for target, expected_stage in ((1, "P1"), (14, "P2"), (26, "P3")):
            assert client.post("/api/playback/seek", json={"frame_index": target}).status_code == 200
            deadline = time.monotonic() + 8
            state = None
            while time.monotonic() < deadline:
                state = client.get("/api/playback/state").json()
                if (state["stage"] == expected_stage and
                        target <= state["frame_index"] < target + 5 and
                        state["revision"] > 0):
                    break
                time.sleep(.02)
            assert state is not None and state["stage"] == expected_stage, state
            assert state["roi"]["cx"] == calibrations[expected_stage]["cx"]
            assert state["recognition_mode"] == "auto"
            assert state["position_confidence"] >= 0.32
            assert state["wafer_center"] is not None
            assert abs(state["wafer_center"][0] - calibrations[expected_stage]["cx"]) < 7
            assert state["jpeg_base64"]
            assert state["s_water"] is not None

        # Missing one calibrated ROI must FAIL CLOSED, not silently use P3.
        missing = client.post("/api/playback/update", json={
            **request, "calibrations": {"P1": calibrations["P1"],
                                       "P2": calibrations["P2"]}
        })
        assert missing.status_code == 200
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = client.get("/api/playback/state").json()
            if state["stage"] == "UNKNOWN" and state["reason"] == "POSITION_UNKNOWN":
                break
            time.sleep(.02)
        assert state["stage"] == "UNKNOWN", state
        assert state["status"] == "PAUSED"
        assert state["roi"] is None
        assert state["s_water"] is None
        assert client.get("/api/monitor/status").json()["state"] == "IDLE"
        assert client.get("/api/config/active").json() == saved


def test_position_recognizer_geometry_rejects_blank_and_ambiguous_frames() -> None:
    from water_detector.vision.position import PositionRecognizer
    from water_detector.config.schema import Calibration
    calibs = {
        k: Calibration(cx=x, cy=60, radius=25,
                       reference_width=160, reference_height=120)
        for k, x in (("P1", 34), ("P2", 80), ("P3", 126))
    }
    recognizer = PositionRecognizer(calibs)
    blank = np.zeros((120, 160, 3), dtype=np.uint8)
    assert recognizer.classify(blank)[0] == "UNKNOWN"
    # Identically strong circular rims in multiple calibrated positions are
    # ambiguous, and the algorithm must not report a guessed OK.
    both = blank.copy()
    cv2.circle(both, (34, 60), 25, (220, 220, 220), 2)
    cv2.circle(both, (80, 60), 25, (220, 220, 220), 2)
    assert recognizer.classify(both)[0] == "UNKNOWN"
    # Repeatedly calibrated at the same centre is not three distinct positions.
    same = {k: Calibration(cx=80, cy=60, radius=25,
                           reference_width=160, reference_height=120) for k in calibs}
    assert PositionRecognizer(same).classify(both)[0] == "UNKNOWN"


def test_roi_visibility_keeps_water_anomaly_pixels() -> None:
    """Hiding guide geometry must not turn off algorithm anomaly overlays."""
    from water_detector.render.overlay import render_overlay
    from water_detector.config.schema import Calibration, DetectorConfig

    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    persistent = np.zeros((120, 160), dtype=bool)
    persistent[50:55, 65:70] = True
    calib = Calibration(cx=80, cy=60, radius=35, reference_width=160, reference_height=120)
    cfg = DetectorConfig()
    result = {"persistent": persistent, "sector_scores": [0.0] * cfg.sector_count}
    no_roi = render_overlay(frame, calib, cfg, result, show_roi=False)
    with_roi = render_overlay(frame, calib, cfg, result, show_roi=True)
    assert np.count_nonzero(no_roi) > 0  # Persistent anomaly still red.
    assert np.count_nonzero(no_roi[~persistent]) == 0  # No guide rings.
    assert np.count_nonzero(with_roi[~persistent]) > 0
