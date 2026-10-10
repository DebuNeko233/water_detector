"""Video capture adapters. Live sources use acquisition-time monotonic timestamps."""
from __future__ import annotations
import time
from pathlib import Path
import cv2
from water_detector.config.schema import SourceConfig
from water_detector.models.frames import FramePacket


class OpenCVSource:
    def __init__(self, config: SourceConfig):
        self.config = config
        self.cap: cv2.VideoCapture | None = None
        self.frame_id = 0
        self._origin: float | None = None
        self.fps = 30.0

    def open(self):
        cfg = self.config
        if cfg.kind == "industrial":
            raise NotImplementedError("Industrial SDK adapter requires vendor/model details")
        source: str | int = cfg.device_index if cfg.kind == "usb" else cfg.address
        if cfg.kind in ("file", "rtsp") and not source:
            raise ValueError("Video source address is empty")
        if cfg.kind == "file" and not Path(str(source)).is_file():
            raise FileNotFoundError(str(source))
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            self.cap.release()
            self.cap = None
            raise RuntimeError(f"Unable to open video source: {cfg.kind}")
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS))
        if not 1 <= self.fps <= 240:
            self.fps = 30.0
        self._origin = time.monotonic()
        self.frame_id = 0

    def read(self) -> FramePacket | None:
        if self.cap is None:
            raise RuntimeError("Source not opened")
        ok, image = self.cap.read()
        if not ok:
            return None
        if self.config.kind == "file":
            # POS_MSEC is unreliable for some MP4 backends; constant-FPS fallback.
            timestamp_s = self.frame_id / self.fps
        else:
            timestamp_s = time.monotonic() - (self._origin or time.monotonic())
        pkt = FramePacket(self.frame_id, timestamp_s, image, self.config.address or str(self.config.device_index), self.fps)
        self.frame_id += 1
        return pkt

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def open_source(config: SourceConfig) -> OpenCVSource:
    camera = OpenCVSource(config)
    camera.open()
    return camera
