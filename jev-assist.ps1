$ErrorActionPreference = 'Stop'
$Command = if ($args.Count) { [string]$args[0] } else { 'help' }
$Rest = @(if ($args.Count -gt 1) { $args[1..($args.Count - 1)] })
$Python = Join-Path $PSScriptRoot 'router\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = 'python' }
switch ($Command) {
  'install' { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\install.ps1" @Rest }
  'router' { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\router\codex-router.ps1" @Rest }
  'service' { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\server\service-windows.ps1" @Rest }
  'smoke' { & $Python -X utf8 "$PSScriptRoot\server\smoke.py" @Rest }
  'report' { & $Python -X utf8 "$PSScriptRoot\server\report_routing.py" @Rest }
  'test' { & $Python -X utf8 "$PSScriptRoot\poc\eval_routing.py" @Rest }
  'dashboard' { & $Python -X utf8 "$PSScriptRoot\server\open_dashboard.py" @Rest }
  'remote-dashboard' { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\open-remote-dashboard.ps1" @Rest }
  'help' {
    Write-Output 'jev-assist install [-PrepareOnly] [-SkipSmoke]'
    Write-Output 'jev-assist router <command> | service install|status|stop|uninstall'
    Write-Output 'jev-assist dashboard | smoke | report [args] | test [--live]'
    Write-Output 'jev-assist remote-dashboard [SSH-alias]'
    exit 0
  }
  default { throw "Unknown command: $Command" }
}
exit $LASTEXITCODE
