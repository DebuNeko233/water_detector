/** Explicit frontend-only mode for developers using pnpm dev:backend.
 * By default Electron owns its own Python; external mode is opt-in.
 */
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const windows = process.platform === 'win32'
const child = spawn(windows ? 'cmd.exe' : 'npm',
  windows ? ['/d', '/s', '/c', 'npm run dev:frontend'] : ['run', 'dev:frontend'], {
    cwd: root,
    shell: false,
    windowsHide: true,
    stdio: 'inherit',
    env: { ...process.env, WAFER_USE_EXTERNAL_BACKEND: '1' },
  })
child.on('error', error => { console.error('[ui-only]', error); process.exitCode = 1 })
child.on('exit', code => { process.exitCode = code ?? 1 })
