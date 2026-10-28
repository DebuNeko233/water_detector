"""后端统一入口：--dev 是开发热重载；发行版由 Electron 托管动态端口。"""

import argparse
import asyncio
import json
import multiprocessing
import os
import threading
from pathlib import Path

import uvicorn
from loguru import logger

from .main import app


def watch_parent(parent_pid: int, request_stop, done: threading.Event) -> None:
    """Observe the actual parent handle on Windows, not a reusable PID alone."""
    # 父进程监测只供托管模式使用，避免 Electron 异常退出后遗留后台 Python。
    if os.name != "nt":
        while not done.wait(1):
            if os.getppid() != parent_pid:
                logger.warning("Electron parent disappeared; stopping backend")
                request_stop()
                return
        return

    # Windows 通过进程句柄检测退出，而不是仅检查可能复用的 PID。
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    # SYNCHRONIZE 权限足以等待父进程退出，无需申请更多权限。
    handle = kernel32.OpenProcess(0x00100000, 0, parent_pid)
    if not handle:
        logger.error("Cannot watch Electron parent PID {}", parent_pid)
        request_stop()
        return
    try:
        while not done.wait(0.5):
            result = kernel32.WaitForSingleObject(handle, 0)
            if result == 0:
                logger.warning("Electron parent exited; stopping backend")
                request_stop()
                return
            if result != 0x102:
                logger.error("Parent process watch failed: {}", result)
                request_stop()
                return
    finally:
        kernel32.CloseHandle(handle)


async def run_managed(port: int) -> None:
    # 打包模式传入 port=0，交由系统选择空闲端口。
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    # 内部关闭接口请求 Uvicorn 优雅退出，不直接结束进程。
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
    done = threading.Event()
    parent_pid = int(os.environ.get("APP_PARENT_PID", "0"))
    if parent_pid:
        threading.Thread(
            target=watch_parent,
            args=(parent_pid, app.state.request_shutdown, done),
            name="electron-parent-watch",
            daemon=True,
        ).start()

    server_task = asyncio.create_task(server.serve())
    try:
        while not server.started and not server_task.done():
            await asyncio.sleep(0.05)
        if not server.started:
            await server_task
            raise RuntimeError("Backend failed before becoming ready")

        # 监听成功后才写就绪文件，供 Electron 获取真实端口。
        ready_path = os.environ.get("APP_READY_FILE")
        if ready_path:
            actual_port = server.servers[0].sockets[0].getsockname()[1]
            target = Path(ready_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子替换，防止 Electron 读取不完整的 JSON。
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "port": actual_port,
                "pid": os.getpid(),
                "session_id": os.environ.get("APP_SESSION_ID", ""),
            }), encoding="utf-8")
            temporary.replace(target)
        await server_task
    finally:
        done.set()
        if not server_task.done():
            server.should_exit = True
            await server_task


def main() -> None:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="Standalone desktop sample backend")
    parser.add_argument("--dev", action="store_true", help="Enable Uvicorn autoreload")
    arguments = parser.parse_args()
    # --dev 为固定 8765 端口；不带参数为 Electron 使用的动态端口。
    if arguments.dev:
        uvicorn.run("sample_backend.main:app", host="127.0.0.1", port=8765, reload=True)
    else:
        asyncio.run(run_managed(0))


if __name__ == "__main__":
    main()
