import {defineStore} from 'pinia'
import {ref} from 'vue'
import {getConfig, type AppConfig, type MonitorState, BASE} from '../api/client'

export const useSystemStore=defineStore('system',()=>{
  const config=ref<AppConfig|null>(null)
  const monitor=ref<MonitorState>({state:'IDLE',status:'IDLE',history:[]})
  const connected=ref(false)
  let socket:WebSocket|null=null
  async function loadConfig(){config.value=await getConfig()}
  function connect(){
    if(socket && (socket.readyState===WebSocket.OPEN||socket.readyState===WebSocket.CONNECTING))return
    socket=new WebSocket(BASE.replace('http','ws')+'/ws/monitor')
    socket.onopen=()=>{connected.value=true}
    socket.onmessage=e=>{monitor.value=JSON.parse(e.data) as MonitorState}
    socket.onerror=()=>{connected.value=false}
    socket.onclose=()=>{connected.value=false;socket=null;setTimeout(connect,1500)}
  }
  return {config,monitor,connected,loadConfig,connect}
})
