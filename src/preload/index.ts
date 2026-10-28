import { contextBridge, ipcRenderer } from 'electron'
import type { BackendEvent, BackendStatus, DesktopBridge } from '../shared/bridge'

// 只把经过明确设计的白名单方法提供给 Vue，不暴露原始 ipcRenderer。
const desktop: DesktopBridge = {
  backend: {
    request: (method, path, body) =>
      ipcRenderer.invoke('backend:request', method, path, body),
    onEvent(listener) {
      // 返回取消订阅函数，Vue 组件销毁后可以移除 IPC 监听，避免重复回调。
      const handler = (_event: Electron.IpcRendererEvent, message: BackendEvent) => listener(message)
      ipcRenderer.on('backend:event', handler)
      return () => ipcRenderer.removeListener('backend:event', handler)
    },
    onStatus(listener) {
      const handler = (_event: Electron.IpcRendererEvent, status: BackendStatus) => listener(status)
      ipcRenderer.on('backend:status', handler)
      return () => ipcRenderer.removeListener('backend:status', handler)
    },
    status: () => ipcRenderer.invoke('backend:status'),
    diagnostics: () => ipcRenderer.invoke('backend:diagnostics'),
    retry: () => ipcRenderer.invoke('backend:retry'),
  },
  logging: {
    write: (level, source, message) => ipcRenderer.invoke('logging:write', level, source, message),
  },
  system: {
    selectFile: () => ipcRenderer.invoke('system:select-file'),
    openLogs: (target) => ipcRenderer.invoke('system:open-logs', target),
    closeWindow: () => ipcRenderer.invoke('system:close-window'),
  },
}

// contextIsolation 下 Renderer 只能通过 window.desktop 调用受控桌面功能。
contextBridge.exposeInMainWorld('desktop', desktop)
