<#
.SYNOPSIS
    Agent Codespaces - standardized installer interface.

.DESCRIPTION
    Manages the agent-codespaces infrastructure lifecycle: install, uninstall,
    status, update.

    Runtime (venv, package, ssh-manager) lives at ~/.agent-codespaces/.
    Binstub goes to ~/.local/bin/.

    Run from the repo root:
      pwsh -File plugins\agent-codespaces\scripts\install.ps1 install
      pwsh -File plugins\agent-codespaces\scripts\install.ps1 status

.PARAMETER Action
    Lifecycle action to perform.

.PARAMETER Force
    Overwrite without confirmation.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status', 'update')]
    [string]$Action = 'status',

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Load shared utilities (if available) --------------------------------

$serviceUtilsPath = Join-Path $PSScriptRoot 'service-utils.ps1'
$hasServiceUtils = Test-Path $serviceUtilsPath
if ($hasServiceUtils) {
    . $serviceUtilsPath
} else {
    # Inline minimal helpers when service-utils.ps1 is not present
    function Write-ServiceOk      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
    function Write-ServiceChanged { param([string]$Msg) Write-Host "  [->]   $Msg" -ForegroundColor Yellow }
    function Write-ServiceSkipped { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
    function Write-ServiceWarn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
    function Write-ServiceErr     { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
    function Write-ServiceHeader  { param([string]$Name) Write-Host "`n=== $Name ===" -ForegroundColor Cyan }
    function Ensure-InstallDir    { param([string]$Dir) if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null } }
}

# -- Metadata -------------------------------------------------------------

$ServiceName     = 'Agent Codespaces'
$InstallDir      = Join-Path $env:USERPROFILE '.agent-codespaces'
$LocalBin        = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir       = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$RepoRoot        = (Resolve-Path (Join-Path $PluginDir '..\..')).Path

$LibDir          = Join-Path $InstallDir 'lib'
$VenvDir         = Join-Path $InstallDir '.venv'
$VenvPython      = Join-Path $VenvDir 'Scripts\python.exe'
$PkgSrcDir       = Join-Path $PluginDir 'src\agent_codespaces'
# ssh-manager: prefer the plugin-vendored copy (marketplace layout), fall back
# to the repo-root copy (git checkout layout).
$SshMgrDir       = Join-Path $PluginDir 'libs\ssh-manager'
if (-not (Test-Path (Join-Path $SshMgrDir 'src\ssh_manager'))) {
    $SshMgrDir   = Join-Path $RepoRoot 'libs\ssh-manager'
}
$SshMgrSrc       = Join-Path $SshMgrDir 'src\ssh_manager'

$DeploySourcePaths = @('plugins/agent-codespaces/')
$InstallerRelPath  = 'plugins/agent-codespaces/scripts/install.ps1'

# -- Helpers ---------------------------------------------------------------

function Deploy-Package {
    <# Copy the agent_codespaces Python package to lib/. #>
    $dst = Join-Path $LibDir 'agent_codespaces'
    if (-not (Test-Path $PkgSrcDir)) {
        Write-ServiceErr "Package source not found: $PkgSrcDir"
        return $false
    }
    if (Test-Path $dst) {
        Remove-Item $dst -Recurse -Force
    }
    New-Item -ItemType Directory -Path (Split-Path $dst) -Force -ErrorAction SilentlyContinue | Out-Null
    Copy-Item $PkgSrcDir $dst -Recurse

    # Deploy ssh-manager
    $sshDst = Join-Path $LibDir 'ssh_manager'
    if (-not (Test-Path $SshMgrSrc)) {
        Write-ServiceErr "ssh-manager source not found: $SshMgrSrc"
        return $false
    }
    if (Test-Path $sshDst) {
        Remove-Item $sshDst -Recurse -Force
    }
    Copy-Item $SshMgrSrc $sshDst -Recurse
    Write-ServiceOk "ssh-manager deployed"

    # Stamp build info
    $buildInfoPath = Join-Path $dst '_build_info.py'
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $commit = ''; $branch = ''
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

    Write-ServiceOk "Package deployed to $dst"
    return $true
}

function Deploy-Venv {
    <# Create or update the Python venv. #>
    if (-not (Test-Path $VenvDir)) {
        New-Item -ItemType Directory -Path $VenvDir -Force | Out-Null
    }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) {
        & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & python -m venv $VenvDir 2>&1 | Out-Null
        }
    } else {
        & python -m venv $VenvDir 2>&1 | Out-Null
    }
    $ErrorActionPreference = $prevEAP

    if (-not (Test-Path $VenvPython)) {
        Write-ServiceErr "Venv creation failed"
        return $false
    }

    # Install pyyaml
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($uvCmd) {
        & uv pip install --python $VenvPython pyyaml 2>&1 | Out-Null
    } else {
        & $VenvPython -m pip install --quiet pyyaml 2>&1 | Out-Null
    }
    $ErrorActionPreference = $prevEAP

    Write-ServiceOk "Venv ready at $VenvDir"
    return $true
}

