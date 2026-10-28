<script setup lang="ts">
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import SWaterChart from '../components/SWaterChart.vue'
import {request,commitConfig,type AppConfig,type Calibration} from '../api/client'
import {useSystemStore} from '../stores/system'
const store=useSystemStore()
const stage=ref<'P1'|'P2'|'P3'>('P1')
const frameIndex=ref(30)
const seekFrame=ref(30)
interface VideoMetadata {
  frame_count:number
  last_frame_index:number
  fps:number
  duration_seconds:number
  width:number
  height:number
}
const videoMeta=ref<VideoMetadata|null>(null)
const metadataBusy=ref(false)
let metadataVersion=0
let previewVersion=0
let previewWorkerActive=false
const previewImage=ref('')
const rawImage=ref('')
const previewStatus=ref('尚未预览')
const previewScore=ref(0)
const previewHistory=ref<number[]>([])
const busy=ref(false)
const dirty=ref(false)
const draft=ref<AppConfig|null>(null)
const error=ref('')
const pointer=ref<'center'|'radius'|null>(null)
const svg=ref<SVGSVGElement|null>(null)
let timer:ReturnType<typeof setTimeout>|null=null
const calib=computed(()=>draft.value?.calibrations[stage.value])
const detector=computed(()=>draft.value?.detector)
const isVideoFile=computed(()=>draft.value?.source.kind==='file')
const canPreview=computed(()=>isVideoFile.value && !!draft.value?.source.address && !!calib.value && !!videoMeta.value)
const maxFrame=computed(()=>videoMeta.value?.last_frame_index ?? 0)
function formatTime(value:number):string {
 const seconds=Number.isFinite(value)?Math.max(0,value):0
 const h=Math.floor(seconds/3600)
 const m=String(Math.floor(seconds/60)%60).padStart(2,'0')
 const s=String(Math.floor(seconds%60)).padStart(2,'0')
 const tenth=Math.floor((seconds%1)*10)
 return h?`${h}:${m}:${s}.${tenth}`:`${m}:${s}.${tenth}`
}
const currentTime=computed(()=>formatTime(seekFrame.value/(videoMeta.value?.fps||30)))
const totalTime=computed(()=>formatTime(videoMeta.value?.duration_seconds||0))
const resolutionMismatch=computed(()=>!!(videoMeta.value&&calib.value&&
 (calib.value.reference_width!==videoMeta.value.width || calib.value.reference_height!==videoMeta.value.height)))
function sliderTooltip(v:number):string {return `${formatTime(v/(videoMeta.value?.fps||30))} · 第 ${v} 帧`}
function clampFrame(v:number):number {
 return Math.max(0,Math.min(maxFrame.value,Number.isFinite(v)?Math.round(v):0))
}
function defaultCalib():Calibration{return {cx:360,cy:288,radius:160,reference_width:720,reference_height:576}}
function init(){if(store.config){draft.value=JSON.parse(JSON.stringify(store.config)); for(const s of ['P1','P2','P3']){if(!draft.value!.calibrations[s])draft.value!.calibrations[s]=defaultCalib()}}}
watch(()=>store.config,()=>{init();void loadVideoMetadata(false)},{immediate:true})
function selectStage(s:'P1'|'P2'|'P3'){stage.value=s;void requestPreview()}

// Query metadata once per selected local file. Responses for an older file are discarded.
async function loadVideoMetadata(resetPosition:boolean):Promise<void> {
 const version=++metadataVersion
 previewVersion++ // invalidate in-flight previews when video source changes
 videoMeta.value=null
 previewImage.value=''
 rawImage.value=''
 previewHistory.value=[]
 error.value=''
 if(!draft.value?.source.address || !isVideoFile.value)return
 metadataBusy.value=true
 const source={...draft.value.source}
 try {
  const result=await request<VideoMetadata>('/api/camera/metadata','POST',{source})
  if(version!==metadataVersion)return
  videoMeta.value=result
  frameIndex.value=resetPosition?0:clampFrame(frameIndex.value)
  seekFrame.value=frameIndex.value
  await requestPreview()
 }catch(e){
  if(version===metadataVersion)error.value='无法读取视频信息：'+String(e)
 }finally{
  if(version===metadataVersion)metadataBusy.value=false
 }
}

