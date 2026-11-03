import { spawn, type ChildProcess } from 'node:child_process'
import { randomBytes, randomUUID } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { setTimeout as pause } from 'node:timers/promises'
import { app } from 'electron'
import type { BackendStatus } from '../shared/bridge'
import { validateApiPath } from './api-path.mjs'

// 就绪文件由 Python 原子写入；pid/session_id 防止误连到其他本机服务。
interface ReadyMessage { port: number; pid: number; session_id: string }
interface Manifest { python: string }

function exited(child: ChildProcess): boolean {
  return child.exitCode !== null || child.signalCode !== null
}

// 使用一次性 exit 监听并在超时后移除，防止关闭过程中遗留监听器。
function waitForExit(child: ChildProcess, ms: number): Promise<boolean> {
  if (exited(child)) return Promise.resolve(true)
  return new Promise((resolve) => {
    const timer = setTimeout(() => finish(false), ms)
    const listener = () => finish(true)
    const finish = (ok: boolean) => {
      clearTimeout(timer)
      child.off('exit', listener)
      resolve(ok)
    }
    child.once('exit', listener)
  })
}

/** Electron owns Python in both development and production; explicit external mode is debug-only. */
export class BackendManager {
  private child: ChildProcess | null = null
  private endpoint = ''
  private sessionToken = ''
  private controlToken = ''
  private readyFile = ''
  private stopping = false
  private currentStatus: BackendStatus = 'starting'
  private latestError = ''

  constructor(private readonly notify: (status: BackendStatus) => void) {}

  get status(): BackendStatus { return this.currentStatus }
  get diagnostic(): string { return this.latestError }
  get token(): string { return this.sessionToken }
  get ownedPid(): number | null { return this.child?.pid ?? null }

  connected(): void { this.change('ready') }
  disconnected(): void {
    if (!this.stopping) this.change('offline')
  }
  get baseUrl(): string {
    if (!this.endpoint) throw new Error('Python backend is not ready')
    return this.endpoint
  }

  private change(status: BackendStatus): void {
    if (status === 'ready') this.latestError = ''
    this.currentStatus = status
    this.notify(status)
  }

  // 不信任清单中的路径：必须解析到随应用分发的 backend 目录内部。
  private packagedPython(): string {
    const root = path.join(process.resourcesPath, 'backend')
    const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8')) as Manifest
    const executable = path.resolve(root, manifest.python)
    const relative = path.relative(root, executable)
    if (relative.startsWith('..') || path.isAbsolute(relative)) {
      throw new Error('Invalid Python location in manifest')
    }
    if (!fs.existsSync(executable)) throw new Error('Bundled Python missing: ' + executable)
    return executable
  }

  /** In development, uv installs the editable package into backend/.venv. */
  private developmentPython(): string {
    const relative = process.platform === 'win32'
      ? path.join('Scripts', 'python.exe')
      : path.join('bin', 'python')
    const executable = path.resolve(app.getAppPath(), 'backend', '.venv', relative)
    if (!fs.existsSync(executable)) {
      throw new Error('Python 开发环境未安装，请在仓库根目录运行 pnpm setup，再运行 pnpm dev。')
    }
    return executable
  }

