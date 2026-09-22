param([Parameter(Mandatory)][string]$CodexDirectory, [Parameter(Mandatory)][string]$StateDirectory)
$ErrorActionPreference = 'Stop'
$env:CODEX_HOME = $CodexDirectory
$env:MODEL_ROUTER_STATE_DIR = $StateDirectory
$Python = Join-Path (Split-Path $PSScriptRoot) 'router\.venv\Scripts\python.exe'
$Child = Start-Process -FilePath $Python -ArgumentList ('-X utf8 "' + $PSScriptRoot + '\jev_server.py"') -WindowStyle Hidden -PassThru
$Marker = Join-Path $StateDirectory 'jev-service-process.json'
@{ pid = $Child.Id; started = $Child.StartTime.ToUniversalTime().Ticks; codex_home = $CodexDirectory; state = $StateDirectory } |
  ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8
$Child.WaitForExit()
exit $Child.ExitCode
