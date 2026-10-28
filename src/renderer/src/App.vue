<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {useSystemStore} from './stores/system'
import MonitorView from './views/MonitorView.vue'
import DebugView from './views/DebugView.vue'
import RecordsView from './views/RecordsView.vue'
import SettingsView from './views/SettingsView.vue'
const tab=ref('monitor')
const store=useSystemStore()
onMounted(async()=>{try{await store.loadConfig()}catch(e){console.error(e)}store.connect()})
</script>
<template>
 <div class="app-shell">
  <aside class="sidebar"><div class="logo"><span class="logo-mark">W</span><div><strong>晶圆洗片检测</strong><small>VISION INSPECTION</small></div></div>
   <nav aria-label="主导航">
    <button :class="['nav-btn',{selected:tab==='monitor'}]" @click="tab='monitor'">▣ &nbsp; 实时监测</button>
    <button :class="['nav-btn',{selected:tab==='debug'}]" @click="tab='debug'">◎ &nbsp; 标定与调试</button>
    <button :class="['nav-btn',{selected:tab==='records'}]" @click="tab='records'">▤ &nbsp; 检测记录</button>
    <button :class="['nav-btn',{selected:tab==='settings'}]" @click="tab='settings'">⚙ &nbsp; 设备与系统</button>
   </nav>
   <div class="sidebar-footer"><span :class="['dot',{on:store.connected}]"></span>{{store.connected?'后端已连接':'等待 Python 后端'}}<small>检测判定仅在 Python 运行</small></div>
  </aside>
  <div class="main-area"><header class="topbar"><div><strong>{{({monitor:'实时监测',debug:'标定与调试',records:'检测记录',settings:'设备与系统'} as Record<string,string>)[tab]}}</strong><span> / Wafer Water Detector</span></div><span class="version">V0.1 · 开发版本</span></header>
   <main class="content"><MonitorView v-if="tab==='monitor'"/><DebugView v-else-if="tab==='debug'"/><RecordsView v-else-if="tab==='records'"/><SettingsView v-else/></main>
  </div>
 </div>
</template>
