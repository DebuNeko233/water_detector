"""FastAPI 应用工厂：装配中间件、路由和生命周期资源。"""

import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from .api.control import router as control_router
from .api.routes import router as api_router
from .api.websocket import router as websocket_router
from .core.config import session_token
from .core.logging import configure_logging
from .services.demo_jobs import DemoJobs
from .services.event_hub import EventHub
from water_detector.service.api import create_app as create_wafer_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 生命周期内初始化任务和事件中心，确保不同 app 实例相互隔离。
    configure_logging()
    app.state.events = EventHub()
    app.state.jobs = DemoJobs(app.state.events.publish)
    logger.info("FastAPI is starting")
    try:
        yield
    finally:
        # 先停任务，再关闭事件中心，避免退出时任务仍在广播。
        await app.state.jobs.close()
        await app.state.events.close()
        logger.info("FastAPI is shutting down")


def create_app() -> FastAPI:
    app = FastAPI(title="Wafer Water Detector", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def authenticate_api(request: Request, call_next):
        # /api/ 使用会话令牌；/internal/shutdown 由专用控制令牌保护。
        if request.url.path.startswith("/api/"):
            required = session_token()
            supplied = request.headers.get("x-session-token", "")
            # 开发模式令牌为空；发行模式才启用随机令牌校验。
            if required and not hmac.compare_digest(required, supplied):
                return JSONResponse(status_code=401, content={"detail": "INVALID_SESSION"})
        return await call_next(request)

    app.include_router(api_router)
    app.include_router(websocket_router)
    app.include_router(control_router)
    # Reuse the actual wafer detector FastAPI routes and their cleanup lifespan.
    # No detector algorithm or calibration model is replaced by the desktop shell.
    app.include_router(create_wafer_app().router)
    return app


app = create_app()
