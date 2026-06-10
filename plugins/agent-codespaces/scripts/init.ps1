<#
.SYNOPSIS
    Bootstrap the agent-codespaces runtime. PS5+ compatible.

.DESCRIPTION
    Creates the shared runtime at ~/.agent-codespaces/ -- venv, Python
    package (file copy), ssh-manager dependency, and the agent-codespaces
    binstub.

    Run once per machine. Idempotent -- safe to re-run for repairs or
    upgrades.

    This script has no dependencies on service-utils.ps1 and works
    under both PowerShell 5.1 (powershell.exe) and PowerShell 7+ (pwsh).

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-codespaces).

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
$PkgSrcDir  = Join-Path $PluginDir 'src\agent_codespaces'

# ssh-manager: prefer the plugin-vendored copy (marketplace layout), fall back
# to the repo-root copy (git checkout layout).
$RepoRoot   = (Resolve-Path (Join-Path $PluginDir '..\..')).Path
$SshMgrDir  = Join-Path $PluginDir 'libs\ssh-manager'
if (-not (Test-Path (Join-Path $SshMgrDir 'src\ssh_manager'))) {
    $SshMgrDir = Join-Path $RepoRoot 'libs\ssh-manager'
}

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-codespaces'
}
$LibDir     = Join-Path $InstallDir 'lib'
$VenvDir    = Join-Path $InstallDir '.venv'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}

# -- Preflight checks --------------------------------------------------

Write-Host ''
Write-Host '=== agent-codespaces init ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    Write-Host "  Are you running this from the correct plugin directory?"
    exit 1
}

if (-not (Test-Path (Join-Path $SshMgrDir 'src\ssh_manager'))) {
    Write-Fail "ssh-manager not found (looked in plugin libs/ and repo libs/)"
    Write-Host "  Reinstall the agent-codespaces plugin from the marketplace:"
    Write-Host "    copilot plugin install agent-codespaces@copilot-extensions"
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

$ghVer = gh --version 2>$null | Select-Object -First 1
if ($LASTEXITCODE -ne 0) {
    Write-Step 'gh CLI not found -- agent-codespaces requires it for CodeSpace operations'
} else {
    Write-Ok "gh CLI: $ghVer"
}

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

foreach ($dir in @($InstallDir, $LibDir, $LocalBin)) {
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

$PkgDst = Join-Path $LibDir 'agent_codespaces'
if (Test-Path $PkgDst) {
    Remove-Item $PkgDst -Recurse -Force
}
Copy-Item $PkgSrcDir $PkgDst -Recurse

# Deploy ssh-manager alongside (agent_codespaces imports it)
$SshMgrSrc = Join-Path $SshMgrDir 'src\ssh_manager'
$SshMgrDst = Join-Path $LibDir 'ssh_manager'
if (Test-Path $SshMgrSrc) {
    if (Test-Path $SshMgrDst) {
        Remove-Item $SshMgrDst -Recurse -Force
    }
    Copy-Item $SshMgrSrc $SshMgrDst -Recurse
    Write-Ok "ssh-manager deployed to $SshMgrDst"
} else {
    Write-Fail "ssh-manager source not found at $SshMgrSrc"
    exit 1
}

# Stamp build info so --version reflects this deployment
$buildInfoPath = Join-Path $PkgDst '_build_info.py'
$ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$commit = ''
$branch = ''
try {
    $commit = (git -C $RepoRoot rev-parse HEAD 2>$null)
    $branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null)
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

# -- 5. Deploy binstub -------------------------------------------------

$stubName = 'agent-codespaces'
if ($env:OS -eq 'Windows_NT') {
    # .cmd wrapper for Windows
    $stubPath = Join-Path $LocalBin "$stubName.cmd"
    $stubContent = @"
@echo off
set "PYTHONUTF8=1"
set "PYTHONPATH=%USERPROFILE%\.agent-codespaces\lib;%PYTHONPATH%"
"%USERPROFILE%\.agent-codespaces\.venv\Scripts\python.exe" -m agent_codespaces %*
"@
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
} else {
    $stubPath = Join-Path $LocalBin $stubName
    $stubContent = @"
#!/usr/bin/env bash
export PYTHONUTF8=1
export PYTHONPATH="`$HOME/.agent-codespaces/lib`${PYTHONPATH:+:`$PYTHONPATH}"
exec "`$HOME/.agent-codespaces/.venv/bin/python" -m agent_codespaces "`$@"
"@
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
}
Write-Ok "Binstub: $stubPath"

# -- 6. Write deploy manifest ------------------------------------------

$manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$manifestContent = @"
{
  "service": "agent-codespaces",
  "commit": "$commit",
  "deployed_at": "$ts",
  "runtime": "python",
  "plugin_source": "$srcNorm",
  "install_dir": "$($InstallDir -replace '\\', '/')"
}
"@
[System.IO.File]::WriteAllText($manifestPath, $manifestContent, $utf8NoBom)
Write-Ok "Manifest: $manifestPath"

# -- 7. Verify ----------------------------------------------------------

Write-Host ''
$env:PYTHONPATH = "$LibDir;$env:PYTHONPATH"
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$importCheck = & $VenvPython -c 'import agent_codespaces; print("OK")' 2>$null
$ErrorActionPreference = $prevEAP
if ($importCheck -eq 'OK') {
    Write-Ok 'Verification: module imports successfully'
} else {
    Write-Fail 'Verification: module import failed'
    exit 1
}

# Check PATH
$pathDirs = $env:PATH -split ';'
if ($pathDirs -contains $LocalBin) {
    Write-Ok "PATH: $LocalBin is on PATH"
} else {
    # Add to User PATH permanently
    $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "PATH: Added $LocalBin to User PATH"
    }
}

Write-Host ''
Write-Host '=== agent-codespaces init complete ===' -ForegroundColor Green
Write-Host ''
