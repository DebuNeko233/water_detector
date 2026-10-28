/**
 * Windows 绿色构建总入口：组装独立 CPython + 正式依赖，再编译 Electron。
 * 不复制开发 .venv，用户只需完整的 win-unpacked 目录。
 */

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveProcessCommand } from './lib/process-command.mjs'
import { ensureUv } from './uv-bootstrap.mjs'
import { ensureElectron } from './ensure-electron.mjs'

// 以脚本自身定位仓库根目录，不受调用它的终端当前目录影响。
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const backend = path.join(root, 'backend')
const buildRoot = path.join(root, 'build')
// staging 是中间产物；验证完成才改名为 electron-builder 使用的 backend。
const staging = path.join(buildRoot, 'backend-staging')
const finalBackend = path.join(buildRoot, 'backend')
const runtime = path.join(staging, 'runtime')
const pythonVersion = fs.readFileSync(path.join(backend, '.python-version'), 'utf8').trim()
// uv 可以来自系统 PATH，也可以来自本地自动引导的 .tools/ 缓存。
let uv

function run(command, args, options = {}) {
  console.log('[build]', command, ...args)
  // Windows 的 pnpm/npm 是 .cmd 启动器，需要特别处理；uv/Python 直接执行。
  const processCommand = resolveProcessCommand(command === 'uv' ? uv : command, args)
  const result = spawnSync(processCommand.command, processCommand.args, {
    cwd: root,
    stdio: 'inherit',
    shell: false,
    ...options,
  })
  // 任一步骤失败即中断，防止把不完整的环境当成构建成功。
  if (result.error || result.status !== 0) {
    throw new Error(command + ' failed: ' + (result.error?.message ?? result.status))
  }
}

/**
 * Locate the Python version uv already installed on this computer.
 * This lookup is offline and ignores the project's disposable .venv.
 */
function findManagedPython() {
  const result = spawnSync(
    uv,
    // 只查 uv 管理的 Python 缓存，不查项目 .venv，也不在查找时下载。
    ['python', 'find', '--managed-python', '--no-project', '--no-python-downloads', pythonVersion],
    { cwd: root, encoding: 'utf8', shell: false },
  )
  if (result.error) throw new Error('uv is required: ' + result.error.message)
  if (result.status !== 0) return null

  const executable = fs.realpathSync(result.stdout.trim())
  if (path.basename(executable).toLowerCase() !== 'python.exe') {
    throw new Error('uv returned an unexpected Python executable: ' + executable)
  }
  return executable
}

/**
 * Install into uv's persistent user-level Python directory only once.
 * Copy the interpreter from there into build/, so installing dependencies
 * for the release cannot modify the cached original.
 */
function preparePythonRuntime() {
  let sourcePython = findManagedPython()
  if (!sourcePython) {
    console.log('[build] Python ' + pythonVersion + ' is not cached; installing it once with uv.')
    run('uv', ['python', 'install', pythonVersion])
    sourcePython = findManagedPython()
  }
  if (!sourcePython) throw new Error('uv could not find Python ' + pythonVersion)

  console.log('[build] Reusing local Python:', sourcePython)
  // 拷贝标准库、DLL 与解释器完整目录，不能只复制 python.exe。
  fs.cpSync(path.dirname(sourcePython), runtime, {
    recursive: true,
    dereference: true,
    force: true,
  })

  const packagedPython = path.join(runtime, 'python.exe')
  if (!fs.existsSync(packagedPython)) {
    throw new Error('The copied Python runtime has no python.exe')
  }
  return packagedPython
}

if (process.platform !== 'win32') {
  throw new Error('The initial release builder targets Windows x64. Build on Windows.')
}

fs.mkdirSync(buildRoot, { recursive: true })
fs.rmSync(staging, { recursive: true, force: true })
fs.mkdirSync(staging, { recursive: true })
// 正式运行时使用独立副本，避免依赖和开发机 uv 缓存之间的硬链接。
const env = { ...process.env, UV_LINK_MODE: 'copy' }

try {
  // Electron 42+ no longer downloads itself at pnpm install time.
  ensureElectron()
  uv = await ensureUv()
  // Resolve using pyproject.toml; uv may create an ignored, local uv.lock.
  // Keep the project .venv separate from the self-contained release runtime.
  run('uv', ['sync', '--project', backend])
  const python = preparePythonRuntime()

  const requirements = path.join(staging, 'requirements.txt')
  // 导出生产依赖但不导出 pytest 等开发依赖；业务包稍后单独安装。
  run('uv', [
    'export', '--project', backend, '--no-dev',
    '--no-emit-project', '--format', 'requirements-txt', '--output-file', requirements,
  ])
  // This interpreter is a fresh, project-owned copy inside build/, never a system Python.
  // uv marks managed CPython externally managed, so explicit permission is required here.
  run('uv', ['pip', 'sync', '--python', python, '--system', '--break-system-packages', requirements], { env })
  // 第三方依赖已同步，这一步只安装自己的 sample_backend 和 water_detector 包。
  run('uv', ['pip', 'install', '--python', python, '--system', '--break-system-packages', '--no-deps', backend], { env })
  // 隔离模式验证发行环境可以独立导入，避免开发机路径掩盖缺包问题。
  run(python, ['-I', '-c', 'import fastapi, uvicorn, loguru, sample_backend, water_detector; print("Wafer backend import passed")'])

  // The packaged manifest points only to the self-contained release copy.
  const relativePython = path.relative(staging, python).replaceAll('\\', '/')
  fs.writeFileSync(path.join(staging, 'manifest.json'), JSON.stringify({
    python: relativePython,
    python_version: pythonVersion,
    backend_package: 'sample_backend',
  }, null, 2) + '\n')

  // Python 环境准备好后再整体转为正式资源目录。
  fs.rmSync(finalBackend, { recursive: true, force: true })
  fs.renameSync(staging, finalBackend)
  // pnpm/npm 的 exec 参数略有不同；统一执行类型检查、前端编译和打包。
  const isPnpm = (process.env.npm_config_user_agent ?? '').startsWith('pnpm/')
  const packageManager = isPnpm ? 'pnpm' : 'npm'
  const prefix = isPnpm ? ['exec'] : ['exec', '--']
  run(packageManager, ['run', 'typecheck'])
  run(packageManager, [...prefix, 'electron-vite', 'build'])
  run(packageManager, [...prefix, 'electron-builder', '--win', '--dir', '--x64'])
  console.log('Portable Windows directory: dist/win-unpacked')
} catch (error) {
  console.error('[build] Failed:', error)
  process.exitCode = 1
}
