# 晶圆洗片水流异常检测 · Electron + Vue + Python

本仓库基于 [python-electron-vue-template](https://github.com/DebuNeko233/python-electron-vue-template) 的工程布局和生命周期机制改造，**保留 water_detector 的 OpenCV 检测算法、标定数据模型、业务 REST API 和四个 Vue 页面**。

## 项目结构

```
backend/src/water_detector/       现有检测算法、视频/标定、业务 API
backend/src/sample_backend/       模板的启动握手、会话认证、HTTP/WebSocket、父进程监视
src/main/                         Electron 主进程、Python 子进程管理
src/preload/                      sandbox 安全 IPC 白名单
src/renderer/src/                 从原 frontend/src 迁移的 Vue3/Element Plus 业务界面
src/shared/                       TS DesktopBridge 契约
scripts/                          uv 初始化、开发启动、Windows 自包含构建及冒烟测试
backend/tests/                    原算法回归及迁移验证
```

## 开发运行

需要 Node.js 22+，pnpm，Windows/Linux/macOS，以及可安装 uv/Python 的环境。

```bash
corepack enable
pnpm install
pnpm setup
pnpm dev
```

`pnpm dev` 同时启动 Electron 和 Python，本机开发只显示一个 Electron 应用窗口；额外的 Python 控制台默认隐藏。要观察 Python 日志可运行 `pnpm dev:combined`。开发时也可分别执行 `pnpm dev:backend` 和 `pnpm dev:frontend`。

**发行版：** 运行 `pnpm build`（Windows x64），生成 `dist/win-unpacked/WaferWaterDetector.exe`。与模板一样打包独立 CPython、自动选择空闲 localhost 端口、随机 session/control token，通过就绪 JSON 握手验证子进程身份。关闭 Electron 时先请求受鉴权的 `/internal/shutdown`，超时只清理本窗口拥有的 Python 进程；强制关闭 Electron 时 Python 父进程监视器也会退出。没有额外 Python 控制台。发行版不依赖预装 Python。

## 原有功能保持不变

- 首页「实时监测」：监测启停、S_water、环带红色异常叠加及曲线。
- 「标定与调试」：P1/P2/P3 两点圆心和半径标定、任意视频位置拖动及逐帧预览。
- 「检测记录」：CSV/MP4 结果路径。
- 「设备与系统」：本地文件、USB、RTSP 源和单/自动工位模式。
- 真实 Python 检测代码位于 `backend/src/water_detector/detector/water.py`，迁移没有重写算法。

预览现在通过 `GET /api/monitor/frame` 经 Electron Preload/IPC 取得 JPEG，避免随机端口+会话令牌下直接以 <img> 访问本地服务失败。Web 版调试也可由开发后端直连 localhost:8765。

## 质量检查

```bash
pnpm test:backend
pnpm test:scripts
pnpm typecheck
pnpm exec electron-vite build
```

GitHub Actions 在 Windows 和 Linux 检测 Python 与 TS 代码；Windows 另外执行自包含 Python/真实 Electron 冒烟测试和异常退出清理检查。

## 注意事项

工位识别是实验功能，输出 UNKNOWN/PAUSED 时不能视为 OK。水流 S_water 是持续视觉扰动，不是转速或真实流速测量。真实视频中应继续校验标定偏差、喷头移动、短暂失焦和误报/漏报。本次迁移改变桌面骨架，不主张改变检测阈值。
