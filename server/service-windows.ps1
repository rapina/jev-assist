param(
  [ValidateSet('install', 'status', 'stop', 'uninstall')][string]$Action = 'status',
  [string]$CodexDirectory = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }),
  [string]$StateDirectory = $(if ($env:MODEL_ROUTER_STATE_DIR) { $env:MODEL_ROUTER_STATE_DIR } elseif ($env:CODEX_ROUTER_STATE_DIR) { $env:CODEX_ROUTER_STATE_DIR } elseif ($env:KIMI_CODEX_STATE_DIR) { $env:KIMI_CODEX_STATE_DIR } else { Join-Path $CodexDirectory 'codex-router' })
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot
$Python = Join-Path $Repo 'router\.venv\Scripts\python.exe'
$Script = Join-Path $PSScriptRoot 'run-service.ps1'
$PowerShell = Join-Path $PSHOME 'powershell.exe'
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Name = "JevCodexRouter-$Sid"
$Arguments = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $Script + '" -CodexDirectory "' + $CodexDirectory + '" -StateDirectory "' + $StateDirectory + '"'
$Existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
if ($Existing -and ($Existing.Actions.Count -ne 1 -or $Existing.Actions[0].Execute -ne $PowerShell -or $Existing.Actions[0].Arguments -ne $Arguments)) {
  throw "Task $Name belongs to another checkout; refusing to replace it."
}
function Stop-JevTask {
  if (-not $Existing) { return }
  Stop-ScheduledTask -TaskName $Name
  # Windows venv redirectors can survive termination of the task's PowerShell.
  # Stop only our exact script, owned by this user, with our venv parent.
  $ServerScript = Join-Path $PSScriptRoot 'jev_server.py'
  $Tail = '\s+-X\s+utf8\s+"?' + [regex]::Escape($ServerScript) + '"?\s*$'
  $Marker = Join-Path $StateDirectory 'jev-service-process.json'
  if (-not (Test-Path -LiteralPath $Marker)) { return }
  $Owned = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
  if ($Owned.codex_home -ne $CodexDirectory -or $Owned.state -ne $StateDirectory) { return }
  foreach ($Connection in @(Get-NetTCPConnection -LocalPort 4319 -State Listen -ErrorAction SilentlyContinue)) {
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $($Connection.OwningProcess)"
    if (-not $Process -or $Process.CommandLine -notmatch $Tail) { continue }
    $Parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.ParentProcessId)"
    if (-not $Parent -or $Parent.ExecutablePath -ne $Python -or $Parent.CommandLine -notmatch $Tail) { continue }
    $ParentInfo = Get-Process -Id $Parent.ProcessId -ErrorAction SilentlyContinue
    if (-not $ParentInfo -or $Parent.ProcessId -ne $Owned.pid -or
        $ParentInfo.StartTime.ToUniversalTime().Ticks -ne $Owned.started) { continue }
    if ((Invoke-CimMethod -InputObject $Process -MethodName GetOwnerSid).Sid -ne $Sid -or
        (Invoke-CimMethod -InputObject $Parent -MethodName GetOwnerSid).Sid -ne $Sid) { continue }
    Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $Parent.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
switch ($Action) {
  'status' {
    if ($Existing) { $Existing | Select-Object TaskName, State } else { Write-Output 'Jev service is not installed.' }
  }
  'stop' { Stop-JevTask }
  'uninstall' {
    if ($Existing) {
      Stop-JevTask
      Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    }
  }
  'install' {
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Run the root install.ps1 first.' }
    Stop-JevTask
    # Do not mistake an unrelated listener for this task's healthy service.
    $Deadline = (Get-Date).AddSeconds(10)
    do {
      $Listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 4319)
      $Available = $false
      try { $Listener.Start(); $Available = $true } catch { } finally { $Listener.Stop() }
      if ($Available) { break }
      Start-Sleep -Milliseconds 250
    } while ($Existing -and (Get-Date) -lt $Deadline)
    if (-not $Available) { throw 'Port 4319 is still in use; refusing to replace its listener.' }
    $TaskAction = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $Repo
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Sid
    $Principal = New-ScheduledTaskPrincipal -UserId $Sid -LogonType Interactive -RunLevel Limited
    $Settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $Name -Action $TaskAction -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
    Start-ScheduledTask -TaskName $Name
    $Deadline = (Get-Date).AddSeconds(30)
    do {
      Start-Sleep -Milliseconds 500
      try {
        $Health = Invoke-RestMethod http://127.0.0.1:4319/health -TimeoutSec 2
        if ($Health.ok -and $Health.service -eq 'jev-router' -and $Health.auth_configured) {
          Write-Output "Jev service ready: $Name"
          exit 0
        }
      } catch { }
    } while ((Get-Date) -lt $Deadline)
    throw 'Jev service did not become healthy; inspect its task status and run server/jev_server.py in a terminal.'
  }
}
