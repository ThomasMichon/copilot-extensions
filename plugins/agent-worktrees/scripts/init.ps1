<#
.SYNOPSIS
    Bootstrap the agent-worktrees runtime. PS5+ compatible.

.DESCRIPTION
    Creates the shared runtime at ~/.agent-worktrees/ -- venv, Python
    package (file copy), shell wrappers, bootstrap scripts, and the
    agent-worktrees binstub.

    Run once per machine. Idempotent -- safe to re-run for repairs or
    upgrades. Does NOT create per-project config or binstubs; use
    "agent-worktrees register" or the adopt skill for that.

    This script has no dependencies on service-utils.ps1 and works
    under both PowerShell 5.1 (powershell.exe) and PowerShell 7+ (pwsh).

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-worktrees).

.PARAMETER Force
    Re-create the venv even if it already exists.
#>
[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# -- Output helpers (PS5-safe) ------------------------------------------

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

# -- Paths --------------------------------------------------------------

$PluginDir  = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ScriptDir  = $PSScriptRoot
$BinSrcDir  = Join-Path $PluginDir 'bin'
$PkgSrcDir  = Join-Path $PluginDir 'src\agent_worktrees'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-worktrees'
}
$LibDir     = Join-Path $InstallDir 'lib'
$BinDir     = Join-Path $InstallDir 'bin'
$VenvDir    = Join-Path $InstallDir '.venv'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}

# -- Preflight checks --------------------------------------------------

Write-Host ''
Write-Host '=== agent-worktrees init ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    Write-Host "  Are you running this from the correct plugin directory?"
    exit 1
}

# Check for winget (Windows package installer)
$hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

# Find a Python interpreter (skip Windows Store aliases that aren't real)
$pythonCmd = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $testOut = & $found.Source --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') {
                $pythonCmd = $found.Source
            }
        } catch { }
        $ErrorActionPreference = $prevEAP
        if ($pythonCmd) { break }
    }
}
if (-not $pythonCmd) {
    if ($hasWinget) {
        Write-Step 'Python not found -- installing via winget...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & winget install --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        # Refresh PATH to pick up newly installed Python
        $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        foreach ($candidate in @('python', 'python3', 'py')) {
            $found = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($found) {
                $prevEAP = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    $testOut = & $found.Source --version 2>&1
                    if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') {
                        $pythonCmd = $found.Source
                    }
                } catch { }
                $ErrorActionPreference = $prevEAP
                if ($pythonCmd) { break }
            }
        }
    }
    if (-not $pythonCmd) {
        Write-Fail 'Python not found on PATH (need 3.10+)'
        Write-Host '  Install Python from https://python.org or via winget:' -ForegroundColor DarkGray
        Write-Host '    winget install Python.Python.3.13' -ForegroundColor DarkGray
        exit 1
    }
}

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$pyVer = & $pythonCmd -c "import sys; print('{0}.{1}'.format(sys.version_info.major, sys.version_info.minor))" 2>$null
$ErrorActionPreference = $prevEAP
Write-Ok "Python: $pythonCmd ($pyVer)"

$gitVer = git --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Fail 'git not found on PATH'
    exit 1
}
Write-Ok "Git: $gitVer"

# Check for uv (fast Python package manager) -- install if missing
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if ($hasWinget) {
        Write-Step 'uv not found -- installing via winget...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            Write-Ok 'uv installed'
        }
    }
}

# -- 1. Create directories ---------------------------------------------

foreach ($dir in @($InstallDir, $LibDir, $BinDir, $LocalBin)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Directories: $InstallDir"

# -- 2. Create venv ----------------------------------------------------

if ($Force -or -not (Test-Path $VenvPython)) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) {
        Write-Step 'Creating venv via uv...'
        $uvArgs = @('venv', $VenvDir, '--allow-existing')
        & uv @uvArgs 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Step 'uv venv failed -- falling back to python -m venv'
            & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
        }
    } else {
        Write-Step 'Creating venv via python -m venv...'
        & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
    }
    $ErrorActionPreference = $prevEAP

    if (-not (Test-Path $VenvPython)) {
        Write-Fail "Venv creation failed -- $VenvPython not found"
        exit 1
    }
    Write-Ok 'Venv created'
} else {
    Write-Skip 'Venv already exists'
}