// Run just one preview request chain. If a newer seek arrives while processing,
// discard the stale response and process the latest position instead.
async function requestPreview():Promise<void> {
 ++previewVersion
 if(previewWorkerActive || !canPreview.value)return
 previewWorkerActive=true
 busy.value=true
 try {
  while(canPreview.value){
   const version=previewVersion
   const source={...draft.value!.source}
   const calibration={...calib.value!}
   const params={...detector.value!}
   const index=frameIndex.value
   const selectedStage=stage.value
   error.value=''
   previewStatus.value='计算中…'
   previewImage.value=''
   try {
    // Show target frame quickly while the Python time-history preview computes.
    const frame=await request<{width:number;height:number;jpeg_base64:string}>(
      '/api/camera/frame','POST',{source,frame_index:index})
    if(version!==previewVersion)continue
    rawImage.value='data:image/jpeg;base64,'+frame.jpeg_base64
    const resp=await request<{status:string;s_water:number;samples:{time_s:number;s_water:number}[];jpeg_base64:string}>(
      '/api/debug/preview','POST',{
       source,stage:selectedStage,calibration,detector:params,frame_index:index
      })
    if(version!==previewVersion)continue
    previewImage.value='data:image/jpeg;base64,'+resp.jpeg_base64
    previewScore.value=resp.s_water
    previewStatus.value=resp.status
    previewHistory.value=resp.samples.map(v=>v.s_water)
   }catch(e){
    if(version===previewVersion){
     previewStatus.value='预览失败'
     error.value=String(e)
    }
   }
   if(version===previewVersion)break
  }
 }finally{
  previewWorkerActive=false
  busy.value=false
 }
}
function jumpToFrame(value:number):void {
 if(!videoMeta.value)return
 const target=clampFrame(value)
 frameIndex.value=target
 seekFrame.value=target
 void requestPreview()
}
function onSeekCommit(value:number|number[]):void {
 jumpToFrame(Array.isArray(value)?value[0]:value)
}
function stepFrame(delta:number):void {jumpToFrame(frameIndex.value+delta)}
function schedulePreview(){dirty.value=true;if(timer)clearTimeout(timer);timer=setTimeout(requestPreview,400)}
function pointInSource(e:PointerEvent){
 if(!svg.value||!calib.value)return null
 const rect=svg.value.getBoundingClientRect()
 // SVG preserveAspectRatio="xMidYMid meet": account for letterboxing in source coordinates
 const w=calib.value.reference_width,h=calib.value.reference_height
 const scale=Math.min(rect.width/w,rect.height/h)
 const left=(rect.width-w*scale)/2,top=(rect.height-h*scale)/2
 return {x:Math.max(0,Math.min(w,(e.clientX-rect.left-left)/scale)),y:Math.max(0,Math.min(h,(e.clientY-rect.top-top)/scale))}
}
function down(e:PointerEvent,kind:'center'|'radius'){
 if(!calib.value)return
 pointer.value=kind;(e.currentTarget as Element).setPointerCapture(e.pointerId);e.preventDefault()
}
function move(e:PointerEvent){
 if(!pointer.value||!calib.value)return
 const pt=pointInSource(e);if(!pt)return
 if(pointer.value==='center'){calib.value.cx=Math.round(pt.x);calib.value.cy=Math.round(pt.y)}
 else calib.value.radius=Math.max(11,Math.round(Math.hypot(pt.x-calib.value.cx,pt.y-calib.value.cy)))
 dirty.value=true
}
function up(){if(pointer.value){pointer.value=null;schedulePreview()}}
async function save(){if(!draft.value)return;try{const cfg=await commitConfig(draft.value);store.config=cfg;dirty.value=false;ElMessage.success('已保存正式参数')}catch(e){ElMessage.error(String(e))}}
async function chooseVideo(){
 const file=await window.desktop?.system.selectFile()
 if(file&&draft.value){
  draft.value.source.kind='file'
  draft.value.source.address=file
  dirty.value=true
  await loadVideoMetadata(true)
 }
}
onBeforeUnmount(()=>{
 if(timer)clearTimeout(timer)
 metadataVersion++
 previewVersion++
})
</script>
<template>
 <div class="debug-grid" v-if="draft&&detector&&calib">
  <div class="debug-left">
   <section class="panel"><div class="toolbar"><div class="stage-tabs"><button v-for="s in (['P1','P2','P3'] as const)" :key="s" :class="{active:stage===s}" @click="selectStage(s)">{{s}}</button></div><el-tag type="info">鼠标拖动中心 / 半径</el-tag><el-button @click="chooseVideo">选择视频</el-button></div>
    <svg ref="svg" class="roi-view" :viewBox="`0 0 ${calib.reference_width} ${calib.reference_height}`" preserveAspectRatio="xMidYMid meet" @pointermove="move" @pointerup="up" @pointercancel="up">
      <image v-if="previewImage||rawImage" :href="previewImage||rawImage" x="0" y="0" :width="calib.reference_width" :height="calib.reference_height" preserveAspectRatio="none"/>
      <circle :cx="calib.cx" :cy="calib.cy" :r="calib.radius*detector.outer_radius_ratio" fill="none" stroke="#45e0a2" :stroke-width="Math.max(1.5,calib.reference_width/360)"/>
      <circle :cx="calib.cx" :cy="calib.cy" :r="calib.radius*detector.inner_radius_ratio" fill="none" stroke="#f4ba58" stroke-width="1.8" stroke-dasharray="6 4"/>
      <circle :cx="calib.cx" :cy="calib.cy" r="10" fill="#408def" stroke="#fff" stroke-width="2" style="cursor:move" @pointerdown="down($event,'center')"/>
      <circle :cx="calib.cx+calib.radius" :cy="calib.cy" r="10" fill="#2acb7f" stroke="#fff" stroke-width="2" style="cursor:ew-resize" @pointerdown="down($event,'radius')"/>
    </svg>
    <p class="hint">蓝色手柄：拖动圆心；绿色手柄：改变半径。跳转后仍可调节 ROI 与参数，喷头 Mask 将由 Python 动态检测（当前版本尚未实现）。</p>
    <div v-if="isVideoFile" class="video-timeline">
     <div class="timeline-top">
      <div><strong>视频进度</strong><span class="muted"> · {{videoMeta ? `${videoMeta.frame_count} 帧 · ${videoMeta.fps.toFixed(2)} FPS` : '正在读取信息'}}</span></div>
      <div class="timeline-time">{{currentTime}} <span>/ {{totalTime}}</span></div>
     </div>
     <el-slider v-model="seekFrame" :min="0" :max="maxFrame" :step="1" :disabled="!videoMeta || metadataBusy"
       :format-tooltip="sliderTooltip" aria-label="视频进度，松开后跳转" @change="onSeekCommit"/>
     <div class="timeline-bottom">
      <div class="timeline-step">
       <el-button size="small" :disabled="!videoMeta" @click="jumpToFrame(0)">开头</el-button>
       <el-button size="small" :disabled="!videoMeta" @click="stepFrame(-1)">◀ 上一帧</el-button>
       <el-button size="small" :disabled="!videoMeta" @click="stepFrame(1)">下一帧 ▶</el-button>
       <el-button size="small" :disabled="!videoMeta" @click="jumpToFrame(maxFrame)">结尾</el-button>
      </div>
      <div class="timeline-frame"><span class="muted">帧号</span>
       <el-input-number v-model="frameIndex" size="small" :min="0" :max="maxFrame" :step="1"
        :disabled="!videoMeta" @change="jumpToFrame(frameIndex)"/>
       <span class="muted">/ {{maxFrame}}</span>
      </div>
     </div>
     <p class="hint">拖动时可查看目标时间；松开后 Python 跳转至目标帧并重新计算局部 S_water history。切换位置不会修改正式参数。</p>
     <el-alert v-if="resolutionMismatch" title="当前标定的分辨率与视频不匹配，请重新标定对应工位" type="warning" :closable="false"/>
    </div>
    <div v-else class="hint">当前是实时视频源，只有本地视频文件支持拖动跳转。</div>
    <div class="toolbar" style="margin-top:12px"><el-button type="primary" :loading="busy||metadataBusy" :disabled="!canPreview" @click="requestPreview">重新计算预览</el-button><el-tag :type="dirty?'warning':'success'">{{dirty?'草稿未保存':'正式配置'}}</el-tag><span v-if="busy" class="muted">正在读取画面 / 计算历史…</span></div>
    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false"/>
   </section>
   <section class="panel"><div class="panel-title">S_water history · 参数草稿预览</div><div class="muted">{{previewStatus}} · 当前帧 S_water = {{previewScore.toFixed(4)}}</div><SWaterChart :values="previewHistory" :threshold="detector.s_water_threshold"/></section>
  </div>
  <div class="debug-right">
   <section class="panel"><div class="panel-title">当前工位标定 {{stage}}</div>
    <div class="form-line"><span>圆心 X</span><el-input-number v-model="calib.cx" :min="0" :max="calib.reference_width" @change="schedulePreview"/></div>
    <div class="form-line"><span>圆心 Y</span><el-input-number v-model="calib.cy" :min="0" :max="calib.reference_height" @change="schedulePreview"/></div>
    <div class="form-line"><span>半径 R</span><el-input-number v-model="calib.radius" :min="11" @change="schedulePreview"/></div>
   </section>
   <section class="panel"><div class="panel-title">水流判定参数（继承 main.py）</div>
    <div v-for="item in ([['inner_radius_ratio','内半径比例'],['outer_radius_ratio','外半径比例'],['diff_threshold','帧差阈值'],['history_seconds','驻留窗口 / s'],['persistence_threshold','像素驻留阈值'],['s_water_threshold','S_water 阈值'],['ng_hold_seconds','NG 持续时间 / s'],['recover_hold_seconds','恢复时间 / s'],['sector_count','扇区数量'],['sector_threshold','扇区显示阈值'],['blur_ksize','高斯核大小']] as const)" :key="item[0]" class="form-line">
      <span>{{item[1]}}</span><el-input-number v-model="detector[item[0]]" :step="['history_seconds','persistence_threshold','s_water_threshold','ng_hold_seconds','recover_hold_seconds','sector_threshold','inner_radius_ratio','outer_radius_ratio'].includes(item[0])?0.01:1" :precision="['diff_threshold','sector_count','blur_ksize'].includes(item[0])?0:3" @change="schedulePreview"/>
    </div>
    <div class="footer-actions"><el-button @click="init">放弃修改</el-button><el-button type="primary" :disabled="!dirty" @click="save">保存为正式参数</el-button></div>
   </section>
  </div>
 </div>
 <el-empty v-else description="等待后端加载配置"/>
</template>
