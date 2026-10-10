"""Electron-owned Python shutdown: authenticated loopback HTTP, no stdin dependency."""
from __future__ import annotations
import hmac
import ipaddress
import os
import threading
from collections.abc import Callable


def is_authorized(host: str | None, token: str | None, expected: str | None) -> bool:
    if not host or not token or not expected:
        return False
    try:
        if not ipaddress.ip_address(host).is_loopback:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(token, expected)


class ShutdownController:
    def __init__(self, token: str | None = None):
        self.token = token if token is not None else os.environ.get("WAFER_DESKTOP_CONTROL_TOKEN", "")
        self._callback: Callable[[], None] | None = None
        self._scheduled = False
        self._lock = threading.Lock()

    def register(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callback = callback

    def request(self) -> bool:
        # Respond to Electron before initiating graceful Uvicorn shutdown.
        with self._lock:
            if self._scheduled:
                return True
            if self._callback is None:
                return False
            self._scheduled = True
            callback = self._callback
        timer = threading.Timer(0.2, callback)
        timer.daemon = True
        timer.start()
        return True
