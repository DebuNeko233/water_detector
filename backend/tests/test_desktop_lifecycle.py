"""Contract: Electron-owned Python child starts quietly and exits on authenticated HTTP shutdown.

This is intentionally exercised on Windows runners too. It does not open a GUI.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_backend_is_ready_and_stops_with_electron():
    port = free_port()
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env.update({
            "WAFER_PORT": str(port),
            "WAFER_DESKTOP_CHILD": "1",
            "WAFER_DESKTOP_CONTROL_TOKEN": "test-ephemeral-secret",
            "WAFER_DATA_DIR": str(Path(tmp) / "data"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", ""),
        })
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [sys.executable, "-m", "water_detector.main"],
            cwd=Path(__file__).resolve().parents[1],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=flags,
        )
        try:
            deadline = time.monotonic() + 15
            healthy = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.3) as response:
                        healthy = json.load(response)["ok"] is True
                    if healthy:
                        break
                except (OSError, ValueError):
                    time.sleep(0.12)
            assert healthy, "embedded backend did not become healthy within 15 seconds"
            from urllib.request import Request
            request = Request(
                f"http://127.0.0.1:{port}/api/system/process/shutdown",
                data=b"", method="POST", headers={"X-Wafer-Control-Token": "test-ephemeral-secret"})
            with urlopen(request, timeout=2) as response:
                assert response.status == 202
            assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
