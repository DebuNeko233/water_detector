import type { ChildProcess } from 'node:child_process'
export class RuntimeProcessManager {
  constructor(options: {
    platform: string
    createControlToken: () => string
    spawnRuntime: (token: string) => ChildProcess
    requestShutdown: (token: string) => Promise<boolean>
    spawnCommand: typeof import('node:child_process').spawn
    sendSignal: typeof process.kill
    gracefulWaitMs: number
    termWaitMs: number
    forceWaitMs: number
    logger: {info: (...args: any[]) => void; warn: (...args: any[]) => void; error: (...args: any[]) => void}
  })
  start(): boolean
  stop(): Promise<void>
  hasOwnedProcess(): boolean
  ownedPid(): number | null
}
