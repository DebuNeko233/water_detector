/** Save Electron diagnostics beside Python logs without a new framework. */

import fs from 'node:fs'
import path from 'node:path'
import { app } from 'electron'

export function installLogging(): void {
  const directory = path.join(app.getPath('userData'), 'data', 'logs')
  fs.mkdirSync(directory, { recursive: true })
  const logfile = path.join(directory, 'electron.log')
  // 保留原始控制台方法：写入文件同时仍能在开发终端查看日志。
  const original = {
    info: console.info.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console),
  }

  function write(level: 'info' | 'warn' | 'error', values: unknown[]): void {
    const message = values.map(value => value instanceof Error ? value.stack : String(value)).join(' ')
    const line = new Date().toISOString() + ' [' + level.toUpperCase() + '] ' + message + '\n'
    try {
      // 限制磁盘占用：超过 5 MiB 时仅保留一个上一版日志文件。
      if (fs.existsSync(logfile) && fs.statSync(logfile).size > 5 * 1024 * 1024) {
        fs.rmSync(logfile + '.1', { force: true })
        fs.renameSync(logfile, logfile + '.1')
      }
      fs.appendFileSync(logfile, line, 'utf8')
    } catch {
      // Logging must never crash the desktop application.
    }
    original[level](...values)
  }

  console.info = (...values: unknown[]) => write('info', values)
  console.warn = (...values: unknown[]) => write('warn', values)
  console.error = (...values: unknown[]) => write('error', values)
  // 未捕获异常也落盘，便于排查绿色软件在用户电脑上的崩溃。
  process.on('uncaughtException', error => console.error('Uncaught Electron error:', error))
  process.on('unhandledRejection', reason => console.error('Unhandled Electron rejection:', reason))
}
