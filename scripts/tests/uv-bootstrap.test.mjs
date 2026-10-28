import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { ensureUv, uvAsset, uvPath, verifyUvArchive, BOOTSTRAP_UV_VERSION, checkUv } from '../uv-bootstrap.mjs'

test('uv assets are mapped to official supported architectures', () => {
  assert.equal(uvAsset('win32', 'x64'), 'uv-x86_64-pc-windows-msvc.zip')
  assert.equal(uvAsset('linux', 'x64'), 'uv-x86_64-unknown-linux-gnu.tar.gz')
  assert.equal(uvAsset('darwin', 'arm64'), 'uv-aarch64-apple-darwin.tar.gz')
  assert.throws(() => uvAsset('freebsd', 'arm64'), /unsupported/)
})

test('uv self version is used to detect installed uv', () => {
  let args
  const probe = checkUv('uv', (command, values) => {
    args = [command, values]
    return { status: 0, stdout: 'uv 0.13.5 (build)', stderr: '' }
  })
  assert.equal(probe, true)
  assert.deepEqual(args, ['uv', ['self', 'version']])
  assert.equal(checkUv('uv', () => ({ error: new Error('ENOENT') })), false)
})

test('sha256 verification rejects corrupt downloads', () => {
  const buffer = Buffer.from('fake uv archive')
  const sha = createHash('sha256').update(buffer).digest('hex')
  assert.doesNotThrow(() => verifyUvArchive(buffer, sha + '  uv.zip'))
  assert.throws(() => verifyUvArchive(Buffer.from('altered'), sha + '  uv.zip'), /SHA256 mismatch/)
  assert.throws(() => verifyUvArchive(buffer, 'not a checksum'), /Malformed/)
})

test('installed system uv is used without touching local cache', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-uv-test-'))
  try {
    const binary = await ensureUv({ root, check: candidate => candidate === 'uv' })
    assert.equal(binary, 'uv')
    assert.equal(fs.existsSync(path.join(root, '.tools')), false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

// 用伪造的离线下载与 tar 解压器测试缓存逻辑，普通单元测试不依赖网络。
test('missing uv installs locally once and reuses cache without downloads', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-uv-test-'))
  const archive = Buffer.from('fake downloaded archive')
  const hash = createHash('sha256').update(archive).digest('hex')
  let requests = 0
  const options = {
    root, platform: 'win32', arch: 'x64', systemCommand: 'missing-uv',
    check: candidate => candidate !== 'missing-uv' && fs.existsSync(candidate),
    fetcher: async url => {
      requests++
      const bytes = url.endsWith('.sha256') ? Buffer.from(hash + '  uv.zip') : archive
      return { ok: true, headers: new Headers(), arrayBuffer: async () => bytes }
    },
    extractRunner: (_command, args) => {
      const destination = args[args.indexOf('-C') + 1]
      fs.writeFileSync(path.join(destination, 'uv.exe'), 'stub')
      return { status: 0 }
    },
  }
  try {
    const first = await ensureUv(options)
    assert.equal(first, uvPath(root, 'win32', 'x64'))
    assert.equal(fs.readFileSync(first, 'utf8'), 'stub')
    assert.equal(await ensureUv(options), first)
    assert.equal(requests, 2) // One archive + one checksum, no redownload.
    assert.equal(fs.existsSync(first + '.download-lock'), false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('failed checksum does not install an executable', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-uv-test-'))
  const options = {
    root, platform: 'win32', arch: 'x64', systemCommand: 'missing-uv',
    check: candidate => candidate !== 'missing-uv' && fs.existsSync(candidate),
    fetcher: async url => {
      const bytes = url.endsWith('.sha256') ? Buffer.from('0'.repeat(64) + '  uv.zip') : Buffer.from('bad')
      return { ok: true, headers: new Headers(), arrayBuffer: async () => bytes }
    },
  }
  try {
    await assert.rejects(() => ensureUv(options), /SHA256 mismatch/)
    assert.equal(fs.existsSync(uvPath(root, 'win32', 'x64')), false)
    assert.equal(fs.existsSync(uvPath(root, 'win32', 'x64') + '.download-lock'), false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

// Windows CI 单独开启此联网用例，强制模拟系统不存在 uv，并验证真实官方安装包。
test('Windows can bootstrap verified official uv with no system uv', {
  skip: process.platform !== 'win32' || process.env.UV_BOOTSTRAP_ONLINE !== '1',
  timeout: 120_000,
}, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-uv-online-'))
  try {
    const binary = await ensureUv({ root, systemCommand: '__uv_does_not_exist__' })
    assert.equal(binary, uvPath(root, 'win32', 'x64'))
    const result = spawnSync(binary, ['self', 'version'], { encoding: 'utf8' })
    assert.equal(result.status, 0, result.error?.message || result.stderr)
    assert.match(result.stdout + result.stderr, new RegExp('uv ' + BOOTSTRAP_UV_VERSION))
    assert.equal(await ensureUv({ root, systemCommand: '__uv_does_not_exist__' }), binary)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
