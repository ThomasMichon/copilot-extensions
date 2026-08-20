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

# Not provisioned yet -> do the cheap FIRST install ('stamp') so the binstub is
# on PATH this session; the self-provisioning binstub then builds the venv on
# first use (#1393). hooks.json runs the PAYLOAD copy, so $PSScriptRoot is the
# plugin's scripts/ dir even on a fresh box. Fires only when init.ps1 declares a
# 'stamp' action; else a safe no-op.
if (-not (Test-Path $Manifest)) {
    $payloadInit = Join-Path $PSScriptRoot 'init.ps1'
    if ((Test-Path $payloadInit) -and (Select-String -Path $payloadInit -Pattern "'stamp'" -Quiet)) {
        $pw = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
        & $exe -NoProfile -ExecutionPolicy Bypass -File $payloadInit stamp *> $null
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

    # Up to date and binstub present -> fast no-op (the common case).
    if ((Test-Path $Binstub) -and $deployed -eq $current) { exit 0 }

    $init = Join-Path $pluginDir 'scripts\init.ps1'
    if (-not (Test-Path $init)) { exit 0 }

    Write-Host "[agent-machines] runtime $deployed -> $current; reconciling in background..." -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    # conhost --headless so Windows Terminal / the DefTerm handoff can't surface
    # it as a window -- -WindowStyle Hidden ALONE is ignored by DefTerm (see
    # agent-bridge). Base64-encode the reconcile command to avoid arg quoting.
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("& `"$init`""))
    Start-Process -FilePath 'conhost.exe' `
        -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc) `
        -WindowStyle Hidden | Out-Null
} catch { }

exit 0
