"""Portable, validated ZIP profiles for one wafer inspection device.

Contains the full AppConfig (ROI geometry, source, detector parameters, etc.)
and copies of all referenced images. A profile never bundles video recordings.
Import validates all members before writing, uses new isolated directories,
and commits the config only after every image is written successfully.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from water_detector.config.manager import ConfigManager
from water_detector.config.schema import AppConfig

PACKAGE_FORMAT = "wafer-water-detector-device-profile"
PACKAGE_VERSION = 1
STAGES = ("P1", "P2", "P3")
MAX_ARCHIVE_BYTES = 120 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 90 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGES = 300
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
REFERENCE_NAME = re.compile(r"^references/(P1|P2|P3)/([0-9]{3})[.]png$")


class PackageError(ValueError):
    """Bad or unsafe device profile; must not change the active config."""


def _image_png(data: bytes, stage: str, config: AppConfig) -> bytes:
    if len(data) > MAX_IMAGE_BYTES:
        raise PackageError("Reference image is too large")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise PackageError(f"Cannot decode {stage} reference image")
    cal = config.calibrations.get(stage)
    if cal is None or image.shape[:2] != (cal.reference_height, cal.reference_width):
        raise PackageError(f"{stage} reference image size does not match calibration")
    ok, png = cv2.imencode(".png", image)
    if not ok or len(png) > MAX_IMAGE_BYTES:
        raise PackageError(f"Cannot encode {stage} reference image")
    return png.tobytes()


def _file_path(path: str) -> Path:
    target = Path(path).expanduser()
    if target.suffix.lower() != ".zip":
        raise PackageError("Select a .zip device package")
    return target


def _summary(config: AppConfig, count: dict[str, int]) -> dict:
    return {
        "device_name": config.device_name,
        "stage_mode": config.stage_mode,
        "selected_stage": config.selected_stage,
        "source_kind": config.source.kind,
        "source_reselection_required": config.source.kind == "file",
        "reference_counts": count,
        "calibrated_stages": [name for name in STAGES if name in config.calibrations],
        "detector_parameters": config.detector.model_dump(),
        "has_complete_auto_references": all(count[name] >= 5 for name in STAGES),
    }


def export_device_package(config: AppConfig, file_path: str) -> dict:
    target = _file_path(file_path)
    if target.is_dir():
        raise PackageError("Cannot export ZIP over a directory")
    config = config.model_copy(deep=True)
    copied = config.model_copy(deep=True)
    copied.position_references = {}
    assets: dict[str, bytes] = {}
    checksums: dict[str, str] = {}
    for stage in STAGES:
        assets_list = config.position_references.get(stage, [])
        if not isinstance(assets_list, list):
            raise PackageError("Invalid reference image list")
        copied.position_references[stage] = []
        for index, original in enumerate(assets_list):
            if len(assets) >= MAX_IMAGES:
                raise PackageError("Too many reference images in device profile")
            try:
                source = Path(original).resolve(strict=True)
                if source == target.resolve() or not source.is_file():
                    raise PackageError(f"Missing reference image in {stage}")
                if source.stat().st_size > MAX_IMAGE_BYTES:
                    raise PackageError(f"Reference image is too large in {stage}")
                data = _image_png(source.read_bytes(), stage, config)
            except (OSError, cv2.error) as exc:
                raise PackageError(f"Cannot read {stage} reference image") from exc
            name = f"references/{stage}/{index:03d}.png"
            assets[name] = data
            checksums[name] = hashlib.sha256(data).hexdigest()
            copied.position_references[stage].append(name)

    manifest = {
        "format": PACKAGE_FORMAT,
        "schema_version": PACKAGE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "config": copied.model_dump(mode="json"),
        "sha256": checksums,
    }
    encoded = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise PackageError("Device profile manifest is too large")
    if sum(map(len, assets.values())) > MAX_TOTAL_UNCOMPRESSED:
        raise PackageError("Device profile exceeds image size limit")
    if not target.parent.is_dir():
        raise PackageError("ZIP destination directory does not exist")
    fd, temp_file = tempfile.mkstemp(prefix=".wafer-export-", suffix=".zip", dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp_file, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", encoded)
            for name, data in assets.items():
                archive.writestr(name, data)
        if Path(temp_file).stat().st_size > MAX_ARCHIVE_BYTES:
            raise PackageError("ZIP file exceeds size limit")
        os.replace(temp_file, target)
    finally:
        if Path(temp_file).exists():
            Path(temp_file).unlink()
    result = _summary(config, {
        stage: len(config.position_references.get(stage, [])) for stage in STAGES
    })
    return {**result, "file_path": str(target), "image_count": len(assets)}


def read_device_package(file_path: str) -> tuple[AppConfig, dict[str, bytes], dict]:
    target = _file_path(file_path)
    if not target.is_file() or target.stat().st_size > MAX_ARCHIVE_BYTES:
        raise PackageError("ZIP package is missing or exceeds size limit")
    try:
        with zipfile.ZipFile(target, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_IMAGES + 1:
                raise PackageError("Invalid archive entry count")
            entries = {}
            total = 0
            for info in infos:
                name = info.filename
                if (name in entries or name.startswith("/") or "\\" in name or
                        ".." in name.split("/") or name.endswith("/") or
                        (name != "manifest.json" and REFERENCE_NAME.fullmatch(name) is None)):
                    raise PackageError("Unexpected or unsafe ZIP entry")
                if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                    raise PackageError("ZIP symbolic links are not allowed")
                limit = MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_IMAGE_BYTES
                total += info.file_size
                if (info.file_size > limit or total > MAX_TOTAL_UNCOMPRESSED or
                        info.file_size > max(1, info.compress_size) * 250):
                    raise PackageError("ZIP decompression limit exceeded")
                entries[name] = info
            if "manifest.json" not in entries:
                raise PackageError("Missing device profile manifest")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if (not isinstance(manifest, dict) or manifest.get("format") != PACKAGE_FORMAT or
                    manifest.get("schema_version") != PACKAGE_VERSION):
                raise PackageError("Unsupported device profile version")
            config = AppConfig.model_validate(manifest["config"])
            checksums = manifest.get("sha256")
            if not isinstance(checksums, dict):
                raise PackageError("Missing reference checksums")
            expected: list[str] = []
            for stage in STAGES:
                for name in config.position_references.get(stage, []):
                    match = REFERENCE_NAME.fullmatch(name)
                    if not match or match.group(1) != stage:
                        raise PackageError("Unsafe reference image path in manifest")
                    expected.append(name)
            if (len(expected) != len(set(expected)) or
                    set(entries) != {"manifest.json", *expected} or
                    set(checksums) != set(expected)):
                raise PackageError("ZIP contents differ from device profile manifest")
            assets = {}
            for name in expected:
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != checksums[name]:
                    raise PackageError("Reference image checksum mismatch")
                stage = REFERENCE_NAME.fullmatch(name).group(1)
                # Verify PNG dimensions before decoding; compressed images can
                # otherwise allocate far more RAM than their ZIP entry size.
                cal = config.calibrations.get(stage)
                if (not data.startswith(b"\\x89PNG\\r\\n\\x1a\\n") or
                        len(data) < 24 or cal is None):
                    raise PackageError("Invalid embedded PNG reference")
                image_width, image_height = struct.unpack(">II", data[16:24])
                if ((image_width, image_height) !=
                        (cal.reference_width, cal.reference_height) or
                        image_width * image_height > 32_000_000):
                    raise PackageError("Unsafe or mismatched PNG dimensions")
                assets[name] = _image_png(data, stage, config)
            summary = _summary(config, {
                stage: len(config.position_references.get(stage, [])) for stage in STAGES
            })
            return config, assets, summary
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, TypeError,
            UnicodeDecodeError, json.JSONDecodeError, OSError, cv2.error) as exc:
        raise PackageError("Invalid or damaged device ZIP package") from exc


def preview_device_package(file_path: str) -> dict:
    _, _, summary = read_device_package(file_path)
    return summary


def import_device_package(manager: ConfigManager, file_path: str) -> dict:
    config, assets, summary = read_device_package(file_path)
    # Local video files are not included: on another machine the old absolute
    # path is invalid. Keep camera/RTSP parameters for operator verification.
    if config.source.kind == "file":
        config.source.address = ""
    profiles = manager.home / "device_profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=profiles))
    destination = profiles / ("profile-" + uuid.uuid4().hex)
    try:
        for stage in STAGES:
            new_paths = []
            for name in config.position_references.get(stage, []):
                file = staging / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(assets[name])
                new_paths.append(str(destination / name))
            config.position_references[stage] = new_paths
        staging.rename(destination)
        saved = manager.save(config)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"config": saved.model_dump(mode="json"), "summary": summary}
