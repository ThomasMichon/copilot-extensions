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
    Session-start runtime reconcile -- self-locating. Invoked (via hooks.json) from
    the plugin's own scripts/ dir. Derives the plugin from its own location and
    the install dir from plugin.json's name (~/.<name>), then re-runs the
    installer in the BACKGROUND only when the deployed runtime version drifts
    from the payload -- so a `copilot plugin update` is picked up automatically.
    Reconciles the TOOL, never machine state/config. PS5.1+.

    NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
    reconcile used to require a checked-in, per-project opt-in flag in
    ``<project>/.copilot-extensions/config.yaml``, because a raw reconcile
    could race a live daemon/session. Now that every reconcile-capable
    plugin's update path is always-ZDD (safe to run unattended), that
    justification is gone; the gate was removed rather than kept as a
    redundant consent checkbox. Frequency/trigger stays deliberately
    bounded -- still only once per session start, only on a real version
    drift. What DOES still bound concurrency is the single-flight +
    stale-reap guard below (a lock file, not the removed opt-in).
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
$PluginDir = Split-Path -Parent $PSScriptRoot
$reconcileMutex = $null
$ownsReconcileMutex = $false
try {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) { Exit-SessionStart }
    $name = (Get-Content (Join-Path $PluginDir 'plugin.json') -Raw | ConvertFrom-Json).name
    if (-not $name) { Exit-SessionStart }
    $InstallDir = Join-Path $env:USERPROFILE ".$name"
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $Manifest)) {
        # Not provisioned yet -- do the cheap FIRST install ('stamp') so the
        # binstub is on PATH this session; the self-provisioning binstub then
        # builds the venv on first use (#1393). Fires only when the installer
        # (init.ps1 or install.ps1) declares a 'stamp' action; else a safe no-op.
        $stampInst = @("$PluginDir\scripts\init.ps1", "$PluginDir\scripts\install.ps1") |
            Where-Object { (Test-Path $_) -and (Select-String -Path $_ -Pattern "'stamp'" -Quiet) } |
            Select-Object -First 1
        if ($stampInst) {
            $pw = Get-Command pwsh -ErrorAction SilentlyContinue
            $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
            & $exe -NoProfile -ExecutionPolicy Bypass -File $stampInst stamp *> $null
        }
        Exit-SessionStart
    }
    $deployed = "" + (Get-Content $Manifest -Raw | ConvertFrom-Json).source.version
    $current = $deployed
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
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

    $init = Join-Path $PluginDir 'scripts\init.ps1'
    if (Test-Path $init) {
        $reInner = "& '$($init.Replace("'", "''"))'"
    } else {
        $inst = Join-Path $PluginDir 'scripts\install.ps1'
        if (-not (Test-Path $inst)) { Exit-SessionStart }
        $reInner = "& '$($inst.Replace("'", "''"))' install"
    }

    $lockFile = Join-Path $InstallDir 'reconcile.lock'
    $statusFile = Join-Path $InstallDir 'reconcile-status.json'
    $reconcileLog = Join-Path $InstallDir 'reconcile.log'
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $key = -join ($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes(
            [IO.Path]::GetFullPath($InstallDir).ToLowerInvariant())) |
            ForEach-Object { $_.ToString('x2') })
    } finally { $hash.Dispose() }
    $mutexName = "Global\agent-dispatch-reconcile-$key"
    $reconcileMutex = New-Object Threading.Mutex($false, $mutexName)
    try { $ownsReconcileMutex = $reconcileMutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $ownsReconcileMutex = $true }
    if (-not $ownsReconcileMutex) { Exit-SessionStart }

    # Allow the installer's own bounded build + graceful cutover to finish first.
    $deadlineSeconds = 1050
    $deadlineRaw = $env:AGENT_DISPATCH_INSTALL_DEADLINE_SEC
    if (-not $deadlineRaw) { $deadlineRaw = $env:COPILOT_PLUGIN_INSTALL_DEADLINE_SEC }
    if ($deadlineRaw -and -not [int]::TryParse($deadlineRaw, [ref]$deadlineSeconds)) {
        throw 'Invalid agent-dispatch installer deadline'
    }
    $staleSeconds = [Math]::Max(600, $deadlineSeconds + 60)
    try {
        if (Test-Path $statusFile) {
            $prev = Get-Content -LiteralPath $statusFile -Raw -ErrorAction Stop |
                ConvertFrom-Json -ErrorAction Stop
            $previousProcess = Get-Process -Id ([int]$prev.launched_pid) -ErrorAction SilentlyContinue
            $recordedStart = if ($prev.worker_started_at) {
                $prev.worker_started_at
            } else { $prev.wrapper_started_at }
            if ($previousProcess -and -not $recordedStart) {
                throw 'Prior reconcile has no verified process birth time'
            }
            if ($previousProcess) {
                $startedAt = if ($recordedStart -is [DateTime]) {
                    [DateTimeOffset]$recordedStart
                } else { [DateTimeOffset]::Parse([string]$recordedStart) }
                if ($previousProcess.StartTime.ToUniversalTime() -eq $startedAt.UtcDateTime) {
                    $at = if ($prev.at -is [DateTime]) {
                        [DateTimeOffset]$prev.at
                    } else { [DateTimeOffset]::Parse([string]$prev.at) }
                    if (([DateTimeOffset]::UtcNow - $at).TotalSeconds -lt $staleSeconds) {
                        Exit-SessionStart
                    }
                    $reapProcess = Get-Process -Id ([int]$prev.launched_pid) -ErrorAction Stop
                    try {
                        [void]$reapProcess.Handle
                        if ($reapProcess.HasExited -or
                            $reapProcess.StartTime.ToUniversalTime() -ne $startedAt.UtcDateTime) {
                            throw 'Reconcile process identity changed before retirement'
                        }
                        & taskkill.exe /PID $reapProcess.Id /T /F *> $null
                        if ($LASTEXITCODE -ne 0 -and -not $reapProcess.HasExited) {
                            throw 'Could not retire the stale reconcile process tree'
                        }
                    } finally { $reapProcess.Dispose() }
                }
            }
        } elseif (Test-Path $lockFile) {
            $lockPid = 0
            [void][int]::TryParse((Get-Content $lockFile -Raw -ErrorAction SilentlyContinue), [ref]$lockPid)
            if ($lockPid -gt 0 -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
                [Console]::Error.WriteLine("[$name] legacy reconcile PID $lockPid has no verified process birth time; leaving it running")
                Exit-SessionStart
            }
        }
    } catch {
        [Console]::Error.WriteLine("[$name] could not validate or retire prior reconcile: $($_.Exception.Message)")
        Exit-SessionStart
    }

    [Console]::Error.WriteLine("[$name] runtime $deployed -> $current; reconciling in background (log: $reconcileLog)...")
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    $attemptId = [guid]::NewGuid().ToString('N')
    # The worker claims its own identity after the parent publishes this attempt.
    $worker = @'