# -- 3. Install dependencies into venv ---------------------------------

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
if ($uvCmd) {
    & uv pip install --python $VenvPython pyyaml 2>&1 | Out-Null
} else {
    & $VenvPython -m pip install --quiet pyyaml 2>&1 | Out-Null
}
$depResult = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($depResult -ne 0) {
    Write-Fail 'Failed to install pyyaml'
    exit 1
}
Write-Ok 'Dependencies: pyyaml'

# -- 4. Deploy package (file copy to lib/) -----------------------------

$PkgDst = Join-Path $LibDir 'agent_worktrees'
if (Test-Path $PkgDst) {
    Remove-Item $PkgDst -Recurse -Force
}
Copy-Item $PkgSrcDir $PkgDst -Recurse

# Stamp build info so --version reflects this deployment
$buildInfoPath = Join-Path $PkgDst '_build_info.py'
$ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$commit = ''
$branch = ''
try {
    $repoRoot = Split-Path (Split-Path $PluginDir)
    $commit = (git -C $repoRoot rev-parse HEAD 2>$null)
    $branch = (git -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null)
} catch { }
if (-not $commit) { $commit = 'unknown' }
if (-not $branch) { $branch = 'unknown' }
$srcNorm = ($PluginDir -replace '\\', '/')
$ver = '0.0.0'
$pyproj = Join-Path $PluginDir 'pyproject.toml'
if (Test-Path $pyproj) {
    $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
    if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
}
$biContent = @"
`"`"`"Build provenance -- auto-generated at deploy time. Do not edit.`"`"`"

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$ver",
    "commit": "$commit",
    "branch": "$branch",
    "build_timestamp": "$ts",
    "source": "$srcNorm",
}
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($buildInfoPath, $biContent, $utf8NoBom)

Write-Ok "Package deployed to $PkgDst"

# -- 5. Deploy wrappers & bootstrap scripts ----------------------------

if ($env:OS -eq 'Windows_NT') {
    $wrappers = @('launch-session.cmd', 'launch-session.ps1')
} else {
    $wrappers = @('launch-session.sh')
}

foreach ($name in $wrappers) {
    $src = Join-Path $BinSrcDir $name
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $BinDir $name) -Force
        Write-Ok "Wrapper: $name"
    } else {
        Write-Fail "Wrapper not found: $src"
    }
}

foreach ($name in @('bootstrap-check.ps1', 'bootstrap-check.sh')) {
    $src = Join-Path $ScriptDir $name
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $BinDir $name) -Force
        Write-Ok "Bootstrap: $name"
    }
}

# -- 6. Deploy binstub -------------------------------------------------

if ($env:OS -eq 'Windows_NT') {
    $stubPath = Join-Path $LocalBin 'agent-worktrees.cmd'
    $stubContent = "@echo off`r`nset `"PYTHONUTF8=1`"`r`n`"%USERPROFILE%\.agent-worktrees\.venv\Scripts\python.exe`" -m agent_worktrees %*"
    [System.IO.File]::WriteAllText($stubPath, $stubContent)
    Write-Ok "Binstub: $stubPath"
} else {
    $stubPath = Join-Path $LocalBin 'agent-worktrees'
    $stubContent = "#!/usr/bin/env bash`nexport PYTHONUTF8=1`nexec `"`$HOME/.agent-worktrees/.venv/bin/python`" -m agent_worktrees `"`$@`""
    [System.IO.File]::WriteAllText($stubPath, $stubContent)
    & chmod +x $stubPath 2>$null
    Write-Ok "Binstub: $stubPath"
}

# -- 6b. Install terminal multiplexer (optional) ----------------------

