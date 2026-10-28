/** Electron is responsible for desktop permissions and process ownership. */

import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { BackendManager } from './backend-manager'
import { EventStream } from './event-stream'
import { installLogging } from './logging'
import type { BackendStatus } from '../shared/bridge'

const mainDirectory = path.dirname(fileURLToPath(import.meta.url))
let mainWindow: BrowserWindow | null = null
let closing = false
let allowQuit = false

// 仅允许一个桌面应用实例，避免重复占用配置和托管多个 Python 进程。
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  })
}

function publishStatus(status: BackendStatus): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('backend:status', status)
  }
}

const backend = new BackendManager(publishStatus)
const stream = new EventStream(backend, (event) => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('backend:event', event)
  }
})

// 合并连续点击“重新连接”触发的重试，避免并发重建 WebSocket/Python。
let retryPromise: Promise<void> | null = null

/** Manual retry also works when the developer started Python after Electron. */
function retryBackend(): Promise<void> {
  if (retryPromise) return retryPromise
  retryPromise = (async () => {
    stream.stop()
    await backend.stop()
    await backend.start()
    stream.connect()
  })().finally(() => { retryPromise = null })
  return retryPromise
}

// 每个 IPC 入口都必须确认消息来自当前窗口的主 frame，拒绝其他内容调用。
function checkSender(event: Electron.IpcMainInvokeEvent): void {
  if (!mainWindow || mainWindow.isDestroyed() ||
      event.sender !== mainWindow.webContents ||
      event.senderFrame !== mainWindow.webContents.mainFrame) {
    throw new Error('Unauthorized IPC sender')
  }
}

// Renderer 只能调用这些预先批准的 IPC；不能直接使用 Node.js 或传任意命令。
function registerIpc(): void {
  ipcMain.handle('backend:status', (event) => {
    checkSender(event)
    return backend.status
  })
  ipcMain.handle('backend:diagnostics', (event) => {
    checkSender(event)
    return backend.diagnostic
  })
  ipcMain.handle('backend:retry', (event) => {
    checkSender(event)
    return retryBackend()
  })
  ipcMain.handle('backend:request', (event, method: unknown, apiPath: unknown, body?: unknown) => {
    checkSender(event)
    if ((method !== 'GET' && method !== 'POST') || typeof apiPath !== 'string') {
      throw new Error('Invalid API request')
    }
    return backend.request(method, apiPath, body)
  })
  ipcMain.handle('system:select-file', async (event) => {
    checkSender(event)
    const selection = await dialog.showOpenDialog(mainWindow!, { properties: ['openFile'] })
    return selection.canceled ? null : selection.filePaths[0]
  })
  // 日志内容也来自 Renderer，必须校验级别、模块名、长度和控制字符。
  ipcMain.handle('logging:write', (event, level: unknown, source: unknown, message: unknown) => {
    checkSender(event)
    // Renderer content is untrusted: accept only short, single-line, text-only events.
    if ((level !== 'info' && level !== 'warn' && level !== 'error') ||
        typeof source !== 'string' || !/^[a-zA-Z][a-zA-Z0-9_.-]{0,39}$/.test(source) ||
        typeof message !== 'string' || message.length < 1 || message.length > 1000 ||
        /[\r\n\x00-\x1f\x7f]/.test(message)) {
      throw new Error('Invalid renderer log entry')
    }
    console[level]('[renderer:' + source + ']', message)
  })
  ipcMain.handle('system:open-logs', async (event, target: unknown = 'python') => {
    checkSender(event)
    if (target !== 'python' && target !== 'electron') {
      throw new Error('Invalid log target')
    }
    // 开发 Python 是独立进程，日志与 Electron 不在同一个目录。
    const directory = app.isPackaged || target === 'electron'
      ? path.join(app.getPath('userData'), 'data', 'logs')
      : path.join(os.homedir(), 'WaferWaterDetector', 'logs')
    fs.mkdirSync(directory, { recursive: true })
    const error = await shell.openPath(directory)
    if (error) throw new Error(error)
  })
  ipcMain.handle('system:close-window', (event) => {
    checkSender(event)
    mainWindow?.close()
  })
}

