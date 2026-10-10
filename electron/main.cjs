'use strict'
/**
 * Electron owns the Python service only when it spawned that service.
 * In development, pnpm desktop starts Vite first; this file waits for Python
 * readiness before showing the UI. In production, it loads bundled dist.
 */
const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')

const API_PORT = Number(process.env.WAFER_PORT || '8765')
const API_URL = `http://127.0.0.1:${API_PORT}`
const DEV_URL = process.env.WAFER_FRONTEND_URL || 'http://127.0.0.1:5173'
let backend = null
let ownsBackend = false
let quitting = false
let mainWindow = null
let recentLogs = []

function appendLog(value) {
  for (const line of String(value).split(/\r?\n/)) {
    if (!line.trim()) continue
    recentLogs.push(line)
    if (recentLogs.length > 120) recentLogs.shift()
    if (process.env.WAFER_DEBUG_CONSOLE === '1') console.log('[wafer-backend]', line)
  }
}

function healthOnce() {
  return new Promise(resolve => {
    const request = http.get(`${API_URL}/api/health`, { timeout: 750 }, response => {
      let responseBody = ''
      response.on('data', chunk => { responseBody += chunk.toString() })
      response.on('end', () => {
        try {
          const parsed = JSON.parse(responseBody)
          resolve(response.statusCode === 200 && parsed.ok === true)
        } catch (_) { resolve(false) }
      })
    })
    request.on('timeout', () => request.destroy())
    request.on('error', () => resolve(false))
  })
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)) }

function pythonLaunch() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'backend', 'wafer-backend.exe')
    if (!fs.existsSync(exe)) {
      throw new Error(`未找到 Python 后端程序：${exe}\n请确认 Windows 安装包包含 backend/wafer-backend.exe。`)
    }
    return { command: exe, args: [], cwd: path.dirname(exe) }
  }
  const projectRoot = path.resolve(__dirname, '..')
  const venv = process.env.VIRTUAL_ENV
  const envPython = venv && path.join(venv, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const python = process.env.WAFER_PYTHON || (envPython && fs.existsSync(envPython) ? envPython : (process.platform === 'win32' ? 'python' : 'python3'))
  return { command: python, args: ['-m', 'water_detector.main'], cwd: projectRoot }
}

async function startBackend() {
  // The user may deliberately start a standalone backend for development.
  // Attach in that case, and NEVER terminate somebody else's process.
  if (await healthOnce()) {
    appendLog(`连接到已运行的外部 Python 服务：${API_URL}（关闭 Electron 不会关闭它）`)
    ownsBackend = false
    return
  }
  const launch = pythonLaunch()
  const environment = { ...process.env, WAFER_DESKTOP_CHILD: '1', WAFER_PORT: String(API_PORT), PYTHONUNBUFFERED: '1' }
  if (!app.isPackaged) {
    const sourcePath = path.resolve(__dirname, '../backend/src')
    environment.PYTHONPATH = [sourcePath, environment.PYTHONPATH].filter(Boolean).join(path.delimiter)
  }
  let launchError = null
  backend = spawn(launch.command, launch.args, {
    cwd: launch.cwd,
    env: environment,
    windowsHide: true,
    shell: false,
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  ownsBackend = true
  backend.stdout.on('data', appendLog)
  backend.stderr.on('data', appendLog)
  backend.on('error', error => { launchError = error; appendLog(`Python 启动失败：${error.message}`) })
  backend.on('exit', (code, signal) => appendLog(`Python 已退出：code=${code}, signal=${signal}`))
  for (let i = 0; i < 100; i++) {
    if (await healthOnce()) return
    if (launchError) throw launchError
    if (backend.exitCode !== null) break
    await sleep(150)
  }
  throw new Error(`Python 后端无法启动或健康检查超时。\n${recentLogs.slice(-12).join('\n')}`)
}

async function forceKillTree(pid) {
  if (!pid) return
  if (process.platform === 'win32') {
    await new Promise(resolve => {
      const killer = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
      killer.on('error', resolve)
      killer.on('close', resolve)
    })
  } else if (backend) {
    backend.kill('SIGKILL')
  }
}

async function stopBackend() {
  if (!ownsBackend || !backend || backend.exitCode !== null) return
  const owned = backend
  // Embedded Python listens for shutdown on stdin and asks uvicorn to stop;
  // its FastAPI shutdown handler first stops the inspection worker.
  try { owned.stdin.write('shutdown\n') } catch (_) { /* already closed */ }
  const exited = await Promise.race([
    new Promise(resolve => owned.once('close', () => resolve(true))),
    sleep(4500).then(() => false),
  ])
  if (!exited) await forceKillTree(owned.pid)
  ownsBackend = false
}

function showLoading(win) {
  win.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(`
    <!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
    body{margin:0;background:#eff3f7;color:#263449;display:grid;place-items:center;height:100vh;font:16px system-ui}
    section{text-align:center}small{display:block;margin-top:12px;color:#66758c}
    </style><section><h2>晶圆洗片检测系统</h2><div>正在初始化 Python 检测服务…</div>
    <small>请稍候，应用将在服务就绪后自动打开。</small></section></html>`)}`)
}

async function loadFrontend(win) {
  if (app.isPackaged || process.env.WAFER_USE_DIST === '1') {
    const html = path.resolve(__dirname, '../frontend/dist/index.html')
    if (!fs.existsSync(html)) throw new Error(`未找到已构建的前端：${html}，请先执行 pnpm build`)
    await win.loadFile(html)
  } else {
    await win.loadURL(DEV_URL)
  }
}

function showError(win, error) {
  const message = String(error && error.stack ? error.stack : error)
  appendLog(message)
  const escaped = message.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]))
  win.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(`<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <style>body{font-family:system-ui;padding:35px;color:#27364a;background:#f8fafc}pre{white-space:pre-wrap;background:#fff;border:1px solid #ddd;padding:18px;border-radius:6px}</style>
    <h2>检测服务启动失败</h2><p>请检查 Python 环境、后端依赖或前端构建文件。</p><pre>${escaped}</pre><p>可通过 WAFER_DEBUG_CONSOLE=1 查看日志。</p></html>`)}`)
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1510, height: 940, minWidth: 980, minHeight: 650,
    title: '晶圆洗片视觉检测系统', backgroundColor: '#eff3f7',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true,
      nodeIntegration: false, sandbox: true,
    },
  })
  showLoading(mainWindow)
  try {
    await startBackend()
    await loadFrontend(mainWindow)
  } catch (error) {
    if (mainWindow && !mainWindow.isDestroyed()) showError(mainWindow, error)
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus() }
  })
  app.whenReady().then(() => {
    ipcMain.handle('select-video-file', async () => {
      const selected = await dialog.showOpenDialog({ properties: ['openFile'], filters: [{ name: 'Video', extensions: ['mp4', 'avi', 'mov', 'mkv'] }] })
      return selected.canceled ? null : selected.filePaths[0]
    })
    ipcMain.handle('backend-logs', () => recentLogs.join('\n'))
    return createWindow()
  })
  app.on('window-all-closed', () => app.quit())
  app.on('before-quit', event => {
    if (quitting) return
    event.preventDefault()
    quitting = true
    stopBackend().finally(() => app.quit())
  })
}
