"""Regression tests for the P1/P2/P3 five-screenshot reference pools."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from water_detector.config.manager import ConfigManager
from water_detector.config.schema import AppConfig, Calibration
from water_detector.pipeline.inspection import InspectionPipeline
from water_detector.models.frames import FramePacket
from water_detector.service.api import create_app
from water_detector.vision.position import PositionRecognizer


def _calibrations():
    # Overlapping preset ROIs, similar to the real calibration geometry.
    return {
        stage: Calibration(cx=x, cy=y, radius=r,
                           reference_width=160, reference_height=120)
        for stage, x, y, r in (
            ("P1", 70, 83, 30), ("P2", 73, 65, 34), ("P3", 72, 48, 38)
        )
    }


def _image(stage: int, variation: int = 0) -> np.ndarray:
    rng = np.random.default_rng(451 + stage * 927)
    texture = rng.integers(25, 205, (120, 160), dtype=np.uint8)
    texture = cv2.GaussianBlur(texture, (7, 7), 2)
    frame = cv2.cvtColor(texture, cv2.COLOR_GRAY2BGR)
    x, y, r = ((70, 83, 30), (73, 65, 34), (72, 48, 38))[stage]
    cv2.circle(frame, (x, y), r, (90 + 35 * stage, 130, 195), -1)
    cv2.circle(frame, (x, y), r, (238, 238, 238), 2)
    # Different light/spray positions within a stage, while the location
    # remains recognizable from the screenshot collection.
    cv2.line(frame, (5 + variation * 6, 2), (24 + variation * 6, 65),
             (70, 70, 70), 2)
    return frame


def _save(path: Path, frame: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", frame)
    assert ok
    encoded.tofile(str(path))


def _refs(tmp_path: Path):
    refs = {}
    for i, stage in enumerate(("P1", "P2", "P3")):
        paths = []
        for sample in range(5):
            path = tmp_path / "中文参考截图" / stage / f"{sample}.png"
            _save(path, _image(i, sample))
            paths.append(str(path))
        refs[stage] = paths
    return refs


def test_five_refs_required_for_each_stage_and_legacy_single_path_migrates(tmp_path):
    refs = _refs(tmp_path)
    cfg = AppConfig.model_validate({
        "position_references": {stage: paths[0] for stage, paths in refs.items()}
    })
    assert all(len(cfg.position_references[stage]) == 1 for stage in refs)
    mgr = ConfigManager(tmp_path / "data")
    mgr.save(cfg)
    loaded = ConfigManager(tmp_path / "data").active()
    assert loaded.position_references == cfg.position_references
    # One screenshot per position is migrated, but never treated as five.
    candidate = PositionRecognizer(loaded.position_references, _calibrations())
    assert candidate.classify(_image(0))[0] == "UNKNOWN"
    for stage in refs:
        insufficient = {key: list(paths) for key, paths in refs.items()}
        insufficient[stage] = insufficient[stage][:4]
        assert PositionRecognizer(insufficient, _calibrations()).classify(
            _image(0)
        )[0] == "UNKNOWN"
    assert PositionRecognizer(refs, _calibrations()).classify(_image(0))[0] == "P1"


def test_five_screenshot_pool_selects_correct_roi_and_handles_nozzle(tmp_path):
    refs = _refs(tmp_path)
    recognizer = PositionRecognizer(refs, _calibrations())
    assert all(len(recognizer.reference_thumbnails[stage]) == 5 for stage in refs)
    for i, stage in enumerate(("P1", "P2", "P3")):
        frame = _image(i, variation=2)
        label, similarity, scores = recognizer.classify(frame)
        assert label == stage, (stage, label, scores)
        assert similarity >= .78
        assert scores[stage] > max(value for key, value in scores.items() if key != stage)
        excluded = np.zeros(frame.shape[:2], dtype=bool)
        excluded[15:55, 14:36] = True
        assert recognizer.classify(frame, excluded)[0] == stage
    # Unknown input cannot be asserted OK just because a stage exists.
    blank = np.full((120, 160, 3), 120, dtype=np.uint8)
    assert recognizer.classify(blank)[0] == "UNKNOWN"
    try:
        recognizer.classify(_image(0), np.zeros((100, 100), bool))
        assert False, "mismatched mask should be rejected"
    except ValueError:
        pass


def test_duplicate_stage_screenshots_are_ambiguous(tmp_path):
    refs = _refs(tmp_path)
    refs["P2"] = list(refs["P1"])
    recognizer = PositionRecognizer(refs, _calibrations())
    stage, _, scores = recognizer.classify(_image(0))
    assert stage == "UNKNOWN", scores


def test_capture_multiple_reference_screenshots_does_not_commit_and_pipeline_uses_them(tmp_path):
    video = tmp_path / "stages.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"),
                             15.0, (160, 120))
    assert writer.isOpened()
    for stage in range(3):
        for sample in range(8):
            writer.write(_image(stage, sample))
    writer.release()
    source = {"kind": "file", "address": str(video), "device_index": 0}
    data_home = tmp_path / "app"
    with TestClient(create_app(ConfigManager(data_home))) as client:
        saved = client.get("/api/config/active").json()
        refs = {}
        for stage_id, name in enumerate(("P1", "P2", "P3")):
            paths = []
            for i in range(5):
                index = stage_id * 8 + i
                result = client.post("/api/position/reference/capture", json={
                    "stage": name, "frame_index": index, "source": source,
                })
                assert result.status_code == 200, result.text
                filename = result.json()["path"]
                assert Path(filename).is_file()
                assert name in filename
                paths.append(filename)
            assert len(set(paths)) == 5
            refs[name] = paths
        assert client.get("/api/config/active").json() == saved
        invalid = client.post("/api/position/reference/capture", json={
            "stage": "P4", "frame_index": 0, "source": source,
        })
        assert invalid.status_code == 400

        cfg = AppConfig.model_validate(saved)
        cfg.stage_mode = "auto"
        cfg.calibrations = _calibrations()
        cfg.position_references = refs
        for stage_id, name in enumerate(("P1", "P2", "P3")):
            image = _image(stage_id, 3)
            result, _ = InspectionPipeline(cfg).process(
                FramePacket(stage_id, stage_id / 15.0, image, "test", 15.0),
                show_roi=False,
            )
            assert result["stage"] == name, result
            assert result["position_method"] == "screenshot"
        assert client.get("/api/monitor/status").json()["state"] == "IDLE"
        assert not (data_home / "results").exists()
