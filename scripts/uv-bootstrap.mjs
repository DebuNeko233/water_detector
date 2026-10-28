/**
 * 开发机先检测系统 uv；缺失时下载并校验官方压缩包，缓存到 .tools/。
 * 不修改全局 PATH，也不会将 uv 放进绿色发行包。
 */
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// 固定的是本地引导工具版本，不是 Python 的版本。
export const BOOTSTRAP_UV_VERSION = '0.13.5'
export const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function uvAsset(platform = process.platform, arch = process.arch) {
  const targets = {
    'win32-x64': 'uv-x86_64-pc-windows-msvc.zip',
    'win32-arm64': 'uv-aarch64-pc-windows-msvc.zip',
    'linux-x64': 'uv-x86_64-unknown-linux-gnu.tar.gz',
    'linux-arm64': 'uv-aarch64-unknown-linux-gnu.tar.gz',
    'darwin-x64': 'uv-x86_64-apple-darwin.tar.gz',
    'darwin-arm64': 'uv-aarch64-apple-darwin.tar.gz',
  }
  // 只能选择已支持架构对应的官方归档，不推测陌生平台的文件名。
  const result = targets[platform + '-' + arch]
  if (!result) {
    throw new Error('Automatic uv bootstrap is unsupported on ' + platform + '/' + arch +
      '; install uv manually from https://docs.astral.sh/uv/getting-started/installation/')
  }
  return result
}

export function uvPath(root = projectRoot, platform = process.platform, arch = process.arch) {
  return path.join(root, '.tools', 'uv', BOOTSTRAP_UV_VERSION, platform + '-' + arch,
    platform === 'win32' ? 'uv.exe' : 'uv')
}

export function checkUv(command, run = spawnSync) {
  const result = run(command, ['self', 'version'], {
    encoding: 'utf8', timeout: 10_000, windowsHide: true, shell: false,
  })
  return !result.error && result.status === 0 &&
    /^uv\s+\d+\.\d+\.\d+/m.test((result.stdout || '') + (result.stderr || ''))
}

// 先校验 SHA-256，校验失败不能提取或执行归档中的文件。
export function verifyUvArchive(bytes, checksumText) {
  const expected = checksumText.trim().match(/^([a-fA-F0-9]{64})(?:\s|$)/)?.[1]
  if (!expected) throw new Error('Malformed uv release checksum')
  const actual = createHash('sha256').update(bytes).digest('hex')
  if (actual !== expected.toLowerCase()) {
    throw new Error('uv download SHA256 mismatch; refusing to run unverified binary')
  }
}

async function download(url, fetcher = fetch) {
  const response = await fetcher(url, { signal: AbortSignal.timeout(90_000) })
  if (!response.ok) throw new Error('HTTP ' + response.status + ' while downloading ' + url)
  // 除响应声明的大小外，还要在读取后检查真实字节长度。
  const length = Number(response.headers.get('content-length') || '0')
  if (length > 35 * 1024 * 1024) throw new Error('uv download exceeds 35 MiB')
  const bytes = Buffer.from(await response.arrayBuffer())
  if (bytes.length > 35 * 1024 * 1024) throw new Error('uv download exceeds 35 MiB')
  return bytes
}

function extractArchive(archive, destination, platform, runner = spawnSync) {
  const args = platform === 'win32'
    ? ['-xf', archive, '-C', destination]
    : ['-xzf', archive, '-C', destination]
  const result = runner('tar', args, { stdio: 'pipe', encoding: 'utf8', shell: false })
  if (result.error || result.status !== 0) {
    throw new Error('Could not extract official uv archive with tar: ' +
      (result.error?.message || result.stderr || result.status))
  }
}

function findExtractedUv(directory, name, depth = 0) {
  if (depth > 3) return null
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name)
    if (entry.isFile() && entry.name === name) return full
    if (entry.isDirectory()) {
      const found = findExtractedUv(full, name, depth + 1)
      if (found) return found
    }
  }
  return null
}

