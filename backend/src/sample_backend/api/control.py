"""受保护的内部停机接口：仅允许本机请求携带独立控制令牌。"""

import hmac

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from ..core.config import control_token

router = APIRouter()


@router.post("/internal/shutdown", include_in_schema=False)
async def shutdown(
    request: Request,
    background_tasks: BackgroundTasks,
    x_control_token: str = Header(default=""),
) -> dict:
    # 访问业务 API 的会话令牌不能授权关闭整个后端。
    expected = control_token()
    if (
        not expected
        or request.client is None
        or request.client.host not in ("127.0.0.1", "::1")
        or not hmac.compare_digest(expected, x_control_token)
    ):
        raise HTTPException(status_code=403, detail="FORBIDDEN")

    callback = getattr(request.app.state, "request_shutdown", None)
    if callback is None:
        raise HTTPException(status_code=503, detail="SHUTDOWN_UNAVAILABLE")
    # 先给 Electron 返回 HTTP 响应，再调用关闭回调。
    background_tasks.add_task(callback)
    return {"accepted": True}
