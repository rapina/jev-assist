param([Parameter(Mandatory)][string]$CodexDirectory, [Parameter(Mandatory)][string]$StateDirectory)
$ErrorActionPreference = 'Stop'
$env:CODEX_HOME = $CodexDirectory
$env:MODEL_ROUTER_STATE_DIR = $StateDirectory
$Python = Join-Path (Split-Path $PSScriptRoot) 'router\.venv\Scripts\python.exe'
$Child = Start-Process -FilePath $Python -ArgumentList ('-X utf8 "' + $PSScriptRoot + '\jev_server.py"') -WindowStyle Hidden -PassThru
$Child.WaitForExit()
exit $Child.ExitCode