function Deploy-Binstub {
    <# Create the agent-codespaces binstub in ~/.local/bin/. #>
    if (-not (Test-Path $LocalBin)) {
        New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null
    }
    $stubPath = Join-Path $LocalBin 'agent-codespaces.cmd'
    $stubContent = @"
@echo off
set "PYTHONUTF8=1"
set "PYTHONPATH=%USERPROFILE%\.agent-codespaces\lib;%PYTHONPATH%"
"%USERPROFILE%\.agent-codespaces\.venv\Scripts\python.exe" -m agent_codespaces %*
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    Write-ServiceOk "Binstub: $stubPath"

    # Ensure ~/.local/bin is on User PATH
    $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-ServiceChanged "Added $LocalBin to User PATH"
    }
}

function Write-DeployManifest {
    <# Write deploy-manifest.json with provenance info. #>
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $commit = ''
    try { $commit = (git -C $RepoRoot rev-parse HEAD 2>$null) } catch { }
    if (-not $commit) { $commit = 'unknown' }
    $srcNorm = ($PluginDir -replace '\\', '/')
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $content = @"
{
  "service": "agent-codespaces",
  "commit": "$commit",
  "deployed_at": "$ts",
  "runtime": "python",
  "plugin_source": "$srcNorm",
  "install_dir": "$($InstallDir -replace '\\', '/')"
}
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($manifestPath, $content, $utf8NoBom)
    Write-ServiceOk "Deploy manifest: $manifestPath"
}

# -- Actions ---------------------------------------------------------------

function Invoke-Install {
    Write-ServiceHeader $ServiceName

    # Create directories
    foreach ($dir in @($InstallDir, $LibDir, $LocalBin)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }

    # Deploy venv
    if (-not (Deploy-Venv)) { return }

    # Deploy package
    if (-not (Deploy-Package)) { return }

    # Deploy binstub
    Deploy-Binstub

    # Write manifest
    Write-DeployManifest

    # Verify
    $env:PYTHONPATH = "$LibDir;$env:PYTHONPATH"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Verify by exit code, not stdout (PS 5.1 strips embedded double-quotes
    # passed to native processes). Retry briefly for transient AV file locks.
    $importOk = $false
    for ($i = 0; $i -lt 3; $i++) {
        & $VenvPython -c 'import agent_codespaces' 2>$null
        if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
        Start-Sleep -Seconds 1
    }
    $ErrorActionPreference = $prevEAP
    if ($importOk) {
        Write-ServiceOk 'Verification: module imports successfully'
    } else {
        Write-ServiceErr 'Verification: module import failed'
    }

    Write-Host ''
    Write-ServiceOk "$ServiceName installed"
}

function Invoke-Uninstall {
    Write-ServiceHeader "$ServiceName Uninstall"

    # Remove binstub
    $stubPath = Join-Path $LocalBin 'agent-codespaces.cmd'
    if (Test-Path $stubPath) {
        Remove-Item $stubPath -Force
        Write-ServiceChanged "Removed binstub: $stubPath"
    } else {
        Write-ServiceSkipped "Binstub not found"
    }

    # Remove install directory
    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
        Write-ServiceChanged "Removed: $InstallDir"
    } else {
        Write-ServiceSkipped "Install directory not found"
    }

    Write-ServiceOk "$ServiceName uninstalled"
}

