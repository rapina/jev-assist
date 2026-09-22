param(
  [Parameter(Mandatory)][string]$ServiceUrl,
  [string]$CodexHome
)
$ErrorActionPreference = 'Stop'
$Origin = [Uri]$ServiceUrl
if ($Origin.Scheme -notin @('http', 'https') -or $Origin.UserInfo -or $Origin.AbsolutePath -ne '/' -or $Origin.Query -or $Origin.Fragment) { throw 'Expected HTTP service origin' }
$ServiceUrl = $Origin.GetLeftPart([UriPartial]::Authority)
function Find-Node {
  $Command = Get-Command node.exe -ErrorAction SilentlyContinue
  $Candidates = @(
    $(if ($Command) { $Command.Source }),
    "$env:ProgramFiles\nodejs\node.exe",
    "${env:ProgramFiles(x86)}\nodejs\node.exe",
    "$env:LOCALAPPDATA\Programs\nodejs\node.exe",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\node.exe"
  )
  foreach ($Candidate in $Candidates | Select-Object -Unique) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) { continue }
    $Version = & $Candidate -p process.versions.node 2>$null
    if (-not $LASTEXITCODE -and [version]$Version -ge [version]'22.19.0') { return $Candidate }
  }
}
$Node = Find-Node
if (-not $Node) {
  $Winget = Get-Command winget -ErrorAction SilentlyContinue
  if (-not $Winget) { throw 'Node.js 22.19+ is required and WinGet is unavailable. Install Node.js LTS, then run this command again. Docker is not required.' }
  Write-Output 'Installing Node.js LTS with WinGet...'
  & $Winget.Source install --id OpenJS.NodeJS.LTS --exact --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE) { throw 'Automatic Node.js installation failed. Install Node.js LTS, then run this command again. Docker is not required.' }
  $Node = Find-Node
  if (-not $Node) { throw 'Node.js installation completed but version 22.19+ was not found. Open a new PowerShell window and run this command again.' }
}
$env:Path = "$(Split-Path -Parent $Node);$env:Path"
$Base = if ($CodexHome) { [IO.Path]::GetFullPath($CodexHome) } else { Join-Path $env:USERPROFILE '.codex' }
$Runtime = Join-Path $Base 'jev-assist-client'
$Download = Join-Path ([IO.Path]::GetTempPath()) ('jev-assist-' + [Guid]::NewGuid().ToString('N') + '.zip')
try {
  Invoke-WebRequest -UseBasicParsing "$ServiceUrl/dashboard/client.zip" -OutFile $Download -TimeoutSec 30
  New-Item -ItemType Directory -Force $Runtime | Out-Null
  Expand-Archive -LiteralPath $Download -DestinationPath $Runtime -Force
  $Arguments = @((Join-Path $Runtime 'server/configure-client.mjs'), $ServiceUrl, (Join-Path $Runtime 'models.json'))
  if ($CodexHome) { $Arguments += @('--home', $Base) }
  & $Node @Arguments
  if ($LASTEXITCODE) { throw 'Client configuration failed; existing config backups are beside config.toml' }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Runtime 'server/install-local-service.ps1')
  if ($LASTEXITCODE) { throw 'Local execution transport installation failed' }
} finally {
  if (Test-Path -LiteralPath $Download) { Remove-Item -LiteralPath $Download }
}
Write-Output 'Jev Assist installed. Fully quit and reopen Codex/Orca, then start a NEW conversation.'
Write-Output 'Use /hooks to review and enable observation hooks. Routing does not require hook approval.'
