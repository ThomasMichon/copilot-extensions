<#
    agent-bridge session-start runtime reconcile (reference implementation).

    Invoked via hooks.json at session start. Derives the install dir from
    plugin.json's name (~/.<name>), and if the deployed runtime version drifts
    from the plugin payload, re-runs the installer in the BACKGROUND so a
    `copilot plugin update` is picked up automatically. Reconciles the TOOL,
    never machine state/config. PS5.1+.

    NOTE ON SHARING: this file is NOT byte-identical across all agent-* plugins.
    Three deploy-model families exist (see tools/check-bootstrap-sync.py):
    versioned-venv/PSScriptRoot (the common set), versioned-venv/manifest-path
    (agent-ssh, agent-machines), and lib-copy (agent-worktrees). This copy is the
    reference for the observability + venv-or-.venv behavior described below.

    OBSERVABILITY (#167): the background reconcile is otherwise silent -- a failed
    cutover would leave no trace. So this hook records every reconcile ATTEMPT to
    ~/.<name>/reconcile-status.json and redirects the installer's output to
    ~/.<name>/reconcile.log (stdout) / reconcile.err.log (stderr). Check those to
    see whether the last auto-reconcile succeeded.
#>
$ErrorActionPreference = 'SilentlyContinue'
$PluginDir = Split-Path -Parent $PSScriptRoot
try {
    $name = (Get-Content (Join-Path $PluginDir 'plugin.json') -Raw | ConvertFrom-Json).name
    if (-not $name) { exit 0 }
    $InstallDir = Join-Path $env:USERPROFILE ".$name"
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $Manifest)) {
        # Not provisioned yet -- do the cheap FIRST install ('stamp') so the
        # self-provisioning binstub is on PATH this session; the binstub then
        # builds the venv on first use (#1393). Fires only when the installer
        # (init.ps1 or install.ps1) declares a 'stamp' action; else a safe no-op.
        # NOTE: agent-bridge's install.ps1 does not yet expose a 'stamp' action
        # (the Windows self-provisioning lane is a follow-up), so on Windows this
        # is currently a no-op -- matching prior behavior, with no regression.
        $stampInst = @("$PluginDir\scripts\init.ps1", "$PluginDir\scripts\install.ps1") |
            Where-Object { (Test-Path $_) -and (Select-String -Path $_ -Pattern "'stamp'" -Quiet) } |
            Select-Object -First 1
        if ($stampInst) {
            $pw = Get-Command pwsh -ErrorAction SilentlyContinue
            $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
            & $exe -NoProfile -ExecutionPolicy Bypass -File $stampInst stamp *> $null
        }
        exit 0
    }
    $deployed = "" + (Get-Content $Manifest -Raw | ConvertFrom-Json).source.version
    $current = $deployed
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    # The immutable-versioned layout points a stable link at the active slot;
    # Runtime "present & healthy" must be read from the immutable-slot COMPLETION
    # MARKER, not a `venv`/`.venv` link. The Windows layout is junction-free
    # (marker-only: no `venv` link exists), so a link Test-Path is always false
    # there -- and this early-exit would then never fire, re-launching the
    # installer on EVERY session even at the same version. Those redundant
    # same-version reconciles are what stomped the live slot (ce#776/#777,
    # dotfiles#1612). So gate on the marker files directly (pure PowerShell, no
    # python, works pre-venv): the active version + its completion marker.
    $runtimeHealthy = $false
    $curVer = $null
    $curVerFile = Join-Path $InstallDir 'current-version'
    if (Test-Path $curVerFile) {
        $curVer = (Get-Content $curVerFile -Raw -ErrorAction SilentlyContinue)
        if ($curVer) { $curVer = $curVer.Trim() }
        if ($curVer) {
            $marker = Join-Path $InstallDir "versions\$curVer\.install-complete.json"
            if (Test-Path $marker) {
                try {
                    $mj = Get-Content $marker -Raw -ErrorAction Stop | ConvertFrom-Json
                    if ($mj.version -eq $curVer) { $runtimeHealthy = $true }
                } catch { }
            }
        }
    }
    # Legacy (pre-versioned) fallback: a real `venv`/`.venv` dir still counts as
    # present for an install that predates the marker convention.
    if (-not $runtimeHealthy) {
        $runtimeHealthy = (Test-Path (Join-Path $InstallDir '.venv')) -or (Test-Path (Join-Path $InstallDir 'venv'))
    }
    # No drift AND a healthy runtime whose active slot matches the deployed
    # version -> nothing to reconcile. (When a legacy fallback set the flag,
    # $curVer is $null and we fall back to the version-string check alone, as
    # before.)
    if ($runtimeHealthy -and $deployed -eq $current -and (-not $curVer -or $curVer -eq $deployed)) { exit 0 }
    $init = Join-Path $PluginDir 'scripts\init.ps1'
    if (Test-Path $init) {
        $targs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $init)
    } else {
        $inst = Join-Path $PluginDir 'scripts\install.ps1'
        if (-not (Test-Path $inst)) { exit 0 }
        $targs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $inst, 'install', '-NonInteractive')
    }
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }

    # Observability (#167): capture the otherwise-silent background reconcile so a
    # failed auto-update is diagnosable. -RedirectStandard* truncates each file,
    # so reconcile.log always holds the MOST RECENT reconcile's output.
    $reconcileLog = Join-Path $InstallDir 'reconcile.log'
    $reconcileErr = Join-Path $InstallDir 'reconcile.err.log'
    $statusFile   = Join-Path $InstallDir 'reconcile-status.json'
    $reconcileIn  = Join-Path $InstallDir 'reconcile.in'

    # --- Good boot-citizen guard: single-flight + stale-reap ---
    # This hook fires on EVERY new session. Without a guard, a slow or wedged
    # reconcile gets re-spawned each session, stacking orphaned background
    # installers (observed in the wild: 9 wedged copies in one evening, each
    # holding a session-start hook and blocking CLI startup). So, if a prior
    # reconcile PID is still alive:
    #   * YOUNG  -> a reconcile is already in flight; do nothing (never stack).
    #   * STALE  -> it is wedged; reap it, then relaunch (self-heal, so a one-off
    #              wedge can't poison every future session).
    $staleMinutes = 10
    try {
        if (Test-Path $statusFile) {
            $prev = Get-Content $statusFile -Raw | ConvertFrom-Json
            $prevPid = 0; [void][int]::TryParse("" + $prev.launched_pid, [ref]$prevPid)
            if ($prevPid -gt 0 -and (Get-Process -Id $prevPid -ErrorAction SilentlyContinue)) {
                # Age from the recorded UTC timestamp. ConvertFrom-Json may hand
                # back $prev.at as an already-parsed (local-kind) [DateTime], so
                # normalize via [DateTimeOffset] -- comparing instants regardless
                # of whether it arrived as a string or a DateTime, and avoiding
                # the [DateTime]::Parse(...Z).ToUniversalTime() double-convert.
                $ageMin = $staleMinutes  # default to "stale" if the timestamp is unparseable
                try {
                    $atVal = $prev.at
                    $dto = if ($atVal -is [DateTime]) { [DateTimeOffset]$atVal } else { [DateTimeOffset]::Parse([string]$atVal) }
                    $ageMin = ([DateTimeOffset]::UtcNow - $dto).TotalMinutes
                } catch { }
                if ($ageMin -lt $staleMinutes) { exit 0 }         # in flight -- don't stack
                Stop-Process -Id $prevPid -Force -ErrorAction SilentlyContinue  # wedged -- reap
            }
        }
    } catch { }

    Write-Host "[$name] runtime $deployed -> $current; reconciling in background (log: $InstallDir\reconcile.log)..." -ForegroundColor DarkGray

    # The background reconcile is HEADLESS: the installer must NEVER block on
    # input. Three independent guards keep this hook non-blocking:
    #   1. -NonInteractive switch (added to $targs above);
    #   2. a name-derived <NAME>_NONINTERACTIVE env var the installer honors
    #      (covers an init.ps1-style installer with no matching switch);
    #   3. stdin redirected from an EMPTY file below, so any stray Read-Host sees
    #      immediate EOF (returns, never blocks) AND [Console]::IsInputRedirected
    #      reports true so the installer's own interactive-desktop gate skips.
    # (1)+(2) are the deterministic path; (3) is the belt-and-suspenders that
    # makes "no interactive prompt can wedge us" true regardless of the installer.
    $niEnvVar = (($name -replace '[^A-Za-z0-9]+', '_').ToUpper()) + '_NONINTERACTIVE'
    [Environment]::SetEnvironmentVariable($niEnvVar, '1', 'Process')
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($reconcileIn, '', $utf8NoBom)  # empty EOF stdin

    $proc = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru `
        -RedirectStandardInput $reconcileIn `
        -RedirectStandardOutput $reconcileLog -RedirectStandardError $reconcileErr `
        -ArgumentList $targs
    $now = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $launchedPid = if ($proc) { $proc.Id } else { 0 }
    $status = [ordered]@{
        at           = $now
        from         = $deployed
        to           = $current
        launched_pid = $launchedPid
        log          = $reconcileLog
        err_log      = $reconcileErr
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($statusFile, $status, $utf8NoBom)
} catch { }
exit 0