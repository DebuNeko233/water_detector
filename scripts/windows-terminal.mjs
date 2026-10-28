/**
 * 生成 PowerShell 命令，给前端和后端打开独立的 cmd 窗口。
 * 工作目录先转义，避免含空格/单引号的路径导致启动失败。
 */

// PowerShell 单引号文本中的单引号要重复一次，不能直接拼接。
function powerShellLiteral(value) {
  return "'" + value.replaceAll("'", "''") + "'"
}

export function windowsTerminalScript({ workingDirectory, command, title }) {
  const directory = powerShellLiteral(workingDirectory)
  // /k 保留控制台，方便开发者查看退出信息和错误。
  const argumentsLine = powerShellLiteral('/d /k ' + command)

  return [
    "$ErrorActionPreference = 'Stop'",
    '$terminal = Start-Process -FilePath $env:ComSpec -ArgumentList ' +
      argumentsLine + ' -WorkingDirectory ' + directory + ' -PassThru',
    "if (-not $terminal -or -not $terminal.Id) { throw 'Could not open terminal' }",
    'Write-Output ' + powerShellLiteral('Opened ' + title),
  ].join('\n') + '\n'
}
