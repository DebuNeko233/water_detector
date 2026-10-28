/**
 * 启动真实的 win-unpacked/WaferWaterDetector.exe，覆盖 Electron 启动及其管理的 Python。
 * 不与独立 Python 冒烟测试合并，因为这里重点验证进程所有权。
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { isWindowsPidAlive } from './helpers.mjs'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

if (process.platform !== 'win32') {
  throw new Error('Run the packaged Electron smoke test on Windows')
}

const executable = path.resolve('dist/win-unpacked/WaferWaterDetector.exe')
assert.ok(fs.existsSync(executable), 'Packaged WaferWaterDetector.exe was not built')

const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-electron-smoke-'))
const reportPath = path.join(folder, 'report.json')

function launch() {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, ['--smoke-test'], {
      windowsHide: true,
      stdio: 'ignore',
      env: { ...process.env, APP_SMOKE_REPORT: reportPath },
    })
    const timeout = setTimeout(() => {
      child.kill()
      reject(new Error('Packaged Electron did not exit within 45 seconds'))
    }, 45_000)

    child.once('error', (error) => {
      clearTimeout(timeout)
      reject(error)
    })
    child.once('exit', (code) => {
      clearTimeout(timeout)
      resolve(code ?? -1)
    })
  })
}

try {
  const exitCode = await launch()
  assert.ok(fs.existsSync(reportPath), 'Electron did not produce the smoke test report')
  const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'))
  assert.equal(exitCode, 0, 'Electron executable exited with failure')
  assert.equal(report.exitCode, 0, 'Electron reported a failed backend lifecycle')
  assert.ok(Number.isInteger(report.pythonPid) && report.pythonPid > 0)

  // Electron 正常退出后，它自己创建的 Python 必须被正确清理。
  assert.equal(isWindowsPidAlive(report.pythonPid), false, 'A managed Python process was left running')
  console.log('Packaged Electron smoke passed: launched Python, HTTP API, clean process exit')
} finally {
  fs.rmSync(folder, { force: true, recursive: true })
}
