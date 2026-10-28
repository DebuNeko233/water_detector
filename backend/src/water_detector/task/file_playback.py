"""Seekable, paced local-file playback for interactive algorithm testing.

This session NEVER touches MonitorTask, its CSV/MP4 writer, or its detector.
All OpenCV VideoCapture I/O happens in one worker thread. Seeking rebuilds the
water residence window from a short pre-roll; normal playback processes each
frame exactly once without repeatedly re-running /api/debug/preview.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2
from loguru import logger
from pydantic import BaseModel, Field

from water_detector.config.schema import Calibration, DetectorConfig, SourceConfig
from water_detector.detector.water import WaterDetector
from water_detector.render.overlay import jpeg_bytes, render_overlay


class PlaybackRequest(BaseModel):
    source: SourceConfig
    frame_index: int = Field(0, ge=0)
    stage: str = "P1"
    calibration: Calibration | None = None
    detector: DetectorConfig = Field(default_factory=DetectorConfig)


class FilePlayback:
    """Independent, thread-safe local video session with play/pause/seek.

    A monotonically increasing revision lets the browser avoid retransmitting
    identical JPEG frames while paused. The preview frame and S_water snapshot
    are published atomically after the frame has actually been decoded.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._paused = False
        self._pending_seek: int | None = None
        self._generation = 0
        self._request: PlaybackRequest | None = None
        self._jpeg: bytes | None = None
        self._revision = 0
        self._state: dict = self._initial_state()

    @staticmethod
    def _initial_state() -> dict:
        return {
            "state": "IDLE", "frame_index": -1, "fps": 0.0,
            "status": "IDLE", "s_water": None, "history": [],
            "reason": "", "revision": 0,
        }

    def snapshot(self, after_revision: int = -1) -> dict:
        with self._condition:
            data = dict(self._state)
            data["history"] = list(data.get("history", []))
            data["jpeg_base64"] = None
            if self._jpeg is not None and self._revision != after_revision:
                import base64
                data["jpeg_base64"] = base64.b64encode(self._jpeg).decode("ascii")
            return data

    def start(self, request: PlaybackRequest) -> dict:
        if request.source.kind != "file":
            raise ValueError("Playback is only supported for local video files")
        if not request.source.address or not Path(request.source.address).is_file():
            raise FileNotFoundError(request.source.address or "No video file selected")
        self.stop()
        with self._condition:
            self._request = request.model_copy(deep=True)
            self._stopping = False
            self._paused = False
            self._generation += 1
            self._pending_seek = request.frame_index
            self._jpeg = None
            self._state = {
                **self._initial_state(),
                "state": "SEEKING",
                "frame_index": request.frame_index,
            }
            self._thread = threading.Thread(
                target=self._run, name="isolated-video-playback", daemon=True,
            )
            self._thread.start()
            return self.snapshot()

    def seek(self, frame_index: int) -> dict:
        with self._condition:
            if self._thread is None or not self._thread.is_alive():
                raise RuntimeError("No active playback session")
            if frame_index < 0:
                raise ValueError("Frame index must not be negative")
            self._pending_seek = frame_index
            self._generation += 1
            self._paused = False  # Seeking always resumes playback.
            self._state["state"] = "SEEKING"
            self._condition.notify_all()
            return self.snapshot()

    def pause(self) -> dict:
        with self._condition:
            if self._thread and self._thread.is_alive() and self._state["state"] != "COMPLETED":
                self._paused = True
                self._state["state"] = "PAUSED"
                self._condition.notify_all()
            return self.snapshot()

    def resume(self) -> dict:
        with self._condition:
            if self._thread is None or not self._thread.is_alive():
                raise RuntimeError("No active playback session")
            if self._state["state"] == "COMPLETED":
                self._pending_seek = 0
                self._generation += 1
            self._paused = False
            self._state["state"] = "SEEKING" if self._pending_seek is not None else "PLAYING"
            self._condition.notify_all()
            return self.snapshot()

    def stop(self) -> dict:
        with self._condition:
            thread = self._thread
            self._stopping = True
            self._generation += 1
            self._condition.notify_all()
        if thread and thread.is_alive():
            thread.join(timeout=8.0)
            if thread.is_alive():
                raise RuntimeError("Playback worker did not stop cleanly")
        with self._condition:
            self._thread = None
            self._request = None
            self._pending_seek = None
            self._jpeg = None
            self._state = self._initial_state()
            self._state["revision"] = self._revision
            return self.snapshot()

    def _publish(self, frame, index: int, fps: float, detector: WaterDetector | None,
                 request: PlaybackRequest, error: str = "") -> None:
        if detector is None:
            status = "PAUSED"
            score = None
            history: list[float] = []
            visual = frame
            reason = error or "CALIBRATION_MISSING"
        else:
            result = detector.process(frame)
            status = result["status"]
            score = result["s_water"]
            history = [round(float(v), 6) for v in detector.score_history]
            reason = ""
            visual = render_overlay(frame, request.calibration, request.detector, result)
        jpg = jpeg_bytes(visual)
        with self._condition:
            if self._stopping or self._pending_seek is not None:
                return
            self._revision += 1
            self._jpeg = jpg
            self._state.update(
                frame_index=index, fps=fps, status=status,
                s_water=score, history=history, reason=reason,
                revision=self._revision, state="PAUSED" if self._paused else "PLAYING",
            )

    def _run(self) -> None:
        request = self._request
        if request is None:
            return
        cap = cv2.VideoCapture(request.source.address)
        if not cap.isOpened():
            with self._condition:
                self._state.update(state="ERROR", reason="Cannot open video file")
            cap.release()
            return
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            if not 1 <= fps <= 240:
                fps = 30.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            detector = None
            error = ""
            if request.calibration is None:
                error = "CALIBRATION_MISSING"
            elif (width, height) != (
                request.calibration.reference_width,
                request.calibration.reference_height,
            ):
                error = "RESOLUTION_MISMATCH"

            next_frame = 0
            while True:
                with self._condition:
                    if self._stopping:
                        return
                    if self._pending_seek is not None:
                        target = min(max(self._pending_seek, 0), max(0, total - 1))
                        generation = self._generation
                        self._pending_seek = None
                        seeking = True
                        self._state["state"] = "SEEKING"
                    else:
                        seeking = False
                        if self._paused:
                            self._condition.wait()
                            continue
                        target = next_frame
                        generation = self._generation

                frame_started = time.monotonic()
                if seeking:
                    # Same lookback semantics as /api/debug/preview, but process
                    # the pre-roll only ONCE on each seek. No stale old history.
                    lookback = round(fps * (
                        request.detector.history_seconds +
                        request.detector.ng_hold_seconds + 1.0
                    ))
                    start_index = max(0, target - lookback)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, start_index)
                    detector = (
                        WaterDetector((height, width), fps, request.calibration, request.detector)
                        if not error else None
                    )
                    last = None
                    for index in range(start_index, target + 1):
                        with self._condition:
                            if self._stopping:
                                return
                            if self._generation != generation:
                                break
                        ok, frame = cap.read()
                        if not ok:
                            break
                        if index < target and detector is not None:
                            detector.process(frame)
                        last = (index, frame)
                    with self._condition:
                        if self._generation != generation:
                            continue
                    if last is None:
                        with self._condition:
                            self._state.update(state="ERROR", reason="Unable to seek to requested frame")
                        self._paused = True
                        continue
                    index, frame = last
                    self._publish(frame, index, fps, detector, request, error)
                    next_frame = index + 1
                else:
                    if target >= total:
                        with self._condition:
                            self._state["state"] = "COMPLETED"
                            self._paused = True
                        continue
                    ok, frame = cap.read()
                    if not ok:
                        with self._condition:
                            self._state["state"] = "COMPLETED"
                            self._paused = True
                        continue
                    with self._condition:
                        if self._generation != generation:
                            continue
                    self._publish(frame, target, fps, detector, request, error)
                    next_frame = target + 1

                # Playback speed is based on *file* fps. If decoding/inspection
                # costs longer than a frame, processing slows rather than
                # silently skipping intermediate detector frames.
                with self._condition:
                    if not self._stopping and self._pending_seek is None and not self._paused:
                        self._condition.wait(timeout=max(0.0, 1.0 / fps - (time.monotonic() - frame_started)))
        except Exception as exc:
            logger.exception("Video playback failed")
            with self._condition:
                self._state.update(state="ERROR", status="ERROR", reason=str(exc))
        finally:
            cap.release()
