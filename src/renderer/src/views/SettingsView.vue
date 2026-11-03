<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {useSystemStore} from '../stores/system'
import {request,commitConfig,type AppConfig} from '../api/client'

interface DevicePackageSummary {
 device_name:string
 stage_mode:string
 selected_stage:string
 source_kind:string
 source_reselection_required:boolean
 reference_counts:Record<string,number>
 calibrated_stages:string[]
 detector_parameters:Record<string,number|boolean>
 has_complete_auto_references:boolean
}
interface DevicePackageImported {config:AppConfig;summary:DevicePackageSummary}
const store=useSystemStore()
const draft=ref<AppConfig|null>(null)
const saving=ref(false)
const packageBusy=ref(false)
const selectedPackage=ref('')
const packagePreview=ref<DevicePackageSummary|null>(null)
const dirty=computed(()=>!!(draft.value&&store.config&&JSON.stringify(draft.value)!==JSON.stringify(store.config)))
const monitorRunning=computed(()=>['STARTING','RUNNING'].includes(store.monitor.state))
watch(()=>store.config,c=>{if(c)draft.value=JSON.parse(JSON.stringify(c))},{immediate:true})
const count=(summary:DevicePackageSummary,key:string)=>summary.reference_counts[key]??0

async function save(){
 if(!draft.value||saving.value)return
 saving.value=true
 try{
  store.config=await commitConfig(draft.value)
  ElMessage.success('设备配置已保存')
 }catch(e){ElMessage.error('保存失败：'+String(e))}
 finally{saving.value=false}
}
async function chooseVideo(){
 const file=await window.desktop?.system.selectFile()
 if(file&&draft.value){draft.value.source.kind='file';draft.value.source.address=file}
}
async function exportPackage(){
 if(packageBusy.value)return
 if(dirty.value){
  ElMessage.warning('请先保存当前修改，再导出设备配置包')
  return
 }
 const path=await window.desktop?.system.saveDevicePackage()
 if(!path)return
 packageBusy.value=true
 try{
  const summary=await request<DevicePackageSummary&{file_path:string,image_count:number}>(
   '/api/device-package/export','POST',{path})
  ElMessage.success('已导出 '+summary.device_name+' · '+summary.image_count+' 张参考图')
 }catch(e){ElMessage.error('导出失败：'+String(e))}
 finally{packageBusy.value=false}
}
async function selectPackage(){
 const path=await window.desktop?.system.openDevicePackage()
 if(!path)return
 packageBusy.value=true
 packagePreview.value=null
 selectedPackage.value=path
 try{
  packagePreview.value=await request<DevicePackageSummary>(
   '/api/device-package/preview','POST',{path})
 }catch(e){
  selectedPackage.value=''
  ElMessage.error('无法读取设备包：'+String(e))
 }finally{packageBusy.value=false}
}
async function importPackage(){
 if(!selectedPackage.value||!packagePreview.value||monitorRunning.value||packageBusy.value)return
 try{
  await ElMessageBox.confirm(
   '将切换到设备“'+packagePreview.value.device_name+'”的配置，覆盖当前已保存的标定和检测参数。建议先导出当前设备配置作为备份。继续吗？',
   '确认导入设备配置',{type:'warning',confirmButtonText:'导入并应用',cancelButtonText:'取消'})
 }catch{return}
 packageBusy.value=true
 try{
  const result=await request<DevicePackageImported>(
   '/api/device-package/import','POST',{path:selectedPackage.value})
  store.config=result.config
  packagePreview.value=null
  selectedPackage.value=''
  if(result.summary.source_reselection_required){
   ElMessage.warning('设备配置已导入。原本地视频不随 ZIP 打包，请重新选择视频源并保存。')
  }else{
   ElMessage.success('已切换设备配置：'+result.config.device_name)
  }
 }catch(e){ElMessage.error('导入失败，原配置未改变：'+String(e))}
 finally{packageBusy.value=false}
}
</script>

