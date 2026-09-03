<#
    Session-start USER-MODE service ensure (agent-index-specific).

    Guarantees the indexer daemon (and, on host, the durable embedding engine) is
    running as a plain USER process -- NO scheduled task, NO elevation. This is
    the DEFAULT auto-run mechanism: every Copilot session ensures the daemon, so
    it robustly survives reboots WITHOUT relying on a fragile AtLogon scheduled
    task (which also needs elevation to create on locked-down boxes). Replaces the
    task as the default persistence layer; scheduled tasks are an opt-in advanced
    tier (`install.ps1 register-tasks`).

    Fast + timeout-safe: namespaced mode coalesces a background cell-runtime
    ensure; legacy mode kicks a BACKGROUND `install.ps1 ensure`. Neither waits
    for daemon startup or the engine's model load. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'
try {
    # Session start is repository-scoped activation. Merely enabling the plugin
    # must not start a machine-global daemon in an unrelated repository.
    $repoRoot = (& git rev-parse --show-toplevel 2>$null).Trim()
    if (-not $repoRoot) { exit 0 }
    $repoConfig = Join-Path $repoRoot '.agent-index\config.yaml'
    if (-not (Test-Path -LiteralPath $repoConfig -PathType Leaf)) { exit 0 }
    $me = if ($env:AGENT_INDEX_MACHINE) {
        $env:AGENT_INDEX_MACHINE.Trim().ToLower()
    } else {
        [Environment]::MachineName.Trim().ToLower()
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $python) { exit 0 }
    $resolver = Join-Path $PSScriptRoot 'resolve-activation-role.py'
    $previousLocation = Get-Location
    $previousCwd = [IO.Directory]::GetCurrentDirectory()
    try {
        Set-Location -LiteralPath $PSScriptRoot
        [IO.Directory]::SetCurrentDirectory($PSScriptRoot)
        $role = (
            & $python.Source -I -X utf8 $resolver `
                --config $repoConfig --machine $me 2>$null |
                Select-Object -Last 1
        )
    } finally {
        Set-Location -LiteralPath $previousLocation
        [IO.Directory]::SetCurrentDirectory($previousCwd)
    }
    if (("$role").Trim().ToLower() -ne 'host') { exit 0 }

    $runtimeGate = Join-Path $PSScriptRoot 'runtime-gate.ps1'
    if (Test-Path -LiteralPath $runtimeGate -PathType Leaf) {
        & $runtimeGate __cell-service-ensure *> $null
        $cellStatus = $LASTEXITCODE
        if ($cellStatus -eq 0) { exit 0 }
        if ($cellStatus -ne 10) { exit 0 }
    }
    $InstallDir = Join-Path $env:USERPROFILE '.agent-index'
    # Only act on a box where agent-index is actually deployed.
    if (-not (Test-Path (Join-Path $InstallDir 'deploy-manifest.json'))) { exit 0 }

    # Fast health probe on the LIVE routing endpoint (active.json ephemeral port);
    # a stale active.json pointing at a dead pid correctly reads as unhealthy.
    $healthy = $false
    $aj = Join-Path $InstallDir 'active.json'
    if (Test-Path $aj) {
        try {
            $port = [int]((Get-Content $aj -Raw | ConvertFrom-Json).active.port)
            if ($port) {
                $r = Invoke-WebRequest "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 2
                $health = $r.Content | ConvertFrom-Json
                $healthy = (
                    $r.StatusCode -eq 200 -and
                    $health.status -ceq 'ok' -and
                    $health.promoted -ne $false
                )
            }
        } catch { $healthy = $false }
    }
    if ($healthy) { exit 0 }

    $inst = Join-Path $PSScriptRoot 'install.ps1'
    if (-not (Test-Path $inst)) { exit 0 }
    $probe = Join-Path $PSScriptRoot 'installation-context\legacy-entrypoint-probe.ps1'
    if (-not (Test-Path -LiteralPath $probe -PathType Leaf)) {
        Write-Host '[agent-index] legacy mutation probe is unavailable; skipping service ensure.' -ForegroundColor DarkGray
        exit 0
    }
    $probeHost = (Get-Process -Id $PID).Path
    if (-not $probeHost) { exit 0 }
    $global:LASTEXITCODE = 1
    try {
        & $probeHost -NoProfile -ExecutionPolicy Bypass -File $probe `
            -PayloadRoot (Split-Path -Parent $PSScriptRoot) -LegacyRoot $InstallDir |
            Out-Null
    } catch {
        exit 0
    }
    if ($LASTEXITCODE -ne 0) { exit 0 }
    Write-Host '[agent-index] daemon not healthy -- ensuring (user-mode) in background...' -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    # conhost --headless so the DefTerm handoff can't surface a window --
    # -WindowStyle Hidden ALONE is ignored by DefTerm (windows-launch-hardening #786).
    Start-Process -FilePath 'conhost.exe' -ArgumentList @(
        '--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-WindowStyle', 'Hidden', '-File', "`"$inst`"", 'ensure'
    ) -WindowStyle Hidden | Out-Null
} catch { }
exit 0
