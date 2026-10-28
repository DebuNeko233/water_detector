<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { request, startMonitor, stopMonitor } from '../api/client'
import { useSystemStore } from '../stores/system'
import SWaterChart from '../components/SWaterChart.vue'

const emit = defineEmits<{ (event: 'open-settings'): void; (event: 'open-debug'): void }>()
const store = useSystemStore()

interface VideoMetadata {
  frame_count: number
  last_frame_index: number
  fps: number
  duration_seconds: number
  width: number
  height: number
}
interface PlaybackState {
  state: 'IDLE' | 'SEEKING' | 'PLAYING' | 'PAUSED' | 'COMPLETED' | 'ERROR'
  frame_index: number
  fps: number
  status: string
  s_water: number | null
  history: number[]
  reason: string
  revision: number
  jpeg_base64: string | null
}

const busy = ref(false)
const previewImage = ref('')
const videoMeta = ref<VideoMetadata | null>(null)
const metaBusy = ref(false)
const metaError = ref('')
const manualPreview = ref(false)
const fileImage = ref('')
const fileStatus = ref('尚未测试')
const fileScore = ref<number | null>(null)
const fileHistory = ref<number[]>([])
const fileReason = ref('')
const playbackState = ref<PlaybackState['state']>('IDLE')
const playbackBusy = ref(false)
const seekFrame = ref(0)
const isScrubbing = ref(false)
const testStage = ref<'P1' | 'P2' | 'P3'>('P1')
let frameTimer: ReturnType<typeof setInterval> | null = null
let playbackTimer: ReturnType<typeof setInterval> | null = null
let loadingFrame = false
let loadingPlayback = false
let metadataVersion = 0
let playbackRevision = -1
let mounted = false

const running = computed(() => ['RUNNING', 'STARTING'].includes(store.monitor.state))
const source = computed(() => store.config?.source)
const fileSource = computed(() =>
  store.connected && source.value?.kind === 'file' ? source.value.address.trim() : '',
)
const displayImage = computed(() => fileSource.value && manualPreview.value ? fileImage.value : previewImage.value)
const displayStatus = computed(() => fileSource.value && manualPreview.value ? fileStatus.value : store.monitor.status)
const displayScore = computed(() => fileSource.value && manualPreview.value ? fileScore.value : store.monitor.s_water)
const displayHistory = computed(() => fileSource.value && manualPreview.value ? fileHistory.value : store.monitor.history)
const displayStage = computed(() => fileSource.value && manualPreview.value ? testStage.value : (store.monitor.stage || '—'))
const threshold = computed(() => store.config?.detector.s_water_threshold ?? 0.010)
const statusColor = computed(() =>
  ({ OK: '#19855a', NG: '#d14a42', PAUSED: '#b37d2e', WARMUP: '#b37d2e' } as Record<string, string>)[displayStatus.value] || '#76879c',
)
const filePlaying = computed(() => playbackState.value === 'PLAYING' || playbackState.value === 'SEEKING')
const canPlayFile = computed(() => !!fileSource.value && !!videoMeta.value)
const sourceReady = computed(() => {
  if (!store.connected || !source.value) return false
  switch (source.value.kind) {
    case 'file':
    case 'rtsp':
      return source.value.address.trim().length > 0
    case 'usb':
      return Number.isInteger(source.value.device_index) && source.value.device_index >= 0
    default:
      return false
  }
})
const sourceHint = computed(() => {
  if (!store.connected) return '正在等待 Python 后端连接'
  if (!source.value || ((source.value.kind === 'file' || source.value.kind === 'rtsp') && !source.value.address.trim())) {
    return '尚未配置视频源，请先前往「设备与系统」选择视频并保存配置'
  }
  if (source.value.kind === 'industrial') return '工业相机接口尚未接入，当前不可启动监测'
  return '视频源已配置'
})

