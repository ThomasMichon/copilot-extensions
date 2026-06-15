<#
.SYNOPSIS
    Bootstrap the agent-containers runtime. PS5+ compatible.

.DESCRIPTION
    Creates the shared runtime at ~/.agent-containers/ -- a venv with the
    agent_containers package installed (via uv pip install) -- and deploys the
    `agent-containers` binstub into ~/.local/bin.

    Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-containers).

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

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_containers'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-containers'
}
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

# === install-contract:v3 strip-trampolines -- keep byte-identical across plugins ===
function Remove-ConsoleTrampolines {
    <# Strip the uv-regenerated Scripts\<name>.exe console-script trampolines from
       the venv after install. They are unsigned, zero-reputation PEs that Smart
       App Control blocks (CodeIntegrity 3077); nothing launches them (binstubs,
       services, and probes all use "python.exe -m <pkg>"), so remove every
       agent-*.exe. Best-effort -- rename a locked copy aside, then sweep stale
       stashes. Windows-only: POSIX console scripts are the sanctioned launch
       path and must be preserved. #>
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($env:OS -ne 'Windows_NT') { return }
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    if (-not (Test-Path $scriptsDir)) { return }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe') -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Remove-Item $_.FullName -Force -ErrorAction Stop
        } catch {
            try { Rename-Item $_.FullName "$($_.FullName).old-$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop } catch {}
        }
    }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}
# === end install-contract:v3 strip-trampolines ===

# -- Preflight checks --------------------------------------------------

Write-Host ''
Write-Host '=== agent-containers init ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    Write-Host "  Are you running this from the correct plugin directory?"
    exit 1
}

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
    Write-Fail 'Python not found on PATH (need 3.10+)'
    Write-Host '  Install Python from https://python.org or via winget:' -ForegroundColor DarkGray
    Write-Host '    winget install Python.Python.3.13' -ForegroundColor DarkGray
    exit 1
}
Write-Ok "Python: $pythonCmd"

$dockerVer = docker --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Step 'docker CLI not found -- agent-containers requires Docker for fleet operations'
} else {
    Write-Ok "Docker: $dockerVer"
}

# Check for uv -- install via winget if missing
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if ($hasWinget) {
        Write-Step 'uv not found -- installing via winget...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Ok 'uv installed' }
    }
}

# -- 1. Create directories ---------------------------------------------

foreach ($dir in @($InstallDir, $LocalBin)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Directories: $InstallDir"

# -- 2. Create venv ----------------------------------------------------

if ($Force -or -not (Test-Path $VenvPython)) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Prefer a SAC-trusted signed base Python via `--copies` so the venv
    # python.exe is signed (Smart App Control blocks the unsigned uv-managed
    # python); then uv; then plain python -m venv.
    $signedBase = $null
    if ($env:OS -eq 'Windows_NT' -and (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($v in '3.13', '3.12', '3.11') {
            $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $cand -and (Test-Path $cand)) {
                try { if ((Get-AuthenticodeSignature $cand).Status -eq 'Valid') { $signedBase = $cand; break } } catch {}
            }
        }
    }
    if ($signedBase -and (Test-Path $VenvPython)) {
        try { if ((Get-AuthenticodeSignature $VenvPython).Status -ne 'Valid') { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop } } catch {}
    }
    if ($signedBase -and -not (Test-Path $VenvPython)) {
        & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
    }
    if (-not (Test-Path $VenvPython)) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            Write-Step 'Creating venv via uv...'
            & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Step 'uv venv failed -- falling back to python -m venv'
                & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
            }
        } else {
            Write-Step 'Creating venv via python -m venv...'
            & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
        }
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

# -- 3. Install the package into the venv (uv pip install) -------------

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
# Pre-strip any locked console-script trampoline so uv can overwrite it (os err 5).
Remove-ConsoleTrampolines -VenvDir $VenvDir
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1 | Out-Null
} else {
    & $VenvPython -m pip install --quiet "$PluginDir" 2>&1 | Out-Null
}
$pkgResult = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pkgResult -ne 0) {
    Write-Fail 'Failed to install agent-containers package into venv'
    exit 1
}

# Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused).
Remove-ConsoleTrampolines -VenvDir $VenvDir
Write-Ok 'Package installed: agent-containers'

# -- 4. Deploy binstub -------------------------------------------------

$stubName = 'agent-containers'
if ($env:OS -eq 'Windows_NT') {
    $stubPath = Join-Path $LocalBin "$stubName.cmd"
    $stubContent = @"
@echo off
set "PYTHONUTF8=1"
"%USERPROFILE%\.agent-containers\.venv\Scripts\python.exe" -m agent_containers %*
"@
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
} else {
    $stubPath = Join-Path $LocalBin $stubName
    $stubContent = @"
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "`$HOME/.agent-containers/.venv/bin/python" -m agent_containers "`$@"
"@
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
}
Write-Ok "Binstub: $stubPath"

# -- 5. Write deploy manifest ------------------------------------------

$commit = (git -C $PluginDir rev-parse --short HEAD 2>$null)
if ($LASTEXITCODE -ne 0) { $commit = 'unknown' }
$ts = (Get-Date).ToUniversalTime().ToString('o')
$srcNorm = $PluginDir -replace '\\', '/'
$manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$manifestContent = @"
{
  "service": "agent-containers",
  "commit": "$commit",
  "deployed_at": "$ts",
  "runtime": "python",
  "plugin_source": "$srcNorm",
  "install_dir": "$($InstallDir -replace '\\', '/')"
}
"@
[System.IO.File]::WriteAllText($manifestPath, $manifestContent, $utf8NoBom)
Write-Ok "Manifest: $manifestPath"

# -- 6. Verify ----------------------------------------------------------

Write-Host ''
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$importOk = $false
for ($i = 0; $i -lt 3; $i++) {
    & $VenvPython -c 'import agent_containers' 2>$null
    if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
    Start-Sleep -Seconds 1
}
$ErrorActionPreference = $prevEAP
if ($importOk) {
    Write-Ok 'Verification: module imports successfully'
} else {
    Write-Fail 'Verification: module import failed'
    exit 1
}

# Ensure ~/.local/bin is on PATH
$pathDirs = $env:PATH -split ';'
if ($pathDirs -contains $LocalBin) {
    Write-Ok "PATH: $LocalBin is on PATH"
} else {
    $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "PATH: Added $LocalBin to User PATH"
    }
}

Write-Host ''
Write-Host '=== agent-containers init complete ===' -ForegroundColor Cyan
Write-Host '  Try: agent-containers version' -ForegroundColor DarkGray
exit 0