function Invoke-Status {
    Write-ServiceHeader "$ServiceName Status"

    # Install dir
    if (Test-Path $InstallDir) {
        Write-ServiceOk "Install dir: $InstallDir"
    } else {
        Write-ServiceErr "Not installed ($InstallDir not found)"
        return
    }

    # Venv
    if (Test-Path $VenvPython) {
        Write-ServiceOk "Venv: $VenvDir"
    } else {
        Write-ServiceErr "Venv missing"
    }

    # Package
    $pkgDir = Join-Path $LibDir 'agent_codespaces'
    if (Test-Path $pkgDir) {
        Write-ServiceOk "Package: $pkgDir"
    } else {
        Write-ServiceErr "Package missing"
    }

    # ssh-manager
    $sshDir = Join-Path $LibDir 'ssh_manager'
    if (Test-Path $sshDir) {
        Write-ServiceOk "ssh-manager: $sshDir"
    } else {
        Write-ServiceErr "ssh-manager missing"
    }

    # Binstub
    $stubPath = Join-Path $LocalBin 'agent-codespaces.cmd'
    if (Test-Path $stubPath) {
        Write-ServiceOk "Binstub: $stubPath"
    } else {
        Write-ServiceWarn "Binstub not found at $stubPath"
    }

    # Build info
    $buildInfo = Join-Path $LibDir 'agent_codespaces\_build_info.py'
    if (Test-Path $buildInfo) {
        $env:PYTHONPATH = "$LibDir;$env:PYTHONPATH"
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $verInfo = & $VenvPython -c "
import sys; sys.path.insert(0, '$($LibDir -replace '\\', '/')')
from agent_codespaces._build_info import BUILD_INFO
print(f'v{BUILD_INFO[`"version`"]} ({BUILD_INFO[`"commit`"][:8]})')
" 2>$null
        $ErrorActionPreference = $prevEAP
        if ($verInfo) {
            Write-ServiceOk "Version: $verInfo"
        }
    }

    # Deploy manifest
    $manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifest) {
        $m = Get-Content $manifest -Raw | ConvertFrom-Json
        Write-ServiceOk "Deployed: $($m.deployed_at)"
    }

    # gh CLI
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        Write-ServiceOk "gh CLI: $($gh.Source)"
    } else {
        Write-ServiceWarn "gh CLI not found"
    }

    # ssh
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($ssh) {
        Write-ServiceOk "ssh: $($ssh.Source)"
    } else {
        Write-ServiceWarn "ssh not found"
    }
}

function Invoke-Update {
    Write-ServiceHeader "$ServiceName Update"

    if (-not (Test-Path $InstallDir)) {
        Write-ServiceWarn "Not installed -- running full install"
        Invoke-Install
        return
    }

    # Re-deploy venv (update deps)
    Deploy-Venv | Out-Null

    # Re-deploy package
    Deploy-Package | Out-Null

    # Re-deploy binstub
    Deploy-Binstub

    # Update manifest
    Write-DeployManifest

    Write-ServiceOk "$ServiceName updated"
}

# -- Dispatch --------------------------------------------------------------

switch ($Action) {
    'install'   { Invoke-Install }
    'uninstall' { Invoke-Uninstall }
    'status'    { Invoke-Status }
    'update'    { Invoke-Update }
}
