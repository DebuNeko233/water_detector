"""FastAPI application: wafer inspection routes with template process/session runtime.

The original wafer FastAPI app owns MonitorTask and its shutdown lifespan.
The desktop template adds authenticated IPC, event hub and control endpoints,
without replacing or bypassing the existing wafer lifecycle.
"""
from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from .api.control import router as control_router
from .api.routes import router as demo_router
from .api.websocket import router as event_router
from .core.config import session_token
from .core.logging import configure_logging
from .services.demo_jobs import DemoJobs
from .services.event_hub import EventHub
from water_detector.service.api import create_app as create_wafer_app


def create_app() -> FastAPI:
    app = create_wafer_app()
    app.title = "Wafer Water Detector"
    # CRITICAL: Preserve the inspection application's existing lifespan.
    # include_router(other_app.router) alone would not run its shutdown handler.
    wafer_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def desktop_lifespan(application: FastAPI):
        configure_logging()
        application.state.events = EventHub()
        application.state.jobs = DemoJobs(application.state.events.publish)
        logger.info("Wafer desktop backend starting")
        try:
            async with wafer_lifespan(application):
                yield
        finally:
            await application.state.jobs.close()
            await application.state.events.close()
            logger.info("Wafer desktop backend stopped")

    app.router.lifespan_context = desktop_lifespan

    @app.middleware("http")
    async def authenticate_api(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            required = session_token()
            supplied = request.headers.get("x-session-token", "")
            if required and not hmac.compare_digest(required, supplied):
                return JSONResponse(status_code=401, content={"detail": "INVALID_SESSION"})
        return await call_next(request)

    # The wafer routes are already registered, including /api/health,
    # /api/config/*, /api/camera/*, and the monitor endpoints.
    # Template routes add the development echo and event stream.
    app.include_router(demo_router)
    app.include_router(event_router)
    app.include_router(control_router)
    return app


app = create_app()