async function installUv(destination, platform, arch, options) {
  const asset = uvAsset(platform, arch)
  const releaseRoot = 'https://github.com/astral-sh/uv/releases/download/' +
    BOOTSTRAP_UV_VERSION + '/' + asset
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-uv-'))
  try {
    console.info('[uv] Downloading uv ' + BOOTSTRAP_UV_VERSION + ' into project-local .tools/')
    // 官方归档和校验文件都必须成功下载，不能只验证 HTTP 状态。
    const [archive, checksum] = await Promise.all([
      download(releaseRoot, options.fetcher),
      download(releaseRoot + '.sha256', options.fetcher),
    ])
    verifyUvArchive(archive, checksum.toString('utf8'))
    const archiveFile = path.join(temporary, asset)
    fs.writeFileSync(archiveFile, archive)
    extractArchive(archiveFile, temporary, platform, options.extractRunner)
    const extracted = findExtractedUv(temporary, platform === 'win32' ? 'uv.exe' : 'uv')
    if (!extracted) throw new Error('Official uv archive is missing the uv executable')

    fs.mkdirSync(path.dirname(destination), { recursive: true })
    // 先写临时可执行文件再改名，防止其他进程读取未完成的副本。
    const staged = destination + '.new-' + process.pid
    fs.copyFileSync(extracted, staged)
    if (platform !== 'win32') fs.chmodSync(staged, 0o755)
    try {
      fs.renameSync(staged, destination)
    } finally {
      fs.rmSync(staged, { force: true })
    }
    if (!options.check(destination)) {
      fs.rmSync(destination, { force: true })
      throw new Error('Downloaded uv executable failed "uv self version"')
    }
    console.info('[uv] Ready: ' + destination)
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true })
  }
}

/**
 * Optional injected hooks keep unit tests fully offline. Avoid concurrent
 * bootstrap downloads when dev starts separate backend/frontend terminals.
 */
export async function ensureUv(options = {}) {
  const root = options.root ?? projectRoot
  const platform = options.platform ?? process.platform
  const arch = options.arch ?? process.arch
  const systemCommand = options.systemCommand ?? 'uv'
  const check = options.check ?? checkUv
  // 全局存在可用 uv 就复用，本地安装是回退路径。
  if (check(systemCommand)) return systemCommand

  const destination = uvPath(root, platform, arch)
  if (check(destination)) return destination

  // Don't silently treat a broken global uv as usable. Verify the local copy.
  uvAsset(platform, arch)
  fs.mkdirSync(path.dirname(destination), { recursive: true })
  // 多个终端可能同时启动，排他锁确保只有一个下载者。
  const lock = destination + '.download-lock'
  const deadline = Date.now() + 150_000
  let owned = false
  while (Date.now() < deadline) {
    if (check(destination)) return destination
    try {
      const handle = fs.openSync(lock, 'wx')
      try {
        fs.writeFileSync(handle, String(process.pid))
      } finally {
        fs.closeSync(handle)
      }
      owned = true
      break
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
      // Clear locks left behind by a crashed downloader, not fresh locks.
      try {
        if (Date.now() - fs.statSync(lock).mtimeMs > 150_000) fs.rmSync(lock, { force: true })
      } catch (error) {
        if (error.code !== 'ENOENT') throw error
      }
      await new Promise(resolve => setTimeout(resolve, 250))
    }
  }
  if (!owned) throw new Error('Timed out waiting for another uv bootstrap')

  try {
    if (!check(destination)) {
      await installUv(destination, platform, arch, { ...options, check })
    }
    return destination
  } catch (error) {
    throw new Error('Could not bootstrap uv: ' + error.message +
      '. Check access to GitHub Releases, or install uv manually.')
  } finally {
    fs.rmSync(lock, { force: true })
  }
}
