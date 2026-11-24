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
$InstallRelease = 'https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.ps1' # marketplace-isolation: allow third-party-installer-url
$DtsshDir = Join-Path $env:LOCALAPPDATA 'dtssh\bin'
$DtsshExe = Join-Path $DtsshDir 'dtssh.exe'

function Add-UserPath([string]$PathToAdd) {
    $userPath = Get-CopilotPersistentEnvironmentVariable -Name 'Path' -Target 'User'
    if ($userPath -notlike "*$PathToAdd*") {
        Set-CopilotPersistentEnvironmentVariable -Name 'Path' -Value "$PathToAdd;$userPath" -Target 'User'
    }
    if ($env:Path -notlike "*$PathToAdd*") { $env:Path = "$PathToAdd;$env:Path" }
}

function Install-ShimFreeDevTunnel {
    <#
      Put a real devtunnel.exe beside dtssh.exe when devtunnel otherwise only
      resolves through a reparse point (the WinGet Links symlink). Windows
      RedirectionGuard on hardened hosts (for example Dev Box) refuses to start
      such a link from a non-interactive SSH or service logon ("untrusted mount
      point"), which breaks every `ssh <alias>` ProxyCommand on the machine.
      dtssh resolves devtunnel from PATH and the dtssh bin dir is on the User
      PATH, so the sibling copy wins. Returns the sibling path when it is
      current, otherwise $null. Never throws: a locked or missing target warns.
    #>
    param([Parameter(Mandatory)][string]$BinDir)
    $sibling = Join-Path $BinDir 'devtunnel.exe'
    $existing = if (Test-Path -LiteralPath $sibling) { $sibling } else { $null }
    $other = @(Get-Command devtunnel -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -and -not [string]::Equals($_.Source, $sibling, [StringComparison]::OrdinalIgnoreCase) }) |
        Select-Object -First 1
    if (-not $other) { return $existing }
    $item = Get-Item -LiteralPath $other.Source -Force -ErrorAction SilentlyContinue
    if (-not $item -or -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $existing }
    # Follow the link text hop by hop: resolving the final target in one call
    # traverses the link, which RedirectionGuard refuses as well.
    $target = $item
    for ($hop = 0; $target -and ($target.Attributes -band [IO.FileAttributes]::ReparsePoint) -and $hop -lt 8; $hop++) {
        $text = $target.LinkTarget
        if (-not $text) { $target = $null; break }
        if (-not [IO.Path]::IsPathRooted($text)) { $text = Join-Path (Split-Path -Parent $target.FullName) $text }
        $target = Get-Item -LiteralPath $text -Force -ErrorAction SilentlyContinue
    }
    if (-not $target -or -not $target.Exists -or ($target.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Write-Warning "devtunnel resolves only through the link $($item.FullName) and its real binary was not found; over-SSH dtssh connections can fail with 'untrusted mount point'."
        return $existing
    }
    if ($existing -and (Get-FileHash -LiteralPath $sibling).Hash -eq (Get-FileHash -LiteralPath $target.FullName).Hash) {
        return $sibling
    }
    try {
        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
        Copy-Item -LiteralPath $target.FullName -Destination $sibling -Force
        Write-Host "Copied devtunnel.exe beside dtssh.exe from $($target.FullName) so it does not run through a WinGet Links link."
        return $sibling
    } catch {
        Write-Warning "Could not copy devtunnel.exe to ${sibling}: $($_.Exception.Message)"
        return $existing
    }
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
Install-ShimFreeDevTunnel -BinDir $DtsshDir | Out-Null

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