<template>
 <div v-if="draft" class="device-settings">
  <section class="panel device-profile-panel">
   <div class="panel-title">设备档案与配置迁移</div>
   <p class="hint">为每台洗片设备建立独立配置，ZIP 包含 P1/P2/P3 标定、参考截图、水流阈值、摄像头类型及其他已保存的设备参数；不包含视频录像。</p>
   <div class="form-line"><span>设备名称</span><el-input v-model="draft.device_name" maxlength="80" show-word-limit placeholder="例如：洗片机 A / 1 号产线"/></div>
   <div class="device-package-actions">
    <el-button type="primary" :disabled="packageBusy||dirty" :loading="packageBusy" @click="exportPackage">导出当前设备 ZIP</el-button>
    <el-button :disabled="packageBusy" @click="selectPackage">选择设备 ZIP 导入</el-button>
    <el-tag v-if="dirty" type="warning">存在未保存的设备修改，请先保存再导出</el-tag>
   </div>
   <div v-if="packagePreview" class="device-import-preview">
    <strong>导入预览：{{packagePreview.device_name}}</strong>
    <p class="muted">模式：{{packagePreview.stage_mode==='auto'?'三工位自动识别':'手动工位'}} · 视频源：{{packagePreview.source_kind}} · 已标定：{{packagePreview.calibrated_stages.join('、')||'无'}}</p>
    <div class="device-reference-counts">
     <span v-for="key in ['P1','P2','P3']" :key="key">{{key}}：{{count(packagePreview,key)}} 张截图</span>
    </div>
    <el-alert v-if="!packagePreview.has_complete_auto_references" type="warning" :closable="false" title="参考图尚未全部达到每个工位 5 张，自动识别会返回 UNKNOWN"/>
    <el-alert v-if="packagePreview.source_reselection_required" type="info" :closable="false" title="本地视频不随 ZIP 打包，导入后需要重新选择视频文件"/>
    <div class="device-package-actions" style="margin-top:12px">
     <el-button type="primary" :disabled="monitorRunning||packageBusy" :loading="packageBusy" @click="importPackage">确认导入并切换设备</el-button>
     <el-button @click="packagePreview=null;selectedPackage=''">取消</el-button>
    </div>
   </div>
   <el-alert v-if="monitorRunning" type="warning" :closable="false" title="正在执行正式监测。请先停止监测，再导入其他设备的配置。"/>
  </section>

  <section class="panel">
   <div class="panel-title">视频输入源与工位模式</div>
   <el-form label-width="120px">
    <el-form-item label="源类型"><el-select v-model="draft.source.kind" style="width:100%">
     <el-option label="本地视频文件" value="file"/><el-option label="USB 摄像头" value="usb"/>
     <el-option label="RTSP 网络流" value="rtsp"/><el-option label="工业相机（预留）" value="industrial"/>
    </el-select></el-form-item>
    <el-form-item v-if="draft.source.kind==='file'||draft.source.kind==='rtsp'" label="视频源地址">
     <el-input v-model="draft.source.address" placeholder="本地文件路径或 RTSP URL">
      <template #append><el-button v-if="draft.source.kind==='file'" @click="chooseVideo">浏览</el-button></template>
     </el-input>
    </el-form-item>
    <el-form-item v-else-if="draft.source.kind==='usb'" label="设备索引">
     <el-input-number v-model="draft.source.device_index" :min="0"/>
    </el-form-item>
    <el-alert v-if="(draft.source.kind==='file'||draft.source.kind==='rtsp')&&!draft.source.address.trim()"
     title="尚未设置视频源；选择视频或填写 RTSP 地址后保存。" type="warning" show-icon :closable="false"/>
    <el-form-item label="工位识别" style="margin-top:14px">
     <el-radio-group v-model="draft.stage_mode"><el-radio value="single">手动指定</el-radio><el-radio value="auto">截图对比自动选 ROI</el-radio></el-radio-group>
    </el-form-item>
    <el-form-item v-if="draft.stage_mode==='single'" label="当前工位">
     <el-radio-group v-model="draft.selected_stage"><el-radio-button value="P1">P1</el-radio-button><el-radio-button value="P2">P2</el-radio-button><el-radio-button value="P3">P3</el-radio-button></el-radio-group>
    </el-form-item>
    <el-alert v-if="draft.source.kind==='industrial'" type="warning" :closable="false" title="工业相机 SDK 尚未配置，当前不支持连接"/>
    <el-alert v-if="draft.stage_mode==='auto'" type="info" :closable="false" title="请在「标定与调试」分别采集 P1/P2/P3 至少 5 张参考截图。"/>
   </el-form>
  </section>
  <div class="device-settings-footer">
   <span class="muted">设备名称、输入源和工位设置随 ZIP 一起保存。RTSP 地址可能包含连接凭据，请妥善保管导出的 ZIP。</span>
   <el-button type="primary" size="large" :loading="saving" :disabled="!dirty||saving||monitorRunning" @click="save">保存设备配置</el-button>
  </div>
 </div>
 <el-empty v-else description="等待后端加载配置"/>
</template>