  async start(): Promise<void> {
    // Debug-only opt-in: reuse an independently started Python server.
    // The regular desktop always spawns and owns its own child, even in development.
    if (!app.isPackaged && process.env.WAFER_USE_EXTERNAL_BACKEND === '1') {
      this.endpoint = 'http://127.0.0.1:8765'
      this.change('starting')
      try {
        await this.health()
        this.change('ready')
      } catch (error) {
        this.latestError = '独立 Python 尚未运行，请先执行 pnpm dev:backend。'
        this.change('offline')
        throw new Error(this.latestError, { cause: error })
      }
      return
    }
    if (this.child || this.stopping) return
    this.change('starting')
    let python: string
    try {
      python = app.isPackaged ? this.packagedPython() : this.developmentPython()
    } catch (error) {
      this.latestError = String(error)
      this.change('failed')
      throw error
    }
    // 每次启动生成新的会话/控制令牌，前者用于 API，后者仅用于停机。
    const sessionId = randomUUID()
    const startupDirectory = path.join(app.getPath('userData'), 'startup')
    fs.mkdirSync(startupDirectory, { recursive: true })
    this.readyFile = path.join(startupDirectory, sessionId + '.json')
    this.sessionToken = randomBytes(32).toString('hex')
    this.controlToken = randomBytes(32).toString('hex')

    // -I 禁用用户级包路径；APP_PARENT_PID 使 Python 能察觉 Electron 意外退出。
    const debugConsole = !app.isPackaged && process.env.WAFER_DEBUG_CONSOLE === '1'
    const child = spawn(python, ['-I', '-m', 'sample_backend'], {
      cwd: path.dirname(python),
      windowsHide: !debugConsole,
      stdio: debugConsole ? ['ignore', 'inherit', 'inherit'] : 'ignore',
      env: {
        ...process.env,
        APP_PARENT_PID: String(process.pid),
        APP_SESSION_ID: sessionId,
        APP_SESSION_TOKEN: this.sessionToken,
        APP_CONTROL_TOKEN: this.controlToken,
        APP_READY_FILE: this.readyFile,
        APP_DATA_DIR: path.join(app.getPath('userData'), 'data'),
        PYTHONNOUSERSITE: '1',
      },
    })
    this.child = child
    let failure: Error | null = null
    child.on('error', (error) => { failure = error })
    child.once('exit', (code) => {
      console.info('Python exited:', code)
      if (this.child === child) {
        this.child = null
        if (!this.stopping) {
          this.latestError = 'Python 后端意外退出（exit code: ' + String(code) + '）'
          this.change('failed')
        }
      }
    })

    try {
      // 就绪握手不仅验证端口，还校验进程 PID 与本次随机会话 ID。
      const deadline = Date.now() + 30_000
      while (Date.now() < deadline && !this.stopping) {
        if (failure) throw failure
        if (exited(child)) throw new Error('Python exited before becoming ready')
        if (fs.existsSync(this.readyFile)) {
          const ready = JSON.parse(fs.readFileSync(this.readyFile, 'utf8')) as ReadyMessage
          // Windows .venv/Scripts/python.exe is a redirector: the actual
          // interpreter can have a different PID while the launcher remains
          // owned by Electron. Keep strict PID equality for packaged Python.
          const pidMatches = ready.pid === child.pid ||
            (!app.isPackaged && process.platform === 'win32' &&
              Number.isInteger(ready.pid) && ready.pid > 0)
          if (!pidMatches || ready.session_id !== sessionId ||
              !Number.isInteger(ready.port) || ready.port <= 0 || ready.port > 65535) {
            throw new Error('Invalid Python startup handshake')
          }
          this.endpoint = 'http://127.0.0.1:' + ready.port
          // 有就绪文件不等于 HTTP 已可用；健康检查成功后才报告 ready。
          await this.health()
          this.change('ready')
          return
        }
        await pause(150)
      }
      throw new Error('Python startup timeout. 查看后端日志或设置 WAFER_DEBUG_CONSOLE=1')
    } catch (error) {
      console.error('Python startup failed:', error)
      this.latestError = String(error)
      await this.stop()
      this.change('failed')
      throw error
    } finally {
      this.removeReadyFile()
    }
  }

  private removeReadyFile(): void {
    if (this.readyFile) {
      try { fs.rmSync(this.readyFile, { force: true }) } catch { /* optional cleanup */ }
      this.readyFile = ''
    }
  }

  async health(): Promise<void> {
    const response = await fetch(this.baseUrl + '/api/health', {
      headers: this.sessionToken ? { 'x-session-token': this.sessionToken } : {},
      signal: AbortSignal.timeout(2_000),
    })
    if (!response.ok) throw new Error('Python health check failed: ' + response.status)
  }

  async request(method: 'GET' | 'POST', apiPath: string, body?: unknown): Promise<unknown> {
    // Renderer 提供的路径必须在白名单范围内，避免借主进程发任意本机请求。
    validateApiPath(apiPath, method)
    const json = body === undefined ? undefined : JSON.stringify(body)
    if (json && json.length > 65_536) throw new Error('Request too large')
    const response = await fetch(this.baseUrl + apiPath, {
      method,
      headers: {
        ...(this.sessionToken ? { 'x-session-token': this.sessionToken } : {}),
        ...(json === undefined ? {} : { 'content-type': 'application/json' }),
      },
      body: json,
      signal: AbortSignal.timeout(15_000),
    })
    const result: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new Error('Python API ' + response.status + ': ' + JSON.stringify(result))
    return result
  }

  async stop(): Promise<void> {
    if (this.stopping) return
    this.stopping = true
    this.change('stopping')
    const child = this.child
    try {
      if (child && !exited(child)) {
        // 优先请求授权的内部接口优雅关闭；超时后仅终止本进程创建的子进程。
        if (this.endpoint && this.controlToken) {
          try {
            await fetch(this.endpoint + '/internal/shutdown', {
              method: 'POST',
              headers: { 'x-control-token': this.controlToken },
              signal: AbortSignal.timeout(1_500),
            })
          } catch (error) {
            console.warn('Graceful Python shutdown failed:', error)
          }
        }
        if (!(await waitForExit(child, 5_000))) {
          if (process.platform === 'win32' && child.pid) {
            const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
              windowsHide: true, stdio: 'ignore',
            })
            await waitForExit(killer, 2_000)
          } else {
            child.kill('SIGKILL')
          }
          await waitForExit(child, 2_000)
        }
      }
    } finally {
      this.child = null
      this.endpoint = ''
      this.sessionToken = ''
      this.controlToken = ''
      this.removeReadyFile()
      this.change('offline')
      this.stopping = false
    }
  }
}
