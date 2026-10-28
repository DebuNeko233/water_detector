/** Development launcher: Electron is the ONLY owner of its Python process.
 * First-run uv sync prepares backend/.venv; no second Python terminal or port race.
 * pnpm dev:backend and pnpm dev:ui-only are independent debugging tools.
 */
import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ensureUv } from './uv-bootstrap.mjs'
import { resolveProcessCommand } from './lib/process-command.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backend = path.join(root, 'backend')
const python = path.join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const combined = process.argv.includes('--combined')
let uv
try {
  uv = await ensureUv()
  // uv sync is incremental: this also repairs stale .venv environments
  // left by older repository layouts before Electron attempts to start Python.
  console.info('[dev] Checking Python dependencies...')
  const setup = spawnSync(uv, ['sync', '--project', backend, '--group', 'dev'], {
    cwd: root, stdio: 'inherit', windowsHide: true, shell: false,
  })
  if (setup.error || setup.status !== 0) throw new Error('Python setup failed: ' + (setup.error?.message ?? setup.status))
  if (!fs.existsSync(python)) throw new Error('uv completed but Python executable is missing: ' + python)
} catch (error) {
  console.error('[dev]', error)
  process.exit(1)
}

const runner = (process.env.npm_config_user_agent ?? '').startsWith('pnpm/') ? 'pnpm' : 'npm'
const command = resolveProcessCommand(runner, ['run', 'dev:frontend'])
const electron = spawn(command.command, command.args, {
  cwd: root,
  shell: false,
  windowsHide: true,
  stdio: 'inherit',
  env: {
    ...process.env,
    // The template's dev:frontend runs Electron, whose BackendManager now starts Python.
    WAFER_USE_EXTERNAL_BACKEND: '0',
    WAFER_DEBUG_CONSOLE: combined ? '1' : (process.env.WAFER_DEBUG_CONSOLE ?? '0'),
  },
})
let stopping = false
function close() {
  if (stopping) return
  stopping = true
  // Electron first gets a graceful exit whenever possible; its own
  // before-quit and Python parent watchdog handle backend cleanup.
  if (electron.pid && electron.exitCode === null) electron.kill('SIGTERM')
}
electron.on('error', error => { console.error('[dev] Frontend failed:', error); process.exitCode = 1; close() })
electron.on('exit', (code, signal) => {
  if (!stopping && code !== 0 && signal !== 'SIGINT' && signal !== 'SIGTERM') process.exitCode = code || 1
})
process.once('SIGINT', close)
process.once('SIGTERM', close)
console.info('[dev] Electron controls its private Python backend; use --combined for visible diagnostics.')
