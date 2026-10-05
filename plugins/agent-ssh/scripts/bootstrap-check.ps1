# --- bootstrap-killswitch guard (vendored; see libs/bootstrap-killswitch/README.md) ---
# One shared, repo-wide switch (not per-plugin) that pauses EVERY adopting
# plugin's reconcile-on-session-start at once, for when an operator/agent is
# hand-diagnosing a venv/install and a background reconcile must not race it.
# Legacy/default installation ONLY: a namespaced marketplace cell
# (COPILOT_EXTENSIONS_CONTEXT set) reconciles through its own cell-scoped
# mechanism, never this global state file -- crossing that installation-cell
# boundary would let one marketplace's switch pause an unrelated,
# independently-owned cell's reconcile (visions/plugin-services/
# installation-cells). This guard is therefore a deliberate no-op under a
# cell context, same as this hook's own existing cell-context exit below.
if (-not $env:COPILOT_EXTENSIONS_CONTEXT) {
  $_bksGuardDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $_bksGuard = Join-Path $_bksGuardDir "bootstrap-killswitch-guard.ps1"
  if (Test-Path -LiteralPath $_bksGuard) {
    & $_bksGuard check
    if ($LASTEXITCODE -eq 0) { [Console]::Out.Write('{}'); exit 0 }
  }
}
# --- end bootstrap-killswitch guard ---

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

    NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
    reconcile used to require a checked-in, per-project opt-in flag in
    ``<project>/.copilot-extensions/config.yaml``, because a raw reconcile
    could race a live session. Now that every reconcile-capable plugin's
    update path is always-ZDD (safe to run unattended), that justification
    is gone; the gate was removed rather than kept as a redundant consent
    checkbox. Frequency/trigger stays deliberately bounded -- still only
    once per session start, only on a real version drift. What DOES still
    bound concurrency is the single-flight + stale-reap guard below (a
    lock file, not the removed opt-in).
#>
$ErrorActionPreference = 'SilentlyContinue'
$script:SessionStartJsonEmitted = $false
function Write-SessionStartJson {
    if (-not $script:SessionStartJsonEmitted) {
        [Console]::Out.Write('{}')
        $script:SessionStartJsonEmitted = $true
    }
}
function Exit-SessionStart {
    Write-SessionStartJson
    exit 0
}

if ($env:COPILOT_EXTENSIONS_CONTEXT) { Exit-SessionStart }

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
    Exit-SessionStart
}

try {
    $m = Get-Content $Manifest -Raw | ConvertFrom-Json
    $pluginDir = $m.source.path
    if (-not $pluginDir) { Exit-SessionStart }
    $pluginDir = $pluginDir -replace '/', '\'
    if (-not (Test-Path $pluginDir)) { Exit-SessionStart }

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
            # ...and only when it names the CURRENT payload version: the marker is
            # authoritative for the ACTIVE slot, so a stale/corrupt marker naming an
            # older slot must NOT suppress reconcile and strand the wrong runtime.
            if ($cv -and $cv -eq $current -and ((Test-Path (Join-Path $InstallDir "versions\$cv\Scripts\python.exe")) -or (Test-Path (Join-Path $InstallDir "versions/$cv/bin/python")))) { $provisioned = $true }
        }
    }
    if ($provisioned -and $deployed -eq $current) { Exit-SessionStart }

    $init = Join-Path $pluginDir 'scripts\init.ps1'
    if (-not (Test-Path $init)) { Exit-SessionStart }

    # --- Good boot-citizen guard: single-flight + stale-reap ---
    # This hook fires on EVERY new session now that the opt-in gate is gone
    # (agent-bridge-unified-zdd-cutover Phase 0 review finding): without
    # this, a slow or wedged reconcile gets re-spawned every session,
    # stacking orphaned background installers. If a prior reconcile PID
    # recorded in reconcile.lock is still alive:
    #   YOUNG  -> a reconcile is already in flight; do nothing (never stack).
    #   STALE  -> it is wedged; reap it, then relaunch (self-heal, so a
    #             one-off wedge can't poison every future session).
    $staleSeconds = 600
    $lockFile = Join-Path $InstallDir 'reconcile.lock'
    try {
        if (Test-Path $lockFile) {
            $lockPid = 0
            [void][int]::TryParse((Get-Content $lockFile -Raw -ErrorAction SilentlyContinue), [ref]$lockPid)
            if ($lockPid -gt 0 -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
                $ageSec = ((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalSeconds
                if ($ageSec -lt $staleSeconds) { Exit-SessionStart }         # in flight -- don't stack
                Stop-Process -Id $lockPid -Force -ErrorAction SilentlyContinue  # wedged -- reap
            }
        }
    } catch { }

    [Console]::Error.WriteLine("[agent-ssh] runtime $deployed -> $current; reconciling in background...")
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    # conhost --headless so Windows Terminal / the DefTerm handoff can't surface
    # it as a window -- -WindowStyle Hidden ALONE is ignored by DefTerm (see
    # agent-bridge). Base64-encode the reconcile command to avoid arg quoting.
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("& `"$init`""))
    $proc = Start-Process -FilePath 'conhost.exe' -PassThru -WindowStyle Hidden `
        -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc)
    try {
        if ($proc) { Set-Content -LiteralPath $lockFile -Value ([string]$proc.Id) -NoNewline -ErrorAction SilentlyContinue }
    } catch { }
} catch { }

Exit-SessionStart
