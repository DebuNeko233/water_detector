'use strict'
/** Run `pnpm desktop`: start Vite, wait for HTTP readiness, then open Electron.
 * Windows-compatible process cleanup; the Electron main process owns Python.
 */
const { spawn } = require('node:child_process')
const path = require('node:path')
const http = require('node:http')
const fs = require('node:fs')
const frontend = path.resolve(__dirname, '../frontend')
const viteCli = path.join(frontend, 'node_modules/vite/bin/vite.js')
const electronExe = path.join(frontend, 'node_modules/electron/dist', process.platform === 'win32' ? 'electron.exe' : 'electron')
let vite = null
let electron = null
let shuttingDown = false

function exists(p) { return fs.existsSync(p) }
function waitForVite() {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + 30000
    function check() {
      if (Date.now() >= deadline || vite.exitCode !== null) {
        reject(new Error('Vite 启动失败或等待超时，请检查 5173 端口及 Vite 日志。'))
        return
      }
      const req = http.get('http://127.0.0.1:5173/', { timeout: 800 }, r => {
        r.resume()
        if (r.statusCode === 200) resolve()
        else setTimeout(check, 250)
      })
      req.on('error', () => setTimeout(check, 250))
      req.on('timeout', () => req.destroy())
    }
    check()
  })
}
function endProcessTree(child) {
  if (!child || !child.pid) return
  if (process.platform === 'win32') {
    const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
    killer.on('error', () => {})
  } else child.kill('SIGTERM')
}
function cleanup() {
  if (shuttingDown) return
  shuttingDown = true
  // Electron receives graceful app.quit when its last window closes.
  // On Ctrl+C, terminate Electron and its Python child before ending Vite.
  endProcessTree(electron)
  endProcessTree(vite)
}
process.on('SIGINT', () => { cleanup(); process.exit(130) })
process.on('SIGTERM', () => { cleanup(); process.exit(143) })
process.on('exit', cleanup)
async function main() {
  if (!exists(viteCli)) throw new Error('Vite 未安装。请在 frontend 目录先运行 pnpm install。')
  if (!exists(electronExe)) throw new Error('Electron 未安装。请在 frontend 目录先运行 pnpm install。')
  console.log('[Desktop] 正在启动 Vite (http://127.0.0.1:5173) ...')
  vite = spawn(process.execPath, [viteCli, '--host', '127.0.0.1', '--port', '5173', '--strictPort'], { cwd: frontend, stdio: 'inherit', windowsHide: true })
  await waitForVite()
  console.log('[Desktop] Vite 已就绪，正在启动 Electron（Python 将自动启动）...')
  electron = spawn(electronExe, [path.resolve(__dirname, '../electron')], { cwd: frontend, env: process.env, stdio: 'inherit', windowsHide: true })
  electron.on('error', error => { console.error(error); cleanup(); process.exitCode = 1 })
  electron.on('exit', code => { cleanup(); process.exitCode = code || 0 })
}
main().catch(error => { console.error('[Desktop]', error.message); cleanup(); process.exitCode = 1 })
