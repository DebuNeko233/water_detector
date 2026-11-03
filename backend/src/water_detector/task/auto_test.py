"""Offline full-video algorithm test, isolated from production MonitorTask.

Runs every frame from the start with the same InspectionPipeline used by the
interactive debugger, including dynamic nozzle masking and auto ROI choice.
Never modifies committed settings, monitor state, or production CSV/MP4 output.
"""
from __future__ import annotations

import threading
from collections import Counter

import cv2
from loguru import logger

from water_detector.models.frames import FramePacket
from water_detector.pipeline.inspection import InspectionPipeline
from water_detector.task.file_playback import PlaybackRequest, FilePlayback


class AutoVideoTest:
    def __init__(self):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict = self._initial()

    @staticmethod
    def _initial() -> dict:
        return {
            "state": "IDLE", "processed_frames": 0, "total_frames": 0,
            "progress": 0.0, "fps": 0.0, "stage_counts": {},
            "status_counts": {}, "ng_frames": 0,
            "max_s_water": 0.0, "nozzle_excluded_frames": 0,
            "average_nozzle_excluded_fraction": 0.0,
            "events": [], "error": "",
        }

    def snapshot(self) -> dict:
        with self._lock:
            result = dict(self._state)
            result["events"] = list(self._state["events"])
            result["stage_counts"] = dict(self._state["stage_counts"])
            result["status_counts"] = dict(self._state["status_counts"])
            return result

    def start(self, request: PlaybackRequest) -> dict:
        if request.source.kind != "file":
            raise ValueError("Automatic algorithm testing requires a video file")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("An automatic video test is already running")
            self._stop.clear()
            self._state = {**self._initial(), "state": "STARTING"}
            self._thread = threading.Thread(
                target=self._run, args=(request.model_copy(deep=True),),
                daemon=True, name="automatic-algorithm-test",
            )
            self._thread.start()
            return self.snapshot()

    def stop(self) -> dict:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=8)
        return self.snapshot()

    def _run(self, request: PlaybackRequest) -> None:
        source = cv2.VideoCapture(request.source.address)
        if not source.isOpened():
            with self._lock:
                self._state.update(state="ERROR", error="Cannot open video file")
            return
        try:
            fps = float(source.get(cv2.CAP_PROP_FPS))
            if not 1 <= fps <= 240:
                fps = 30.0
            total = int(source.get(cv2.CAP_PROP_FRAME_COUNT))
            pipeline = InspectionPipeline(FilePlayback._pipeline_config(request))
            stages: Counter[str] = Counter()
            statuses: Counter[str] = Counter()
            mask_sum = 0.0
            masked_frames = 0
            max_score = 0.0
            last_key = None
            with self._lock:
                self._state.update(state="RUNNING", total_frames=total, fps=fps)
            index = 0
            while not self._stop.is_set():
                ok, frame = source.read()
                if not ok:
                    break
                result, _ = pipeline.process(
                    FramePacket(index, index / fps, frame,
                                request.source.address, fps),
                    show_roi=False,
                )
                stage, status = result["stage"], result["status"]
                stages[stage] += 1
                statuses[status] += 1
                max_score = max(max_score, float(result.get("s_water") or 0))
                fraction = float(result.get("nozzle_excluded_fraction") or 0)
                mask_sum += fraction
                masked_frames += int(fraction > 0)
                key = (stage, status)
                with self._lock:
                    if key != last_key and len(self._state["events"]) < 256:
                        self._state["events"].append({
                            "frame_index": index, "time_s": round(index / fps, 3),
                            "stage": stage, "status": status,
                            "s_water": round(float(result.get("s_water") or 0), 6),
                        })
                    last_key = key
                    index += 1
                    # Publish periodically, including early frames, without
                    # keeping thousands of full frame images in RAM.
                    if index % 10 == 0 or index == total or index <= 2:
                        self._state.update(
                            processed_frames=index,
                            progress=round(index / total, 4) if total else 0.0,
                            stage_counts=dict(stages), status_counts=dict(statuses),
                            ng_frames=statuses["NG"],
                            max_s_water=max_score,
                            nozzle_excluded_frames=masked_frames,
                            average_nozzle_excluded_fraction=mask_sum / index,
                        )
            with self._lock:
                self._state.update(
                    state="STOPPED" if self._stop.is_set() else "COMPLETED",
                    processed_frames=index,
                    progress=round(index / total, 4) if total else 1.0,
                    stage_counts=dict(stages), status_counts=dict(statuses),
                    ng_frames=statuses["NG"], max_s_water=max_score,
                    nozzle_excluded_frames=masked_frames,
                    average_nozzle_excluded_fraction=mask_sum / max(index, 1),
                )
        except Exception as exc:
            logger.exception("Automatic video algorithm test failed")
            with self._lock:
                self._state.update(state="ERROR", error=str(exc))
        finally:
            source.release()
