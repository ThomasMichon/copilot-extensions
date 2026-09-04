#Requires -Version 7.0
<#!
.SYNOPSIS
    Install and initialize the dtssh client side, then discover live hosts.
#>
param(
    [switch]$SkipLogin,
    [switch]$SkipDiscover,
    [switch]$Prune,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

# === install-contract:test-persistent-environment -- keep byte-identical across installers ===
function Get-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    return [Environment]::GetEnvironmentVariable($Name, $effectiveTarget)
}

function Set-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][string]$Value,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    [Environment]::SetEnvironmentVariable($Name, $Value, $effectiveTarget)
}
# === end install-contract:test-persistent-environment ===
$InstallRelease = 'https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.ps1'
$DtsshDir = Join-Path $env:LOCALAPPDATA 'dtssh\bin'
$DtsshExe = Join-Path $DtsshDir 'dtssh.exe'

function Add-UserPath([string]$PathToAdd) {
    $userPath = Get-CopilotPersistentEnvironmentVariable -Name 'Path' -Target 'User'
    if ($userPath -notlike "*$PathToAdd*") {
        Set-CopilotPersistentEnvironmentVariable -Name 'Path' -Value "$PathToAdd;$userPath" -Target 'User'
    }
    if ($env:Path -notlike "*$PathToAdd*") { $env:Path = "$PathToAdd;$env:Path" }
}

function Test-DevTunnelLogin {
    # dtssh has no `login --status` subcommand; query the bundled devtunnel CLI.
    param([Parameter(Mandatory)][string]$DtsshPath)
    $devtunnel = Join-Path (Split-Path $DtsshPath -Parent) 'devtunnel.exe'
    if (-not (Test-Path $devtunnel)) {
        $cmd = Get-Command devtunnel -ErrorAction SilentlyContinue
        if (-not $cmd) { return $false }
        $devtunnel = $cmd.Source
    }
    try {
        $json = & $devtunnel user show --json 2>$null | Out-String
        return ($json -match '"status"\s*:\s*"Logged in"')
    } catch { return $false }
}

if (-not (Test-Path $DtsshExe)) {
    Write-Host "Installing dtssh..."
    $ProgressPreference = 'SilentlyContinue'
    Invoke-RestMethod $InstallRelease | Invoke-Expression | Out-Null
}
if (-not (Test-Path $DtsshExe)) { throw "dtssh install did not produce $DtsshExe" }
Add-UserPath $DtsshDir

if (-not (Get-Command devtunnel -ErrorAction SilentlyContinue)) {
    Write-Host "devtunnel CLI not found; dtssh login can auto-download it, but winget install is preferred on Windows."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Microsoft.devtunnel -e --accept-source-agreements --accept-package-agreements
    }
}

if (-not $SkipLogin) {
    if (-not (Test-DevTunnelLogin -DtsshPath $DtsshExe)) {
        Write-Host "Starting dtssh login. Complete the Entra/WAM prompt, then return here."
        & $DtsshExe login
    }
}

if (-not $SkipDiscover) {
    $args = @('discover')
    if ($Prune) { $args += '--prune' }
    if ($Clean) { $args += '--clean' }
    & $DtsshExe @args
}

& $DtsshExe list
