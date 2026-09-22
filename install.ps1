param([switch]$PrepareOnly, [switch]$SkipSmoke)
$ErrorActionPreference = 'Stop'
$Repo = $PSScriptRoot
$Router = Join-Path $Repo 'router'
$Python = Join-Path $Router '.venv\Scripts\python.exe'

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
  & $Program @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE." }
}

if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') { throw 'Windows PowerShell FullLanguage is required.' }
foreach ($Name in @('node', 'npm', 'python')) { Get-Command $Name -ErrorAction Stop | Out-Null }
$CodexDirectory = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$StateDirectory = if ($env:MODEL_ROUTER_STATE_DIR) { $env:MODEL_ROUTER_STATE_DIR } elseif ($env:CODEX_ROUTER_STATE_DIR) { $env:CODEX_ROUTER_STATE_DIR } elseif ($env:KIMI_CODEX_STATE_DIR) { $env:KIMI_CODEX_STATE_DIR } else { Join-Path $CodexDirectory 'codex-router' }
$Bin = Join-Path $env:USERPROFILE '.local\bin'
$Launcher = Join-Path $Bin 'jev-assist.cmd'
$Invocation = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $Repo.Replace('%', '%%') + '\jev-assist.ps1" %*'
$Command = "@echo off`r`nsetlocal DisableDelayedExpansion`r`n" +
  'if not defined CODEX_HOME set "CODEX_HOME=' + $CodexDirectory.Replace('%', '%%') + '"' + "`r`n" +
  'if /I "%CODEX_HOME%"=="' + $CodexDirectory.Replace('%', '%%') + '" if not defined MODEL_ROUTER_STATE_DIR if not defined CODEX_ROUTER_STATE_DIR if not defined KIMI_CODEX_STATE_DIR set "MODEL_ROUTER_STATE_DIR=' + $StateDirectory.Replace('%', '%%') + '"' + "`r`n" +
  $Invocation + "`r`n"
if ((Test-Path -LiteralPath $Launcher) -and -not [IO.File]::ReadAllText($Launcher).TrimEnd().EndsWith($Invocation)) {
  throw "Existing launcher belongs to another checkout: $Launcher"
}
Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$Repo\server\service-windows.ps1", 'status', '-CodexDirectory', $CodexDirectory, '-StateDirectory', $StateDirectory)
Invoke-Checked node @("$Router\src\legacy-migration.mjs", 'assert-clear')
if (-not $PrepareOnly) {
  Invoke-Checked python @('-c', "import pathlib,sys; files=[pathlib.Path.home()/'.hermes/.env',pathlib.Path.home()/'.jev.env']; present=any(line.strip().startswith('TYPESAFE_API_KEY=') and line.split('=',1)[1].strip() for p in files if p.is_file() for line in p.read_text(encoding='utf-8').splitlines()); sys.exit(0 if present else 'TypeSafe key missing: configure ~/.jev.env; scheduled tasks do not inherit this shell environment')")
}

