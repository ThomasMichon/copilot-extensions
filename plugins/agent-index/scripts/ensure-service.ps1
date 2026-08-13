<#
    Session-start USER-MODE service ensure (agent-index-specific).

    Guarantees the indexer daemon (and, on host, the durable embedding engine) is
    running as a plain USER process -- NO scheduled task, NO elevation. This is
    the DEFAULT auto-run mechanism: every Copilot session ensures the daemon, so
    it robustly survives reboots WITHOUT relying on a fragile AtLogon scheduled
    task (which also needs elevation to create on locked-down boxes). Replaces the
    task as the default persistence layer; scheduled tasks are an opt-in advanced
    tier (`install.ps1 register-tasks`).

    Fast + timeout-safe: a healthy daemon returns immediately; an unhealthy one
    kicks a BACKGROUND `install.ps1 ensure` and returns without blocking session
    start on the daemon spawn (or the engine's model load). PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'
try {
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
                $healthy = ($r.StatusCode -eq 200)
            }
        } catch { $healthy = $false }
    }
    if ($healthy) { exit 0 }

    $inst = Join-Path $PSScriptRoot 'install.ps1'
    if (-not (Test-Path $inst)) { exit 0 }
    Write-Host '[agent-index] daemon not healthy -- ensuring (user-mode) in background...' -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    Start-Process -FilePath $exe -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $inst, 'ensure'
    ) | Out-Null
} catch { }
exit 0
