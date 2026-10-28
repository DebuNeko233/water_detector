import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { resolveProcessCommand } from '../lib/process-command.mjs'

test('native commands keep their own argument array on Windows', () => {
  const result = resolveProcessCommand('uv', ['python', 'find', '3.12'], 'win32')
  assert.deepEqual(result, { command: 'uv', args: ['python', 'find', '3.12'] })
})

test('non-Windows package managers do not use cmd.exe', () => {
  const result = resolveProcessCommand('pnpm', ['exec', 'electron-vite', 'build'], 'linux')
  assert.deepEqual(result, { command: 'pnpm', args: ['exec', 'electron-vite', 'build'] })
})

test('Windows invokes cmd.exe directly for pnpm / npm', () => {
  for (const packageManager of ['pnpm', 'npm']) {
    const result = resolveProcessCommand(packageManager, ['run', 'typecheck'], 'win32')
    assert.match(result.command.toLowerCase(), /(?:cmd\.exe|\\cmd\.exe)$/)
    assert.deepEqual(result.args, ['/d', '/s', '/c', packageManager + ' run typecheck'])
  }
})

test('unsafe Windows command arguments are rejected', () => {
  for (const unsafe of ['build & whoami', 'hello world', 'foo|bar', 'foo"bar']) {
    assert.throws(
      () => resolveProcessCommand('pnpm', ['exec', unsafe], 'win32'),
      /Unsupported package-manager argument/,
    )
  }
})

test('Windows pnpm shim works without DEP0190', {
  skip: process.platform !== 'win32',
}, () => {
  const command = resolveProcessCommand('pnpm', ['--version'])
  const result = spawnSync(command.command, command.args, {
    shell: false,
    encoding: 'utf8',
    timeout: 15_000,
    env: { ...process.env, NODE_OPTIONS: '--trace-deprecation' },
  })
  assert.equal(result.status, 0, result.error?.message || result.stderr)
  assert.match(result.stdout, /\d+\.\d+\.\d+/)
  assert.doesNotMatch(result.stderr, /DEP0190/)
})
