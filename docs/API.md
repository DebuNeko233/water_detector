# HTTP API (v0.1)

Backend listens only on `127.0.0.1:8765`.

| Method | Endpoint | Usage |
|---|---|---|
| GET | `/api/health` | Python readiness and service identity |
| GET | `/api/config/active` | Active detection and source config |
| POST | `/api/config/commit` | Validate and atomically commit a new Active version (monitor must be stopped) |
| PUT | `/api/calibration/{stage}` | Save a P1/P2/P3 calibration (monitor must be stopped) |
| POST | `/api/camera/metadata` | Local file duration, FPS, frame count, last index, resolution |
| POST | `/api/camera/frame` | Seek a local MP4 and get JPEG + dimensions |
| POST | `/api/debug/preview` | Recompute an isolated, parameter-specific pre-roll and return history+JPEG |
| POST | `/api/monitor/start` | Start worker and disk capture |
| POST | `/api/monitor/stop` | Request graceful stop |
| GET | `/api/monitor/status` | Current monitoring snapshot |
| GET | `/api/monitor/preview.mjpg` | MJPEG overlay preview |
| WS | `/ws/monitor` | Live status + S_water history array |
| GET | `/api/records` | Completed/ongoing result locations |

Frames and masks are never serialized as nested JSON arrays. Commands use validated Pydantic schemas; the browser does **not** calculate inspection status.

Electron process owner only: `POST /api/system/process/shutdown` uses an ephemeral `X-Wafer-Control-Token` header, accepts actual loopback peers only, returns HTTP 202. It is not a user-facing API.

The debug video timeline requests `/api/camera/metadata` when a local file is selected, uses integer **frame indices 0..last_frame_index** for seeking, and updates `S_water` through `/api/debug/preview` only after the slider is released. UI slider motion does not mutate calibration or Active configuration. The FPS-based time is a display approximation for variable-frame-rate files.
