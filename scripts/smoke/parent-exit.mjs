/**
 * 只强制终止 Electron 而不递归终止子进程，验证 Python 看门狗能自行退出。
 * 如果使用 taskkill /T，Python 被外部杀死会掩盖看门狗逻辑失败。
 */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { waitForJsonFile, isWindowsPidAlive, killWindowsPid } from './helpers.mjs'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { setTimeout as pause } from 'node:timers/promises'

if (process.platform !== 'win32') throw new Error('Windows-only test')

const executable = path.resolve('dist/win-unpacked/WaferWaterDetector.exe')
assert.ok(fs.existsSync(executable), 'WaferWaterDetector.exe is missing')

const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-parent-exit-'))
const report = path.join(folder, 'backend.json')
const electron = spawn(executable, ['--smoke-parent-exit'], {
  windowsHide: true,
  stdio: 'ignore',
  env: { ...process.env, APP_SMOKE_REPORT: report },
})

let pythonPid
try {
  const result = await waitForJsonFile(report, {
    child: electron, timeoutMs: 30_000, label: 'Electron Python PID report',
  })
  pythonPid = result.pythonPid
  assert.ok(Number.isInteger(pythonPid) && pythonPid > 0)
  assert.ok(isWindowsPidAlive(pythonPid), 'Python was not running before Electron was killed')

  // 故意不传 tree=true，保证测试实际观察的是 Python 自己的退出机制。
  assert.equal(killWindowsPid(electron.pid).status, 0, 'Cannot force-kill Electron')
  const deadline = Date.now() + 20_000
  while (isWindowsPidAlive(pythonPid) && Date.now() < deadline) {
    await pause(250)
  }
  assert.equal(isWindowsPidAlive(pythonPid), false, 'Python survived the forced Electron exit')
  console.log('Parent watcher passed: Python exited after Electron was force-killed')
} finally {
  if (electron.exitCode === null && electron.pid) killWindowsPid(electron.pid)
  if (pythonPid && isWindowsPidAlive(pythonPid)) killWindowsPid(pythonPid)
  fs.rmSync(folder, { force: true, recursive: true })
}
