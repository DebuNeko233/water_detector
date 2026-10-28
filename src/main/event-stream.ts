/** One WebSocket connection shared by all Vue components. */

import WebSocket from 'ws'
import type { BackendEvent } from '../shared/bridge'
import { BackendManager } from './backend-manager'

export class EventStream {
  private socket: WebSocket | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private closed = false
  private retries = 0

  constructor(
    private readonly backend: BackendManager,
    private readonly publish: (event: BackendEvent) => void,
  ) {}

  connect(): void {
    this.closed = false
    // 已连接或正在连接时不创建第二条 WebSocket，所有 Vue 组件共享同一通道。
    if (this.socket) return
    let url: string
    try {
      url = this.backend.baseUrl.replace(/^http/, 'ws') + '/ws/events'
    } catch {
      this.retryLater()
      return
    }

    // 打包版 WebSocket 需要与 HTTP 相同的会话令牌；开发版令牌为空。
    const headers = this.backend.token ? { 'x-session-token': this.backend.token } : {}
    const socket = new WebSocket(url, { headers })
    this.socket = socket
    socket.on('open', () => {
      this.retries = 0
      this.backend.connected()
    })
    socket.on('message', (data) => {
      try {
        const event = JSON.parse(data.toString()) as BackendEvent
        if (typeof event.event === 'string' && typeof event.sequence === 'number') {
          this.publish(event)
        }
      } catch (error) {
        console.warn('Bad WebSocket message:', error)
      }
    })
    socket.on('error', (error) => {
      console.warn('Python WebSocket:', error.message)
    })
    socket.on('close', () => {
      // Ignore late close notifications from a connection that was replaced.
      if (this.socket !== socket) return
      this.socket = null
      this.backend.disconnected()
      this.retryLater()
    })
  }

  private retryLater(): void {
    if (this.closed || this.retryTimer) return
    // 指数退避，上限 10 秒；不让短暂断网变成密集重连。
    const milliseconds = Math.min(1000 * (2 ** this.retries++), 10_000)
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      this.connect()
    }, milliseconds)
  }

  stop(): void {
    // 显式停止时禁止自动重连，尤其是在 Electron 正在退出时。
    this.closed = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    this.socket?.close()
    this.socket = null
  }
}
