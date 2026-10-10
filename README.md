# Water Detector — 晶圆洗片视觉检测系统

基于 **Vue 3 + Electron + Element Plus** 和 **Python FastAPI + OpenCV** 的模块化桌面检测项目。

> 目前为**首个可运行基础版本（v0.1）**，不等于量产版本。已实现核心单工位回归检测及桌面基础链路。三工位自动识别仍为实验性算法，动态喷头 Mask / 工业相机厂商 SDK 尚未投入使用；识别依据不足时返回 UNKNOWN/PAUSED，绝不伪造 OK。

## 快速启动（Windows 10/11）

要求：Python 3.10+、Node.js 20+、pnpm 10。第一次安装依赖：

```powershell
python -m pip install -e "./backend[test]"
cd frontend
pnpm install
```

**现在用一条命令启动 Vue + Electron + Python：**

```powershell
cd frontend
pnpm desktop
```

参照 `sop-platform` 的前后端联动方式，已改为 `vite-plugin-electron` 直接管理开发版 Electron，
不再依赖独立的 dev-desktop 启动器。Electron 自动启动并在退出时优雅关闭它拥有的 Python；
不会关闭你手动运行的后端。

仅调试前端：`cd frontend; pnpm dev:web`。
仅调试后端：`python -m water_detector.main`。

更多信息：[`docs/DESKTOP_LIFECYCLE.md`](docs/DESKTOP_LIFECYCLE.md)。
正式 Windows 一体化安装包仍需另行打包与验收。

## 功能范围

- `camera`：MP4 / USB / RTSP OpenCV 视频输入；工业相机 SDK 的接口保留，**未实现**。
- `detector`：原 `main.py` 环形 ROI、灰度帧差、中位数补偿、像素驻留、`S_water`、扇区与 OK/NG 判定的等价迁移。
- `pipeline`：给每帧选择标定及生成检测结果；默认**单位置**，自动三工位位置识别为保守实验功能，需要 P1/P2/P3 参考图。
- `service`：FastAPI REST、WebSocket 状态、MJPEG 预览、独立调试预览。
- `config`：持久化 Active 参数，草稿参数仅在前端保留并请求后端预览，提交前不影响正式监测。
- `task`：开始/停止监测；生成 `result.csv` 和 `annotated.mp4`，暂未生成异常截图。
- `frontend`：监测页左侧仅视频，所有控制和曲线在右侧；调试页可拖动圆心及半径，并查看后台重算的曲线。
- `调试视频进度条`：选择本地视频后显示总时长、FPS、总帧数和可拖动进度条；松开后向 Python 请求目标帧与该位置的局部时序预览。支持上一帧/下一帧、开头/结尾、输入帧号；不改变正式配置，USB/RTSP 不支持随机拖动。
- `electron`：安全 preload，文件选择，Python 后端进程启动。

## 标定与回归

1. 在设备设置页填写本地视频路径，例如 `C:\\samples\\good.mp4`，选择单工位 P1，保存。
2. 标定与调试页拖动蓝色圆心、绿色半径手柄，保存 P1 标定。
3. 启动监测；所有计算都在后端完成。
4. `good.mp4` 与 `bad.mp4` 可作为局部流程回归样本。项目测试可通过环境变量 `SOURCE_MAIN`、`GOOD_VIDEO`、`BAD_VIDEO` 提供基准文件。

运行后端测试：

```powershell
cd backend
pytest -q
```

## 工程架构

参见 [`docs/architecture.md`](docs/architecture.md)。

## CI 与 Windows 进程管理

见 [`docs/DESKTOP_LIFECYCLE.md`](docs/DESKTOP_LIFECYCLE.md)。CI 工作流位于 `.github/workflows/ci.yml`，推送到 GitHub 后由 Actions 运行。

## 已知限制

- 目前自动工位识别只有单参考图、边缘相关系数与拒绝阈值，尚需从完整视频做真实工位标注和回归测试。
- **动态喷头 Mask 未接入**。为保证和 `main.py` 数值完全一致，不能把一个未经验证的算法悄悄加进核心检测器。
- 画质门控与动态有效面积统计需后续单独开发与验证。
- 目前正式参数仅在停止监测后提交；工业相机需根据 SDK 定制实现。
- 本版本不是合格的工厂设备放行依据。
