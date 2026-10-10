"""Exclusive monitoring worker. File source processes EVERY frame (no frame skipping)."""
from __future__ import annotations
import csv
import logging
import threading
import uuid
import time
from pathlib import Path
from typing import Any
import cv2
from water_detector.camera.source import open_source
from water_detector.config.schema import AppConfig
from water_detector.config.manager import ConfigManager
from water_detector.pipeline.inspection import InspectionPipeline
from water_detector.render.overlay import jpeg_bytes

logger = logging.getLogger("water_detector.task")


class MonitorTask:
    def __init__(self, config_manager: ConfigManager):
        self.manager = config_manager
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, Any] = {"state": "IDLE", "status": "IDLE", "history": []}
        self._jpeg: bytes | None = None

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def preview(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def start(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A monitoring task is already running")
            cfg = self.manager.active()
            if not cfg.source.address and cfg.source.kind in ("file", "rtsp"):
                raise ValueError("Configure a video source first")
            self._stop.clear()
            task_id = str(uuid.uuid4())
            self._snapshot = {"state": "STARTING", "task_id": task_id, "status": "WARMUP", "history": []}
            self._thread = threading.Thread(target=self._run, args=(task_id, cfg), daemon=True, name="monitor-worker")
            self._thread.start()
            return dict(self._snapshot)

    def stop(self) -> dict:
        self._stop.set()
        return self.snapshot()

    def wait_for_exit(self, timeout: float = 4.0) -> bool:
        """Allow an orderly shutdown to finalize MP4 and CSV output."""
        with self._lock:
            worker = self._thread
        if worker is None or not worker.is_alive():
            return True
        worker.join(timeout=timeout)
        return not worker.is_alive()

    def _update(self, **values):
        with self._lock:
            self._snapshot.update(values)

    def _run(self, task_id: str, cfg: AppConfig):
        source = writer = csv_file = None
        rows = 0
        output_dir = self.manager.home / "results" / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            source = open_source(cfg.source)
            pipeline = InspectionPipeline(cfg)
            csv_path = output_dir / "result.csv"
            csv_file = open(csv_path, "w", newline="", encoding="utf-8-sig")
            csvwriter = csv.writer(csv_file)
            csvwriter.writerow(["frame", "timestamp_s", "stage", "status", "s_water", "max_sector", "abnormal_run_s", "reason"])
            video_path = output_dir / "annotated.mp4"
            last_t = None
            self._update(state="RUNNING", output_dir=str(output_dir))
            while not self._stop.is_set():
                packet = source.read()
                if packet is None:
                    break
                # A discontinuous stream must not continue an old residence/NG window.
                if last_t is not None and (packet.timestamp_s <= last_t or packet.timestamp_s - last_t > max(0.5, 3 / packet.fps)):
                    pipeline.reset()
                last_t = packet.timestamp_s
                result, visual = pipeline.process(packet)
                if writer is None:
                    h, w = visual.shape[:2]
                    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), packet.fps, (w, h))
                    if not writer.isOpened():
                        raise OSError(f"Cannot open video output {video_path}")
                writer.write(visual)
                csvwriter.writerow([packet.frame_id, f"{packet.timestamp_s:.6f}", result["stage"],
                                    result["status"], f"{result['s_water']:.6f}", result["max_sector_idx"],
                                    f"{result['abnormal_run_s']:.6f}", result["reason"]])
                encoded = jpeg_bytes(visual)
                rows += 1
                with self._lock:
                    self._jpeg = encoded
                    self._snapshot.update(result)
                    self._snapshot.update(state="RUNNING", task_id=task_id, processed_frames=rows)
                # Offline frame-accurate processing has no frame drop. UI can poll latest preview.
            self._update(state="STOPPED" if self._stop.is_set() else "COMPLETED", processed_frames=rows)
        except Exception as exc:
            logger.exception("Monitoring task failed")
            self._update(state="ERROR", status="ERROR", reason=str(exc), processed_frames=rows)
        finally:
            if csv_file:
                csv_file.close()
            if writer:
                writer.release()
            if source:
                source.close()
