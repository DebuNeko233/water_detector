"""Atomic persisted configuration, independent of the Electron installation path."""
from __future__ import annotations
import json
import os
import tempfile
import threading
from pathlib import Path
from .schema import AppConfig


def data_home() -> Path:
    root = os.environ.get("WAFER_DATA_DIR")
    if root:
        return Path(root).expanduser().resolve()
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WaferWaterDetector"


class ConfigManager:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else data_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "active.json"
        self._lock = threading.RLock()
        self._active = self._load()

    def _load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        return AppConfig.model_validate_json(self.path.read_text(encoding="utf-8"))

    def active(self) -> AppConfig:
        with self._lock:
            return self._active.model_copy(deep=True)

    def save(self, config: AppConfig) -> AppConfig:
        """Writes config to a temp file and atomically replaces the active version."""
        with self._lock:
            cfg = config.model_copy(deep=True)
            cfg.version = self._active.version + 1
            payload = cfg.model_dump_json(indent=2)
            fd, tmp = tempfile.mkstemp(prefix="config_", suffix=".json", dir=self.home)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            self._active = cfg
            return cfg.model_copy(deep=True)
