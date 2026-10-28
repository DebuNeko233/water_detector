/** Start Python and Electron together without opening a second console window.
 * For independent debugging use pnpm dev:backend or pnpm dev:frontend.
 */
import { spawn, spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ensureUv } from './uv-bootstrap.mjs'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
try { await ensureUv() } catch (error) { console.error(error); process.exit(1) }
const showLogs = process.argv.includes('--combined') || process.env.WAFER_DEBUG_CONSOLE === '1'
let stopping = false
let backend = null
let frontend = null
const children = []
function launch(script, quiet = false) {
  const windows = process.platform === 'win32'
  const command = windows ? 'cmd.exe' : 'npm'
  const args = windows ? ['/d', '/s', '/c', 'npm run ' + script] : ['run', script]
  const child = spawn(command, args, {
    cwd: root, shell: false, windowsHide: true, stdio: quiet ? 'ignore' : 'inherit',
  })
  children.push(child)
  child.on('error', (error) => { console.error('[dev]', script, error); stop(1) })
  child.on('exit', (code) => { if (!stopping) stop(code === 0 ? 0 : (code || 1)) })
  return child
}
function stop(code = 0) {
  if (stopping) return
  stopping = true
  process.exitCode = code
  for (const child of children) {
    if (!child.pid || child.exitCode !== null) continue
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
    } else {
      child.kill('SIGTERM')
    }
  }
}
process.once('SIGINT', () => stop(0))
process.once('SIGTERM', () => stop(0))
backend = launch('dev:backend', !showLogs)
frontend = launch('dev:frontend', false)
console.log('[dev] Electron + Python started. The Python backend is hidden unless --combined is used.')
