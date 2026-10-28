<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useSystemStore } from '../stores/system'
import { request, startMonitor, stopMonitor } from '../api/client'
import SWaterChart from '../components/SWaterChart.vue'

const emit = defineEmits<{ (event: 'open-settings'): void; (event: 'open-debug'): void }>()
const store = useSystemStore()
const busy = ref(false)
const previewImage = ref('')

/** Independent video inspection: never seeks or restarts an active recording task. */
interface VideoMetadata {
  frame_count: number
  last_frame_index: number
  fps: number
  duration_seconds: number
  width: number
  height: number
}
const videoMeta = ref<VideoMetadata | null>(null)
const metaBusy = ref(false)
const metaError = ref('')
const manualPreview = ref(false)
const fileImage = ref('')
const fileStatus = ref('尚未测试')
const fileScore = ref<number | null>(null)
const fileHistory = ref<number[]>([])
const fileReason = ref('')
const testBusy = ref(false)
const seekFrame = ref(0)
const selectedFrame = ref(0)
const testStage = ref<'P1' | 'P2' | 'P3'>('P1')
let metadataVersion = 0
let previewVersion = 0
let workerActive = false

function clampFrame(value: number): number {
  return Math.max(0, Math.min(videoMeta.value?.last_frame_index ?? 0,
    Number.isFinite(value) ? Math.round(value) : 0))
}
function formatTime(value: number): string {
  const seconds = Math.max(0, Number.isFinite(value) ? value : 0)
  const mins = String(Math.floor(seconds / 60) % 60).padStart(2, '0')
  const secs = String(Math.floor(seconds % 60)).padStart(2, '0')
  const hours = Math.floor(seconds / 3600)
  return (hours > 0 ? String(hours) + ':' : '') + mins + ':' + secs + '.' + Math.floor((seconds % 1) * 10)
}
let frameTimer: ReturnType<typeof setInterval> | null = null
let loadingFrame = false

const running = computed(() => ['RUNNING', 'STARTING'].includes(store.monitor.state))
const source = computed(() => store.config?.source)

const fileSource = computed(() =>
  store.connected && source.value?.kind === 'file' ? source.value.address.trim() : ''
)
const calibration = computed(() => store.config?.calibrations[testStage.value])
const calibrationMatches = computed(() => !!videoMeta.value && !!calibration.value &&
  calibration.value.reference_width === videoMeta.value.width &&
  calibration.value.reference_height === videoMeta.value.height)
const displayImage = computed(() => fileSource.value && manualPreview.value ? fileImage.value : previewImage.value)
const displayStatus = computed(() => fileSource.value && manualPreview.value ? fileStatus.value : store.monitor.status)
const displayScore = computed(() => fileSource.value && manualPreview.value ? fileScore.value : store.monitor.s_water)
const displayHistory = computed(() => fileSource.value && manualPreview.value ? fileHistory.value : store.monitor.history)
const displayStage = computed(() => fileSource.value && manualPreview.value ? testStage.value : (store.monitor.stage || '—'))
const timeLabel = computed(() => formatTime(seekFrame.value / (videoMeta.value?.fps || 30)))
const totalLabel = computed(() => formatTime(videoMeta.value?.duration_seconds ?? 0))

async function loadFileMetadata(): Promise<void> {
  const version = ++metadataVersion
  ++previewVersion
  videoMeta.value = null
  fileImage.value = ''
  fileScore.value = null
  fileHistory.value = []
  fileStatus.value = '尚未测试'
  metaError.value = ''
  if (!fileSource.value) {
    manualPreview.value = false
    return
  }
  manualPreview.value = true
  metaBusy.value = true
  try {
    const sourceConfig = { ...store.config!.source }
    const meta = await request<VideoMetadata>('/api/camera/metadata', 'POST', { source: sourceConfig })
    if (version !== metadataVersion) return
    videoMeta.value = meta
    seekFrame.value = 0
    selectedFrame.value = 0
    await testAtFrame(0)
  } catch (error) {
    if (version === metadataVersion) metaError.value = '读取视频信息失败：' + String(error)
  } finally {
    if (version === metadataVersion) metaBusy.value = false
  }
}

