"""Portable full-device ZIP, atomic import, portability and malicious input tests."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from water_detector.config.device_package import (
    PACKAGE_FORMAT, PACKAGE_VERSION, PackageError,
    export_device_package, import_device_package, preview_device_package,
)
from water_detector.config.manager import ConfigManager
from water_detector.config.schema import AppConfig, Calibration
from water_detector.service.api import create_app
from water_detector.vision.position import PositionRecognizer


def _device(tmp_path: Path, *, source_kind: str = "file"):
    refs: dict[str, list[str]] = {}
    for i, stage in enumerate(("P1", "P2", "P3")):
        refs[stage] = []
        for number in range(5):
            frame = np.full((120, 160, 3), 45 + i * 50, np.uint8)
            cv2.circle(frame, (65 + i * 8, 70 - i * 10), 32,
                       (100 + i * 30, 170, 220), -1)
            frame[number: number + 3, :20] = 230
            dest = tmp_path / "中文参考图" / stage / f"{number:02d}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok, encoded = cv2.imencode(".png", frame)
            assert ok
            encoded.tofile(str(dest))
            refs[stage].append(str(dest))
    cfg = AppConfig(
        device_name="洗片设备-A号",
        source={"kind": source_kind,
                "address": "C:/video/源文件.mp4" if source_kind == "file" else "rtsp://cam/stream",
                "device_index": 2},
        stage_mode="auto",
        calibrations={
            name: Calibration(cx=65 + i * 8, cy=70 - i * 10,
                              radius=32, reference_width=160, reference_height=120)
            for i, name in enumerate(("P1", "P2", "P3"))
        },
        position_references=refs,
    )
    cfg.detector.s_water_threshold = 0.027
    cfg.detector.diff_threshold = 11.0
    cfg.detector.nozzle_filter_enabled = False
    return cfg


def test_export_preview_import_retains_all_settings_and_embedded_images(tmp_path):
    original = _device(tmp_path)
    source = ConfigManager(tmp_path / "machine-A")
    source.save(original)
    export_path = tmp_path / "wafer-device-A.zip"
    info = export_device_package(source.active(), str(export_path))
    assert export_path.exists()
    assert info["device_name"] == "洗片设备-A号"
    assert info["image_count"] == 15
    assert info["reference_counts"] == {"P1": 5, "P2": 5, "P3": 5}
    with zipfile.ZipFile(export_path) as zip_file:
        names = zip_file.namelist()
        assert len(names) == 16
        assert "manifest.json" in names
        manifest = json.loads(zip_file.read("manifest.json"))
        assert manifest["format"] == PACKAGE_FORMAT
        assert manifest["config"]["detector"]["s_water_threshold"] == 0.027
        assert manifest["config"]["source"]["address"] == "C:/video/源文件.mp4"
        assert all(name.startswith("references/") for name in names if name != "manifest.json")
        assert not any("中文参考图" in name for name in names)

    destination = ConfigManager(tmp_path / "machine-B")
    untouched = destination.active().model_dump()
    assert preview_device_package(str(export_path))["reference_counts"]["P2"] == 5
    assert destination.active().model_dump() == untouched
    imported = import_device_package(destination, str(export_path))
    current = destination.active()
    assert imported["config"]["device_name"] == "洗片设备-A号"
    assert current.detector.s_water_threshold == 0.027
    assert current.detector.diff_threshold == 11.0
    assert current.detector.nozzle_filter_enabled is False
    assert current.source.kind == "file"
    assert current.source.address == ""  # Old PC's video is not included.
    assert current.stage_mode == "auto"
    assert len(current.calibrations) == 3
    for stage in ("P1", "P2", "P3"):
        assert len(current.position_references[stage]) == 5
        assert all(Path(p).is_file() and str(destination.home) in p
                   for p in current.position_references[stage])
    assert len(PositionRecognizer(current.position_references,
                                  current.calibrations).reference_thumbnails["P1"]) == 5
    assert ConfigManager(destination.home).active() == current


def test_rtsp_camera_configuration_is_preserved_across_devices(tmp_path):
    cfg = _device(tmp_path, source_kind="rtsp")
    package = tmp_path / "device.zip"
    export_device_package(cfg, str(package))
    target = ConfigManager(tmp_path / "imported")
    import_device_package(target, str(package))
    saved = target.active()
    assert saved.source.kind == "rtsp"
    assert saved.source.address == "rtsp://cam/stream"
    assert saved.source.device_index == 2


def test_api_uses_committed_config_and_import_preview_does_not_mutate(tmp_path):
    cfg = _device(tmp_path)
    home = tmp_path / "api"
    with TestClient(create_app(ConfigManager(home))) as client:
        # ZIP export uses saved backend state; an unsaved renderer draft is not exported.
        saved = client.post("/api/config/commit", json=cfg.model_dump()).json()
        path = tmp_path / "api-device.zip"
        exported = client.post("/api/device-package/export", json={"path": str(path)})
        assert exported.status_code == 200, exported.text
        preview = client.post("/api/device-package/preview", json={"path": str(path)})
        assert preview.status_code == 200, preview.text
        assert preview.json()["device_name"] == cfg.device_name
        assert client.get("/api/config/active").json() == saved
        imported = client.post("/api/device-package/import", json={"path": str(path)})
        assert imported.status_code == 200, imported.text
        assert imported.json()["config"]["source"]["address"] == ""
        assert client.get("/api/config/active").json()["device_name"] == cfg.device_name
        assert client.get("/api/monitor/status").json()["state"] == "IDLE"


def _tamper(original: Path, target: Path, *, change_name: str | None = None,
            change_data: bytes | None = None):
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(target, "w") as dest:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if change_name is not None and entry.filename.endswith("000.png"):
                entry = zipfile.ZipInfo(change_name)
            if change_data is not None and entry.filename.endswith("000.png"):
                data = change_data
            dest.writestr(entry, data)


def test_malformed_packages_never_modify_active_profile(tmp_path):
    cfg = _device(tmp_path)
    archive = tmp_path / "valid.zip"
    export_device_package(cfg, str(archive))
    destination = ConfigManager(tmp_path / "keep-this-config")
    before = destination.active()
    invalid_paths = []
    traversal = tmp_path / "traversal.zip"
    _tamper(archive, traversal, change_name="../escape.png")
    invalid_paths.append(traversal)
    checksum = tmp_path / "checksum.zip"
    _tamper(archive, checksum, change_data=b"malicious")
    invalid_paths.append(checksum)
    bad = tmp_path / "random.zip"
    bad.write_bytes(b"not a zip")
    invalid_paths.append(bad)

    for filename in invalid_paths:
        with pytest.raises(PackageError):
            preview_device_package(str(filename))
        with pytest.raises(PackageError):
            import_device_package(destination, str(filename))
        assert destination.active() == before
        assert not list((destination.home / "device_profiles").glob("profile-*")) if (
            destination.home / "device_profiles").exists() else True

    symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink, "w") as z:
        link = zipfile.ZipInfo("references/P1/000.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "../outside")
        z.writestr("manifest.json", "{}")
    with pytest.raises(PackageError):
        import_device_package(destination, str(symlink))
    assert destination.active() == before


def test_export_refuses_missing_reference_without_partial_archive(tmp_path):
    cfg = _device(tmp_path)
    cfg.position_references["P3"][1] = str(tmp_path / "missing.png")
    result = tmp_path / "no-output.zip"
    with pytest.raises(PackageError):
        export_device_package(cfg, str(result))
    assert not result.exists()
