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

### 参考截图自动选择 P1/P2/P3 ROI

- 不再进行晶圆材质、圆周边缘、圆盘包含度和几何贴合度分类。自动 ROI 选择采用**当前画面与三组参考截图的相似度对比**。
- 在「标定与调试」选择 P1，暂停视频并从不同时间点采集至少 **5 张 P1 参考截图**；对 P2、P3 重复此操作（至少 15 张），可以继续追加更多照片。页面显示每个工位的数量，允许移除最后添加的截图。
- 点击 **保存为正式参数** 后，参考图库路径连同三个已标定 ROI 一起写入配置；采集截图本身不会立即更改正式监测参数。旧 `active.json` 里的单张参考路径自动迁移成单元素列表，但仍不能满足 5 张要求。
- 截图对比采用同一个检测区域（P1/P2/P3 三个 ROI 的并集）；可选的喷头遮罩排除其移动像素。每个位置与自己的全部截图逐一比对，取最高相似度；只有相似度足够高且第一、第二名可区分时才使用该 ROI，否则 **UNKNOWN/PAUSED**，不能当作 OK。
- 三组参考图允许对应大量重叠的 ROI。三个位置各需 5 张能正常读取、分辨率相符的图片，否则自动模式拒绝判定。
- **显示手动标定层** 默认关闭，可完全隐藏所有可拖动圆环与手柄；**显示算法 ROI** 可以单独关闭边界线，只看水流异常标记。原始水流 S_water 计算算法不受截图选择逻辑影响。
- 参考图应覆盖喷头不同姿态、正常反光和水流变化，避免全部采集同一帧。截图相似度是数值比较，不是分类概率；如果三个工位画面本来极相近，也可能无法区分。

### 动态喷头过滤和全视频自动检测

- 标定与调试的视频播放器使用同一套 `InspectionPipeline` 逐帧选择 P1/P2/P3，计算 `S_water`。**动态喷头过滤**默认开启，可在右侧检测参数里开关，临时修改马上重新计算，不会立即保存到正式配置。
- 喷头过滤使用画面中从上方伸入 ROI 的**细长暗色物体**候选和短时间位置历史，排除喷头本体、短暂移动轨迹引起的活动像素。滤除像素不参加驻留统计，`S_water` 的分母使用剩余有效环形 ROI；过滤过大时返回 `PAUSED`，不会误报正常。
- 开启 **显示喷头过滤区域** 后，画面以青色标出被排除的区域。请核对遮罩是否覆盖喷头而**未覆盖真正的水流异常**；遮罩只是保守的图像特征估计，不代表已经训练好的喷头分割模型。
- 点击 **从头自动测试整段视频**，后端创建独立的离线分析线程，从第 0 帧逐帧计算 P1/P2/P3 识别、`S_water`、OK/NG/UNKNOWN；页面显示处理进度、各工位帧数、NG 帧数、最大 `S_water`、喷头过滤统计。可手动停止，不改变正式监测任务的结果或原视频位置。
- 自动测试读取**点击开始时的调试参数快照**，之后调参不会悄悄修改正在进行的全视频测试；需要按新参数复测时重新启动。
- 实际设备中，水流、喷头、金属反光有时会相互遮挡；这套过滤是实验性启发式算法。必须用 `9月30日-2.mp4` 等真实视频核查误检和漏检，再用于生产验收。

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
