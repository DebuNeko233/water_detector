<script setup lang="ts">
import {computed,onMounted,onBeforeUnmount,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import SWaterChart from '../components/SWaterChart.vue'
import {request,commitConfig,type AppConfig,type Calibration} from '../api/client'
import {useSystemStore} from '../stores/system'
const store=useSystemStore()
const stage=ref<'P1'|'P2'|'P3'>('P1')
/** Video algorithm testing is isolated from the official monitoring task. */
interface VideoMetadata {
 frame_count:number;last_frame_index:number;fps:number;duration_seconds:number;width:number;height:number
}
interface PlaybackState {
 state:'IDLE'|'SEEKING'|'PLAYING'|'PAUSED'|'COMPLETED'|'ERROR'
 frame_index:number;fps:number;status:string;s_water:number|null
 history:number[];reason:string;revision:number;jpeg_base64:string|null
 stage:'P1'|'P2'|'P3'|'UNKNOWN'
 position_confidence:number
 position_scores:Record<string,number>
 recognition_mode:'single'|'auto'
 roi:Calibration|null
}
const frameIndex=ref(0)
const seekFrame=ref(0)
const videoMeta=ref<VideoMetadata|null>(null)
const metadataBusy=ref(false)
const playbackBusy=ref(false)
const playbackState=ref<PlaybackState['state']>('IDLE')
const isScrubbing=ref(false)
const showCalibrationEditor=ref(false)
const showAlgorithmRoi=ref(true)
const capturingReference=ref(false)
const recognizedStage=ref('UNKNOWN')
const matchScore=ref(0)
const matchScores=ref<Record<string,number>>({})
const effectiveRoi=ref<Calibration|null>(null)
const previewImage=ref('')
const previewStatus=ref('尚未预览')
const previewScore=ref<number|null>(null)
const previewHistory=ref<number[]>([])
const busy=ref(false)
const saving=ref(false)
const dirty=ref(false)
const draft=ref<AppConfig|null>(null)
const error=ref('')
const pointer=ref<'center'|'radius'|null>(null)
const svg=ref<SVGSVGElement|null>(null)
let timer:ReturnType<typeof setTimeout>|null=null
let pollTimer:ReturnType<typeof setInterval>|null=null
let metadataVersion=0
let previewRevision=-1
let pollBusy=false
let updateBusy=false
let updateVersion=0
let active=true
const calib=computed(()=>draft.value?.calibrations[stage.value])
const detector=computed(()=>draft.value?.detector)
const isVideoFile=computed(()=>draft.value?.source.kind==='file')
const canPreview=computed(()=>isVideoFile.value && !!draft.value?.source.address && !!videoMeta.value)
const isPlaying=computed(()=>playbackState.value==='PLAYING'||playbackState.value==='SEEKING')
const isAuto=computed(()=>draft.value?.stage_mode==='auto')
const referenceCount=computed(()=>['P1','P2','P3'].filter(k=>!!draft.value?.position_references[k]).length)
const roiSummary=computed(()=>{
 const r=effectiveRoi.value
 return r?'(cx='+r.cx.toFixed(0)+', cy='+r.cy.toFixed(0)+', R='+r.radius.toFixed(0)+')':'无有效 ROI'
})
const scoreText=computed(()=>isAuto.value?(Number.isFinite(matchScore.value)?matchScore.value.toFixed(3):'—'):'手动指定')
const maxFrame=computed(()=>videoMeta.value?.last_frame_index ?? 0)
const resolutionMismatch=computed(()=>!!(videoMeta.value&&calib.value&&
 (calib.value.reference_width!==videoMeta.value.width||calib.value.reference_height!==videoMeta.value.height)))
function formatTime(value:number):string{
 const seconds=Number.isFinite(value)?Math.max(0,value):0
 const h=Math.floor(seconds/3600)
 const m=String(Math.floor(seconds/60)%60).padStart(2,'0')
 const ss=String(Math.floor(seconds%60).toString()).padStart(2,'0')
 return (h?h+':':'')+m+':'+ss+'.'+Math.floor((seconds%1)*10)
}
const currentTime=computed(()=>formatTime(seekFrame.value/(videoMeta.value?.fps||30)))
const totalTime=computed(()=>formatTime(videoMeta.value?.duration_seconds||0))
function sliderTooltip(v:number):string{return formatTime(v/(videoMeta.value?.fps||30))+' · 第 '+v+' 帧'}
function clampFrame(v:number):number{
 return Math.max(0,Math.min(maxFrame.value,Number.isFinite(v)?Math.round(v):0))
}
function defaultCalib():Calibration{
 return {cx:360,cy:288,radius:160,reference_width:720,reference_height:576}
}
function init(){
 if(!store.config)return
 draft.value=JSON.parse(JSON.stringify(store.config)) as AppConfig
 stage.value=store.config.selected_stage
 for(const key of ['P1','P2','P3']){
  if(!draft.value.calibrations[key])draft.value.calibrations[key]=defaultCalib()
 }
 dirty.value=false
}
function discardChanges():void {
 const priorSource=draft.value?.source.address
 init()
 if(priorSource!==draft.value?.source.address)void loadVideoMetadata(true)
 else void updatePlayback()
}
function selectStage(value:'P1'|'P2'|'P3'){
 stage.value=value
 if(draft.value){draft.value.selected_stage=value;dirty.value=true}
 schedulePreview()
}
function playbackRequest(index:number){
 const config=draft.value!
 return {
  source:{...config.source},frame_index:clampFrame(index),stage:stage.value,
  calibration:calib.value?{...calib.value}:null,detector:{...config.detector},
  stage_mode:config.stage_mode,
  calibrations:{...config.calibrations},
  position_references:{...config.position_references},
  show_algorithm_roi:showAlgorithmRoi.value,
 }
}
async function pollPlayback():Promise<void>{
 if(!active||!canPreview.value||pollBusy)return
 pollBusy=true
 try{
  const result=await request<PlaybackState>('/api/playback/state?after_revision='+previewRevision)
  if(!active||!canPreview.value)return
  playbackState.value=result.state
  previewStatus.value=result.status
  previewScore.value=result.s_water
  previewHistory.value=result.history
  recognizedStage.value=result.stage || 'UNKNOWN'
  matchScore.value=result.position_confidence || 0
  matchScores.value=result.position_scores || {}
  effectiveRoi.value=result.roi || null
  if(result.jpeg_base64){
   previewImage.value='data:image/jpeg;base64,'+result.jpeg_base64
   previewRevision=result.revision
  }
  if(result.reason)error.value=result.reason
  else if(!metadataBusy.value)error.value=''
  if(!isScrubbing.value&&result.frame_index>=0){
   frameIndex.value=clampFrame(result.frame_index)
   seekFrame.value=frameIndex.value
  }
 }catch(e){if(active)error.value='视频播放状态获取失败：'+String(e)}
 finally{pollBusy=false}
}
async function startPlayback(index:number):Promise<void>{
 if(!canPreview.value)return
 playbackBusy.value=true
 previewRevision=-1
 previewImage.value=''
 error.value=''
 try{
  const state=await request<PlaybackState>('/api/playback/start','POST',playbackRequest(index))
  if(!active)return
  playbackState.value=state.state
  await pollPlayback()
 }catch(e){
  if(active){playbackState.value='ERROR';error.value='视频播放失败：'+String(e)}
 }finally{playbackBusy.value=false}
}
async function loadVideoMetadata(resetPosition:boolean):Promise<void>{
 const version=++metadataVersion
 videoMeta.value=null
 previewImage.value=''
 previewHistory.value=[]
 previewScore.value=null
 error.value=''
 if(!draft.value?.source.address||!isVideoFile.value)return
 metadataBusy.value=true
 try{
  const info=await request<VideoMetadata>('/api/camera/metadata','POST',{source:{...draft.value.source}})
  if(version!==metadataVersion||!active)return
  videoMeta.value=info
  frameIndex.value=resetPosition?0:clampFrame(frameIndex.value)
  seekFrame.value=frameIndex.value
  await startPlayback(frameIndex.value)
 }catch(e){
  if(version===metadataVersion&&active)error.value='无法读取视频信息：'+String(e)
 }finally{if(version===metadataVersion)metadataBusy.value=false}
}
/** Uncommitted draft tuning resets the detector's residence window, not the
 * monitoring worker. A paused preview stays paused after recalculation. */
async function updatePlayback():Promise<void>{
 ++updateVersion
 if(updateBusy||!canPreview.value||!draft.value)return
 updateBusy=true
 busy.value=true
 try{
  while(active&&canPreview.value&&draft.value){
   const revision=updateVersion
   try{
    await request<PlaybackState>('/api/playback/update','POST',playbackRequest(frameIndex.value))
    if(revision===updateVersion)break
   }catch(e){
    if(active)error.value='应用临时参数失败：'+String(e)
    break
   }
  }
 }finally{updateBusy=false;busy.value=false}
}
function schedulePreview(){
 dirty.value=true
 if(timer)clearTimeout(timer)
 timer=setTimeout(()=>{void updatePlayback()},280)
}
/** Resize a previously saved circular ROI to the active video dimensions.
 * The operator can then drag center/radius to refine it.
 */
function adaptCalibrationToVideo():void{
 if(!videoMeta.value||!calib.value)return
 const old=calib.value
 const scaleX=videoMeta.value.width/old.reference_width
 const scaleY=videoMeta.value.height/old.reference_height
 old.cx=Math.round(old.cx*scaleX)
 old.cy=Math.round(old.cy*scaleY)
 old.radius=Math.max(11,Math.round(old.radius*Math.min(scaleX,scaleY)))
 old.reference_width=videoMeta.value.width
 old.reference_height=videoMeta.value.height
 schedulePreview()
}
async function captureReference():Promise<void>{
 if(!draft.value||!canPreview.value||isPlaying.value||capturingReference.value)return
 capturingReference.value=true
 try{
  const result=await request<{stage:string;frame_index:number;path:string}>(
   '/api/position/reference/capture','POST',
   {source:{...draft.value.source},frame_index:frameIndex.value,stage:stage.value})
  draft.value.position_references[result.stage]=result.path
  dirty.value=true
  ElMessage.success('已采集 '+result.stage+' 参考帧（尚未保存配置）')
  void updatePlayback()
 }catch(e){ElMessage.error('采集工位参考帧失败：'+String(e))}
 finally{capturingReference.value=false}
}
function onModeChange():void{
 if(draft.value)draft.value.selected_stage=stage.value
 schedulePreview()
}
function toggleAlgorithmRoi():void{
 // This is a visualization-only setting, not part of the persisted detector.
 void updatePlayback()
}
function jumpToFrame(value:number):void{
 if(!canPreview.value)return
 const target=clampFrame(value)
 frameIndex.value=target
 seekFrame.value=target
 if(timer){clearTimeout(timer);timer=null}
 isScrubbing.value=false
 playbackBusy.value=true
 void request<PlaybackState>('/api/playback/seek','POST',{frame_index:target})
  .then(data=>{playbackState.value=data.state})
  .catch(()=>startPlayback(target))
  .finally(()=>{playbackBusy.value=false})
}
function onSeekCommit(value:number|number[]):void{
 jumpToFrame(Array.isArray(value)?value[0]:value)
}
function stepFrame(delta:number):void{jumpToFrame(frameIndex.value+delta)}
async function togglePlayback():Promise<void>{
 if(playbackBusy.value||!canPreview.value)return
 playbackBusy.value=true
 try{
  const endpoint=isPlaying.value?'/api/playback/pause':'/api/playback/resume'
  const result=await request<PlaybackState>(endpoint,'POST')
  playbackState.value=result.state
 }catch(e){error.value=String(e)}
 finally{playbackBusy.value=false}
}
watch(()=>store.config,()=>{init();void loadVideoMetadata(false)},{immediate:true})
onMounted(()=>{pollTimer=setInterval(()=>{void pollPlayback()},120)})
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
 if(!showCalibrationEditor.value||!calib.value)return
 pointer.value=kind;(e.currentTarget as Element).setPointerCapture(e.pointerId);e.preventDefault()
}
function move(e:PointerEvent){
 if(!showCalibrationEditor.value||!pointer.value||!calib.value)return
 const pt=pointInSource(e);if(!pt)return
 if(pointer.value==='center'){calib.value.cx=Math.round(pt.x);calib.value.cy=Math.round(pt.y)}
 else calib.value.radius=Math.max(11,Math.round(Math.hypot(pt.x-calib.value.cx,pt.y-calib.value.cy)))
 dirty.value=true
}
function up(){if(pointer.value){pointer.value=null;schedulePreview()}}
async function save(){
 if(!draft.value || saving.value)return
 saving.value=true
 try{
  const cfg=await commitConfig(draft.value)
  store.config=cfg
  dirty.value=false
  ElMessage.success('已保存正式参数')
 }catch(e){
  ElMessage.error('保存标定失败：'+String(e))
 }finally{
  saving.value=false
 }
}
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
 active=false
 if(timer)clearTimeout(timer)
 if(pollTimer)clearInterval(pollTimer)
 metadataVersion++
 updateVersion++
 if(isVideoFile.value)void request('/api/playback/stop','POST').catch(()=>{})
})
</script>
<template>
 <div class="debug-grid" v-if="draft&&detector&&calib">
  <div class="debug-left">
   <section class="panel"><div class="panel-title">视频播放与算法测试</div><div class="toolbar"><div class="stage-tabs"><button v-for="s in (['P1','P2','P3'] as const)" :key="s" :class="{active:stage===s}" @click="selectStage(s)">{{s}}</button></div><el-switch v-model="showCalibrationEditor" active-text="显示手动标定层" inactive-text="隐藏手动标定层"
      aria-label="显示或隐藏所有手动标定圆环和拖动手柄"/>
    <el-switch v-model="showAlgorithmRoi" active-text="显示算法 ROI" inactive-text="只看异常区域"
      aria-label="显示或隐藏算法实际使用的 ROI 边界" @change="toggleAlgorithmRoi"/><el-button @click="chooseVideo">选择视频</el-button></div>
    <svg ref="svg" class="roi-view" :viewBox="`0 0 ${videoMeta?.width ?? calib.reference_width} ${videoMeta?.height ?? calib.reference_height}`" preserveAspectRatio="xMidYMid meet" @pointermove="move" @pointerup="up" @pointercancel="up">
      <image v-if="previewImage" :href="previewImage" x="0" y="0" :width="videoMeta?.width ?? calib.reference_width" :height="videoMeta?.height ?? calib.reference_height" preserveAspectRatio="none"/>
      <circle v-if="showCalibrationEditor" :cx="calib.cx" :cy="calib.cy" :r="calib.radius*detector.outer_radius_ratio" fill="none" stroke="#45e0a2" :stroke-width="Math.max(1.5,calib.reference_width/360)"/>
      <circle v-if="showCalibrationEditor" :cx="calib.cx" :cy="calib.cy" :r="calib.radius*detector.inner_radius_ratio" fill="none" stroke="#f4ba58" stroke-width="1.8" stroke-dasharray="6 4"/>
      <circle v-if="showCalibrationEditor" :cx="calib.cx" :cy="calib.cy" r="10" fill="#408def" stroke="#fff" stroke-width="2" style="cursor:move" @pointerdown="down($event,'center')"/>
      <circle v-if="showCalibrationEditor" :cx="calib.cx+calib.radius" :cy="calib.cy" r="10" fill="#2acb7f" stroke="#fff" stroke-width="2" style="cursor:ew-resize" @pointerdown="down($event,'radius')"/>
    </svg>
    <p class="hint">关闭「手动标定层」可隐藏全部交互圆环和手柄；还可单独关闭「算法 ROI」边界，只看 Python 识别出的水流异常像素。</p>
    <div class="debug-position-panel">
      <div class="toolbar">
        <strong>工位与 ROI 切换验证</strong>
        <el-radio-group v-model="draft.stage_mode" size="small" @change="onModeChange">
          <el-radio-button value="single">手动工位</el-radio-button>
          <el-radio-button value="auto">自动识别（实验性）</el-radio-button>
        </el-radio-group>
        <el-button size="small" :disabled="!canPreview || isPlaying || capturingReference"
          :loading="capturingReference" @click="captureReference">采集当前帧为 {{stage}} 参考图</el-button>
        <span class="muted">已采集 {{referenceCount}} / 3</span>
      </div>
      <div class="debug-stage-result">
        <div><span class="muted">实际识别工位</span><strong>{{recognizedStage}}</strong></div>
        <div><span class="muted">匹配分数（非概率）</span><strong>{{scoreText}}</strong></div>
        <div><span class="muted">算法实际 ROI</span><strong>{{roiSummary}}</strong></div>
        <div><span class="muted">检测状态</span><strong>{{previewStatus}}</strong></div>
      </div>
      <div v-if="isAuto" class="muted">
        P1={{(matchScores.P1 ?? 0).toFixed(3)}} · P2={{(matchScores.P2 ?? 0).toFixed(3)}} · P3={{(matchScores.P3 ?? 0).toFixed(3)}}
      </div>
      <el-alert v-if="isAuto && referenceCount!==3"
        title="自动工位识别需要 P1/P2/P3 三张参考帧：选择工位、暂停在对应位置后逐一采集。未配齐时输出 UNKNOWN，不进行水流 OK 判定。"
        type="warning" :closable="false" show-icon />
      <p class="hint">自动模式使用实验性的图像结构模板匹配。随视频播放观察工位与实际 ROI 是否切换；识别不可靠时显示 UNKNOWN/PAUSED，不能视为 OK。采集参考帧和标定修改仅在保存后成为正式配置。</p>
    </div>
    <div v-if="isVideoFile" class="video-timeline">
     <div class="timeline-top">
      <div><strong>视频进度</strong><span class="muted"> · {{videoMeta ? `${videoMeta.frame_count} 帧 · ${videoMeta.fps.toFixed(2)} FPS` : '正在读取信息'}}</span></div>
      <div class="timeline-time">{{currentTime}} <span>/ {{totalTime}}</span></div>
     </div>
     <el-slider v-model="seekFrame" :min="0" :max="maxFrame" :step="1" :disabled="!videoMeta || metadataBusy"
       :format-tooltip="sliderTooltip" aria-label="视频进度，松开后从当前位置继续播放" @input="isScrubbing=true" @change="onSeekCommit"/>
     <div class="timeline-bottom">
      <div class="timeline-step">
       <el-button type="primary" size="small" :disabled="!videoMeta || playbackBusy" @click="togglePlayback">{{isPlaying?'Ⅱ 暂停':'▶ 播放'}}</el-button>
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
     <p class="hint">拖动松开后自动继续播放；修改参数或标定 ROI 后自动重算当前帧的 S_water。暂停时调参不会自动恢复播放。</p>
     <div v-if="resolutionMismatch" class="toolbar" style="margin-top:8px"><el-alert title="当前标定的分辨率与视频不匹配" type="warning" :closable="false"/><el-button size="small" @click="adaptCalibrationToVideo">按当前视频尺寸缩放 ROI 后微调</el-button></div>
    </div>
    <div v-else class="hint">当前是实时视频源，只有本地视频文件支持拖动跳转。</div>
    <div class="toolbar" style="margin-top:12px"><el-button :loading="busy||metadataBusy" :disabled="!canPreview" @click="updatePlayback">重新计算预览</el-button><el-tag :type="dirty?'warning':'success'">{{dirty?'临时参数（未保存）':'正式配置'}}</el-tag><el-tag type="info">{{playbackState}}</el-tag><span v-if="busy" class="muted">正在按新参数重建检测历史…</span></div>
    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false"/>
   </section>
   <section class="panel"><div class="panel-title">S_water history · 参数草稿预览</div><div class="muted">{{previewStatus}} · 当前帧 S_water = {{previewScore==null?'—':previewScore.toFixed(4)}}</div><SWaterChart :values="previewHistory" :threshold="detector.s_water_threshold"/></section>
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
    <div class="footer-actions"><el-button @click="discardChanges">放弃修改</el-button><el-button type="primary" :loading="saving" :disabled="!dirty || saving" @click="save">保存为正式参数</el-button></div>
   </section>
  </div>
 </div>
 <el-empty v-else description="等待后端加载配置"/>
</template>
