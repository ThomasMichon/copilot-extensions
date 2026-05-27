# Bootstrap hook — runs on session start via hooks.json
# Auto-updates the agent-worktrees runtime payload when stale.
# If not installed, prints a hint (full install requires interactive setup).

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
    $repoRoot = (Resolve-Path (Join-Path $pluginDir '..')).Path
    $currentCommit = (git -C $repoRoot rev-parse HEAD 2>$null)

    if (-not $deployedCommit -or -not $currentCommit -or $deployedCommit -eq $currentCommit) {
        exit 0
    }

    # Stale — re-deploy package
    Write-Host '[agent-worktrees] Updating runtime payload...' -ForegroundColor DarkGray
    if (Test-Path $PkgDst) {
        Remove-Item $PkgDst -Recurse -Force
    }
    New-Item -ItemType Directory -Path $LibDir -Force | Out-Null
    Copy-Item $PkgSrc $PkgDst -Recurse

    $m.commit = $currentCommit
    $m.deployed_at = (Get-Date -Format 'o')
    $m.dirty = $false
    $m | ConvertTo-Json -Depth 4 | Set-Content $Manifest -Encoding UTF8

    Write-Host '[agent-worktrees] Runtime updated.' -ForegroundColor DarkGray
} catch { }

exit 0
