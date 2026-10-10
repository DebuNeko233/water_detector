/** Electron ↔ Python integration only. The water detection algorithm stays in Python. */
import { app, BrowserWindow, dialog, ipcMain } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { RuntimeProcessManager } from './runtime-process-manager.mjs'

const dir = path.dirname(fileURLToPath(import.meta.url))
const rendererDist = path.resolve(dir, '../dist')
const viteDevUrl = process.env.VITE_DEV_SERVER_URL
const port = Number(process.env.WAFER_PORT || '8765')
let window: BrowserWindow | null = null
let quitInProgress = false
let mayQuit = false
const logs: string[] = []

function log(message: unknown) {
  for (const line of String(message).split(/\r?\n/)) {
    if (!line.trim()) continue
    logs.push(line)
    if (logs.length > 150) logs.shift()
    if (process.env.WAFER_DEBUG_CONSOLE === '1') console.info('[Wafer Python]', line)
  }
}

function localRequest(method: string, endpoint: string, token = ''): Promise<{status: number, body: any}> {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1', port, method, path: endpoint,
      timeout: 1000,
      headers: token ? { 'X-Wafer-Control-Token': token } : {},
    }, response => {
      let content = ''
      response.on('data', chunk => {content += String(chunk)})
      response.once('end', () => {
        try { resolve({status: response.statusCode ?? 500, body: content ? JSON.parse(content) : {}}) }
        catch {resolve({status: response.statusCode ?? 500, body: {}})}
      })
    })
    req.once('error', reject)
    req.once('timeout', () => req.destroy(new Error('Local Python request timed out')))
    req.end()
  })
}
async function backendReady() {
  try {
    const result = await localRequest('GET', '/api/health')
    return result.status === 200 && result.body?.ok === true && result.body?.service === 'water_detector'
  } catch {return false}
}

function spawnPython(controlToken: string): ChildProcess {
  const environment: NodeJS.ProcessEnv = {
    ...process.env, WAFER_DESKTOP_CHILD: '1',
    WAFER_DESKTOP_CONTROL_TOKEN: controlToken,
    WAFER_PORT: String(port), PYTHONUNBUFFERED: '1',
  }
  let command: string
  let args: string[]
  let cwd: string
  if (app.isPackaged) {
    cwd = path.join(process.resourcesPath, 'backend')
    command = path.join(cwd, process.platform === 'win32' ? 'wafer-backend.exe' : 'wafer-backend')
    if (!fs.existsSync(command)) throw new Error('Missing bundled Python backend: ' + command)
    args = []
  } else {
    cwd = path.resolve(dir, '../../backend')
    const venv = process.env.VIRTUAL_ENV
    const venvPython = venv && path.join(venv, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
    command = process.env.WAFER_PYTHON ||
      (venvPython && fs.existsSync(venvPython) ? venvPython : (process.platform === 'win32' ? 'python' : 'python3'))
    args = ['-m', 'water_detector.main']
    environment.PYTHONPATH = [path.join(cwd, 'src'), environment.PYTHONPATH].filter(Boolean).join(path.delimiter)
  }
  log('Starting Python: '+command)
  const child = spawn(command, args, {
    cwd, env: environment, windowsHide: true, shell: false,
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.stdout?.on('data', log)
  child.stderr?.on('data', log)
  return child
}

async function shutdownBackend(token: string) {
  try {
    const response = await localRequest('POST', '/api/system/process/shutdown', token)
    return response.status === 202
  } catch {return false}
}
const processManager = new RuntimeProcessManager({
  platform: process.platform,
  createControlToken: () => crypto.randomBytes(32).toString('hex'),
  spawnRuntime: spawnPython,
  requestShutdown: shutdownBackend,
  spawnCommand: spawn,
  sendSignal: process.kill.bind(process),
  gracefulWaitMs: 5000, termWaitMs: 900, forceWaitMs: 1500,
  logger: {info: log, warn: log, error: log},
})

async function startOrConnect() {
  if (await backendReady()) {
    log('Connected to independently running backend. It will not be stopped on exit.')
    return
  }
  processManager.start()
  const deadline = Date.now() + 16000
  while (Date.now() < deadline) {
    if (await backendReady()) return
    if (!processManager.hasOwnedProcess()) throw new Error('Python exited during startup. Check backend logs.')
    await new Promise(resolve => setTimeout(resolve, 200))
  }
  throw new Error('Python startup timeout.\n' + logs.slice(-12).join('\n'))
}

function errorHtml(e: unknown) {
  const message = String(e instanceof Error ? e.stack : e)
  const escaped = message.replace(/[&<>"']/g, ch =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch] || ch))
  return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>' +
    'body{font:15px system-ui;background:#eff3f7;padding:36px;color:#27364a}' +
    'pre{white-space:pre-wrap;background:#fff;border:1px solid #ddd;padding:18px;border-radius:8px}' +
    '</style><h2>检测服务启动失败</h2><pre>' + escaped +
    '</pre><p>设置 WAFER_DEBUG_CONSOLE=1 可在开发终端查看详细日志。</p></html>'
}

async function showWindow() {
  window = new BrowserWindow({
    width: 1510, height: 940, minWidth: 980, minHeight: 650,
    title: '晶圆洗片视觉检测系统', backgroundColor: '#eff3f7',
    webPreferences: {
      preload: path.join(dir, 'preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
    },
  })
  const current = window
  current.webContents.on('preload-error', (_event, filename, error) => log('Preload: '+filename+' '+error))
  const loading = '<html lang="zh-CN"><meta charset="utf-8"><body style="font:16px system-ui;color:#30475d;background:#eff3f7;padding:40px"><h2>晶圆洗片视觉检测系统</h2>正在启动 Python 服务…</body></html>'
  await current.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(loading))
  try {
    await startOrConnect()
    if (current.isDestroyed()) return
    if (viteDevUrl) await current.loadURL(viteDevUrl)
    else await current.loadFile(path.join(rendererDist, 'index.html'))
  } catch (e) {
    log(e)
    if (!current.isDestroyed())
      await current.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(errorHtml(e)))
  }
}

if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', () => {
    if (!window) return
    if (window.isMinimized()) window.restore()
    window.focus()
  })
  app.whenReady().then(() => {
    ipcMain.handle('select-video-file', async () => {
      const selected = await dialog.showOpenDialog({
        properties: ['openFile'],
        filters: [{name:'Video', extensions:['mp4','avi','mov','mkv']}],
      })
      return selected.canceled ? null : selected.filePaths[0]
    })
    ipcMain.handle('backend-logs', () => logs.join('\n'))
    void showWindow()
  })
  app.on('window-all-closed', () => app.quit())
  app.on('before-quit', event => {
    if (mayQuit || !processManager.hasOwnedProcess()) return
    event.preventDefault()
    if (quitInProgress) return
    quitInProgress = true
    void processManager.stop().finally(() => {
      mayQuit = true
      app.quit()
    })
  })
}
