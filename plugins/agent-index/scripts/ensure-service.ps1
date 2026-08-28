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

    # A client runs NO local indexer daemon -- its MCP/CLI route to the designated
    # host's service over SSH -- so there is nothing to keep alive here. Skip fast
    # (no background install.ps1 spawn) on any non-host. Mirrors config.resolve_role
    # precedence: a VALID AGENT_INDEX_ROLE env (host/client) wins; otherwise the
    # config.yaml role:/engine: scalar; else client. An unrecognized env value is
    # ignored (falls through), never treated as a role.
    $role = ''
    $envRole = if ($env:AGENT_INDEX_ROLE) { ($env:AGENT_INDEX_ROLE).Trim().ToLower() } else { '' }
    if ($envRole -in @('host', 'client')) {
        $role = $envRole
    } else {
        $cfg = Join-Path $InstallDir 'config.yaml'
        if (Test-Path $cfg) {
            $rm = Select-String -Path $cfg -Pattern '^\s*(?:role|engine)\s*:\s*"?([A-Za-z]+)"?' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($rm) { $role = $rm.Matches[0].Groups[1].Value.ToLower() }
        }
    }
    if ($role -notin @('host', 'engine', 'server', 'indexer')) { exit 0 }

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
