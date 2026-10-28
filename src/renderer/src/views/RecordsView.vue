<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {request} from '../api/client'
const rows=ref<{task_id:string;csv:string;video:string}[]>([])
const error=ref('')
onMounted(async()=>{try{rows.value=await request('/api/records')}catch(e){error.value=String(e)}})
</script>
<template><div class="panel"><div class="panel-title">检测任务结果</div><el-alert v-if="error" :title="error" type="warning"/><el-table :data="rows" stripe><el-table-column prop="task_id" label="任务 ID" min-width="280"/><el-table-column prop="csv" label="CSV 路径" min-width="280"/><el-table-column prop="video" label="标注视频路径" min-width="280"/></el-table><p class="hint">当前版本的记录由 Python 后端输出到本机数据目录；下一阶段增加事件截图、筛选与文件打开操作。</p></div></template>
