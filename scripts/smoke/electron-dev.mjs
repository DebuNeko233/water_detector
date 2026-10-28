/** CI smoke test for Electron development launch: verifies automatic Python
 * startup/handshake/shutdown WITHOUT packaging, which previously regressed.
 */
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import path from 'node:path'
const require = createRequire(import.meta.url)
const electron = require('electron')
if (typeof electron !== 'string') throw new Error('Electron executable is not installed')
const child = spawn(electron, ['.', '--smoke-dev'], {
  cwd: path.resolve('.'), shell: false, windowsHide: true,
  stdio: ['ignore', 'pipe', 'pipe'],
})
let output = ''
for (const stream of [child.stdout, child.stderr]) {
  stream.on('data', data => { output += String(data) })
}
const code = await new Promise((resolve,reject) => {
  const timeout = setTimeout(() => {
    child.kill()
    reject(new Error('Development Electron smoke timed out: ' + output.slice(-4000)))
  }, 40_000)
  child.once('error', error => { clearTimeout(timeout); reject(error) })
  child.once('exit', (code) => { clearTimeout(timeout); resolve(code) })
})
if (code !== 0) throw new Error('Development Electron smoke failed (exit '+code+'): '+output.slice(-5000))
console.log('Development Electron smoke passed: automatically launched and stopped Python')
