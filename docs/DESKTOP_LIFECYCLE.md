# Python / Electron 生命周期（迁移到模板后）

运行时采用模板的 BackendManager，保留单实例锁、Electron 安全 IPC、独立子进程所有权、随机会话端口、就绪文件和父进程监视器。

- `pnpm dev` 在一个终端中同时启动后端与 Electron，不额外弹出 Python 终端；进程由开发启动脚本管理，退出时清理本次启动的子进程树。
- `pnpm dev:backend` 可以独立启动 Python（127.0.0.1:8765）。
- `pnpm dev:frontend` 可以独立启动 Electron+Vue 前端并尝试连接已经启动的 Python。
- 发行版 Electron 创建 `sample_backend` Python 子进程，使用动态端口、随机 APP_SESSION_TOKEN 和 APP_CONTROL_TOKEN 进行请求和停机认证。它不误杀用户独立启动的 Python。
- `water_detector.service.api.create_app` 作为业务路由集成到模板 FastAPI 中，其水流检测和监控任务生命周期仍由原业务模块执行。
- 发行版 Electron 关闭时优先 POST /internal/shutdown，超时终止自身的 Python 子进程；Electron 异常退出由 Python 父进程监视器结束进程。
- Renderer 的参数请求通过预加载 IPC 发起，不直接获知生产端口/令牌；视频用 GET /api/monitor/frame 传输 JPEG。
- 单独运行旧 `python -m water_detector.main` 仍保留为开发/回归入口，但桌面托管使用模板的 `python -m sample_backend`。
