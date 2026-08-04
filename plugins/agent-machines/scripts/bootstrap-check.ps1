<#
    agent-machines session-start hook -- version-gated runtime reconcile.

    Runs at session start (via hooks.json). Ensures the installed
    `agent-machines` binstub/venv matches the plugin source version, so a
    `copilot plugin update` that bumps the payload is picked up automatically --
    without ever running machine *restoration* itself.

    Fast path: read the deployed version from ~/.agent-machines/deploy-manifest.json
    and the source version from the plugin's pyproject.toml. If they match and the
    binstub exists, exit immediately. Otherwise re-run the plugin's own installer
    (scripts/init.ps1) in the BACKGROUND so session start never blocks on a venv
    build; the versioned-venv swap is atomic, so concurrent use stays safe.

    Deployed to ~/.agent-machines/bin/ by scripts/init.ps1. Never installs from
    scratch (that is the one-time `agent-machines-setup` step) -- it only exists
    once the runtime has been installed, and only reconciles staleness. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'

$InstallDir = Join-Path $env:USERPROFILE '.agent-machines'
$Manifest   = Join-Path $InstallDir 'deploy-manifest.json'
$Binstub    = Join-Path $env:USERPROFILE '.local\bin\agent-machines.cmd'

# Not installed yet -> nothing to reconcile (first install is the setup skill).
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

    # Up to date and binstub present -> fast no-op (the common case).
    if ((Test-Path $Binstub) -and $deployed -eq $current) { exit 0 }

    $init = Join-Path $pluginDir 'scripts\init.ps1'
    if (-not (Test-Path $init)) { exit 0 }

    Write-Host "[agent-machines] runtime $deployed -> $current; reconciling in background..." -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    Start-Process -FilePath $exe -WindowStyle Hidden `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $init | Out-Null
} catch { }

exit 0
