<script setup lang="ts">
import {computed,onMounted,reactive,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import SWaterChart from '../components/SWaterChart.vue'
import {request,commitConfig,type AppConfig,type Calibration} from '../api/client'
import {useSystemStore} from '../stores/system'
const store=useSystemStore()
const stage=ref<'P1'|'P2'|'P3'>('P1')
const frameIndex=ref(30)
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
const canPreview=computed(()=>!!draft.value?.source.address && !!calib.value)
function defaultCalib():Calibration{return {cx:360,cy:288,radius:160,reference_width:720,reference_height:576}}
function init(){if(store.config){draft.value=JSON.parse(JSON.stringify(store.config)); for(const s of ['P1','P2','P3']){if(!draft.value!.calibrations[s])draft.value!.calibrations[s]=defaultCalib()}}}
watch(()=>store.config,()=>{init();setTimeout(requestPreview,0)},{immediate:true})
function selectStage(s:'P1'|'P2'|'P3'){stage.value=s;requestPreview()}
async function loadFrame(){
 if(!draft.value?.source.address) return
 try{const resp=await request<{width:number;height:number;jpeg_base64:string}>('/api/camera/frame','POST',{source:draft.value.source,frame_index:frameIndex.value})
 rawImage.value='data:image/jpeg;base64,'+resp.jpeg_base64
 const c=calib.value
 if(c && !dirty.value && c.reference_width!==resp.width){c.reference_width=resp.width;c.reference_height=resp.height}
 }catch(e){error.value=String(e)}
}
async function requestPreview(){if(!canPreview.value||busy.value)return;busy.value=true;error.value='';try{
 await loadFrame()
 const resp=await request<{status:string;s_water:number;samples:{s_water:number}[];jpeg_base64:string}>('/api/debug/preview','POST',{
 source:draft.value!.source,stage:stage.value,calibration:calib.value,detector:detector.value,frame_index:frameIndex.value})
 previewImage.value='data:image/jpeg;base64,'+resp.jpeg_base64
 previewScore.value=resp.s_water;previewStatus.value=resp.status;previewHistory.value=resp.samples.map(v=>v.s_water)
 }catch(e){error.value=String(e)}finally{busy.value=false}}
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
async function chooseVideo(){const file=await window.desktop?.selectVideoFile();if(file&&draft.value){draft.value.source.kind='file';draft.value.source.address=file;schedulePreview()}}
onMounted(()=>{if(canPreview.value)requestPreview()})
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
    <p class="hint">蓝色手柄：拖动圆心。绿色手柄：改变半径。所有坐标均由原始视频像素计算，不受窗口缩放影响。喷头 Mask 将由后端动态识别；当前版本不假装已识别成功。</p>
    <div class="toolbar"><span class="muted">预览帧</span><el-input-number v-model="frameIndex" :min="0" :step="10" @change="requestPreview"/><el-button type="primary" :loading="busy" @click="requestPreview">重新计算预览</el-button><el-tag :type="dirty?'warning':'success'">{{dirty?'草稿未保存':'正式配置'}}</el-tag></div>
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
