"""Start FastAPI standalone or as an Electron-owned hidden child process."""
from __future__ import annotations
import multiprocessing
import os
from water_detector.service.api import create_app
from water_detector.service.shutdown import ShutdownController
from water_detector.config.manager import ConfigManager
from water_detector.log.setup import configure_logging

manager = ConfigManager()
configure_logging(manager.home)
shutdown = ShutdownController()
app = create_app(manager, shutdown)


def run_server() -> None:
    import uvicorn
    port = int(os.environ.get("WAFER_PORT", "8765"))
    if not 1 <= port <= 65535:
        raise ValueError("WAFER_PORT must be a valid TCP port")
    # Child worker threads must not inherit process control authority.
    os.environ.pop("WAFER_DESKTOP_CONTROL_TOKEN", None)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    shutdown.register(lambda: setattr(server, "should_exit", True))
    server.run()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run_server()