if ($env:OS -eq 'Windows_NT') {
    # psmux -- PowerShell-native terminal multiplexer for session persistence
    if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) {
        if ($hasWinget) {
            Write-Step 'psmux not found -- installing via winget...'
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & winget install --id marlocarlo.psmux --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            $ErrorActionPreference = $prevEAP
            $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')
            if (Get-Command psmux -ErrorAction SilentlyContinue) {
                Write-Ok 'psmux installed (terminal multiplexer)'
            } else {
                Write-Step 'psmux install may need a shell restart to take effect'
            }
        } else {
            Write-Step 'psmux not found -- install manually: winget install marlocarlo.psmux'
        }
    } else {
        Write-Ok 'psmux: already installed'
    }
} else {
    # tmux -- standard terminal multiplexer for Linux session persistence
    if (-not (Get-Command tmux -ErrorAction SilentlyContinue)) {
        Write-Step 'tmux not found -- install with your package manager (apt install tmux, etc.)'
    } else {
        Write-Ok 'tmux: already installed'
    }
}

# -- 7. Write deploy manifest ------------------------------------------

$manifestPath = Join-Path $InstallDir 'deploy-manifest.json'

$repoRoot = $null
try {
    $repoRoot = (git -C $PluginDir rev-parse --show-toplevel 2>$null)
} catch { }

$commit = $null
if ($repoRoot) {
    try { $commit = (git -C $repoRoot rev-parse HEAD 2>$null) } catch { }
}

# Build manifest as hashtable, convert to JSON (PS5-safe)
$manifest = @{
    service       = 'agent-worktrees'
    commit        = $(if ($commit) { $commit } else { 'unknown' })
    deployed_at   = (Get-Date -Format 'o')
    runtime       = 'python'
    plugin_source = $PluginDir
    install_dir   = $InstallDir
}

$manifestJson = $manifest | ConvertTo-Json -Depth 4
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)
Write-Ok "Manifest: $manifestPath"

# -- 8. Verify ---------------------------------------------------------

Write-Host ''
$env:PYTHONPATH = $LibDir
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$importCheck = & $VenvPython -c "import agent_worktrees; print('OK')" 2>$null
$ErrorActionPreference = $prevEAP
if ($importCheck -eq 'OK') {
    Write-Ok 'Verification: module imports successfully'
} else {
    Write-Fail 'Verification: module import failed'
    exit 1
}

# Check PATH and add ~/.local/bin if missing
$pathDirs = $env:PATH -split [System.IO.Path]::PathSeparator
$localBinNorm = $LocalBin.TrimEnd('\', '/')
$onPath = $false
foreach ($dir in $pathDirs) {
    if ($dir.TrimEnd('\', '/') -eq $localBinNorm) {
        $onPath = $true
        break
    }
}
if ($onPath) {
    Write-Ok "PATH: $LocalBin is on PATH"
} else {
    # Add to persistent user PATH (registry)
    $userPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    $userDirs = if ($userPath) { $userPath -split ';' } else { @() }
    $alreadyPersisted = $false
    foreach ($dir in $userDirs) {
        if ($dir.TrimEnd('\', '/') -eq $localBinNorm) {
            $alreadyPersisted = $true
            break
        }
    }
    if (-not $alreadyPersisted) {
        $newUserPath = if ($userPath) { "$LocalBin;$userPath" } else { $LocalBin }
        [System.Environment]::SetEnvironmentVariable('PATH', $newUserPath, 'User')
        Write-Ok "PATH: Added $LocalBin to user PATH (persistent)"
    }
    # Also update current session so binstubs work immediately
    $env:PATH = "$LocalBin;$env:PATH"
    Write-Ok "PATH: Added $LocalBin to current session PATH"
}

Write-Host ''
Write-Host '=== Init complete ===' -ForegroundColor Green
Write-Host ''
Write-Host "  Runtime:  $InstallDir" -ForegroundColor DarkGray
Write-Host "  Binstub:  agent-worktrees" -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Next: cd into a repo and run: agent-worktrees register <project-name>' -ForegroundColor DarkGray
Write-Host '  Or ask Copilot to adopt a repo with the copilot-extensions-setup skill.' -ForegroundColor DarkGray
Write-Host ''
exit 0
