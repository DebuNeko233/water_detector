import { contextBridge, ipcRenderer } from 'electron'
contextBridge.exposeInMainWorld('desktop', {
  selectVideoFile: () => ipcRenderer.invoke('select-video-file'),
  backendLogs: () => ipcRenderer.invoke('backend-logs')
})