function createWindow(show = true): BrowserWindow {
  mainWindow = new BrowserWindow({
    show,
    width: 1400,
    height: 860,
    minWidth: 800,
    minHeight: 600,
    autoHideMenuBar: true,
    backgroundColor: '#f5f7fb',
    // 只经 Preload 暴露白名单 API：Renderer 保持沙箱、上下文隔离和无 Node 权限。
    webPreferences: {
      preload: path.join(mainDirectory, '../preload/index.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  })
  // 禁止网页内容擅自打开新窗口或导航至任意外部地址。
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const developmentUrl = process.env.ELECTRON_RENDERER_URL
    const indexFile = path.join(mainDirectory, '../renderer/index.html')
    const allowedFile = pathToFileURL(indexFile).href
    if (url !== allowedFile && !(developmentUrl && url.startsWith(developmentUrl))) {
      event.preventDefault()
    }
  })
  if (process.env.ELECTRON_RENDERER_URL) {
    void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void mainWindow.loadFile(path.join(mainDirectory, '../renderer/index.html'))
  }
  const createdWindow = mainWindow
  mainWindow.on('closed', () => { mainWindow = null })
  return createdWindow
}

async function startBackend(): Promise<void> {
  try {
    await backend.start()
    stream.connect()
  } catch (error) {
    console.error('Python startup failed. Read the backend log for details:', error)
    publishStatus('failed')
  }
}

app.on('window-all-closed', () => app.quit())
// 正式退出前必须先停 WebSocket 和本应用拥有的 Python，再允许 Electron 真正退出。
app.on('before-quit', (event) => {
  if (allowQuit || closing) return
  event.preventDefault()
  closing = true
  stream.stop()
  void backend.stop().finally(() => {
    allowQuit = true
    app.quit()
  })
})

/**
 * Exercise the actual packaged Electron executable without opening a window.
 * CI runs this after bundling, so it catches missing resources and startup bugs
 * that testing python.exe alone cannot catch.
 */
async function runPackagedSmokeTest(): Promise<void> {
  let exitCode = 1
  let pythonPid: number | null = null
  try {
    if (!app.isPackaged) throw new Error('Packaged smoke test requires a release build')
    await backend.start()
    pythonPid = backend.ownedPid
    if (backend.status !== 'ready') throw new Error('Python backend not ready')
    const health = await backend.request('GET', '/api/health') as { status: string }
    if (health.status !== 'ready') throw new Error('Python health check failed')
    const config = await backend.request('GET', '/api/config/active')
    if (!config || typeof config !== 'object') throw new Error('Wafer config endpoint failed')
    registerIpc()
    const browser = createWindow(false)
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Renderer load timeout')), 15_000)
      browser.webContents.once('did-finish-load', () => { clearTimeout(timeout); resolve() })
      browser.webContents.once('did-fail-load', (_, code, error) => {
        clearTimeout(timeout); reject(new Error('Renderer failed: ' + code + ' ' + error))
      })
    })
    const result = await browser.webContents.executeJavaScript(`(() => {
      const buttons = Array.from(document.querySelectorAll('aside nav button.nav-btn'))
      if (buttons.length !== 4) throw new Error('Expected four wafer inspection tabs')
      buttons[1].click()
      return document.querySelector('.topbar')?.textContent ?? ''
    })()`, true) as string
    if (!result.includes('标定与调试')) throw new Error('Wafer navigation failed')
    console.info('Wafer packaged smoke test passed:', result)
    exitCode = 0
  } catch (error) {
    console.error('Wafer packaged smoke test failed:', error)
  } finally {
    await backend.stop()
    const report = process.env.APP_SMOKE_REPORT
    if (report) {
      try { fs.writeFileSync(report, JSON.stringify({ exitCode, pythonPid }), 'utf8') }
      catch { exitCode = 1 }
    }
    app.exit(exitCode)
  }
}

/** Forced parent exit is covered by the template parent-watch test. */
async function runParentExitSmokeTest(): Promise<void> {
  try {
    if (!app.isPackaged) throw new Error('Requires a packaged Electron executable')
    const report = process.env.APP_SMOKE_REPORT
    if (!report) throw new Error('Missing APP_SMOKE_REPORT')
    await backend.start()
    if (backend.status !== 'ready' || !backend.ownedPid) {
      throw new Error('Python backend failed to start')
    }
    fs.writeFileSync(report, JSON.stringify({ pythonPid: backend.ownedPid }), 'utf8')
    // Keep the main process alive until the test kills it using taskkill /F.
    setInterval(() => {}, 5_000)
  } catch (error) {
    console.error('Parent-exit test setup failed:', error)
    await backend.stop()
    app.exit(1)
  }
}

// 冒烟测试只在 CI 打包程序中运行；正常启动才创建可见窗口和后端连接。
app.whenReady().then(() => {
  if (!app.hasSingleInstanceLock()) return
  installLogging()
  if (process.argv.includes('--smoke-parent-exit')) {
    void runParentExitSmokeTest()
    return
  }
  if (process.argv.includes('--smoke-test')) {
    void runPackagedSmokeTest()
    return
  }
  registerIpc()
  createWindow()
  void startBackend()
})
