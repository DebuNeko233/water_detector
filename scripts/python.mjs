/**
 * 统一后端开发命令：自动选择 uv，在 backend 工作目录创建/复用虚拟环境。
 * 不必手动激活 .venv，也不要直接执行含相对导入的 main.py。
 */

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ensureUv } from './uv-bootstrap.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backend = path.join(root, 'backend')
const environment = path.join(backend, '.venv')
const command = process.argv[2] ?? 'setup'

function run(args, options = {}) {
  const result = spawnSync(uv, args, {
    // 在 backend 下执行，uv 才能解析 pyproject.toml 和 .python-version。
    cwd: backend,
    stdio: 'inherit',
    shell: false,
    ...options,
  })
  if (result.error) {
    console.error('Failed to launch uv: ' + result.error.message)
    process.exit(1)
  }
  if (result.status !== 0) process.exit(result.status ?? 1)
}

// 全局无 uv 时自动使用项目 .tools/ 下经过校验的版本。
const uv = await ensureUv()

switch (command) {
  case 'setup':
    run(['sync'])
    break
  case 'use': {
    const version = process.argv[3]
    if (!version || !/^3\.(10|11|12|13|14|15)(\.\d+)?$/.test(version)) {
      console.error('Usage: pnpm python:use 3.11 (or 3.12.9)')
      process.exit(1)
    }
    // A complete replacement is easier to understand than in-place migration.
    // Do not delete anything except the project's own virtual environment.
    fs.rmSync(environment, { recursive: true, force: true })
    run(['python', 'install', version])
    // 更新 .python-version 会影响开发和下一次绿色打包所选的解释器。
    run(['python', 'pin', version])
    run(['sync'])
    run(['run', 'python', '-c', 'import sys; print("Active Python:", sys.version)'])
    break
  }
  case 'run':
    // 使用包入口 -m 以保留 from .api 等相对导入所需的包上下文。
    run(['run', 'python', '-m', 'sample_backend', '--dev'])
    break
  case 'test':
    run(['run', '--group', 'dev', 'pytest', '-q'])
    break
  case 'info':
    run(['run', 'python', '-c', 'import sys; print(sys.executable); print(sys.version)'])
    break
  case 'list':
    run(['python', 'list'])
    break
  default:
    console.error('Unknown python command: ' + command)
    process.exit(1)
}