function clampFrame(value: number): number {
  return Math.max(0, Math.min(videoMeta.value?.last_frame_index ?? 0, Number.isFinite(value) ? Math.round(value) : 0))
}
function formatTime(value: number): string {
  const seconds = Math.max(0, Number.isFinite(value) ? value : 0)
  const hours = Math.floor(seconds / 3600)
  const mins = String(Math.floor(seconds / 60) % 60).padStart(2, '0')
  const secs = String(Math.floor(seconds % 60)).padStart(2, '0')
  return (hours ? hours + ':' : '') + mins + ':' + secs + '.' + Math.floor((seconds % 1) * 10)
}
function sliderTooltip(value: number): string {
  return formatTime(value / (videoMeta.value?.fps || 30)) + ' · 第 ' + value + ' 帧'
}
const timeLabel = computed(() => formatTime(seekFrame.value / (videoMeta.value?.fps || 30)))
const totalLabel = computed(() => formatTime(videoMeta.value?.duration_seconds ?? 0))

/** A single Python worker owns VideoCapture + the water detector. Each seek
 * reconstructs the detector's pre-roll before continuing sequential playback.
 * This is separate from the recording MonitorTask. */
function playbackRequest(frameIndex: number) {
  const config = store.config!
  return {
    source: { ...config.source },
    frame_index: clampFrame(frameIndex),
    stage: testStage.value,
    calibration: config.calibrations[testStage.value] ? { ...config.calibrations[testStage.value] } : null,
    detector: { ...config.detector },
  }
}
async function startFilePlayback(frameIndex: number): Promise<void> {
  if (!canPlayFile.value || !store.config) return
  playbackBusy.value = true
  manualPreview.value = true
  fileReason.value = ''
  playbackRevision = -1
  try {
    const data = await request<PlaybackState>('/api/playback/start', 'POST', playbackRequest(frameIndex))
    playbackState.value = data.state
    await pollPlayback()
  } catch (error) {
    fileStatus.value = '播放失败'
    fileReason.value = String(error)
    playbackState.value = 'ERROR'
  } finally {
    playbackBusy.value = false
  }
}
async function loadFileMetadata(): Promise<void> {
  const version = ++metadataVersion
  videoMeta.value = null
  metaError.value = ''
  fileImage.value = ''
  fileStatus.value = '尚未测试'
  if (!fileSource.value) {
    manualPreview.value = false
    return
  }
  manualPreview.value = true
  metaBusy.value = true
  try {
    const config = { ...store.config!.source }
    const meta = await request<VideoMetadata>('/api/camera/metadata', 'POST', { source: config })
    if (!mounted || version !== metadataVersion) return
    videoMeta.value = meta
    seekFrame.value = 0
    await startFilePlayback(0)
  } catch (error) {
    if (mounted && version === metadataVersion) metaError.value = '读取视频信息失败：' + String(error)
  } finally {
    if (version === metadataVersion) metaBusy.value = false
  }
}
async function pollPlayback(): Promise<void> {
  if (loadingPlayback || !mounted || !canPlayFile.value) return
  loadingPlayback = true
  try {
    const response = await request<PlaybackState>('/api/playback/state?after_revision=' + playbackRevision)
    if (!mounted || !fileSource.value) return
    playbackState.value = response.state
    fileStatus.value = response.status
    fileScore.value = response.s_water
    fileHistory.value = response.history
    fileReason.value = response.reason
    if (response.jpeg_base64) {
      fileImage.value = 'data:image/jpeg;base64,' + response.jpeg_base64
      playbackRevision = response.revision
    }
    // Never let the playback timer fight the user's finger on the slider.
    if (!isScrubbing.value && response.frame_index >= 0) {
      seekFrame.value = clampFrame(response.frame_index)
    }
  } catch (error) {
    // Error is visible but do not flood console or repeatedly pop notifications.
    if (mounted) fileReason.value = '视频播放连接异常：' + String(error)
  } finally {
    loadingPlayback = false
  }
}

