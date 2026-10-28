import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const file = path => readFileSync(new URL('../../' + path, import.meta.url), 'utf8')

test('file playback and live tuning live only on the calibration and debug page', () => {
  const debug = file('src/renderer/src/views/DebugView.vue')
  const monitor = file('src/renderer/src/views/MonitorView.vue')
  assert.match(debug, /视频播放与算法测试/)
  assert.match(debug, /\/api\/playback\/start/)
  assert.match(debug, /\/api\/playback\/seek/)
  assert.match(debug, /\/api\/playback\/update/)
  assert.match(debug, /schedulePreview\(\)/)
  assert.match(debug, /showHandles/)
  assert.match(debug, /v-if="showHandles"[^>]+@pointerdown=/)
  assert.match(debug, /v-model="seekFrame"/)
  assert.doesNotMatch(monitor, /\/api\/playback\//)
  assert.doesNotMatch(monitor, /monitor-timeline/)
})

test('parameter updates preserve the original detection core', () => {
  const playback = file('backend/src/water_detector/task/file_playback.py')
  assert.match(playback, /def update\(self, request: PlaybackRequest\)/)
  assert.match(playback, /self\._pending_seek = current/)
  assert.doesNotMatch(playback.slice(playback.indexOf('def update('), playback.indexOf('def pause(')), /self\._paused = False/)
})
