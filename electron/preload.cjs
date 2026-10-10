const {contextBridge,ipcRenderer}=require('electron')
contextBridge.exposeInMainWorld('desktop',{
  selectVideoFile:()=>ipcRenderer.invoke('select-video-file')
})
