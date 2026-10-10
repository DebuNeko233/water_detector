"""Centralized rotating file logger."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(home: Path):
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("water_detector")
    root.setLevel(logging.INFO)
    if not root.handlers:
        handler = RotatingFileHandler(log_dir / "backend.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    return root
