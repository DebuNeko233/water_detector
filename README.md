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

`pnpm dev` **由 Electron 主进程自动启动并管理 Python**，启动、关闭时与窗口同步；不再通过两个独立终端同时争用固定的 8765 端口。第一次运行会自动安装缺失的 backend/.venv。默认无额外 Python 控制台；`pnpm dev:combined` 将 Python 调试日志输出到现有终端。

独立调试：`pnpm dev:backend` 单独启动 Python（端口 8765）；在另一终端运行 `pnpm dev:ui-only`，只启动 Electron 并明确连接这个已存在的后端，关闭前端不会误杀独立的 Python。`pnpm dev:frontend` 会单独启动 Electron，但 Electron **仍自动启动属于自己的 Python**。如遇 `Python 开发环境未安装`，先执行 `pnpm setup`。

**发行版：** 运行 `pnpm build`（Windows x64），生成 `dist/win-unpacked/WaferWaterDetector.exe`。与模板一样打包独立 CPython、自动选择空闲 localhost 端口、随机 session/control token，通过就绪 JSON 握手验证子进程身份。关闭 Electron 时先请求受鉴权的 `/internal/shutdown`，超时只清理本窗口拥有的 Python 进程；强制关闭 Electron 时 Python 父进程监视器也会退出。没有额外 Python 控制台。发行版不依赖预装 Python。

## 原有功能保持不变

- 首页「实时监测」：监测启停、S_water、环带红色异常叠加及曲线。
- 「标定与调试」：P1/P2/P3 两点圆心和半径标定、任意视频位置拖动及逐帧预览。
- 「检测记录」：CSV/MP4 结果路径。
- 「设备与系统」：本地文件、USB、RTSP 源和单/自动工位模式。
- 真实 Python 检测代码位于 `backend/src/water_detector/detector/water.py`，迁移没有重写算法。

预览现在通过 `GET /api/monitor/frame` 经 Electron Preload/IPC 取得 JPEG，避免随机端口+会话令牌下直接以 <img> 访问本地服务失败。Web 版调试也可由开发后端直连 localhost:8765。

## 标定与调试：本地视频播放及热调参数

1. 打开 **标定与调试**，点击 **选择视频** 打开本地 MP4/AVI；调试页自动开始播放。**实时监测** 页面仅显示正式检测，不提供跳转进度条。
2. 在视频下方点击 **▶ 播放 / Ⅱ 暂停**，或拖动进度条到任意帧并松开，都会从目标帧继续播放。也可以逐帧、回到开头或结尾。
3. 选择 P1/P2/P3 工位，用蓝色圆心手柄调整 ROI 中心、用绿色半径手柄调整检测半径。**显示手动标定层** 开关可隐藏圆环和操作点，方便只观察视频、ROI 环带和异常叠加。
4. 修改右侧的帧差阈值、S_water 阈值、驻留窗口等参数，会自动防抖并在当前帧附近重建 Python 检测历史，更新标注画面和 S_water 曲线。视频暂停时调节参数不会自动恢复播放。
5. 以上改动均为**临时测试参数**，不会影响正在运行的正式监测任务及 CSV/MP4 记录；确认后点击 **保存为正式参数** 才写入持久化配置。
6. 标定参考尺寸与视频分辨率不一致时，可以使用 **按当前视频尺寸缩放 ROI 后微调**，再保存。

注意：跳转与改参数都会重建短时间的帧差驻留历史；刚重建时可能显示 WARMUP，这是正常预热，不应直接判定为 OK。

### 纯算法画面与 P1/P2/P3 自动 ROI 验证

- **显示手动标定层**：默认关闭。关闭后连同圆心、半径手柄、可交互内外圆环一起隐藏；只有主动开启才可拖动标定。
- **显示算法 ROI**：独立开关。开启显示 Python 实际应用的环带与扇区边界（会跟随自动工位切换），关闭后仅保留检测出的异常红色像素，更容易检查水流检测效果。这不影响 WaterDetector 的 ROI 计算。
- **工位与 ROI 切换验证**：使用 P1/P2/P3 三组已经标定好的圆心和半径，直接比较**哪一个 ROI 当前框住晶圆**；三个圆允许大量重叠（实际 `active.json` 中主要是上下位置偏移），仅当三个 ROI 完全相同、无法区分时才拒绝。无需额外参考照片。
- Python 逐帧分别计算三个预设 ROI 的 **晶圆表面包含度（圆内外亮度/材质差异）**、**晶圆圆形边缘与标定圆心/半径的贴合度**和局部纹理证据；优先选择能够框住晶圆的那个 ROI。如果喷淋遮挡了部分圆周，则用强且明显优于另外两个 ROI 的表面包含度保守地判断。单独出现的空心金属圆环不足以证实晶圆。
- 页面会显示实际使用工位、检测到的晶圆中心、实际 ROI 坐标，以及 P1/P2/P3 的**边缘、材质、综合得分**。这些分数是视觉证据，**不是概率、不是经过训练的材料分类器**。
- 没有足够的圆形边缘或材料内外差异、三个位置配置重叠、画面分辨率与标定不同，或多个位置得分接近时，**UNKNOWN/PAUSED，不做 OK 判断**。喷头遮挡、失焦、反光、设备本身固定的金属圆形结构也可能干扰识别；自动模式仍属实验功能。
- 位置匹配不依赖保存的参考图片；原有旧配置中的 `position_references` 字段仅为兼容性保留，现已不参与自动识别。


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
