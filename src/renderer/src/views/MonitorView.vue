<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useSystemStore } from '../stores/system'
import { request, startMonitor, stopMonitor } from '../api/client'
import SWaterChart from '../components/SWaterChart.vue'

const emit = defineEmits<{ (event: 'open-settings'): void }>()
const store = useSystemStore()
const busy = ref(false)
const previewImage = ref('')
let frameTimer: ReturnType<typeof setInterval> | null = null
let loadingFrame = false

const running = computed(() => ['RUNNING', 'STARTING'].includes(store.monitor.state))
const source = computed(() => store.config?.source)
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
  ({ OK: '#19855a', NG: '#d14a42', PAUSED: '#b37d2e', WARMUP: '#b37d2e' } as Record<string, string>)[store.monitor.status] || '#76879c'
)

async function fetchPreview() {
  if (loadingFrame || !store.connected) return
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
    else await startMonitor()
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
        <img v-if="previewImage" :src="previewImage" alt="Python 后端标注的实时晶圆检测画面" />
        <div v-else class="muted" style="padding:24px;text-align:center">
          {{ sourceReady ? '等待第一帧监测画面' : sourceHint }}
        </div>
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
        <div class="large-score" :style="{color:statusColor}">{{ store.monitor.status }}</div>
        <div class="stat-grid">
          <div class="cell"><span class="muted">S_water</span><strong>{{ (store.monitor.s_water || 0).toFixed(4) }}</strong></div>
          <div class="cell"><span class="muted">阈值</span><strong>{{ threshold.toFixed(4) }}</strong></div>
          <div class="cell"><span class="muted">当前工位</span><strong>{{ store.monitor.stage || '—' }}</strong></div>
          <div class="cell"><span class="muted">异常持续</span><strong>{{ (store.monitor.abnormal_run_s || 0).toFixed(2) }} s</strong></div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-title">S_water history <span class="muted">（Python 计算）</span></div>
        <SWaterChart :values="store.monitor.history || []" :threshold="threshold" />
      </section>
      <section class="panel">
        <div class="panel-title">运行信息</div>
        <div class="stat-grid">
          <div class="cell"><span class="muted">任务</span><strong>{{ store.monitor.state }}</strong></div>
          <div class="cell"><span class="muted">处理帧</span><strong>{{ store.monitor.processed_frames || 0 }}</strong></div>
        </div>
        <p class="hint" v-if="store.monitor.reason">{{ store.monitor.reason }}</p>
        <p class="hint" v-else>标注视频与 CSV 保存在 Python 用户数据目录下。</p>
      </section>
    </aside>
  </div>
</template>
