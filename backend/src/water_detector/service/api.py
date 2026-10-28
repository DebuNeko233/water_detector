"""Localhost-only HTTP control plane. Python owns processing, frontend owns display."""
from __future__ import annotations
import asyncio
import hmac
import os
import uuid
from contextlib import asynccontextmanager
import base64
from pathlib import Path
from typing import Any
import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from water_detector.config.manager import ConfigManager
from water_detector.config.schema import AppConfig, Calibration, DetectorConfig, SourceConfig
from water_detector.camera.source import open_source
from water_detector.camera.metadata import probe_file_video
from water_detector.detector.water import WaterDetector
from water_detector.render.overlay import render_overlay, jpeg_bytes
from water_detector.task.monitor import MonitorTask
from water_detector.task.file_playback import FilePlayback, PlaybackRequest
from water_detector.service.shutdown import ShutdownController, is_authorized


class PreviewRequest(BaseModel):
    source: SourceConfig
    stage: str = "P1"
    calibration: Calibration
    detector: DetectorConfig
    frame_index: int = Field(0, ge=0)


class PositionReferenceCaptureRequest(BaseModel):
    source: SourceConfig
    frame_index: int = Field(ge=0)
    stage: str


class PlaybackSeekRequest(BaseModel):
    frame_index: int = Field(ge=0)


class RefFrameRequest(BaseModel):
    source: SourceConfig
    frame_index: int = Field(0, ge=0)


def read_file_frame(req: RefFrameRequest):
    if req.source.kind != "file":
        raise ValueError("Frame seeking is only supported for local video files")
    cap = cv2.VideoCapture(req.source.address)
    if not cap.isOpened():
        raise FileNotFoundError(req.source.address)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, req.frame_index)
        ok, frame = cap.read()
        if not ok:
            raise ValueError("Requested frame is unavailable")
        return frame
    finally:
        cap.release()


