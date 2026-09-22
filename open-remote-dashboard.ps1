param(
  [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.@-]*$')][string]$SshHost,
  [string]$Checkout = 'C:\work\jev-codex-router'
)
$ErrorActionPreference = 'Stop'
$Ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Forward = '127.0.0.1:4319:127.0.0.1:4319'
$Arguments = "-NT -o BatchMode=yes -o ConnectTimeout=8 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L $Forward $SshHost"
$Listeners = @(Get-NetTCPConnection -LocalPort 4319 -State Listen -ErrorAction SilentlyContinue)
foreach ($Listener in $Listeners) {
  $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $($Listener.OwningProcess)"
  if (-not $Process -or $Process.ExecutablePath -ne $Ssh -or
      $Process.CommandLine.TrimEnd() -notmatch ([regex]::Escape($Arguments) + '$') -or
      (Invoke-CimMethod -InputObject $Process -MethodName GetOwnerSid).Sid -ne $Sid) {
    throw 'Port 4319 is occupied by another process. Stop the local Jev service before opening the remote dashboard.'
  }
}
if (-not $Listeners.Count) {
  $Tunnel = Start-Process -FilePath $Ssh -ArgumentList $Arguments -WindowStyle Hidden -PassThru
  Start-Sleep -Seconds 1
  if ($Tunnel.HasExited) { throw 'SSH forwarding failed; check your SSH alias and authentication.' }
}
$Remote = '$ProgressPreference=''SilentlyContinue''; ' + "Set-Location -LiteralPath '" + $Checkout.Replace("'", "''") + "\server'; & '..\router\.venv\Scripts\python.exe' -X utf8 -c 'from open_dashboard import login_url; print(login_url())'"
$Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Remote))
# The one-use login stays in memory; neither provider credentials nor URLs are logged.
$Url = (& $Ssh -o BatchMode=yes -o ConnectTimeout=8 $SshHost powershell.exe -NoProfile -EncodedCommand $Encoded) -join ''
if ($LASTEXITCODE -ne 0 -or $Url -notmatch '^http://127\.0\.0\.1:4319/dashboard/login\?code=[A-Za-z0-9_-]+$') {
  if ($Tunnel -and -not $Tunnel.HasExited) { Stop-Process -Id $Tunnel.Id }
  throw 'Remote dashboard login failed.'
}
Start-Process $Url
Write-Output "Dashboard: $SshHost via http://127.0.0.1:4319/dashboard"
