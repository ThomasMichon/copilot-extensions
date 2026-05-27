# Bootstrap hook -- runs on session start via hooks.json
# Auto-updates the agent-worktrees runtime payload when stale.
# If not installed, prints a hint (full install requires interactive setup).
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

$InstallDir = Join-Path $env:USERPROFILE '.agent-worktrees'
$LibDir     = Join-Path $InstallDir 'lib'
$PkgDst     = Join-Path $LibDir 'agent_worktrees'
$VenvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
$Manifest   = Join-Path $InstallDir 'deploy-manifest.json'

# --- Not installed: hint only (install needs interactive machine selection) ---
if (-not (Test-Path $VenvPython)) {
    Write-Host ''
    Write-Host '[agent-worktrees] Runtime not installed.' -ForegroundColor Yellow
    Write-Host '  Ask Copilot to ''set up agent-worktrees'' to bootstrap the runtime.' -ForegroundColor DarkGray
    Write-Host ''
    exit 0
}

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
    $buildContent = @"
`"`"`"Build provenance -- auto-generated at deploy time. Do not edit.`"`"`"

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "1.0.0",
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
