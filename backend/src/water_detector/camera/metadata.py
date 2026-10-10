"""Small on-demand metadata probe for seekable local video sources.

Keep VideoCapture lifetime short: only the Python backend touches the source.
FPS and frame count are required to build an accurate frame-index timeline.
"""
from __future__ import annotations

import math
from pathlib import Path
import cv2
from water_detector.config.schema import SourceConfig


def probe_file_video(source: SourceConfig) -> dict:
    if source.kind != "file":
        raise ValueError("Timeline seeking is only available for local video files")
    if not source.address or not Path(source.address).is_file():
        raise FileNotFoundError(source.address or "(empty video path)")
    cap = cv2.VideoCapture(source.address)
    if not cap.isOpened():
        cap.release()
        raise ValueError("Unable to open the selected video")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if (not math.isfinite(fps) or fps <= 0
                or not math.isfinite(frames) or frames < 1
                or width < 1 or height < 1):
            raise ValueError("Video does not report valid seekable metadata")
        frame_count = int(frames)
        return {
            "frame_count": frame_count,
            "last_frame_index": frame_count - 1,
            "fps": fps,
            "duration_seconds": frame_count / fps,
            "width": width,
            "height": height,
        }
    finally:
        cap.release()