$ErrorActionPreference = 'Continue'
function Update-ReconcileAttempt([bool]$Completed, [int]$Code) {
    $mutex = New-Object Threading.Mutex($false, '__MUTEX__')
    $held = $false
    try {
        try { $held = $mutex.WaitOne(15000) }
        catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { throw 'reconcile status lock timed out' }
        $status = Get-Content -LiteralPath '__STATUSFILE__' -Raw -ErrorAction Stop |
            ConvertFrom-Json -ErrorAction Stop
        if ($status.attempt_id -ne '__ATTEMPT__') { return $false }
        $status.launched_pid = $PID
        $status.worker_started_at = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
        if ($Completed) {
            $status | Add-Member -NotePropertyName completed_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o'))
            $status | Add-Member -NotePropertyName exit_code -NotePropertyValue $Code
            $status | Add-Member -NotePropertyName success -NotePropertyValue ($Code -eq 0)
        }
        $utf8 = New-Object Text.UTF8Encoding($false)
        [IO.File]::WriteAllText('__STATUSFILE__', ($status | ConvertTo-Json -Compress), $utf8)
        [IO.File]::WriteAllText('__LOCKFILE__', [string]$PID, $utf8)
        return $true
    } finally {
        if ($held) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
try {
    if (-not (Update-ReconcileAttempt $false 0)) { exit 125 }
    # The new worker must own its staging and watchdog, not inherit another install's.
    $env:COPILOT_PLUGIN_INSTALL_STAGED = $null
    $env:COPILOT_PLUGIN_STAGED_FROM = $null
    $LASTEXITCODE = 0
    & { __INSTALLER__ } *> '__RECONCILELOG__'
    $ok = $?
    $code = if ($ok) { $LASTEXITCODE } else { 1 }
} catch {
    $failure = "attempt=__ATTEMPT__ background reconcile failed: $($_.Exception.Message)"
    Add-Content -LiteralPath '__RECONCILELOG__' -Value $failure
    [Console]::Error.WriteLine($failure)
    $code = 1
}
try { [void](Update-ReconcileAttempt $true $code) }
catch {
    $failure = "attempt=__ATTEMPT__ reconcile completion could not be recorded: $($_.Exception.Message)"
    Add-Content -LiteralPath '__RECONCILELOG__' -Value $failure
    [Console]::Error.WriteLine($failure)
}
exit $code
'@
    $worker = $worker.Replace('__MUTEX__', $mutexName).Replace('__ATTEMPT__', $attemptId).`
        Replace('__STATUSFILE__', $statusFile.Replace("'", "''")).`
        Replace('__LOCKFILE__', $lockFile.Replace("'", "''")).`
        Replace('__RECONCILELOG__', $reconcileLog.Replace("'", "''")).Replace('__INSTALLER__', $reInner)
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($worker))
    $proc = Start-Process -FilePath 'conhost.exe' -PassThru -WindowStyle Hidden -ErrorAction Stop `
        -WorkingDirectory $env:USERPROFILE `
        -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc)
    $utf8 = New-Object Text.UTF8Encoding($false)
    $status = [ordered]@{
        at = (Get-Date).ToUniversalTime().ToString('o')
        from = $deployed
        to = $current
        launched_pid = $proc.Id
        wrapper_pid = $proc.Id
        wrapper_started_at = $proc.StartTime.ToUniversalTime().ToString('o')
        worker_started_at = $null
        attempt_id = $attemptId
        log = $reconcileLog
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($statusFile, $status, $utf8)
    [IO.File]::WriteAllText($lockFile, [string]$proc.Id, $utf8)
} catch {
    [Console]::Error.WriteLine("[agent-dispatch] background reconcile could not start: $($_.Exception.Message)")
} finally {
    if ($ownsReconcileMutex) { $reconcileMutex.ReleaseMutex() }
    if ($reconcileMutex) { $reconcileMutex.Dispose() }
}
Exit-SessionStart