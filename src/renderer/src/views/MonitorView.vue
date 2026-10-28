<script setup lang="ts">
import {computed,ref,onMounted,onUnmounted} from 'vue'
import {ElMessage} from 'element-plus'
import {useSystemStore} from '../stores/system'
import {request,startMonitor,stopMonitor} from '../api/client'
import SWaterChart from '../components/SWaterChart.vue'
const store=useSystemStore()
const busy=ref(false)
const previewImage=ref('')
let frameTimer:ReturnType<typeof setInterval>|null=null
let loadingFrame=false
async function fetchPreview(){
 if(loadingFrame||!store.connected)return
 loadingFrame=true
 try {
   const data=await request<{jpeg_base64:string|null}>('/api/monitor/frame')
   if(data.jpeg_base64) previewImage.value='data:image/jpeg;base64,'+data.jpeg_base64
 }catch{ /* backend temporarily unavailable */ }
 finally{loadingFrame=false}
}
onMounted(()=>{void fetchPreview();frameTimer=setInterval(()=>{void fetchPreview()},250)})
onUnmounted(()=>{if(frameTimer)clearInterval(frameTimer)})

const running=computed(()=>['RUNNING','STARTING'].includes(store.monitor.state))
const threshold=computed(()=>store.config?.detector.s_water_threshold||0.010)
const statusColor=computed(()=>({OK:'#19855a',NG:'#d14a42',PAUSED:'#b37d2e',WARMUP:'#b37d2e'} as Record<string,string>)[store.monitor.status]||'#76879c')
async function toggle(){busy.value=true;try{if(running.value){await stopMonitor()}else{await startMonitor()}}catch(e){ElMessage.error(String(e))}finally{busy.value=false}}
</script>
<template>
 <div class="monitor-grid">
  <section class="panel video-panel" style="padding:10px"><div class="video-area"><img :src="previewImage" alt="Python 后端标注的实时晶圆检测画面"/></div></section>
  <aside class="right-rail">
   <section class="panel"><div class="panel-title">监测操作</div><div class="button-row"><el-button type="primary" size="large" :loading="busy" @click="toggle">{{running?'■ 停止监测':'▶ 开始监测'}}</el-button></div><p class="muted">{{store.config?.source.kind||'未设置'}} · {{store.config?.source.address||'请先配置视频源'}}</p></section>
   <section class="panel"><div class="muted">实时检测状态</div><div class="large-score" :style="{color:statusColor}">{{store.monitor.status}}</div><div class="stat-grid"><div class="cell"><span class="muted">S_water</span><strong>{{(store.monitor.s_water||0).toFixed(4)}}</strong></div><div class="cell"><span class="muted">阈值</span><strong>{{threshold.toFixed(4)}}</strong></div><div class="cell"><span class="muted">当前工位</span><strong>{{store.monitor.stage||'—'}}</strong></div><div class="cell"><span class="muted">异常持续</span><strong>{{(store.monitor.abnormal_run_s||0).toFixed(2)}} s</strong></div></div></section>
   <section class="panel"><div class="panel-title">S_water history <span class="muted">（Python 计算）</span></div><SWaterChart :values="store.monitor.history||[]" :threshold="threshold"/></section>
   <section class="panel"><div class="panel-title">运行信息</div><div class="stat-grid"><div class="cell"><span class="muted">任务</span><strong>{{store.monitor.state}}</strong></div><div class="cell"><span class="muted">处理帧</span><strong>{{store.monitor.processed_frames||0}}</strong></div></div><p class="hint" v-if="store.monitor.reason">{{store.monitor.reason}}</p><p class="hint" v-else>标注视频与 CSV 保存在 Python 用户数据目录下。</p></section>
  </aside>
 </div>
</template>
