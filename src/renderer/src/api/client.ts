import { serializeBody } from './payload.mjs'
export const BASE = 'http://127.0.0.1:8765'

export interface DetectorConfig {
  inner_radius_ratio:number; outer_radius_ratio:number; diff_threshold:number
  history_seconds:number; persistence_threshold:number; s_water_threshold:number
  ng_hold_seconds:number; recover_hold_seconds:number; sector_count:number
  sector_threshold:number; blur_ksize:number; nozzle_filter_enabled:boolean
}
export interface Calibration { cx:number; cy:number; radius:number; reference_width:number; reference_height:number }
export interface SourceConfig {kind:'file'|'usb'|'rtsp'|'industrial'; address:string; device_index:number}
export interface AppConfig {
  version:number; detector:DetectorConfig; calibrations:Record<string,Calibration>
  source:SourceConfig; stage_mode:'single'|'auto'; selected_stage:'P1'|'P2'|'P3'
  position_references:Record<string,string>
}
export interface MonitorState {
  state:string; status:string; task_id?:string; stage?:string; s_water?:number
  timestamp_s?:number; frame_id?:number; abnormal_run_s?:number; max_sector_idx?:number
  history?:number[]; processed_frames?:number; reason?:string; position_confidence?:number
}
export async function request<T>(path:string, method='GET', body?:unknown):Promise<T> {
  const { json, plain } = serializeBody(body)
  if (typeof window !== 'undefined' && window.desktop?.backend) {
    if (method !== 'GET' && method !== 'POST') throw new Error('Unsupported API method')
    return window.desktop.backend.request<T>(method, path, plain)
  }
  const response=await fetch(BASE+path,{method,headers:{'Content-Type':'application/json'},body:json})
  if(!response.ok){const e=await response.text();throw new Error(e)}
  return response.json() as Promise<T>
}
export const getConfig=()=>request<AppConfig>('/api/config/active')
export const commitConfig=(v:AppConfig)=>request<AppConfig>('/api/config/commit','POST',v)
export const startMonitor=()=>request<MonitorState>('/api/monitor/start','POST')
export const stopMonitor=()=>request<MonitorState>('/api/monitor/stop','POST')
export const mjpegUrl=BASE+'/api/monitor/preview.mjpg'
