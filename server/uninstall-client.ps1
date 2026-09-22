param(
  [string]$CodexHome
)
$ErrorActionPreference = 'Stop'
$Base = [IO.Path]::GetFullPath($(if ($CodexHome) { $CodexHome } else { Join-Path $env:USERPROFILE '.codex' }))
$Runtime = Join-Path $Base 'jev-assist-client'
$ExpectedScript = Join-Path $Runtime 'server/local-client.mjs'
$Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$Name = "JevAssistClient-$Sid"
$Task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
if ($Task) {
  if ($Task.Actions.Count -ne 1) { throw 'Client task belongs to another installation' }
  $Action = $Task.Actions[0]
  $ExpectedArguments = '"' + $ExpectedScript + '"'
  $ExpectedWorkingDirectory = Split-Path -Parent $ExpectedScript
  if (-not [StringComparer]::OrdinalIgnoreCase.Equals($Action.Arguments, $ExpectedArguments) -or -not [StringComparer]::OrdinalIgnoreCase.Equals($Action.WorkingDirectory, $ExpectedWorkingDirectory)) {
    throw 'Client task belongs to another installation'
  }
  Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false
}

function Restore-Backup {
  param([string]$Target, [string]$Backup)
  if (-not (Test-Path -LiteralPath $Backup)) { return }
  $BackupHash = (Get-FileHash -LiteralPath $Backup).Hash
  if (Test-Path -LiteralPath $Target) {
    if ((Get-FileHash -LiteralPath $Target).Hash -ne $BackupHash) {
      $Snapshot = $Target + '.before-jev-uninstall'
      if (Test-Path -LiteralPath $Snapshot) { $Snapshot += '.' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff') }
      Move-Item -LiteralPath $Target -Destination $Snapshot
    } else {
      Remove-Item -LiteralPath $Target
    }
  }
  Move-Item -LiteralPath $Backup -Destination $Target
  if ((Get-FileHash -LiteralPath $Target).Hash -ne $BackupHash) { throw "Could not restore $Target" }
}

$Homes = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
[void]$Homes.Add($Base)
if (-not $CodexHome) {
  if ($env:CODEX_HOME) { [void]$Homes.Add([IO.Path]::GetFullPath($env:CODEX_HOME)) }
  $Accounts = Join-Path $env:APPDATA 'orca\codex-accounts'
  if (Test-Path -LiteralPath $Accounts) {
    foreach ($Account in Get-ChildItem -LiteralPath $Accounts -Directory) {
      $AccountHome = Join-Path $Account.FullName 'home'
      if (Test-Path -LiteralPath (Join-Path $AccountHome 'config.toml')) { [void]$Homes.Add([IO.Path]::GetFullPath($AccountHome)) }
    }
  }
}

$ManagedSkill = $null
$Template = Join-Path $Runtime 'skills/jev-assist/SKILL.md'
$ClientConfig = Join-Path $Runtime 'server/client.json'
if ((Test-Path -LiteralPath $Template) -and (Test-Path -LiteralPath $ClientConfig)) {
  try {
    $Origin = ([IO.File]::ReadAllText($ClientConfig) | ConvertFrom-Json).origin
    $ManagedSkill = [IO.File]::ReadAllText($Template).Replace('http://127.0.0.1:4320', $Origin)
  } catch {}
}

foreach ($ConfiguredHome in $Homes) {
  Restore-Backup (Join-Path $ConfiguredHome 'config.toml') (Join-Path $ConfiguredHome 'config.toml.before-jev-local')
  Restore-Backup (Join-Path $ConfiguredHome 'hooks.json') (Join-Path $ConfiguredHome 'hooks.json.before-jev-observer')
  $Skill = Join-Path $ConfiguredHome 'skills/jev-assist/SKILL.md'
  $SkillBackup = $Skill + '.before-jev-http'
  $CurrentSkill = if (Test-Path -LiteralPath $Skill) { [IO.File]::ReadAllText($Skill).Replace("`r`n", "`n") } else { $null }
  if (Test-Path -LiteralPath $SkillBackup) {
    Restore-Backup $Skill $SkillBackup
  } elseif ($ManagedSkill -ne $null -and $CurrentSkill -ceq $ManagedSkill.Replace("`r`n", "`n")) {
    Remove-Item -LiteralPath $Skill
    $SkillDirectory = Split-Path -Parent $Skill
    if (-not (Get-ChildItem -LiteralPath $SkillDirectory -Force)) { Remove-Item -LiteralPath $SkillDirectory }
  }
}

if (Test-Path -LiteralPath $Runtime) {
  $ResolvedRuntime = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Runtime).Path)
  if (-not [StringComparer]::OrdinalIgnoreCase.Equals((Split-Path -Parent $ResolvedRuntime), $Base)) {
    throw 'Refusing to remove a runtime outside Codex home'
  }
  Remove-Item -LiteralPath $ResolvedRuntime -Recurse -Force
}
Write-Output 'Jev Assist uninstalled. Fully quit and reopen Codex/Orca.'