/** Seeking ALWAYS resumes playback, even if the user had paused previously. */
async function seekTo(value: number | number[]): Promise<void> {
  isScrubbing.value = false
  if (!canPlayFile.value) return
  const target = clampFrame(Array.isArray(value) ? value[0] : value)
  seekFrame.value = target
  manualPreview.value = true
  playbackBusy.value = true
  try {
    // The seek request resets the Python detector's temporal history and
    // automatically resumes from the requested frame.
    const state = await request<PlaybackState>('/api/playback/seek', 'POST', { frame_index: target })
    playbackState.value = state.state
  } catch {
    await startFilePlayback(target)
  } finally {
    playbackBusy.value = false
  }
}
async function stepFrame(delta: number): Promise<void> {
  await seekTo(seekFrame.value + delta)
}
async function toggleFilePlayback(): Promise<void> {
  if (!canPlayFile.value || playbackBusy.value) return
  playbackBusy.value = true
  try {
    if (filePlaying.value) {
      const state = await request<PlaybackState>('/api/playback/pause', 'POST')
      playbackState.value = state.state
    } else if (playbackState.value === 'IDLE' || playbackState.value === 'ERROR') {
      await startFilePlayback(seekFrame.value)
    } else {
      manualPreview.value = true
      const state = await request<PlaybackState>('/api/playback/resume', 'POST')
      playbackState.value = state.state
    }
  } catch (error) {
    fileReason.value = String(error)
  } finally {
    playbackBusy.value = false
  }
}
async function onStageChange(): Promise<void> {
  // Stage calibration changed; recreate the isolated session at current frame.
  if (canPlayFile.value) await startFilePlayback(seekFrame.value)
}
async function showLive(): Promise<void> {
  manualPreview.value = false
  if (canPlayFile.value) {
    try {
      const state = await request<PlaybackState>('/api/playback/pause', 'POST')
      playbackState.value = state.state
    } catch { /* monitoring is independent from file playback */ }
  }
}
async function fetchPreview(): Promise<void> {
  if (loadingFrame || !store.connected || (fileSource.value && manualPreview.value)) return
  loadingFrame = true
  try {
    const data = await request<{ jpeg_base64: string | null }>('/api/monitor/frame')
    if (data.jpeg_base64) previewImage.value = 'data:image/jpeg;base64,' + data.jpeg_base64
  } catch { /* monitoring may not have started yet */ }
  finally { loadingFrame = false }
}
async function toggle(): Promise<void> {
  if (busy.value) return
  if (!running.value && !sourceReady.value) {
    ElMessage.warning(sourceHint.value)
    return
  }
  busy.value = true
  try {
    if (running.value) await stopMonitor()
    else {
      await startMonitor()
      await showLive()
    }
  } catch (error) {
    const msg = String(error)
    ElMessage.error(msg.includes('Configure a video source first') ? '请先配置视频源并保存' : msg)
  } finally {
    busy.value = false
  }
}
watch(fileSource, () => { if (mounted) void loadFileMetadata() })
watch(() => store.config?.selected_stage, stage => {
  if (stage) testStage.value = stage
}, { immediate: true })

onMounted(() => {
  mounted = true
  void loadFileMetadata()
  frameTimer = setInterval(() => { void fetchPreview() }, 250)
  playbackTimer = setInterval(() => { void pollPlayback() }, 80)
})
onUnmounted(() => {
  mounted = false
  ++metadataVersion
  if (frameTimer) clearInterval(frameTimer)
  if (playbackTimer) clearInterval(playbackTimer)
  if (fileSource.value) void request('/api/playback/stop', 'POST').catch(() => {})
})
</script>