Push-Location $Router
try {
  $DependencyPlan = & node src/install-plan.mjs status node-deps
  if ($LASTEXITCODE -ne 0 -or "$DependencyPlan".Trim() -ne 'skip') {
    Invoke-Checked npm @('ci', '--omit=dev')
    Invoke-Checked node @('src/install-plan.mjs', 'record', 'node-deps')
  }
} finally { Pop-Location }
if ($PrepareOnly) {
  $Scratch = Join-Path ([IO.Path]::GetTempPath()) ('jev-prepare-' + [guid]::NewGuid())
  $SavedEnvironment = @{}
  foreach ($Name in @('CODEX_HOME', 'MODEL_ROUTER_STATE_DIR', 'CODEX_ROUTER_STATE_DIR', 'KIMI_CODEX_STATE_DIR')) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name)
    [Environment]::SetEnvironmentVariable($Name, $null, 'Process')
  }
  try {
    $env:CODEX_HOME = $Scratch
    Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$Router\install.ps1", '-CheckoutInstall', '-PrepareOnly')
  } finally {
    foreach ($Name in $SavedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], 'Process') }
    $Resolved = [IO.Path]::GetFullPath($Scratch)
    $TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($Resolved.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $Resolved)) {
      Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
  }
} else {
  $Selection = Join-Path $StateDirectory 'enabled-providers.json'
  if (Test-Path -LiteralPath $Selection) {
    Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$Router\install.ps1", '-CheckoutInstall')
  } else {
    Invoke-Checked node @("$Router\src\setup.mjs", '--selection-only', '--no-provider', '--no-tray')
    Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$Router\install.ps1", '-CheckoutInstall')
  }
}
if (Get-Command uv -ErrorAction SilentlyContinue) {
  Invoke-Checked uv @('pip', 'install', '--python', $Python, '-r', "$Repo\server\requirements-windows.txt")
} else {
  Invoke-Checked $Python @('-m', 'pip', 'install', '-r', "$Repo\server\requirements-windows.txt")
}
if ($PrepareOnly) { Write-Output 'Dependencies prepared; Codex settings and services unchanged.'; exit 0 }

# Shared native routing explicitly needs the user's existing Codex session.
Invoke-Checked node @("$Router\src\discovery-mode.mjs", 'set', 'enabled')
# A missing provider is an expected non-zero result, not a PowerShell error.
try {
  $ErrorActionPreference = 'Continue'
  & node "$Router\src\providers.mjs" generic show jev --json 2>$null | Out-Null
} finally { $ErrorActionPreference = 'Stop' }
$Verb = if ($LASTEXITCODE -eq 0) { 'edit' } else { 'add' }
Invoke-Checked node @("$Router\src\providers.mjs", 'generic', $Verb, 'jev', '--name', 'Jev Router', '--base-url', 'http://127.0.0.1:4319/v1', '--adapter', 'openai-responses', '--allow-private')
Invoke-Checked node @("$Router\src\providers.mjs", 'generic', 'enable', 'jev')
Invoke-Checked node @("$Repo\server\configure-model.mjs")
Invoke-Checked node @("$Repo\server\configure-auth.mjs")
Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$Router\codex-router.ps1", 'chatgpt-session', 'enable')
Invoke-Checked node @("$Router\src\refresh-catalog.mjs")
Invoke-Checked node @("$Router\src\control.mjs", 'picker', 'set', 'jev/auto', 'show')
$StandaloneCodexDirectory = Join-Path $env:USERPROFILE '.codex'
$SavedCodexHome = $env:CODEX_HOME
$SavedRouterState = $env:MODEL_ROUTER_STATE_DIR
try {
  $env:CODEX_HOME = $StandaloneCodexDirectory
  $env:MODEL_ROUTER_STATE_DIR = $StateDirectory
  Invoke-Checked node @("$Router\src\config-manager.mjs", 'enable')
} finally {
  $env:CODEX_HOME = $SavedCodexHome
  $env:MODEL_ROUTER_STATE_DIR = $SavedRouterState
}
Invoke-Checked node @("$Repo\server\configure-orca.mjs", 'http://127.0.0.1:4202', (Join-Path $StateDirectory 'merged-models.json'))
Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$Repo\server\service-windows.ps1", 'install', '-CodexDirectory', $CodexDirectory, '-StateDirectory', $StateDirectory)
Invoke-Checked node @("$Router\src\service.mjs", 'restart')
if (-not $SkipSmoke) { Invoke-Checked $Python @("$Repo\server\smoke.py") }

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
[IO.File]::WriteAllText($Launcher, $Command)
$UserPath = [string][Environment]::GetEnvironmentVariable('Path', 'User')
if ($Bin -notin ($UserPath -split ';')) {
  [Environment]::SetEnvironmentVariable('Path', ($UserPath.TrimEnd(';') + ';' + $Bin), 'User')
}
Write-Output 'Installed. Terminal Codex and Orca global defaults now share Jev Assist; new Orca accounts require no setup. Start a new Codex process.'
