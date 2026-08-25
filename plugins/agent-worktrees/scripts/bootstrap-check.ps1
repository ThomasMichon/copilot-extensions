# Bootstrap hook -- runs on session start via hooks.json. hooks.json runs the
# PLUGIN PAYLOAD copy first, falling back to the deployed ~/.agent-worktrees\bin
# copy. Two jobs, both grace-window-cheap:
#   1. FIRST install (runtime not provisioned yet): fire the installer's cheap
#      'stamp' action so the self-provisioning agent-worktrees TOOL binstub lands
#      on PATH THIS session; the binstub builds the versioned venv on first use
#      (#1236/#1393). No venv build on the hook. Only fires from the plugin
#      payload (install.ps1 is a sibling) when the installer declares a 'stamp'
#      action; otherwise a setup hint (deployed-copy fallback).
#   2. RECONCILE (already provisioned via the full launcher install): refresh the
#      deployed lib-copy package when the source commit drifts.
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

$InstallDir = Join-Path $env:USERPROFILE '.agent-worktrees'
$LibDir     = Join-Path $InstallDir 'lib'
$PkgDst     = Join-Path $LibDir 'agent_worktrees'
$_r         = Join-Path $InstallDir 'bin\resolve-runtime.ps1'
$VenvPython = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
$Manifest   = Join-Path $InstallDir 'deploy-manifest.json'

# Is the tools-half runtime already provisioned? (#581/#1393: a `.venv` link OR a
# current-version marker whose slot python exists.) A tools-half box has no
# full-launcher resolve-runtime.ps1 (so $VenvPython is null) yet IS provisioned;
# don't mistake it for "not installed" and re-stamp/nag every session.
function Test-AwProvisioned {
    if (Test-Path (Join-Path $InstallDir '.venv')) { return $true }
    $cvMarker = Join-Path $InstallDir 'current-version'
    if (Test-Path $cvMarker) {
        $cv = ('' + (Get-Content $cvMarker -Raw)).Trim()
        if ($cv -and ((Test-Path (Join-Path $InstallDir "versions\$cv\Scripts\python.exe")) -or (Test-Path (Join-Path $InstallDir "versions/$cv/bin/python")))) { return $true }
    }
    return $false
}

# --- FIRST install (nothing provisioned yet): fire the installer's cheap 'stamp'
#     so the self-provisioning tool binstub lands on PATH this session; it builds
#     the versioned venv on first use (#1236/#1393). ---
if ((-not $VenvPython) -and (-not (Test-AwProvisioned))) {
    $installer = Join-Path $PSScriptRoot 'install.ps1'
    if ((Test-Path $installer) -and (Select-String -Path $installer -Pattern "'stamp'" -Quiet)) {
        $pw = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
        & $exe -NoProfile -ExecutionPolicy Bypass -File $installer stamp *> $null
        exit 0
    }
    # Deployed-copy fallback on a still-unprovisioned box -> setup hint.
    Write-Host ''
    Write-Host '[agent-worktrees] Runtime not installed.' -ForegroundColor Yellow
    Write-Host '  Ask Copilot to ''set up agent-worktrees'' to bootstrap the runtime.' -ForegroundColor DarkGray
    Write-Host ''
    exit 0
}

# Provisioned via the tools-half (versioned slot) but the full-launcher resolver
# isn't deployed -> nothing to reconcile via the legacy lib-copy path; no-op.
if (-not $VenvPython) { exit 0 }

# --- Installed: check if package is stale ---
if (-not (Test-Path $Manifest)) { exit 0 }

try {
    $m = Get-Content $Manifest -Raw | ConvertFrom-Json
    $pluginDir = $m.plugin_source
    if (-not $pluginDir -or -not (Test-Path $pluginDir)) { exit 0 }

    $PkgSrc = Join-Path $pluginDir 'src\agent_worktrees'
    if (-not (Test-Path $PkgSrc)) { exit 0 }

    $deployedCommit = $m.commit
    $currentCommit = $null
    try {
        $currentCommit = (git -C $pluginDir rev-parse HEAD 2>$null)
    } catch { }

    if (-not $deployedCommit -or -not $currentCommit -or $deployedCommit -eq $currentCommit) {
        exit 0
    }

    # Stale -- re-deploy package
    Write-Host '[agent-worktrees] Updating runtime payload...' -ForegroundColor DarkGray
    if (Test-Path $PkgDst) {
        Remove-Item $PkgDst -Recurse -Force
    }
    New-Item -ItemType Directory -Path $LibDir -Force | Out-Null
    Copy-Item $PkgSrc $PkgDst -Recurse

    # Stamp build info so --version reflects the update
    $buildInfoPath = Join-Path $PkgDst '_build_info.py'
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $branch = ''
    try { $branch = (git -C $pluginDir rev-parse --abbrev-ref HEAD 2>$null) } catch { }
    if (-not $branch) { $branch = 'unknown' }
    $ver = '0.0.0'
    $pyproj = Join-Path $pluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    $buildContent = @"
`"`"`"Build provenance -- auto-generated at deploy time. Do not edit.`"`"`"

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$ver",
    "commit": "$currentCommit",
    "branch": "$branch",
    "build_timestamp": "$ts",
    "source": "$($pluginDir -replace '\\', '/')",
}
"@
    $utf8NoBomBi = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($buildInfoPath, $buildContent, $utf8NoBomBi)

    $m.commit = $currentCommit
    $m.deployed_at = (Get-Date -Format 'o')
    # Add or update dirty flag (PS5-safe: use Add-Member for new properties)
    if ($m.PSObject.Properties['dirty']) {
        $m.dirty = $false
    } else {
        $m | Add-Member -NotePropertyName 'dirty' -NotePropertyValue $false -Force
    }
    $manifestJson = $m | ConvertTo-Json -Depth 4
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Manifest, $manifestJson, $utf8NoBom)

    Write-Host '[agent-worktrees] Runtime updated.' -ForegroundColor DarkGray
} catch { }

exit 0
