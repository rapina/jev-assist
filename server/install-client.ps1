param(
  [Parameter(Mandatory)][string]$ServiceUrl,
  [string]$CodexHome
)
$ErrorActionPreference = 'Stop'
$Origin = [Uri]$ServiceUrl
if ($Origin.Scheme -notin @('http', 'https') -or $Origin.UserInfo -or $Origin.AbsolutePath -ne '/' -or $Origin.Query -or $Origin.Fragment) { throw 'Expected HTTP service origin' }
$ServiceUrl = $Origin.GetLeftPart([UriPartial]::Authority)
$Node = (Get-Command node -ErrorAction Stop).Source
$NodeVersion = & $Node -p process.versions.node
if ($LASTEXITCODE -or [version]$NodeVersion -lt [version]'22.19.0') { throw 'Node.js 22.19 or newer is required' }
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
