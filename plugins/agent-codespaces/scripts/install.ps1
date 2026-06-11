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

$VenvDir         = Join-Path $InstallDir '.venv'
$VenvPython      = Join-Path $VenvDir 'Scripts\python.exe'
$VenvBin         = Join-Path $VenvDir 'Scripts\agent-codespaces.exe'
# ssh-manager dir (contains pyproject.toml): plugin-vendored (marketplace
# layout) or repo-root (git checkout layout).
$SshMgrDir       = Join-Path $PluginDir 'libs\ssh-manager'
if (-not (Test-Path (Join-Path $SshMgrDir 'pyproject.toml'))) {
    $SshMgrDir   = Join-Path $RepoRoot 'libs\ssh-manager'
}

$DeploySourcePaths = @('plugins/agent-codespaces/')
$InstallerRelPath  = 'plugins/agent-codespaces/scripts/install.ps1'

# -- Helpers ---------------------------------------------------------------

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
function Get-SourceKind {
    param([string]$PluginPath)
    if (($PluginPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        if (git -C $Path status --porcelain 2>$null) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

function Get-InstalledPackageDir {
    param([string]$Python, [string]$Module)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $dir = & $Python -c "import $Module, os; print(os.path.dirname($Module.__file__))" 2>$null
    $ErrorActionPreference = $prevEAP
    if ($dir) { return ($dir | Out-String).Trim() }
    return $null
}

function Stamp-BuildInfo {
    <# Stamp _build_info.py into the INSTALLED site-packages copy (post-install).
       agent-codespaces ships no _build_info.py in source, so this provides the
       version/commit that `agent-codespaces version` reports. #>
    param([string]$Python)
    $pkgDir = Get-InstalledPackageDir -Python $Python -Module 'agent_codespaces'
    if (-not $pkgDir) {
        Write-ServiceWarn "Could not locate installed agent_codespaces -- build info not stamped"
        return
    }
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $git = Get-GitInfo -Path $RepoRoot
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
    "commit": "$($git.commit)",
    "branch": "$($git.branch)",
    "build_timestamp": "$ts",
    "source": "$srcNorm",
}
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $pkgDir '_build_info.py'), $biContent, $utf8NoBom)
}

function Assert-Uv {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required but not found on PATH. Install uv and retry."
    }
}

function Install-PackageInto {
    <# uv pip install ssh-manager (sibling lib) then agent-codespaces into the
       given venv python. Non-editable; deps resolved from pyproject.toml. #>
    param([string]$Python)
    if (-not (Test-Path (Join-Path $SshMgrDir 'pyproject.toml'))) {
        Write-ServiceErr "ssh-manager source not found at $SshMgrDir"
        return $false
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv pip install --python $Python --reinstall-package agent-ssh-manager "$SshMgrDir" --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = $prevEAP
        Write-ServiceErr "ssh-manager install failed"
        return $false
    }
    & uv pip install --python $Python --reinstall-package agent-codespaces "$PluginDir" --quiet 2>&1 | Out-Null
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($rc -ne 0) {
        Write-ServiceErr "agent-codespaces install failed (exit $rc)"
        return $false
    }
    return $true
}

function Deploy-Package {
    <# Install agent-codespaces into its own venv and stamp build info. #>
    if (-not (Install-PackageInto -Python $VenvPython)) { return $false }
    Stamp-BuildInfo -Python $VenvPython
    Write-ServiceOk "Package installed into venv"

    # Keep the agent-bridge venv's in-process resolver in sync (issue #14): the
    # bridge imports agent_codespaces for the codespace: namespace + relay, so a
    # standalone codespaces update must refresh that copy or it drifts stale and
    # breaks codespace dispatch.
    $bridgePy = Join-Path $env:USERPROFILE '.agent-bridge\venv\Scripts\python.exe'
    if (Test-Path $bridgePy) {
        if (Install-PackageInto -Python $bridgePy) {
            Write-ServiceOk "Refreshed agent-bridge venv resolver copy"
        } else {
            Write-ServiceWarn "Could not refresh agent-bridge venv -- its codespace resolver may be stale"
        }
    }
    return $true
}

function Deploy-Venv {
    <# Create the Python venv via uv. Deps come from pyproject at package
       install time -- no ad-hoc pyyaml here. #>
    Assert-Uv
    if (-not (Test-Path $VenvDir)) {
        New-Item -ItemType Directory -Path $VenvDir -Force | Out-Null
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv venv $VenvDir --python 3.11 --allow-existing 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
    }
    $ErrorActionPreference = $prevEAP

    if (-not (Test-Path $VenvPython)) {
        Write-ServiceErr "Venv creation failed"
        return $false
    }
    Write-ServiceOk "Venv ready at $VenvDir"
    return $true
}

