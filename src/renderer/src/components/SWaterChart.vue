<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{values:number[];threshold:number}>()
const width=620,height=144,pad=23
const ymax=computed(()=>Math.max(0.05,props.threshold*3, ...props.values.map(x=>x*1.15)))
const points=computed(()=>props.values.map((v,i)=>`${pad+(width-2*pad)*i/Math.max(1,props.values.length-1)},${height-pad-Math.min(v,ymax.value)/ymax.value*(height-2*pad)}`).join(' '))
const lineY=computed(()=>height-pad-props.threshold/ymax.value*(height-2*pad))
</script>
<template>
 <div class="chart-box"><svg :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" style="width:100%;height:160px" role="img" aria-label="S_water 5秒历史和阈值线">
   <rect x="23" y="12" width="574" height="109" fill="#f7fafc" stroke="#dfe6ef"/>
   <line v-for="i in 3" :key="i" x1="23" x2="597" :y1="12+i*27" :y2="12+i*27" stroke="#e6edf2"/>
   <line x1="23" x2="597" :y1="lineY" :y2="lineY" stroke="#e29d4b" stroke-width="2" stroke-dasharray="7 5"/>
   <polyline v-if="values.length>1" :points="points" fill="none" stroke="#298bd3" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
   <text x="27" y="10" fill="#8799ab" font-size="12">{{ymax.toFixed(3)}}</text>
   <text x="28" y="138" fill="#8799ab" font-size="12">0</text>
   <text x="510" y="138" fill="#8799ab" font-size="12">最近 5 秒</text>
   <text x="430" :y="Math.max(13,lineY-7)" fill="#c28b44" font-size="11">阈值 {{threshold.toFixed(3)}}</text>
 </svg></div>
</template>
