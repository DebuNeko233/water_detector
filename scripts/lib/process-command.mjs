/**
 * Windows pnpm/npm 是 .cmd，需要显式调用 cmd.exe；禁止 shell:true + 参数数组。
 * 该工具同时规避 DEP0190 和未经校验的 Shell 字符串拼接。
 */
export function resolveProcessCommand(command, args, platform = process.platform) {
  if (platform !== 'win32' || (command !== 'pnpm' && command !== 'npm')) {
    return { command, args }
  }

  // Only internally defined package-manager commands are supported.
  // Never concatenate a user-supplied path or unchecked argument into cmd /c.
  // 严格限制参数，只允许内部固定命令；拒绝 &、|、空格等元字符。
  if (!args.every((argument) => /^[a-zA-Z0-9_./:-]+$/.test(argument))) {
    throw new Error('Unsupported package-manager argument for Windows cmd.exe')
  }

  return {
    command: process.env.ComSpec || 'cmd.exe',
    args: ['/d', '/s', '/c', [command, ...args].join(' ')],
  }
}
