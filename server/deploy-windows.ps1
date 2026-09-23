param([ValidateSet('install', 'run', 'status', 'uninstall')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repo = Split-Path $PSScriptRoot
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Name = "JevAssistDeploy-$Sid"
$PowerShell = Join-Path $PSHOME 'powershell.exe'
$Arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" run'
$Existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
$StateDir = Join-Path $env:USERPROFILE '.codex\codex-router'
$Log = Join-Path $StateDir 'buildbox-deploy.log'

function Write-Log([string]$Message) {
  Add-Content -LiteralPath $Log -Encoding UTF8 -Value ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

function Test-JevHealthy {
  try {
    $Health = Invoke-RestMethod 'http://127.0.0.1:4319/health' -TimeoutSec 3
    return [bool]($Health.ok -and $Health.service -eq 'jev-router')
  } catch { return $false }
}

function Invoke-Git([string[]]$Arguments) {
  $Output = & git -C $Repo @Arguments
  if ($LASTEXITCODE) { throw "git $($Arguments -join ' ') failed" }
  return "$Output".Trim()
}

function Wait-Health([string]$Uri, [string]$Service) {
  $Deadline = (Get-Date).AddSeconds(30)
  do {
    Start-Sleep -Milliseconds 500
    try {
      $Health = Invoke-RestMethod $Uri -TimeoutSec 2
      if ($Health.ok -and $Health.service -eq $Service) { return }
    } catch {}
  } while ((Get-Date) -lt $Deadline)
  throw "$Service health check failed"
}

function Restart-Jev {
  $Config = Get-Content "$env:USERPROFILE\.codex\codex-router\jev-http.json" -Raw | ConvertFrom-Json
  $Python = Join-Path $Repo 'router\.venv\Scripts\python.exe'
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --quiet --python $Python -r "$Repo\server\requirements-windows.txt"
  } else {
    & $Python -m pip install --quiet -r "$Repo\server\requirements-windows.txt"
  }
  if ($LASTEXITCODE) { throw 'Python dependency install failed' }

  & $PowerShell -NoProfile -ExecutionPolicy Bypass -File "$Repo\server\service-windows.ps1" install `
    -CodexDirectory (Split-Path $Config.stateDir) -StateDirectory $Config.stateDir
  if ($LASTEXITCODE) { throw 'Jev service restart failed' }

  $Gateway = Get-ScheduledTask -TaskName "JevHttpGateway-$Sid" -ErrorAction Stop
  $Gateway | Stop-ScheduledTask
  for ($Attempt = 0; $Attempt -lt 20 -and $Gateway.State -eq 'Running'; $Attempt++) {
    Start-Sleep -Milliseconds 250
    $Gateway = Get-ScheduledTask -TaskName $Gateway.TaskName
  }
  $Gateway | Start-ScheduledTask
  Wait-Health "$($Config.origin)/health" 'jev-lan-gateway'
}

function Deploy {
  $Dirty = Invoke-Git @('status', '--porcelain')
  if ($Dirty) { throw 'Buildbox checkout has local changes' }
  $Before = Invoke-Git @('rev-parse', 'HEAD')
  Invoke-Git @('fetch', '--quiet', '--no-tags', 'origin', 'main') | Out-Null
  $Target = Invoke-Git @('rev-parse', 'origin/main')
  $Moved = $false

  if ($Target -ne $Before) {
    & git -C $Repo merge-base --is-ancestor $Before $Target
    if ($LASTEXITCODE) { throw 'origin/main is not a fast-forward' }
    $Remote = Invoke-Git @('remote', 'get-url', 'origin')
    if ($Remote -notmatch '^https://github\.com/([^/]+)/([^/]+)\.git$') { throw 'Expected a public GitHub HTTPS origin' }
    $Runs = Invoke-RestMethod "https://api.github.com/repos/$($Matches[1])/$($Matches[2])/actions/workflows/ci.yml/runs?head_sha=$Target&event=push&per_page=10" `
      -Headers @{'Accept'='application/vnd.github+json'; 'User-Agent'='jev-assist-buildbox'} -TimeoutSec 15
    if ($Runs.workflow_runs | Where-Object { $_.head_sha -eq $Target -and $_.status -eq 'completed' -and $_.conclusion -eq 'success' }) {
      Write-Log "update $($Before.Substring(0, 7)) -> $($Target.Substring(0, 7))"
      Invoke-Git @('merge', '--ff-only', $Target) | Out-Null
      $Moved = $true
    }
  }

  # No rollback on a failed restart: reverting the checkout and restarting a
  # second time took the service down twice per run without ever fixing the
  # cause. Advance once, and let the health probe retry the restart on the
  # next run until it succeeds.
  if (-not $Moved -and (Test-JevHealthy)) { return }
  Restart-Jev
  Write-Log "restarted at $(Invoke-Git @('rev-parse', '--short', 'HEAD'))"
}

switch ($Action) {
  'status' {
    if ($Existing) { $Existing | Select-Object TaskName, State } else { Write-Output 'Buildbox auto-deploy is not installed.' }
  }
  'uninstall' {
    if ($Existing) {
      Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
      Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
  }
  'install' {
    if ($Existing -and ($Existing.Actions.Count -ne 1 -or $Existing.Actions[0].Execute -ne $PowerShell -or $Existing.Actions[0].Arguments -ne $Arguments)) {
      throw "Task $Name belongs to another checkout"
    }
    $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
      -RepetitionInterval (New-TimeSpan -Minutes 2) -RepetitionDuration (New-TimeSpan -Days 3650)
    $TaskAction = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $Repo
    $Principal = New-ScheduledTaskPrincipal -UserId $Sid -LogonType Interactive -RunLevel Limited
    $Settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries `
      -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    Register-ScheduledTask -TaskName $Name -Action $TaskAction -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
    Start-ScheduledTask -TaskName $Name
    Write-Output "Buildbox auto-deploy installed: $Name"
  }
  'run' {
    $Lock = Join-Path $StateDir 'buildbox-deploy.lock'
    if ((Test-Path -LiteralPath $Lock) -and ((Get-Date) - (Get-Item -LiteralPath $Lock).LastWriteTime).TotalMinutes -lt 30) { return }
    New-Item -ItemType File -Path $Lock -Force | Out-Null
    try { Deploy }
    catch { Write-Log "ERROR: $($_.Exception.Message)"; throw }
    finally { Remove-Item -LiteralPath $Lock -Force -ErrorAction SilentlyContinue }
  }
}
