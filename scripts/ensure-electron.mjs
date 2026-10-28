/**
 * Electron 可执行文件可能在 pnpm install 后仍不存在；electron-vite 直接启动会失败。
 * 检查当前包的运行时，缺失才执行其自带的官方 install.js。
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)

export function electronPackageDirectory(resolve = require.resolve) {
  try {
    return path.dirname(resolve('electron/package.json'))
  } catch (error) {
    throw new Error('Electron npm package is missing. Run "pnpm install" first.', { cause: error })
  }
}

export function electronIsReady(directory, platform = process.platform) {
  const executable = platform === 'win32' ? 'electron.exe'
    : platform === 'darwin' ? 'Electron.app/Contents/MacOS/Electron'
      : 'electron'
  try {
    const details = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'))
    const actualPath = fs.readFileSync(path.join(directory, 'path.txt'), 'utf8').trim()
    const binary = path.join(directory, 'dist', actualPath)
    const installedVersion = fs.readFileSync(path.join(directory, 'dist', 'version'), 'utf8').trim().replace(/^v/, '')
    // 同时核对 path.txt、版本和实际二进制文件，不能只看 npm 包存在。
    return actualPath === executable && installedVersion === details.version && fs.existsSync(binary)
  } catch {
    return false
  }
}

export function ensureElectron({ directory = electronPackageDirectory(), run = spawnSync, platform = process.platform } = {}) {
  // 已安装正确版本就立即复用，不重复联网下载。
  if (electronIsReady(directory, platform)) return

  const installer = path.join(directory, 'install.js')
  if (!fs.existsSync(installer)) {
    throw new Error('Electron install script is missing. Run "pnpm install" first.')
  }

  console.info('[electron] Runtime missing; downloading with Electron official install.js')
  // No shell, no remote script execution, no version mismatch: this installer
  // belongs to the already installed Electron npm package.
  const result = run(process.execPath, [installer], {
    stdio: 'inherit',
    shell: false,
    windowsHide: true,
  })
  if (result.error || result.status !== 0) {
    throw new Error('Electron runtime installation failed: ' +
      (result.error?.message ?? 'exit code ' + result.status) +
      '. Check access to Electron downloads (GitHub Releases) or your mirror/proxy settings.')
  }
  if (!electronIsReady(directory, platform)) {
    throw new Error('Electron install command succeeded but path.txt / runtime is missing or incorrect.')
  }
  console.info('[electron] Runtime ready')
}

// 支持被 build.mjs 导入，也支持 pnpm electron:setup 直接执行。
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  try {
    ensureElectron()
  } catch (error) {
    console.error('[electron] ' + error.message)
    process.exitCode = 1
  }
}
