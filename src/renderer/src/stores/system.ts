import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getConfig, request, type AppConfig, type MonitorState } from '../api/client'

/** Renderer doesn't know the backend port or credentials; all requests use preload IPC. */
export const useSystemStore = defineStore('system', () => {
  const config = ref<AppConfig | null>(null)
  const monitor = ref<MonitorState>({ state: 'IDLE', status: 'IDLE', history: [] })
  const connected = ref(false)
  let timer: ReturnType<typeof setInterval> | null = null
  let polling = false

  async function loadConfig() {
    config.value = await getConfig()
  }
  async function poll() {
    if (polling) return
    polling = true
    try {
      monitor.value = await request<MonitorState>('/api/monitor/status')
      connected.value = true
      // Python may become ready AFTER App.vue first mounted.
      if (!config.value) await loadConfig()
    } catch {
      connected.value = false
    } finally {
      polling = false
    }
  }
  function connect() {
    if (timer) return
    void poll()
    timer = setInterval(() => { void poll() }, 350)
  }
  return { config, monitor, connected, loadConfig, connect }
})
