import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = path => readFileSync(new URL('../../' + path, import.meta.url), 'utf8')

test('desktop ZIP workflows use dedicated dialogs and authorized file paths', () => {
  const main = source('src/main/index.ts')
  const preload = source('src/preload/index.ts')
  const types = source('src/shared/bridge.ts')
  for (const operation of ['open-device-package','save-device-package']) {
    assert.match(main, new RegExp('system:' + operation))
    assert.match(preload, new RegExp('system:' + operation))
  }
  assert.match(main, /authorizedImportPackages/)
  assert.match(main, /authorizedExportPackages/)
  assert.match(main, /Device package path must be selected through the desktop dialog/)
  assert.match(types, /openDevicePackage/)
  assert.match(types, /saveDevicePackage/)
})

test('device settings requires save-before-export and import preview confirmation', () => {
  const settings = source('src/renderer/src/views/SettingsView.vue')
  for (const operation of ['/api/device-package/export','/api/device-package/preview','/api/device-package/import']) {
    assert.ok(settings.includes(operation), operation)
  }
  assert.match(settings, /ElMessageBox.confirm/)
  assert.match(settings, /store.config=result.config/)
  assert.match(settings, /导出当前设备 ZIP/)
  assert.match(settings, /确认导入并切换设备/)
  assert.match(settings, /device_name/)
  assert.match(settings, /!dirty/)
  assert.match(settings, /monitorRunning/)
})

test('seek only changes frame, not paused/playing intent', () => {
  const player = source('backend/src/water_detector/task/file_playback.py')
  const seek = player.slice(player.indexOf('def seek('),player.indexOf('def update('))
  assert.match(seek, /self\._pending_seek = frame_index/)
  assert.doesNotMatch(seek, /self\._paused\s*=\s*False/)
  const debug = source('src/renderer/src/views/DebugView.vue')
  assert.match(debug, /暂停时拖动或逐帧跳转仍保持暂停/)
  assert.match(debug, /seekTarget/)
  assert.match(debug, /正在跳转到第/)
})
