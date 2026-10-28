import assert from 'node:assert/strict'
import test from 'node:test'
import { reactive } from 'vue'
import { serializeBody } from '../../src/renderer/src/api/payload.mjs'

test('reactive video configuration can cross Electron IPC after JSON normalization', () => {
  const config = reactive({
    version: 1,
    source: { kind: 'file', address: String.raw`C:\Users\Example\Desktop\洗片视频\9月30日-2.mp4`, device_index: 0 },
    detector: { diff_threshold: 8, s_water_threshold: 0.01 },
    calibrations: { P1: { cx: 300, cy: 200, radius: 150, reference_width: 720, reference_height: 576 } },
  })
  // This reproduces the user's original "An object could not be cloned" error.
  assert.throws(() => structuredClone(config), { name: 'DataCloneError' })
  const { json, plain } = serializeBody(config)
  assert.equal(json, JSON.stringify(config))
  assert.doesNotThrow(() => structuredClone(plain))
  assert.deepEqual(plain, JSON.parse(json))
  assert.equal(plain.source.address, config.source.address)
  config.calibrations.P1.cx = 322
  assert.equal(plain.calibrations.P1.cx, 300)
})

test('no-body and null JSON request payloads remain distinct', () => {
  assert.deepEqual(serializeBody(undefined), { json: undefined, plain: undefined })
  assert.deepEqual(serializeBody(null), { json: 'null', plain: null })
})

test('nested reactive preview parameters also survive IPC cloning', () => {
  const source = reactive({ kind: 'file', address: 'sample.mp4', device_index: 0 })
  const detector = reactive({ diff_threshold: 8, history_seconds: 0.5 })
  const payload = reactive({ source, detector, frame_index: 30, stage: 'P1' })
  const { plain } = serializeBody(payload)
  assert.doesNotThrow(() => structuredClone(plain))
  assert.deepEqual(plain, { source: {kind:'file',address:'sample.mp4',device_index:0},
    detector: {diff_threshold:8,history_seconds:0.5},frame_index:30,stage:'P1' })
})