<template>
  <div class="monitor-grid">
    <section class="panel video-panel" style="padding:10px">
      <div class="video-area">
        <img v-if="displayImage" :src="displayImage" alt="晶圆水流检测：本地视频按帧连续播放" />
        <div v-else class="muted" style="padding:24px;text-align:center">
          {{ fileSource && manualPreview ? (metaBusy ? '正在读取视频…' : metaError || '等待视频画面') : (sourceReady ? '等待实时监测画面' : sourceHint) }}
        </div>
        <span v-if="fileSource && manualPreview" class="video-mode-badge">独立视频测试 · {{ playbackState }}</span>
      </div>
      <div v-if="fileSource" class="monitor-timeline">
        <div class="timeline-top">
          <div>
            <strong>视频播放与算法测试</strong>
            <span class="muted"> · {{ videoMeta ? videoMeta.fps.toFixed(2) + ' FPS / ' + videoMeta.frame_count + ' 帧' : '读取中' }}</span>
          </div>
          <div class="timeline-time">{{ timeLabel }} <span>/ {{ totalLabel }}</span></div>
        </div>
        <el-slider v-model="seekFrame" :min="0" :max="videoMeta?.last_frame_index ?? 0" :step="1"
          :disabled="!videoMeta || metaBusy" aria-label="视频进度，松开后从当前位置继续播放"
          :format-tooltip="sliderTooltip" @input="isScrubbing=true" @change="seekTo" />
        <div class="monitor-timeline-actions">
          <el-button size="small" :disabled="!canPlayFile || playbackBusy" @click="toggleFilePlayback">
            {{ filePlaying ? 'Ⅱ 暂停' : '▶ 播放' }}
          </el-button>
          <el-button size="small" :disabled="!canPlayFile" @click="seekTo(0)">开头</el-button>
          <el-button size="small" :disabled="!canPlayFile" @click="stepFrame(-1)">◀ 上一帧</el-button>
          <el-button size="small" :disabled="!canPlayFile" @click="stepFrame(1)">下一帧 ▶</el-button>
          <el-button size="small" :disabled="!canPlayFile" @click="seekTo(videoMeta?.last_frame_index ?? 0)">结尾</el-button>
          <el-select v-model="testStage" size="small" class="monitor-stage-select" @change="onStageChange">
            <el-option label="P1" value="P1" /><el-option label="P2" value="P2" /><el-option label="P3" value="P3" />
          </el-select>
          <el-button v-if="running && manualPreview" size="small" @click="showLive">返回实时监测</el-button>
          <span v-if="playbackBusy" class="muted">正在重建检测历史…</span>
        </div>
        <div v-if="metaError || fileReason" class="monitor-file-hint">
          {{ metaError || fileReason }}
          <el-button v-if="fileReason==='CALIBRATION_MISSING' || fileReason==='RESOLUTION_MISMATCH'" size="small"
            type="primary" link @click="emit('open-debug')">去标定</el-button>
        </div>
        <p v-else class="muted monitor-file-hint">拖动松开后自动继续播放，水流历史从目标位置重建；不影响正式检测任务和记录。</p>
      </div>
    </section>
    <aside class="right-rail">
      <section class="panel">
        <div class="panel-title">监测操作</div>
        <div class="button-row">
          <el-button type="primary" size="large" :loading="busy"
            :disabled="!running && !sourceReady" @click="toggle">
            {{ running ? '■ 停止监测' : '▶ 开始监测' }}
          </el-button>
          <el-button v-if="!running && !sourceReady" @click="emit('open-settings')">配置视频源</el-button>
        </div>
        <p class="muted">
          {{ source?.kind || '未设置' }} · {{ source?.address || (source?.kind === 'usb' ? '设备索引 ' + source.device_index : sourceHint) }}
        </p>
      </section>
      <section class="panel">
        <div class="muted">{{ fileSource && manualPreview ? '视频算法测试状态' : '实时检测状态' }}</div>
        <div class="large-score" :style="{color:statusColor}">{{ displayStatus }}</div>
        <div class="stat-grid">
          <div class="cell"><span class="muted">S_water</span><strong>{{ displayScore == null ? '—' : displayScore.toFixed(4) }}</strong></div>
          <div class="cell"><span class="muted">阈值</span><strong>{{ threshold.toFixed(4) }}</strong></div>
          <div class="cell"><span class="muted">当前工位</span><strong>{{ displayStage }}</strong></div>
          <div class="cell"><span class="muted">异常持续</span><strong>{{ fileSource && manualPreview ? '独立测试' : (store.monitor.abnormal_run_s || 0).toFixed(2) + ' s' }}</strong></div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-title">S_water history <span class="muted">（连续逐帧计算）</span></div>
        <SWaterChart :values="displayHistory || []" :threshold="threshold" />
      </section>
      <section class="panel">
        <div class="panel-title">运行信息</div>
        <div class="stat-grid">
          <div class="cell"><span class="muted">正式任务</span><strong>{{ store.monitor.state }}</strong></div>
          <div class="cell"><span class="muted">检测帧数</span><strong>{{ store.monitor.processed_frames || 0 }}</strong></div>
        </div>
        <p class="hint" v-if="fileSource && manualPreview">视频测试为独立 Python 播放会话；正式监测的 CSV/MP4 不受跳转影响。</p>
        <p class="hint" v-else-if="store.monitor.reason">{{ store.monitor.reason }}</p>
      </section>
    </aside>
  </div>
</template>