def create_app(manager: ConfigManager | None = None, shutdown: ShutdownController | None = None) -> FastAPI:
    manager = manager or ConfigManager()
    monitor = MonitorTask(manager)
    playback = FilePlayback()
    shutdown = shutdown or ShutdownController()
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        monitor.stop()
        # Wait in a worker thread so that WebSocket/HTTP resources can unwind.
        await asyncio.to_thread(monitor.wait_for_exit, 4.0)
        await asyncio.to_thread(playback.stop)

    app = FastAPI(title="Wafer Water Detection API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "null"],
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])
    app.state.monitor = monitor
    app.state.playback = playback
    app.state.config_manager = manager

    @app.get("/api/health")
    def health():
        return {"ok": True, "status": "ready", "service": "water_detector", "version": "0.1.0"}

    @app.post("/api/system/process/shutdown", status_code=202)
    def controlled_shutdown(request: Request, x_wafer_control_token: str | None = Header(default=None)):
        # Only the actual peer address, never X-Forwarded-For.
        peer = request.client.host if request.client else None
        if not is_authorized(peer, x_wafer_control_token, shutdown.token):
            raise HTTPException(403, "Desktop process control is not authorized")
        if not shutdown.request():
            raise HTTPException(503, "Shutdown callback not registered")
        return {"accepted": True}

    @app.get("/api/config/active", response_model=AppConfig)
    def config_get():
        return manager.active()

    @app.post("/api/config/commit", response_model=AppConfig)
    def config_commit(config: AppConfig):
        if monitor.snapshot()["state"] in ("RUNNING", "STARTING"):
            raise HTTPException(409, "Stop the monitoring task before committing configuration")
        return manager.save(config)

    @app.put("/api/calibration/{stage}", response_model=AppConfig)
    def calibration_save(stage: str, calibration: Calibration):
        if stage not in ("P1", "P2", "P3"):
            raise HTTPException(400, "stage must be P1/P2/P3")
        if monitor.snapshot()["state"] in ("RUNNING", "STARTING"):
            raise HTTPException(409, "Stop monitoring before changing calibration")
        cfg = manager.active()
        cfg.calibrations[stage] = calibration
        return manager.save(cfg)

    @app.post("/api/camera/metadata")
    def camera_metadata(req: RefFrameRequest):
        # FPS, total frames and duration come from Python's video decoder.
        try:
            return probe_file_video(req.source)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/camera/frame")
    def camera_frame(req: RefFrameRequest):
        try:
            frame = read_file_frame(req)
            h, w = frame.shape[:2]
            return {"width": w, "height": h, "jpeg_base64": base64.b64encode(jpeg_bytes(frame)).decode("ascii")}
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/debug/preview")
    def debug_preview(req: PreviewRequest):
        """Replays required pre-roll with a SEPARATE detector; never touches monitor state."""
        if req.source.kind != "file":
            raise HTTPException(400, "Debug pre-roll preview currently supports MP4 files")
        cap = cv2.VideoCapture(req.source.address)
        if not cap.isOpened():
            raise HTTPException(400, "Cannot open video")
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
            if not 1 <= fps <= 240:
                fps = 30.0
            start = max(0, req.frame_index - int(round(fps * (req.detector.history_seconds + req.detector.ng_hold_seconds + 1.0))))
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            detector = None
            result = None
            frame = None
            samples = []
            for index in range(start, req.frame_index + 1):
                ok, frame = cap.read()
                if not ok:
                    break
                if detector is None:
                    h, w = frame.shape[:2]
                    if (w, h) != (req.calibration.reference_width, req.calibration.reference_height):
                        raise HTTPException(400, "Calibration resolution does not match video")
                    detector = WaterDetector((h, w), fps, req.calibration, req.detector)
                result = detector.process(frame)
                samples.append({"time_s": round(index / fps, 3), "s_water": result["s_water"]})
            if detector is None or result is None or frame is None:
                raise HTTPException(400, "Frame not available")
            overlay = render_overlay(frame, req.calibration, req.detector, result)
            return {"status": result["status"], "s_water": result["s_water"],
                    "samples": samples, "max_sector_idx": result["max_sector_idx"],
                    "jpeg_base64": base64.b64encode(jpeg_bytes(overlay)).decode("ascii")}
        finally:
            cap.release()

    # Local-file playback for interactive algorithm testing. This session is
    # entirely independent of the production monitor task and its recordings.
    @app.post("/api/position/reference/capture")
    def position_reference_capture(req: PositionReferenceCaptureRequest):
        """Capture a P1/P2/P3 reference from the displayed test video frame.

        The frontend only assigns this path to its UNCOMMITTED draft. Capturing
        an image does not enable auto mode or modify the active configuration.
        """
        if req.stage not in ("P1", "P2", "P3"):
            raise HTTPException(400, "Reference stage must be P1/P2/P3")
        try:
            frame = read_file_frame(RefFrameRequest(
                source=req.source, frame_index=req.frame_index
            ))
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
        directory = manager.home / "position_references"
        directory.mkdir(parents=True, exist_ok=True)
        success, encoded = cv2.imencode(".png", frame)
        if not success:
            raise HTTPException(500, "Reference image encode failed")
        destination = directory / (req.stage + "_" + uuid.uuid4().hex + ".png")
        destination.write_bytes(encoded.tobytes())
        return {"stage": req.stage, "frame_index": req.frame_index, "path": str(destination)}

    @app.post("/api/playback/start")
    def playback_start(req: PlaybackRequest):
        try:
            return playback.start(req)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/playback/update")
    def playback_update(req: PlaybackRequest):
        # Unsaved ROI and threshold edits only affect the independent preview.
        # The production monitor and committed app config are not modified.
        try:
            return playback.update(req)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/playback/seek")
    def playback_seek(req: PlaybackSeekRequest):
        try:
            return playback.seek(req.frame_index)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/playback/pause")
    def playback_pause():
        return playback.pause()

    @app.post("/api/playback/resume")
    def playback_resume():
        try:
            return playback.resume()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/playback/stop")
    def playback_stop():
        return playback.stop()

    @app.get("/api/playback/state")
    def playback_state(after_revision: int = -1):
        return playback.snapshot(after_revision=after_revision)

    @app.post("/api/monitor/start")
    def monitor_start():
        try:
            return monitor.start()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/monitor/stop")
    def monitor_stop():
        return monitor.stop()

    @app.get("/api/monitor/status")
    def monitor_status():
        return monitor.snapshot()

    @app.get("/api/monitor/frame")
    def monitor_frame():
        # JPEG travels through Electron's authenticated IPC rather than unauthenticated <img>.
        frame = monitor.preview()
        return {"jpeg_base64": base64.b64encode(frame).decode("ascii") if frame else None}

    @app.get("/api/monitor/preview.mjpg")
    async def preview_stream():
        async def frames():
            while True:
                jpeg = monitor.preview()
                if jpeg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                await asyncio.sleep(0.15)
        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.websocket("/ws/monitor")
    async def websocket_monitor(ws: WebSocket):
        expected = os.environ.get("APP_SESSION_TOKEN", "")
        if expected and not hmac.compare_digest(ws.headers.get("x-session-token", ""), expected):
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                await ws.send_json(monitor.snapshot())
                await asyncio.sleep(0.15)
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.get("/api/records")
    def records():
        home = manager.home / "results"
        if not home.exists():
            return []
        return [{"task_id": p.name, "csv": str(p / "result.csv"), "video": str(p / "annotated.mp4")}
                for p in sorted(home.iterdir(), reverse=True) if p.is_dir()]

    return app
