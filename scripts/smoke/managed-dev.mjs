/** Exercise development .venv in the exact mode used by BackendManager.
 * No fixed 8765 port, no uvicorn --reload process, no console window.
 */
import { spawn } from 'node:child_process'
import { randomBytes, randomUUID } from 'node:crypto'
import { setTimeout as pause } from 'node:timers/promises'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
const root = path.resolve('backend')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
if (!fs.existsSync(python)) throw new Error('Missing dev venv: run pnpm setup first')
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wafer-dev-smoke-'))
const ready = path.join(tmp, 'ready.json')
const token = randomBytes(16).toString('hex')
const control = randomBytes(16).toString('hex')
const session = randomUUID()
let stderr = ''
const child = spawn(python, ['-I', '-m', 'sample_backend'], {
  cwd: path.dirname(python), shell: false, windowsHide: true,
  stdio: ['ignore', 'ignore', 'pipe'],
  env: {
    ...process.env, APP_PARENT_PID: String(process.pid),
    APP_SESSION_ID: session, APP_SESSION_TOKEN: token,
    APP_CONTROL_TOKEN: control, APP_READY_FILE: ready,
    APP_DATA_DIR: path.join(tmp, 'data'),
  },
})
child.stderr.on('data', b => { stderr += String(b) })
const deadline = Date.now() + 25_000
try {
  let message = null
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error('Python died during startup: ' + stderr)
    if (fs.existsSync(ready)) {
      message = JSON.parse(fs.readFileSync(ready, 'utf8'))
      break
    }
    await pause(100)
  }
  if (!message || (process.platform !== 'win32' && message.pid !== child.pid) || message.session_id !== session)
    throw new Error('Bad ready-file handshake: ' + JSON.stringify(message) + ' ' + stderr)
  const base = 'http://127.0.0.1:' + message.port
  const unauthorized = await fetch(base + '/api/config/active')
  if (unauthorized.status !== 401) throw new Error('API exposed without session authentication')
  const headers = { 'x-session-token': token }
  const health = await fetch(base + '/api/health', { headers })
  if (!health.ok || !(await health.json()).ok) throw new Error('Wafer health check failed')
  const cfg = await fetch(base + '/api/config/active', { headers })
  if (!cfg.ok || !(await cfg.json()).detector) throw new Error('Original wafer business routes not available')
  const stopping = await fetch(base + '/internal/shutdown', {
    method: 'POST', headers: { 'x-control-token': control },
  })
  if (!stopping.ok) throw new Error('Graceful shutdown request failed')
  const stopDeadline = Date.now() + 10_000
  while (child.exitCode === null && Date.now() < stopDeadline) await pause(100)
  if (child.exitCode !== 0) throw new Error('Backend did not exit cleanly: ' + stderr)
  console.log('Development Python smoke passed: dynamic port, token, wafer API, shutdown')
} finally {
  if (child.exitCode === null) {
    if (process.platform === 'win32' && child.pid) {
      const { spawnSync } = await import('node:child_process')
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
    } else child.kill('SIGKILL')
  }
  fs.rmSync(tmp, { recursive: true, force: true })
}
