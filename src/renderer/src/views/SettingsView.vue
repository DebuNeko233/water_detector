<script setup lang="ts">
import {ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {useSystemStore} from '../stores/system'
import {commitConfig,type AppConfig} from '../api/client'
const store=useSystemStore();const draft=ref<AppConfig|null>(null)
watch(()=>store.config,c=>{if(c)draft.value=JSON.parse(JSON.stringify(c))},{immediate:true})
async function save(){if(!draft.value)return;try{store.config=await commitConfig(draft.value);ElMessage.success('保存成功')}catch(e){ElMessage.error(String(e))}}
async function chooseVideo(){const p=await window.desktop?.system.selectFile();if(p&&draft.value){draft.value.source.kind='file';draft.value.source.address=p}}
</script>
<template><div class="panel" style="max-width:720px" v-if="draft"><div class="panel-title">视频输入源</div><el-form label-width="130px">
  <el-form-item label="源类型"><el-select v-model="draft.source.kind" style="width:100%"><el-option label="本地 MP4" value="file"/><el-option label="USB 摄像头" value="usb"/><el-option label="RTSP 网络流" value="rtsp"/><el-option label="工业相机（预留）" value="industrial"/></el-select></el-form-item>
  <el-form-item v-if="draft.source.kind==='file'||draft.source.kind==='rtsp'" label="视频源地址"><el-input v-model="draft.source.address" placeholder="视频文件完整路径或 RTSP URL"><template #append><el-button v-if="draft.source.kind==='file'" @click="chooseVideo">浏览</el-button></template></el-input></el-form-item>
  <el-form-item v-else-if="draft.source.kind==='usb'" label="设备索引"><el-input-number v-model="draft.source.device_index" :min="0"/></el-form-item>
  <el-form-item label="工位识别"><el-radio-group v-model="draft.stage_mode"><el-radio value="single">单工位手动指定</el-radio><el-radio value="auto">自动识别（实验性）</el-radio></el-radio-group></el-form-item>
  <el-form-item v-if="draft.stage_mode==='single'" label="当前工位"><el-radio-group v-model="draft.selected_stage"><el-radio-button value="P1">P1</el-radio-button><el-radio-button value="P2">P2</el-radio-button><el-radio-button value="P3">P3</el-radio-button></el-radio-group></el-form-item>
  <el-alert v-if="draft.source.kind==='industrial'" title="需确定厂商相机 SDK 后提供适配器；该类型当前不支持连接" type="warning" :closable="false"/>
  <el-alert v-if="draft.stage_mode==='auto'" title="自动工位识别为实验性功能，未标定三个位置的参考图时会输出 UNKNOWN/PAUSED" type="warning" :closable="false" style="margin-top:12px"/>
  <el-form-item style="margin-top:18px"><el-button type="primary" @click="save">保存配置</el-button></el-form-item>
 </el-form></div></template>
