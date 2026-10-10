/** Based solely on sop-platform's Electron↔Python process supervision approach. */
export function processExited(child) {
  return child.exitCode !== null || child.signalCode !== null
}
export function waitForProcessExit(child, ms) {
  if (processExited(child)) return Promise.resolve(true)
  return new Promise(resolve => {
    let done = false
    const finish = exited => {
      if (done) return
      done = true
      clearTimeout(timer)
      child.off('exit', onExit)
      child.off('error', onError)
      resolve(exited)
    }
    const onExit = () => finish(true)
    const onError = () => finish(true)
    child.once('exit', onExit)
    child.once('error', onError)
    const timer = setTimeout(() => finish(false), ms)
  })
}
export class RuntimeProcessManager {
  constructor(options) {
    this.options = options
    this.child = null
    this.controlToken = ''
    this.stopPromise = null
  }
  hasOwnedProcess() {
    return Boolean(this.child && !processExited(this.child))
  }
  ownedPid() { return this.hasOwnedProcess() ? this.child.pid ?? null : null }
  start() {
    if (this.hasOwnedProcess()) return false
    const token = this.options.createControlToken()
    const child = this.options.spawnRuntime(token)
    this.child = child
    this.controlToken = token
    child.on('error', e => this.options.logger.error('[Python] launch error:', e))
    child.once('exit', (code, signal) => {
      this.options.logger.info('[Python] exit: '+(code ?? '-')+' / '+(signal ?? '-'))
      if (this.child === child) {
        this.child = null
        this.controlToken = ''
      }
    })
    return true
  }
  async forceStopProcessTree(child) {
    if (!child.pid || processExited(child)) return
    if (this.options.platform === 'win32') {
      const killer = this.options.spawnCommand('taskkill',
        ['/PID', String(child.pid), '/T', '/F'],
        {windowsHide: true, stdio: 'ignore', shell: false})
      await new Promise(resolve => {
        const timer = setTimeout(resolve, this.options.forceWaitMs)
        const done = () => {clearTimeout(timer);resolve()}
        killer.once('error', done)
        killer.once('exit', done)
      })
    } else {
      try {this.options.sendSignal(-child.pid, 'SIGTERM')}
      catch {child.kill('SIGTERM')}
    }
    if (await waitForProcessExit(child, this.options.termWaitMs)) return
    try {
      if (this.options.platform === 'win32') child.kill('SIGKILL')
      else this.options.sendSignal(-child.pid, 'SIGKILL')
    } catch {child.kill('SIGKILL')}
  }
  stop() {
    if (this.stopPromise) return this.stopPromise
    const child = this.child
    const token = this.controlToken
    if (!child) return Promise.resolve()
    this.stopPromise = (async () => {
      const accepted = token && !processExited(child)
        ? await this.options.requestShutdown(token) : false
      const exited = accepted && await waitForProcessExit(child, this.options.gracefulWaitMs)
      if (!exited && !processExited(child)) {
        await this.forceStopProcessTree(child)
        await waitForProcessExit(child, this.options.forceWaitMs)
      }
      if (this.child === child) {
        this.child = null
        this.controlToken = ''
      }
    })().finally(() => {this.stopPromise = null})
    return this.stopPromise
  }
}
