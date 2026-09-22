param([string]$SkillsDirectory = $(if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex\skills' }))
$ErrorActionPreference = 'Stop'
$Source = Join-Path $PSScriptRoot 'skills\jev-assist'
$Destination = Join-Path $SkillsDirectory 'jev-assist'
if (Test-Path -LiteralPath $Destination) { throw "Skill already exists at $Destination; review it before updating." }
New-Item -ItemType Directory -Force -Path $SkillsDirectory | Out-Null
Copy-Item -LiteralPath $Source -Destination $Destination -Recurse
Write-Output "Installed skill: $Destination. Available on the next turn."
