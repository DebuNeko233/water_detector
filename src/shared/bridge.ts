/** Types shared by the renderer and the secure preload bridge. */

// Python → Main WebSocket → Renderer 的统一事件格式；sequence 用于识别事件顺序。
export interface BackendEvent {
  event: string
  sequence: number
  data: Record<string, unknown>
}

export type BackendStatus = 'starting' | 'ready' | 'offline' | 'stopping' | 'failed'
export type LogLevel = 'info' | 'warn' | 'error'
export type LogTarget = 'python' | 'electron'

// Preload 的唯一公开契约；新增方法需同步修改 Preload、Main 和调用方。
export interface DesktopBridge {
  backend: {
    // Main 对请求方法和 /api/ 路径做二次校验，不能用此接口代理任意 URL。
    request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T>
    // 订阅函数返回取消订阅回调，避免 Vue 组件重挂载时产生重复监听。
    onEvent(listener: (event: BackendEvent) => void): () => void
    onStatus(listener: (status: BackendStatus) => void): () => void
    status(): Promise<BackendStatus>
    diagnostics(): Promise<string>
    retry(): Promise<void>
  }
  logging: {
    write(level: LogLevel, source: string, message: string): Promise<void>
  }
  system: {
    selectFile(): Promise<string | null>
    openLogs(target?: LogTarget): Promise<void>
    closeWindow(): Promise<void>
  }
}

declare global {
  interface Window {
    desktop: DesktopBridge
  }
}
