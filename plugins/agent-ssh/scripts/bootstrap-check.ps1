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

# Not installed yet -> nothing to reconcile (first install is the setup step).
if (-not (Test-Path $Manifest)) { exit 0 }

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
    if ((Test-Path (Join-Path $InstallDir '.venv')) -and $deployed -eq $current) { exit 0 }

    $init = Join-Path $pluginDir 'scripts\init.ps1'
    if (-not (Test-Path $init)) { exit 0 }

    Write-Host "[agent-ssh] runtime $deployed -> $current; reconciling in background..." -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    Start-Process -FilePath $exe -WindowStyle Hidden `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $init | Out-Null
} catch { }

exit 0
