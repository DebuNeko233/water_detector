/** 冒烟测试复用的等待和进程工具；父进程异常退出测试绝不能顺便杀掉 Python。 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import { setTimeout as pause } from 'node:timers/promises'

export async function waitForJsonFile(filename, { child, timeoutMs, label, details = () => '' }) {
  const deadline = Date.now() + timeoutMs
  // 子进程提前退出则立即失败，不再无意义地等待就绪文件。
  while (Date.now() < deadline) {
    if (child && child.exitCode !== null) {
      throw new Error(label + ': child exited before reporting. ' + details())
    }
    if (fs.existsSync(filename)) return JSON.parse(fs.readFileSync(filename, 'utf8'))
    await pause(150)
  }
  throw new Error(label + ': timed out. ' + details())
}

export function isWindowsPidAlive(pid) {
  if (process.platform !== 'win32') throw new Error('Windows-only process check')
  if (!Number.isInteger(pid) || pid <= 0) throw new Error('Invalid process ID')
  // PowerShell Get-Process 用于判断给定 PID 是否仍有存活进程。
  const result = spawnSync('powershell.exe', [
    '-NoProfile', '-NonInteractive', '-Command',
    'if (Get-Process -Id ' + pid + ' -ErrorAction SilentlyContinue) { exit 1 }',
  ], { stdio: 'ignore', timeout: 5_000 })
  return result.status !== 0
}

export function killWindowsPid(pid, { tree = false } = {}) {
  if (process.platform !== 'win32') throw new Error('Windows-only process kill')
  if (!Number.isInteger(pid) || pid <= 0) throw new Error('Invalid process ID')
  // tree=false 只杀 Electron；tree=true 用于失败时彻底清理测试进程树。
  return spawnSync('taskkill', [
    '/PID', String(pid), ...(tree ? ['/T'] : []), '/F',
  ], { stdio: 'ignore', timeout: 5_000 })
}
