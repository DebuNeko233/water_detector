"""内存 WebSocket 广播中心：消息不持久化，断线后需靠 HTTP 查询任务状态。"""

import asyncio
import itertools
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class EventHub:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        # 每条事件递增序号，便于区分收到事件的先后（进程重启会重置）。
        self._sequence = itertools.count(1)

    def _message(self, event: str, data: dict[str, Any]) -> dict:
        return {"event": event, "sequence": next(self._sequence), "data": data}

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        message = self._message(event, data)
        # 遍历快照，广播失败时可以安全删除已断开的客户端。
        for client in tuple(self._clients):
            try:
                await client.send_json(message)
            except Exception:
                self._clients.discard(client)

    async def serve(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        try:
            # 建连事件通知前端补查可能错过的任务进度。
            await websocket.send_json(self._message(
                "system.connected", {"message": "WebSocket connected"}
            ))
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=10)
                except asyncio.TimeoutError:
                    # 一段时间无客户端消息时仍发心跳，帮助检测断线。
                    await websocket.send_json(self._message("system.heartbeat", {}))
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(websocket)

    async def close(self) -> None:
        # 先清除注册表，再逐个关闭，避免退出期间继续广播。
        clients = tuple(self._clients)
        self._clients.clear()
        for client in clients:
            try:
                await client.close()
            except Exception:
                pass
