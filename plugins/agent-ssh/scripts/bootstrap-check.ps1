<#
    agent-ssh session-start hook -- version-gated runtime reconcile.

    Runs at session start (via hooks.json). Ensures the installed `agent-ssh`
    binstub/venv matches the plugin source version, so a `copilot plugin update`
    that bumps the payload is picked up automatically -- without any manual
    reinstall.

    Fast path: compare the deployed version (~/.agent-ssh/deploy-manifest.json)
    to the source version (plugin pyproject.toml). If they match and the runtime
    venv exists, exit immediately. Otherwise re-run the plugin's own installer
    (scripts/init -> canonical install) in the BACKGROUND so session start never
    blocks on a venv build; the versioned-venv swap is atomic, so concurrent use
    stays safe.

    Deployed to ~/.agent-ssh/bin/ by scripts/install.ps1. Only reconciles
    staleness -- first install is the one-time setting-up-ssh-* / setup step. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'

$InstallDir = Join-Path $env:USERPROFILE '.agent-ssh'
$Manifest   = Join-Path $InstallDir 'deploy-manifest.json'

# Not provisioned yet -> do the cheap FIRST install ('stamp') so the binstub is
# on PATH this session; the self-provisioning binstub then builds the venv on
# first use (#1393). Only fires when the installer declares a 'stamp' action
# (and only resolves when run from the plugin payload, where install.ps1 is a
# sibling); otherwise a safe no-op.
if (-not (Test-Path $Manifest)) {
    $installer = Join-Path $PSScriptRoot 'install.ps1'
    if ((Test-Path $installer) -and (Select-String -Path $installer -Pattern "'stamp'" -Quiet)) {
        $pw = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
        & $exe -NoProfile -ExecutionPolicy Bypass -File $installer stamp *> $null
    }
    exit 0
}

try {
    $m = Get-Content $Manifest -Raw | ConvertFrom-Json
    $pluginDir = $m.source.path
    if (-not $pluginDir) { exit 0 }
    $pluginDir = $pluginDir -replace '/', '\'
    if (-not (Test-Path $pluginDir)) { exit 0 }

    $deployed = "" + $m.source.version
    $current  = $deployed
    $pyproj = Join-Path $pluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }

    # Up to date and runtime present -> fast no-op (the common case).
    # "Provisioned" no longer implies a `.venv`: the marker runtime model (#581)
    # publishes the active slot via a `current-version` marker with NO junction on
    # Windows (RedirectionGuard), so a healthy current runtime has no `.venv` there.
    # Treat a marker whose slot python exists as provisioned too -- otherwise a
    # current runtime needlessly background-rebuilds every session.
    $provisioned = Test-Path (Join-Path $InstallDir '.venv')
    if (-not $provisioned) {
        $cvMarker = Join-Path $InstallDir 'current-version'
        if (Test-Path $cvMarker) {
            $cv = ('' + (Get-Content $cvMarker -Raw)).Trim()
            if ($cv -and ((Test-Path (Join-Path $InstallDir "versions\$cv\Scripts\python.exe")) -or (Test-Path (Join-Path $InstallDir "versions/$cv/bin/python")))) { $provisioned = $true }
        }
    }
    if ($provisioned -and $deployed -eq $current) { exit 0 }

    $init = Join-Path $pluginDir 'scripts\init.ps1'
    if (-not (Test-Path $init)) { exit 0 }

    Write-Host "[agent-ssh] runtime $deployed -> $current; reconciling in background..." -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    Start-Process -FilePath $exe -WindowStyle Hidden `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $init | Out-Null
} catch { }

exit 0
