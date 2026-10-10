# Electron 与 Python 生命周期（Windows）

## 开发命令

| 命令 | 功能 |
|---|---|
| `cd frontend && pnpm dev` | 只启动 Vite（需单独启动 Python 才能取数据） |
| `cd backend && python -m water_detector.main` | 只启动后端，`http://127.0.0.1:8765/api/health` 可检查 |
| `cd frontend && pnpm desktop` | **一键开发运行**：自动启动 Vite → 等待就绪 → Electron → 后端 |
| `cd frontend && pnpm desktop:electron` | 仅 Electron；适合已经手动启动 Vite 的场合 |
| `cd frontend && pnpm desktop:dist` | 构建前端，Electron 从 `dist/index.html` 读取文件（仍为开发模式 Python） |

**重要：** 上面的 `desktop` 是开发用一键启动，不等于 Windows 已打包安装程序。

## Electron 主进程

- 使用唯一实例锁避免多个窗口争抢后端。
- 若 `127.0.0.1:8765/api/health` 已提供兼容服务，则只连接、不声明所有权，不会在 Electron 退出时停止别人手动启动的后端。
- 默认开发后端使用当前虚拟环境 (`VIRTUAL_ENV`)，亦可设置 `WAFER_PYTHON` 指定解释器。
- Electron 拥有的 Python 使用 `windowsHide: true` 和 `stdio: ['pipe','pipe','pipe']`，不打开额外命令行窗口。
- 等待后端 `/api/health` 正常后才加载 Vue；失败时显示可读诊断页。
- Python 使用 `WAFER_DESKTOP_CHILD=1` 启用 stdin 控制协议。Electron 退出先发送 `shutdown\n`；uvicorn 正常结束时停止检测 Worker 并尽量让 CSV、视频写盘收尾；超时后兜底杀进程树。
- 调试时设置 `WAFER_DEBUG_CONSOLE=1` 可在开发终端查看 Python 日志，Electron `backend-logs` IPC 可读取最近日志。**不主动启动 Python 命令行窗口**。

## 打包运行规划

Windows 发布版：`electron-builder` / NSIS（单个安装程序）中包含 Vue 生产构建和通过 PyInstaller `--console`（Electron 的 `windowsHide: true` 隐藏控制台；保留标准输入用于优雅关闭） 打包的 `wafer-backend.exe`。最终布局须包括：

```
resources/
  backend/wafer-backend.exe
  app.asar  # Electron 源码和 Vue dist
```

Electron 发布版加载内置 HTML，启动 `process.resourcesPath/backend/wafer-backend.exe`，用户只启动一个桌面程序而无需安装 Python。**发布构建和安装器尚未在 Windows 实机验收，当前请先使用开发模式。**

## GitHub Actions

`.github/workflows/ci.yml` 对 PR 和推送运行：
- Linux/Windows：后端 pytest（含无需视频素材的原 main.py 等价算法回归、FastAPI、子进程启动/优雅退出）
- Windows：pnpm 锁文件安装、Vue TS 构建、Electron 主进程与启动管理语法校验

当前 CI 配置已包含在源码包中。只有代码推送到 GitHub 后，Actions 才会运行；本地 pytest 通过不等于 GitHub CI 通过。

建议从仓库根目录执行 `git add . && git commit -m "fix: supervise Vite Electron Python and enable CI" && git push`，随后到 GitHub Actions 查看实际作业状态。

客户视频不上传公开仓库，保留本地回归测试 `GOOD_VIDEO` / `BAD_VIDEO` 路径支持。
