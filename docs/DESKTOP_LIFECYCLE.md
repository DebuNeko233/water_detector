# Electron ↔ Python 生命周期（V0.3）
本次**仅参考** [sop-platform](https://github.com/DebuNeko233/sop-platform) 的前后端联动方式；
不复制它的业务、视觉检测或用户界面代码。

## Windows 开发

首次安装：

```powershell
python -m pip install -e "./backend[test]"
cd frontend
pnpm install
```

命令：
- `cd frontend; pnpm desktop` 或 `pnpm dev`：Vite 自动启动 Electron，Electron 自动启动 Python
- `cd frontend; pnpm dev:web`：只运行 Vue（可连接手工启动的 Python）
- `python -m water_detector.main`：只运行 Python（localhost:8765）
- `cd frontend; pnpm test:electron`：测试进程所有权及关闭策略
- `cd frontend; pnpm build`：构建 Vue + Electron main/preload

## 启动与退出

Vite 使用 `vite-plugin-electron/simple` 统一管理 Electron 开发启动，解决之前脚本间
5173 端口启动竞争。Electron 先显示初始化画面，Python 的
`GET /api/health` 返回 `service: water_detector` 后再加载 Vue。

Electron 只管理**它自己启动的** Python 进程。如果 localhost 8765 上已经是
water_detector，Electron 只连接，不会停止该独立进程。

Electron 启动 Python 时通过环境变量传入随机 `WAFER_DESKTOP_CONTROL_TOKEN`，
关闭时向 `POST /api/system/process/shutdown` 发送 `X-Wafer-Control-Token`。
Python 只接受 loopback 对端和正确令牌，返回 202 后启动 Uvicorn 优雅退出，
允许视频写入器、CSV 和相机资源正常释放。超时后才杀掉属于自己的 PID 进程树。

开发模式默认 `windowsHide: true`，Python 日志写入应用日志并由 Electron 收集；
设置 `WAFER_DEBUG_CONSOLE=1` 可让开发终端输出诊断信息。
未来打包的后端可用 PyInstaller `--noconsole`，不再依赖 stdin 管道。

## 发布版本边界

**Windows 安装包尚未制作及验收。** 最终将用 electron-builder 将 `dist`、
`dist-electron` 与 `wafer-backend.exe` 一起放进单一安装程序，Electron 仍是用户唯一入口。
本文中的 `pnpm desktop` 是开发版一键启动。

## 验证

GitHub Actions 在 Windows 运行 Python 测试、Node 进程管理测试和 Vue/Electron 构建；
在 Ubuntu 运行 Python 测试。核心水流 `S_water` 数学公式未修改。