// Only one in-flight preview chain; multiple rapid seeks coalesce to the latest
// target frame. A stale reply must never replace the newly requested picture.
async function testAtFrame(value: number): Promise<void> {
  if (!videoMeta.value || !fileSource.value) return
  const target = clampFrame(value)
  seekFrame.value = target
  selectedFrame.value = target
  manualPreview.value = true
  ++previewVersion
  if (workerActive) return
  workerActive = true
  testBusy.value = true
  try {
    while (videoMeta.value && fileSource.value) {
      const version = previewVersion
      const index = selectedFrame.value
      const src = { ...store.config!.source }
      const chosen = testStage.value
      const cal = store.config?.calibrations[chosen]
      const detector = store.config?.detector
      fileReason.value = ''
      try {
        // Render the requested frame even before calibration is completed.
        const raw = await request<{ jpeg_base64: string }>('/api/camera/frame', 'POST',
          { source: src, frame_index: index })
        if (version !== previewVersion) continue
        fileImage.value = 'data:image/jpeg;base64,' + raw.jpeg_base64
        if (!cal) {
          fileScore.value = null
          fileHistory.value = []
          fileStatus.value = '未标定'
          fileReason.value = '请在「标定与调试」为 ' + chosen + ' 设置晶圆 ROI'
        } else if (!calibrationMatches.value) {
          fileScore.value = null
          fileHistory.value = []
          fileStatus.value = '标定不匹配'
          fileReason.value = '标定分辨率与当前视频不一致'
        } else if (detector) {
          fileStatus.value = '计算中…'
          const response = await request<{
            status: string
            s_water: number
            samples: { time_s: number; s_water: number }[]
            jpeg_base64: string
          }>('/api/debug/preview', 'POST', {
            source: src, stage: chosen, calibration: { ...cal },
            detector: { ...detector }, frame_index: index,
          })
          if (version !== previewVersion) continue
          fileImage.value = 'data:image/jpeg;base64,' + response.jpeg_base64
          fileStatus.value = response.status
          fileScore.value = response.s_water
          fileHistory.value = response.samples.map(item => item.s_water)
        }
      } catch (error) {
        if (version === previewVersion) {
          fileStatus.value = '预览失败'
          fileReason.value = String(error)
        }
      }
      if (version === previewVersion) break
    }
  } finally {
    workerActive = false
    testBusy.value = false
  }
}
function seekTo(value: number | number[]): void {
  void testAtFrame(Array.isArray(value) ? value[0] : value)
}
function stepFrame(delta: number): void {
  void testAtFrame(selectedFrame.value + delta)
}
function sliderTooltip(value: number): string {
  return formatTime(value / (videoMeta.value?.fps || 30)) + ' · 第 ' + value + ' 帧'
}
function showLive(): void {
  manualPreview.value = false
}
watch(fileSource, () => { void loadFileMetadata() }, { immediate: true })
watch(() => store.config?.selected_stage, value => {
  if (value) testStage.value = value
}, { immediate: true })
function onStageChange(): void {
  if (videoMeta.value) void testAtFrame(selectedFrame.value)
}
// While showing the live processed file stream, the seek thumb tracks
// its actual frame number. Dragging explicitly switches to isolated testing.
watch(() => store.monitor.frame_id, frameId => {
  if (fileSource.value && !manualPreview.value && typeof frameId === 'number') {
    seekFrame.value = clampFrame(frameId)
  }
})
const sourceReady = computed(() => {
  if (!store.connected || !source.value) return false
  switch (source.value.kind) {
    case 'file':
    case 'rtsp':
      return source.value.address.trim().length > 0
    case 'usb':
      return Number.isInteger(source.value.device_index) && source.value.device_index >= 0
    case 'industrial':
      return false // No industrial SDK adapter has been implemented yet.
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
const threshold = computed(() => store.config?.detector.s_water_threshold ?? 0.010)
const statusColor = computed(() =>
  ({ OK: '#19855a', NG: '#d14a42', PAUSED: '#b37d2e', WARMUP: '#b37d2e' } as Record<string, string>)[displayStatus.value] || '#76879c'
)

async function fetchPreview() {
  if (loadingFrame || !store.connected || (fileSource.value && manualPreview.value)) return
  loadingFrame = true
  try {
    const data = await request<{ jpeg_base64: string | null }>('/api/monitor/frame')
    if (data.jpeg_base64) previewImage.value = 'data:image/jpeg;base64,' + data.jpeg_base64
  } catch {
    // Preview polling is best-effort during startup/shutdown.
  } finally {
    loadingFrame = false
  }
}
onMounted(() => {
  void fetchPreview()
  frameTimer = setInterval(() => { void fetchPreview() }, 250)
})
onUnmounted(() => {
  if (frameTimer) clearInterval(frameTimer)
  ++metadataVersion
  ++previewVersion
})

async function toggle() {
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
      manualPreview.value = false
    }
  } catch (error) {
    // Another window/action could have invalidated the saved source while the
    // request was in flight. Show the operator what to fix, not a stack trace.
    const message = String(error)
    if (message.includes('Configure a video source first')) {
      ElMessage.warning('尚未配置视频源，请前往「设备与系统」选择视频并保存')
    } else {
      ElMessage.error(message)
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="monitor-grid">
    <section class="panel video-panel" style="padding:10px">
      <div class="video-area">
        <img v-if="displayImage" :src="displayImage" alt="晶圆检测画面，视频测试可按帧跳转" />
        <div v-else class="muted" style="padding:24px;text-align:center">
          {{ fileSource && manualPreview ? (metaBusy ? '正在读取视频…' : metaError || '等待视频画面') : (sourceReady ? '等待第一帧监测画面' : sourceHint) }}
        </div>
        <span v-if="fileSource && manualPreview" class="video-mode-badge">独立算法测试 · 不改变正式监测任务</span>
      </div>
      <div v-if="fileSource" class="monitor-timeline">
        <div class="timeline-top">
          <div><strong>视频进度</strong><span class="muted"> · {{ videoMeta ? videoMeta.fps.toFixed(2) + ' FPS / ' + videoMeta.frame_count + ' 帧' : '读取中' }}</span></div>
          <div class="timeline-time">{{ timeLabel }} <span>/ {{ totalLabel }}</span></div>
        </div>
        <el-slider v-model="seekFrame" :min="0" :max="videoMeta?.last_frame_index ?? 0" :step="1"
          :disabled="!videoMeta || metaBusy" aria-label="本地视频进度条，拖动后跳转到目标帧"
          :format-tooltip="sliderTooltip"
          @change="seekTo" />
        <div class="monitor-timeline-actions">
          <el-button size="small" :disabled="!videoMeta" @click="seekTo(0)">开头</el-button>
          <el-button size="small" :disabled="!videoMeta" @click="stepFrame(-1)">◀ 上一帧</el-button>
          <el-button size="small" :disabled="!videoMeta" @click="stepFrame(1)">下一帧 ▶</el-button>
          <el-button size="small" :disabled="!videoMeta" @click="seekTo(videoMeta?.last_frame_index ?? 0)">结尾</el-button>
          <el-select v-model="testStage" size="small" class="monitor-stage-select" @change="onStageChange">
            <el-option label="P1" value="P1" /><el-option label="P2" value="P2" /><el-option label="P3" value="P3" />
          </el-select>
          <el-button v-if="running && manualPreview" size="small" @click="showLive">返回实时监测</el-button>
          <span v-if="testBusy" class="muted">正在计算目标帧的 S_water…</span>
        </div>
        <div v-if="metaError || fileReason" class="monitor-file-hint">
          {{ metaError || fileReason }}
          <el-button v-if="fileStatus==='未标定' || fileStatus==='标定不匹配'" size="small" link
            type="primary" @click="emit('open-debug')">去标定</el-button>
        </div>
        <p v-else class="muted monitor-file-hint">拖动松开后跳转到任意帧；Python 会独立重算该位置的水流历史，不修改正在运行的检测记录。</p>
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
        <el-alert v-if="!running && !sourceReady" :title="sourceHint" type="warning"
          :closable="false" show-icon style="margin-top:10px" />
      </section>
      <section class="panel">
        <div class="muted">实时检测状态</div>
        <div class="large-score" :style="{color:statusColor}">{{ displayStatus }}</div>
        <div class="stat-grid">
          <div class="cell"><span class="muted">S_water</span><strong>{{ displayScore === null || displayScore === undefined ? '—' : displayScore.toFixed(4) }}</strong></div>
          <div class="cell"><span class="muted">阈值</span><strong>{{ threshold.toFixed(4) }}</strong></div>
          <div class="cell"><span class="muted">当前工位</span><strong>{{ displayStage }}</strong></div>
          <div class="cell"><span class="muted">异常持续</span><strong>{{ fileSource && manualPreview ? '独立预览' : (store.monitor.abnormal_run_s || 0).toFixed(2) + ' s' }}</strong></div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-title">S_water history <span class="muted">（{{ fileSource && manualPreview ? '目标帧局部历史' : '实时监测' }}）</span></div>
        <SWaterChart :values="displayHistory || []" :threshold="threshold" />
      </section>
      <section class="panel">
        <div class="panel-title">运行信息</div>
        <div class="stat-grid">
          <div class="cell"><span class="muted">任务</span><strong>{{ store.monitor.state }}</strong></div>
          <div class="cell"><span class="muted">处理帧</span><strong>{{ store.monitor.processed_frames || 0 }}</strong></div>
        </div>
        <p class="hint" v-if="fileSource && manualPreview">当前为独立视频算法测试，正式监测任务不受进度跳转影响。</p>
        <p class="hint" v-else-if="store.monitor.reason">{{ store.monitor.reason }}</p>
        <p class="hint" v-else>标注视频与 CSV 保存在 Python 用户数据目录下。</p>
      </section>
    </aside>
  </div>
</template>
