<#
    agent-machines session-start hook -- version-gated runtime reconcile.

    Runs at session start (via hooks.json). Ensures the installed
    `agent-machines` binstub/venv matches the plugin source version, so a
    `copilot plugin update` that bumps the payload is picked up automatically --
    without ever running machine *restoration* itself.

    Fast path: compare the deployed and payload versions. Legacy deployments
    read ~/.agent-machines/deploy-manifest.json; an explicit validated
    installation context may redirect that read to its plugin root. Namespaced
    writes remain blocked until the context-aware installer is operative.

    Deployed to ~/.agent-machines/bin/ by scripts/init.ps1. Never installs from
    scratch (that is the one-time `agent-machines-setup` step) -- it only exists
    once the runtime has been installed, and only reconciles staleness. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'

$PluginDir = Split-Path -Parent $PSScriptRoot
function Test-LegacyMutationAllowed {
    $probe = Join-Path $PSScriptRoot 'installation-context\legacy-entrypoint-probe.ps1'
    if (-not (Test-Path -LiteralPath $probe -PathType Leaf)) {
        Write-Host '[agent-machines] legacy mutation probe is unavailable; skipping reconcile.' -ForegroundColor DarkGray
        return $false
    }
    $hostExe = (Get-Process -Id $PID).Path
    if (-not $hostExe) { return $false }
    $global:LASTEXITCODE = 1
    try {
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $probe `
            -PayloadRoot $PluginDir -LegacyRoot (Join-Path $env:USERPROFILE '.agent-machines') |
            Out-Null
    } catch {
        return $false
    }
    return $LASTEXITCODE -eq 0
}
$contextSelected = $false
$InstallDir = Join-Path $env:USERPROFILE '.agent-machines'
if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    $resolver = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    if (-not (Test-Path $resolver)) {
        Write-Host '[agent-machines] installation context is selected but its validator is unavailable; skipping reconcile.' -ForegroundColor DarkGray
        exit 0
    }
    $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
    1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
    $hostExe = (Get-Process -Id $PID).Path
    $validatedJson = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $resolver validate `
        -Context $env:COPILOT_EXTENSIONS_CONTEXT -DurableHome $durableHome
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[agent-machines] installation context is invalid; skipping reconcile without legacy fallback.' -ForegroundColor DarkGray
        exit 0
    }
    try { $contextPlugin = ($validatedJson | ConvertFrom-Json).pluginId } catch {
        Write-Host '[agent-machines] installation context is invalid; skipping reconcile without legacy fallback.' -ForegroundColor DarkGray
        exit 0
    }
    if (-not $contextPlugin) {
        Write-Host '[agent-machines] installation context is invalid; skipping reconcile without legacy fallback.' -ForegroundColor DarkGray
        exit 0
    }
    if ($contextPlugin -ceq 'agent-machines') {
        $contextJson = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $resolver resolve `
            -Context $env:COPILOT_EXTENSIONS_CONTEXT -PluginId agent-machines `
            -PayloadRoot $PluginDir -DurableHome $durableHome
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[agent-machines] installation context is invalid; skipping reconcile without legacy fallback.' -ForegroundColor DarkGray
            exit 0
        }
        $resolvedContext = $contextJson | ConvertFrom-Json
        if (-not $resolvedContext.pluginRoot) {
            Write-Host '[agent-machines] installation context returned no plugin root; skipping reconcile without legacy fallback.' -ForegroundColor DarkGray
            exit 0
        }
        $InstallDir = $resolvedContext.pluginRoot
        $contextSelected = $true
    }
}
$Manifest   = Join-Path $InstallDir 'deploy-manifest.json'
$Binstub    = Join-Path $env:USERPROFILE '.local\bin\agent-machines.cmd'

# Not provisioned yet -> do the cheap FIRST install ('stamp') so the binstub is
# on PATH this session; the self-provisioning binstub then builds the venv on
# first use (#1393). hooks.json runs the PAYLOAD copy, so $PSScriptRoot is the
# plugin's scripts/ dir even on a fresh box. Fires only when init.ps1 declares a
# 'stamp' action; else a safe no-op.
if (-not (Test-Path $Manifest)) {
    if ($contextSelected) {
        Write-Host '[agent-machines] selected context has no deploy manifest; namespaced install remains non-operative.' -ForegroundColor DarkGray
        exit 0
    }
    $payloadInit = Join-Path $PSScriptRoot 'init.ps1'
    if ((Test-Path $payloadInit) -and (Select-String -Path $payloadInit -Pattern "'stamp'" -Quiet)) {
        if (-not (Test-LegacyMutationAllowed)) { exit 0 }
        $pw = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
        & $exe -NoProfile -ExecutionPolicy Bypass -File $payloadInit stamp *> $null
    }
    exit 0
}

try {
    $m = Get-Content $Manifest -Raw | ConvertFrom-Json
    $pluginDir = if ($contextSelected) { $PluginDir } else { $m.source.path }
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
    if ($contextSelected) {
        if ($deployed -eq $current) { exit 0 }
        Write-Host "[agent-machines] selected context runtime $deployed -> $current; context-aware install is not active yet." -ForegroundColor DarkGray
        exit 0
    }

    # Up to date and binstub present -> fast no-op (the common case).
    if ((Test-Path $Binstub) -and $deployed -eq $current) { exit 0 }

    $init = Join-Path $pluginDir 'scripts\init.ps1'
    if (-not (Test-Path $init)) { exit 0 }

    if (-not (Test-LegacyMutationAllowed)) { exit 0 }
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
