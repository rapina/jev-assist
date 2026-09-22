$ErrorActionPreference = 'Stop'
$Node = (Get-Command node -ErrorAction Stop).Source
$Script = Join-Path $PSScriptRoot 'local-client.mjs'
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Name = "JevAssistClient-$Sid"
$Arguments = '"' + $Script + '"'
$Existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
if ($Existing) {
  if ($Existing.Actions.Count -ne 1 -or $Existing.Actions[0].Execute -ne $Node -or $Existing.Actions[0].Arguments -ne $Arguments) { throw 'Client task belongs to another installation' }
  Stop-ScheduledTask -TaskName $Name
}
$Available = $false
for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
  $Listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 4321)
  try { $Listener.Start(); $Available = $true } catch {} finally { $Listener.Stop() }
  if ($Available) { break }
  Start-Sleep -Milliseconds 250
}
if (-not $Available) { throw 'Port 4321 is already in use; no unrelated process was stopped' }
$Action = New-ScheduledTaskAction -Execute $Node -Argument $Arguments -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Sid
$Principal = New-ScheduledTaskPrincipal -UserId $Sid -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $Name
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
  Start-Sleep -Milliseconds 500
  try {
    $Health = Invoke-RestMethod http://127.0.0.1:4321/health -TimeoutSec 2
    if ($Health.service -eq 'jev-local-client') { Write-Output 'Local-account execution transport ready'; exit 0 }
  } catch {}
}
throw 'Local client failed to start'
