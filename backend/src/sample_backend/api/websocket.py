"""WebSocket 在握手处鉴权，连接管理和事件广播交给 EventHub。"""

import hmac

from fastapi import APIRouter, WebSocket

from ..core.config import session_token

router = APIRouter()


@router.websocket("/ws/events")
async def events(websocket: WebSocket) -> None:
    required = session_token()
    supplied = websocket.headers.get("x-session-token", "")
    # WebSocket 不经过 /api/ HTTP 中间件，必须单独验证会话令牌。
    if required and not hmac.compare_digest(required, supplied):
        await websocket.close(code=4401)
        return
    await websocket.app.state.events.serve(websocket)
