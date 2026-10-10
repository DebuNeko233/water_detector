"""Desktop embedded mode: python -m water_detector.main (or packaged executable).

When WAFER_DESKTOP_CHILD=1, Electron owns this process and sends the literal
"shutdown" line on stdin; uvicorn gracefully stops via its should_exit flag.
Standalone development mode remains independent of Electron.
"""
from __future__ import annotations
import os
import sys
import threading
from water_detector.service.api import create_app
from water_detector.config.manager import ConfigManager
from water_detector.log.setup import configure_logging

manager = ConfigManager()
configure_logging(manager.home)
app = create_app(manager)


def run_server() -> None:
    import uvicorn
    port = int(os.environ.get("WAFER_PORT", "8765"))
    if not 1 <= port <= 65535:
        raise ValueError("WAFER_PORT must be a valid TCP port")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    if os.environ.get("WAFER_DESKTOP_CHILD") == "1":
        def listen_for_shutdown():
            for line in sys.stdin:
                if line.strip() == "shutdown":
                    server.should_exit = True
                    break
            else:
                # Electron has exited or closed its pipe unexpectedly.
                server.should_exit = True
        threading.Thread(target=listen_for_shutdown, daemon=True, name="electron-stdin-control").start()
    server.run()


if __name__ == "__main__":
    run_server()
