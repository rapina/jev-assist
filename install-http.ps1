param(
  [Parameter(Mandatory)][string]$BindAddress,
  [Parameter(Mandatory)][string]$AllowedClient,
  [int]$Port = 4320
)
$ErrorActionPreference = 'Stop'
$null = [Net.IPAddress]::Parse($BindAddress)
$ClientParts = $AllowedClient.Split('/')
$ClientAddress = [Net.IPAddress]::Parse($ClientParts[0])
if ($ClientParts.Count -gt 2 -or ($ClientParts.Count -eq 2 -and ($ClientParts[1] -notmatch '^\d+$' -or [int]$ClientParts[1] -lt 1 -or [int]$ClientParts[1] -gt $(if ($ClientAddress.AddressFamily -eq 'InterNetwork') { 32 } else { 128 })))) { throw 'Expected client IP or CIDR subnet' }
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid port' }
$CodexDirectory = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$State = if ($env:MODEL_ROUTER_STATE_DIR) { $env:MODEL_ROUTER_STATE_DIR } elseif ($env:CODEX_ROUTER_STATE_DIR) { $env:CODEX_ROUTER_STATE_DIR } elseif ($env:KIMI_CODEX_STATE_DIR) { $env:KIMI_CODEX_STATE_DIR } else { Join-Path $CodexDirectory 'codex-router' }
$Config = Join-Path $State 'jev-http.json'
$Node = (Get-Command node.exe).Source
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Name = "JevHttpGateway-$Sid"
$Arguments = '"' + $PSScriptRoot + '\server\lan-gateway.mjs" --config "' + $Config + '"'
$Existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
if ($Existing -and ($Existing.Actions.Count -ne 1 -or $Existing.Actions[0].Execute -ne $Node -or $Existing.Actions[0].Arguments -ne $Arguments)) { throw 'Gateway task belongs to another checkout' }
if ($Existing) {
  Stop-ScheduledTask -TaskName $Name
  for ($Attempt = 0; $Attempt -lt 20 -and (Get-ScheduledTask -TaskName $Name).State -eq 'Running'; $Attempt++) { Start-Sleep -Milliseconds 250 }
}
$Listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse($BindAddress), $Port)
for ($Attempt = 0; ; $Attempt++) {
  try { $Listener.Start(); break }
  catch { if (-not $Existing -or $Attempt -ge 20) { throw }; Start-Sleep -Milliseconds 250 }
  finally { $Listener.Stop() }
}
Push-Location $PSScriptRoot
try {
  $SettingsJson = @{host=$BindAddress; port=$Port; origin="http://${BindAddress}:$Port"; stateDir=$State} | ConvertTo-Json -Compress
  $Encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($SettingsJson))
  @'
import {writePrivateFile} from './router/src/file-security.mjs';
const [config,data] = process.argv.slice(2);
writePrivateFile(config,Buffer.from(data,'base64').toString('utf8'));
'@ | & $Node --input-type=module - $Config $Encoded
  if ($LASTEXITCODE) { throw 'Gateway config setup failed' }
} finally { Pop-Location }
$Rule = "Jev HTTP $Port ($Sid)"
if (Get-NetFirewallRule -DisplayName $Rule -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -DisplayName $Rule }
New-NetFirewallRule -DisplayName $Rule -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $BindAddress -LocalPort $Port -RemoteAddress $AllowedClient -Program $Node -Profile Any | Out-Null
$Action = New-ScheduledTaskAction -Execute $Node -Argument $Arguments -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Sid
$Principal = New-ScheduledTaskPrincipal -UserId $Sid -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $Name
$Ready = $false
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
  try {
    $Response = Invoke-WebRequest -UseBasicParsing -Uri "http://${BindAddress}:$Port/health" -TimeoutSec 2
    if ($Response.StatusCode -eq 200) { $Ready = $true; break }
  } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $Ready) { throw 'Gateway failed readiness check; inspect scheduled task and protected key files' }
Write-Output "Gateway installed: http://${BindAddress}:$Port; allowed client: $AllowedClient"
