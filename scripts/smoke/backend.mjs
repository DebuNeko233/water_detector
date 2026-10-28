/**
 * 独立验证发行包中携带的 CPython，不借用开发机 .venv 或系统 Python。
 * 检查鉴权、HTTP、WebSocket、停机响应和日志是否正常。
 */

import { spawn } from 'node:child_process'
import { randomBytes, randomUUID } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { setTimeout as pause } from 'node:timers/promises'
import WebSocket from 'ws'
import { waitForJsonFile, killWindowsPid } from './helpers.mjs'

const release = path.resolve('dist/win-unpacked/resources/backend')
const manifest = JSON.parse(fs.readFileSync(path.join(release, 'manifest.json'), 'utf8'))
const executable = path.join(release, manifest.python)
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-backend-smoke-'))
const readyFile = path.join(temporary, 'ready.json')
const token = randomBytes(32).toString('hex')
const controlToken = randomBytes(32).toString('hex')
const sessionId = randomUUID()

// 尽量模拟 Electron 的托管启动环境，测试的是真实发行版运行契约。
const child = spawn(executable, ['-I', '-m', 'sample_backend'], {
  cwd: path.dirname(executable),
  windowsHide: true,
  stdio: ['ignore', 'ignore', 'pipe'],
  env: {
    ...process.env,
    APP_PARENT_PID: String(process.pid),
    APP_SESSION_ID: sessionId,
    APP_SESSION_TOKEN: token,
    APP_CONTROL_TOKEN: controlToken,
    APP_READY_FILE: readyFile,
    APP_DATA_DIR: path.join(temporary, 'data'),
  },
})

let errors = ''
child.stderr.on('data', data => { errors += data.toString() })

async function waitReady() {
  const ready = await waitForJsonFile(readyFile, {
    child, timeoutMs: 25_000, label: 'Python startup', details: () => errors,
  })
  if (ready.pid !== child.pid || ready.session_id !== sessionId) {
    throw new Error('Mismatched Python readiness handshake')
  }
  return ready.port
}

async function checkWebSocket(url) {
  const socket = new WebSocket(url, { headers: { 'x-session-token': token } })
  try {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('WebSocket did not connect')), 10_000)
      socket.once('message', data => {
        clearTimeout(timeout)
        const event = JSON.parse(data.toString())
        if (event.event !== 'system.connected') reject(new Error('Bad first WS event'))
        else resolve()
      })
      socket.once('error', reject)
    })
  } finally {
    socket.close()
  }
}

async function test() {
  const port = await waitReady()
  const base = 'http://127.0.0.1:' + port
  const health = await fetch(base + '/api/health', {
    headers: { 'x-session-token': token },
  })
  if (!health.ok || (await health.json()).status !== 'ready') {
    throw new Error('Packaged Python health check failed')
  }
  // 发行版业务接口必须拒绝不带会话令牌的请求。
  const unauthorized = await fetch(base + '/api/health')
  if (unauthorized.status !== 401) throw new Error('Missing session authentication')

  const echo = await fetch(base + '/api/echo', {
    method: 'POST',
    headers: { 'x-session-token': token, 'content-type': 'application/json' },
    body: JSON.stringify({ message: 'offline release' }),
  })
  if ((await echo.json()).message !== 'offline release') {
    throw new Error('Packaged HTTP echo failed')
  }
  await checkWebSocket(base.replace('http:', 'ws:') + '/ws/events')

  // 与业务会话令牌不同，停机接口必须使用专用控制令牌。
  const shutdown = await fetch(base + '/internal/shutdown', {
    method: 'POST',
    headers: { 'x-control-token': controlToken },
  })
  if (!shutdown.ok) throw new Error('Graceful backend shutdown request failed')
  const deadline = Date.now() + 10_000
  while (child.exitCode === null && Date.now() < deadline) await pause(100)
  if (child.exitCode !== 0) throw new Error('Python did not exit cleanly: ' + errors)
  if (!fs.existsSync(path.join(temporary, 'data', 'logs', 'backend.log'))) {
    throw new Error('Backend log file was not created')
  }
  console.log('Packaged Python smoke test passed: HTTP, WebSocket, auth, shutdown, logs')
}

try {
  await test()
} finally {
  // 即使验证失败也必须清理启动的 Python，避免 CI 残留后台进程。
  if (child.exitCode === null) {
    if (process.platform === 'win32' && child.pid) {
      killWindowsPid(child.pid, { tree: true })
    } else {
      child.kill('SIGKILL')
    }
  }
  fs.rmSync(temporary, { recursive: true, force: true })
}