function Deploy-Binstub {
    <# Create the agent-codespaces binstub pointing at the venv console script
       (no PYTHONPATH). #>
    if (-not (Test-Path $LocalBin)) {
        New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null
    }
    $stubPath = Join-Path $LocalBin 'agent-codespaces.cmd'
    $stubContent = @"
@echo off
set "PYTHONUTF8=1"
"%USERPROFILE%\.agent-codespaces\.venv\Scripts\agent-codespaces.exe" %*
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
    <# Unified schema_version 3 manifest. Records the source footprint
       (local vs marketplace) and is written atomically (temp+move). #>
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $git = Get-GitInfo -Path $RepoRoot
        $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-codespaces'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-codespaces'
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($VenvDir -replace '\\', '/')
        runtime        = 'python'
    }
    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-ServiceOk "Deploy manifest written (source: $kind)"
}

# -- Actions ---------------------------------------------------------------

function Invoke-Install {
    Write-ServiceHeader $ServiceName

    # Create directories
    foreach ($dir in @($InstallDir, $LocalBin)) {
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

    # Verify the package imports from the venv (no PYTHONPATH). Retry briefly
    # for transient AV file locks.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
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

function Stop-ManagedSshConnections {
    <# Stop SSH ControlMaster processes this plugin started. They multiplex
       connections to CodeSpaces via sockets under ~/.agent-codespaces/sockets.
       A separate uninstall process can't reach ssh-manager's in-memory state,
       so close each master via `ssh -O exit` (best-effort) and then kill any
       lingering ssh.exe bound to the socket dir. #>
    $socketDir = Join-Path $InstallDir 'sockets'
    if (Test-Path $socketDir) {
        Get-ChildItem $socketDir -File -ErrorAction SilentlyContinue | ForEach-Object {
            $sock = $_.FullName
            # ssh -O exit needs a host arg; the socket already pins the target,
            # so any placeholder works to address the existing master.
            & ssh -o "ControlPath=$sock" -O exit placeholder *> $null 2>&1
        }
    }
    # Kill any ssh process still referencing our socket dir (orphaned masters).
    $needle = (Join-Path $InstallDir 'sockets') -replace '\\', '\\'
    Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'agent-codespaces' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-ServiceChanged "Stopped SSH ControlMaster (pid=$($_.ProcessId))"
        }
}

function Invoke-Uninstall {
    Write-ServiceHeader "$ServiceName Uninstall"

    # Stop managed SSH ControlMaster connections before removing files.
    Stop-ManagedSshConnections

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

    # Package (installed into the venv)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $VenvPython -c 'import agent_codespaces' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-ServiceOk "Package: agent_codespaces importable in venv"
    } else {
        Write-ServiceErr "Package not importable in venv"
    }
    & $VenvPython -c 'import ssh_manager' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-ServiceOk "ssh-manager: importable in venv"
    } else {
        Write-ServiceErr "ssh-manager not importable in venv"
    }
    $ErrorActionPreference = $prevEAP

    # Console script
    if (Test-Path $VenvBin) {
        Write-ServiceOk "Console script: $VenvBin"
    } else {
        Write-ServiceErr "Console script missing: $VenvBin"
    }

    # Binstub
    $stubPath = Join-Path $LocalBin 'agent-codespaces.cmd'
    if (Test-Path $stubPath) {
        Write-ServiceOk "Binstub: $stubPath"
    } else {
        Write-ServiceWarn "Binstub not found at $stubPath"
    }

    # Version (from the installed package)
    if (Test-Path $VenvBin) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $verInfo = & $VenvBin version 2>$null
        $ErrorActionPreference = $prevEAP
        if ($verInfo) {
            Write-ServiceOk "Version: $(($verInfo | Out-String).Trim())"
        }
    }

    # Deploy manifest + source footprint (local checkout vs marketplace)
    $manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifest) {
        try {
            $m = Get-Content $manifest -Raw | ConvertFrom-Json
            if ($m.source) {
                $extra = ''
                if ($m.source.kind -eq 'local' -and $m.source.commit) {
                    $extra = " @ $($m.source.commit)$(if ($m.source.dirty) { '+dirty' })"
                }
                Write-ServiceOk "Source: $($m.source.kind) ($($m.source.version))$extra"
            }
            Write-ServiceOk "Deployed: $($m.deployed_at)"
        } catch { }
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
