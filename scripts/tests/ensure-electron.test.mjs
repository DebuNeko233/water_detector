import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { electronIsReady, ensureElectron } from '../ensure-electron.mjs'

function fakeElectron(root, { version = '45.0.0', installed = false } = {}) {
  const directory = path.join(root, 'electron')
  fs.mkdirSync(directory, { recursive: true })
  fs.writeFileSync(path.join(directory, 'package.json'), JSON.stringify({ version }))
  fs.writeFileSync(path.join(directory, 'install.js'), '// simulated bundled installer')
  if (installed) {
    fs.mkdirSync(path.join(directory, 'dist'), { recursive: true })
    fs.writeFileSync(path.join(directory, 'dist', 'version'), version)
    fs.writeFileSync(path.join(directory, 'path.txt'), 'electron.exe')
    fs.writeFileSync(path.join(directory, 'dist', 'electron.exe'), '')
  }
  return directory
}

test('fully installed Electron binary is reused without invoking installation', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-electron-'))
  try {
    const directory = fakeElectron(root, { installed: true })
    assert.equal(electronIsReady(directory, 'win32'), true)
    ensureElectron({
      directory, platform: 'win32',
      run: () => { throw new Error('Installer should not be called') },
    })
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('missing path.txt or version mismatch triggers official installer', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-electron-'))
  try {
    const directory = fakeElectron(root)
    assert.equal(electronIsReady(directory, 'win32'), false)
    let invoked = false
    ensureElectron({
      directory, platform: 'win32',
      run: (command, args, options) => {
        invoked = true
        assert.equal(command, process.execPath)
        assert.deepEqual(args, [path.join(directory, 'install.js')])
        assert.equal(options.shell, false)
        fs.mkdirSync(path.join(directory, 'dist'))
        fs.writeFileSync(path.join(directory, 'dist', 'version'), '45.0.0')
        fs.writeFileSync(path.join(directory, 'path.txt'), 'electron.exe')
        fs.writeFileSync(path.join(directory, 'dist', 'electron.exe'), '')
        return { status: 0 }
      },
    })
    assert.equal(invoked, true)
    assert.equal(electronIsReady(directory, 'win32'), true)
    fs.writeFileSync(path.join(directory, 'dist', 'version'), '44.0.0')
    assert.equal(electronIsReady(directory, 'win32'), false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('install errors are explicit and do not claim success', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sample-electron-'))
  try {
    const directory = fakeElectron(root)
    assert.throws(() => ensureElectron({
      directory, platform: 'win32',
      run: () => ({ status: 1 }),
    }), /installation failed/)
    assert.throws(() => ensureElectron({
      directory, platform: 'win32',
      run: () => ({ status: 0 }),
    }), /path.txt/)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
