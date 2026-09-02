<#
.SYNOPSIS
    Agent Logger -- session-sync installer (Windows).

.DESCRIPTION
    Creates a venv at ~/.agent-logger, installs the agent-logger package, and
    registers a Scheduled Task that runs `session-sync run --prune` every 4
    hours. Windows-first by design: the runtime is the venv's python invoked
    as `python -m agent_logger.sync.engine` (the console-script .exe is not
    relied upon, matching the other plugins' Smart App Control posture). The
    scheduled task runs under the windowless pythonw.exe host so the sync flow
    never flashes a console window.

    Run from the repo root:
      pwsh -File plugins\agent-logger\scripts\install.ps1 install
      pwsh -File plugins\agent-logger\scripts\install.ps1 status

.PARAMETER Action
    Lifecycle action: install | update | uninstall | status.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'update', 'uninstall', 'status', 'stamp', 'provision')]
    [string]$Action = 'status'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# === install-contract:v4 self-stage -- keep byte-identical across plugins ===
# dotfiles #935: a plugin installer reads its own payload (src/, libs/,
# pyproject.toml) to build the venv, so while it runs -- especially if it wedges
# or times out -- it holds the SINGLETON `installed-plugins/<mkt>/<plugin>`
# payload dir open (CWD/handles). A concurrent `copilot plugin update <plugin>`
# then fails on Windows with os error 32 ("used by another process"): the payload
# freezes at the old version and reconcile keeps reverting the runtime toward it
# (the version-drift saga). Fix: when running from the marketplace payload, copy
# the WHOLE payload into a UNIQUE per-invocation staging dir OUTSIDE the payload
# and re-exec from there, so the singleton is touched only for the fast copy. A
# stalled run then holds only its own throwaway stage dir, never blocking the
# next invocation or a `copilot plugin update`. COPILOT_PLUGIN_STAGED_FROM tells
# Get-SourceKind the payload was really the marketplace (see below). Env-guarded
# against re-exec loops; the stage-dir path (not under installed-plugins) is a
# second guard. Best-effort, non-blocking reap of old stage dirs.
if (-not $env:COPILOT_PLUGIN_INSTALL_STAGED) {
    try {
        $__selfStageScriptDir = $PSScriptRoot
        $__selfStagePayload = (Resolve-Path (Join-Path $__selfStageScriptDir '..')).Path
        if (($__selfStagePayload -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
            $__selfStageName = (Get-Content (Join-Path $__selfStagePayload 'plugin.json') -Raw | ConvertFrom-Json).name
            if ($__selfStageName) {
                # CWD guard (#1366): the sessionStart hook launches this installer
                # with CWD = the SINGLETON payload dir, so our process CWD is an
                # open directory handle that blocks `copilot plugin update` (os
                # error 32) for our whole lifetime -- including the watchdog
                # WaitForExit below and, on a self-stage failure, an in-place run.
                # Self-stage relocates our FILE reads but NOT the CWD handle, so
                # re-root the process CWD OFF the payload BEFORE the copy (absolute
                # paths make this safe). Set the WIN32 cwd (the real dir handle),
                # not just the PS provider location.
                try {
                    Set-Location -LiteralPath $env:USERPROFILE
                    [System.IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
                } catch {}
                $__selfStageRoot = Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") '.install-stage'
                $__selfStageDir = Join-Path $__selfStageRoot ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff') + "-$PID")
                New-Item -ItemType Directory -Force -Path $__selfStageDir | Out-Null
                Copy-Item -LiteralPath $__selfStagePayload -Destination $__selfStageDir -Recurse -Force
                $__selfStagedPayload = Join-Path $__selfStageDir (Split-Path -Leaf $__selfStagePayload)
                $__selfStagedEntry = Join-Path (Join-Path $__selfStagedPayload 'scripts') (Split-Path -Leaf $PSCommandPath)
                # Best-effort reap of prior stage dirs; NEVER touch a live one.
                # Only remove a sibling whose owner pid (the <ts>-<pid> suffix) is
                # DEAD -- so a concurrent or wedged installer's dir is left alone
                # (it uses its own unique dir), honoring "a stalled install must
                # never block another copy". Dead leftovers are cleaned up.
                Get-ChildItem $__selfStageRoot -Directory -Force -ErrorAction SilentlyContinue |
                    Where-Object { $_.FullName -ne $__selfStageDir } |
                    ForEach-Object {
                        $__selfStageOwnerPid = 0
                        if ($_.Name -match '-(\d+)$') { [void][int]::TryParse($Matches[1], [ref]$__selfStageOwnerPid) }
                        $__selfStageOwnerAlive = $false
                        if ($__selfStageOwnerPid -gt 0) {
                            $__selfStageOwnerAlive = [bool](Get-Process -Id $__selfStageOwnerPid -ErrorAction SilentlyContinue)
                        }
                        if (-not $__selfStageOwnerAlive) {
                            try { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop } catch {}
                        }
                    }
                # Faithful arg forwarding, independent of this script's param()
                # shape AND of the invocation form. Rebuild the child arg list from
                # $PSBoundParameters (a switch as a bare -Name, else -Name Value), so
                # the staged re-exec carries the SAME action/flags whether the
                # installer was launched via `pwsh -File install.ps1 update` OR the
                # call/`-Command` form `.\install.ps1 update` (the documented
                # interactive form). The old approach -- slicing args after `-File`
                # out of GetCommandLineArgs() -- returned NOTHING for the call form,
                # so the staged child re-ran with the DEFAULT action: a silent no-op
                # that still reported success (#205). All installer args are declared
                # params, so nothing is unbound -- and $args is unavailable in a
                # param()-script under StrictMode, so it is deliberately not consulted.
                $__selfStageFwd = @()
                foreach ($__selfStageK in $PSBoundParameters.Keys) {
                    $__selfStageV = $PSBoundParameters[$__selfStageK]
                    if ($__selfStageV -is [System.Management.Automation.SwitchParameter]) {
                        if ($__selfStageV.IsPresent) { $__selfStageFwd += "-$__selfStageK" }
                    } else {
                        $__selfStageFwd += "-$__selfStageK"
                        $__selfStageFwd += [string]$__selfStageV
                    }
                }
                $env:COPILOT_PLUGIN_INSTALL_STAGED = '1'
                $env:COPILOT_PLUGIN_STAGED_FROM = $__selfStagePayload
                $__selfStageExe = (Get-Process -Id $PID).Path
                # WATCHDOG (#935): the staging parent is already outside the
                # payload and wraps the child's whole lifetime, so it doubles as
                # a watchdog -- launch the staged child, then enforce a deadline.
                # A stalled install (the (4) session-start-hook failure class)
                # self-terminates instead of leaking forever: kill the WHOLE tree
                # (taskkill /T -- Windows' subprocess kill leaves grandchildren)
                # and log. The killed child's stage dir has a dead owner pid, so
                # the next run's pid-guarded reap cleans it; its half-built slot
                # has no completion marker, so it is tossed + rebuilt (retry).
                # Deadline: <NAME>_INSTALL_DEADLINE_SEC, else
                # COPILOT_PLUGIN_INSTALL_DEADLINE_SEC, else 480s; <=0 disables.
                $__wdDeadline = 480
                $__wdEnvVar = (($__selfStageName -replace '[^A-Za-z0-9]+', '_').ToUpper()) + '_INSTALL_DEADLINE_SEC'
                $__wdRaw = [Environment]::GetEnvironmentVariable($__wdEnvVar)
                if (-not $__wdRaw) { $__wdRaw = $env:COPILOT_PLUGIN_INSTALL_DEADLINE_SEC }
                if ($__wdRaw) { [void][int]::TryParse([string]$__wdRaw, [ref]$__wdDeadline) }
                $__wdChild = Start-Process -FilePath $__selfStageExe -PassThru -NoNewWindow `
                    -WorkingDirectory $__selfStagedPayload `
                    -ArgumentList (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $__selfStagedEntry) + $__selfStageFwd)
                if ($__wdDeadline -gt 0 -and -not $__wdChild.WaitForExit($__wdDeadline * 1000)) {
                    try { & taskkill.exe /PID $__wdChild.Id /T /F 2>&1 | Out-Null } catch {}
                    try { Stop-Process -Id $__wdChild.Id -Force -ErrorAction SilentlyContinue } catch {}
                    $__wdLog = Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") 'reconcile.err.log'
                    try {
                        Add-Content -LiteralPath $__wdLog -Value ("[{0}] WATCHDOG-KILL {1}: install exceeded {2}s deadline (child pid {3}); killed tree. Slot lacks a completion marker -> will be tossed + retried. Stage: {4}" -f ((Get-Date).ToUniversalTime().ToString('s') + 'Z'), $__selfStageName, $__wdDeadline, $__wdChild.Id, $__selfStageDir)
                    } catch {}
                    exit 124
                }
                $__wdChild.WaitForExit()
                exit $__wdChild.ExitCode
            }
        }
    } catch {
        Write-Host "  [WARN] self-stage failed, running in place: $_" -ForegroundColor Yellow
    }
}
# === end install-contract:v4 self-stage ===

# === install-contract:v4 smoke seam (test-only) -- keep byte-identical ===
# #935 install-flow test hook. When COPILOT_PLUGIN_INSTALL_SMOKE is set, prove
# the self-stage/lock behavior WITHOUT a heavy venv build: this (post-stage)
# process records where it is running from + the recorded marketplace origin,
# then sleeps to simulate a slow/wedged install so a test can assert the
# SINGLETON payload dir stays replaceable meanwhile. Never set in production.
if ($env:COPILOT_PLUGIN_INSTALL_SMOKE) {
    try {
        $__smokePayload = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
        $__smokeName = (Get-Content (Join-Path $__smokePayload 'plugin.json') -Raw | ConvertFrom-Json).name
        $__smokeHome = Join-Path $env:USERPROFILE ".$__smokeName"
        New-Item -ItemType Directory -Force -Path $__smokeHome | Out-Null
        $__smokeSleep = 6
        [void][int]::TryParse([string]$env:COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP, [ref]$__smokeSleep)
        # Optionally spawn a GRANDCHILD sleeper so a watchdog test can prove the
        # WHOLE tree is killed (Windows subprocess-kill leaves grandchildren).
        $__smokeGrandPid = 0
        if ($env:COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD) {
            try {
                $__g = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -WindowStyle Hidden `
                    -ArgumentList @('-NoProfile', '-Command', "Start-Sleep -Seconds $([Math]::Max($__smokeSleep, 3600))")
                $__smokeGrandPid = $__g.Id
            } catch {}
        }
        ([ordered]@{
            ran_from     = $PSScriptRoot
            staged_from  = [string]$env:COPILOT_PLUGIN_STAGED_FROM
            staged       = [bool]$env:COPILOT_PLUGIN_INSTALL_STAGED
            child_pid    = $PID
            grandchild_pid = $__smokeGrandPid
        } | ConvertTo-Json -Compress) | Set-Content -LiteralPath (Join-Path $__smokeHome 'smoke.json')
        Start-Sleep -Seconds $__smokeSleep
    } catch {}
    exit 0
}
# === end install-contract:v4 smoke seam ===

# Serialize the complete mutating installer. The session-start reconciler and
# agent-worktrees universal reconciler may discover the same version drift at
# once; this process-held, exclusive file handle makes those paths converge
# instead of mutating runtime slots and deployment metadata concurrently.
$script:InstallLockStream = $null
$script:SkipPackageInstall = $false
$script:SkipStamp = $false
function ConvertTo-AgentLoggerVersionKey {
    param([string]$Value)
    if ($Value -notmatch '^(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?$') { return $null }
    $build = if ($Matches[4]) { [int]$Matches[4] } else { [int]::MaxValue }
    return [Version]::new(
        [int]$Matches[1],
        [int]$Matches[2],
        [int]$Matches[3],
        $build
    )
}
if ($Action -ne 'status') {
    $lockRoot = Join-Path $env:USERPROFILE '.agent-logger'
    New-Item -ItemType Directory -Force -Path $lockRoot | Out-Null
    $lockPath = Join-Path $lockRoot '.install.lock'
    $lockContended = $false
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    while (-not $script:InstallLockStream -and [DateTime]::UtcNow -lt $deadline) {
        try {
            $script:InstallLockStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
        } catch [System.IO.IOException] {
            $lockContended = $true
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $script:InstallLockStream) {
        throw "Timed out waiting for the agent-logger install lock: $lockPath"
    }
    # After every lock acquisition, reject stale payloads against the completed
    # active generation. Contention is only needed to collapse equal-version
    # duplicate work; a delayed older process may never observe contention.
    if ($Action -in @('install', 'update', 'provision', 'stamp')) {
        $lockPluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
        $desired = '' + (Get-Content (Join-Path $lockPluginDir 'plugin.json') -Raw | ConvertFrom-Json).version
        $desiredKey = ConvertTo-AgentLoggerVersionKey $desired
        if (-not $desiredKey) { throw "Invalid agent-logger payload version: $desired" }
        $manifestPath = Join-Path $lockRoot 'deploy-manifest.json'
        $currentPath = Join-Path $lockRoot 'current-version'
        $stampedPath = Join-Path $lockRoot 'stamped-version'
        $deployed = ''
        if (Test-Path $manifestPath) {
            try {
                $deployed = '' + (Get-Content $manifestPath -Raw | ConvertFrom-Json).source.version
            } catch {
                Write-Warning "Ignoring unreadable agent-logger deployment manifest: $($_.Exception.Message)"
            }
        }
        $current = if (Test-Path $currentPath) {
            ('' + (Get-Content $currentPath -Raw)).Trim()
        } else { '' }
        $stamped = if (Test-Path $stampedPath) {
            ('' + (Get-Content $stampedPath -Raw)).Trim()
        } else { '' }
        $currentComplete = Join-Path $lockRoot "versions\$current\.install-complete.json"
        $stampSnapshot = Join-Path $lockRoot "snapshots\$stamped"
        $currentKey = ConvertTo-AgentLoggerVersionKey $current
        $stampedKey = ConvertTo-AgentLoggerVersionKey $stamped
        if ($current -and (Test-Path $currentComplete) -and -not $currentKey) {
            throw "Invalid completed agent-logger runtime version: $current"
        }
        if ($stamped -and (Test-Path $stampSnapshot) -and -not $stampedKey) {
            throw "Invalid stamped agent-logger payload version: $stamped"
        }
        if ($Action -in @('install', 'update', 'provision')) {
            $newerActive = $currentKey -and $currentKey -gt $desiredKey
            $duplicateAfterWait = $lockContended -and $current -eq $desired -and $deployed -eq $current
            if ($current -and (Test-Path $currentComplete) -and ($newerActive -or $duplicateAfterWait)) {
                $script:SkipPackageInstall = $true
            }
            if (
                -not $script:SkipPackageInstall -and
                $stampedKey -and
                (Test-Path $stampSnapshot) -and
                $stampedKey -gt $desiredKey
            ) {
                throw "Refusing stale agent-logger $Action payload $desired; stamped payload $stamped is newer."
            }
        } elseif ($Action -eq 'stamp') {
            $candidates = @(
                @{ Version = $current; Key = $currentKey; Path = $currentComplete },
                @{ Version = $stamped; Key = $stampedKey; Path = $stampSnapshot }
            )
            foreach ($candidate in $candidates) {
                if (-not $candidate.Version -or -not (Test-Path $candidate.Path)) { continue }
                $newer = $candidate.Key -gt $desiredKey
                $duplicateAfterWait = $lockContended -and $candidate.Version -eq $desired
                if ($newer -or $duplicateAfterWait) {
                    $script:SkipStamp = $true
                    break
                }
            }
        }
    }
}

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }


function Write-Ok      { param([string]$m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Changed { param([string]$m) Write-Host "  [->]   $m" -ForegroundColor Yellow }
function Write-Warn2   { param([string]$m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Step    { param([string]$m) Write-Host "  ...    $m" }
function Write-Warn    { param([string]$m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Fail    { param([string]$m) Write-Host "  [FAIL] $m" -ForegroundColor Red }

$InstallDir = Join-Path $env:USERPROFILE '.agent-logger'
$VenvDir    = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
# pythonw.exe is the GUI-subsystem (windowless) Python host. Running the
# scheduled sync under it -- rather than console python.exe -- stops the
# engine's own console window from flashing on each 4-hourly run. The engine's
# rsync/ssh children are kept windowless separately via CREATE_NO_WINDOW.
$VenvPythonw = Join-Path $VenvDir 'Scripts\pythonw.exe'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$TaskName   = 'Agent Logger Session Sync'
$BinstubPs1 = Join-Path $LocalBin 'session-sync.ps1'
$BinstubCmd = Join-Path $LocalBin 'session-sync.cmd'
# Every CLI name deployed as a binstub (.ps1 primary + .cmd fallback), each
# launching the venv's signed python via `-m <module>`. Kept in one list so
# Write-Binstubs (install) and the uninstall sweep stay in sync. The segmenter
# tools (collate-session, read-session-digest, prepare-session-log) are included
# so the log-session skill and the session-log-writer agent resolve them on PATH
# -- rather than assuming a console-script trampoline that this installer strips
# (SAC/CodeIntegrity-3077) and never replaces.
$BinstubNames = @('session-sync', 'agent-logger', 'collate-session', 'read-session-digest', 'prepare-session-log', 'ramp-up-session')

# === install-contract:v3 versioned-venv (agent-logger: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version> and
# make the historical `.venv` path a junction into it, so the binstubs and the
# scheduled sync task (which launches the windowless pythonw.exe) resolve through
# the link unchanged. LinkDir/LinkPython/LinkPythonw are the stable `.venv` paths;
# VenvDir/VenvPython(w) are the versions/<v> slot (build + health-gate). ALWAYS
# versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED / AGENT_LOGGER_VERSIONED)
# and the legacy in-place fork are retired; the code below reads neither var.
# scripts/versioned_runtime.py owns the swap + migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$LinkPythonw      = $VenvPythonw
$VersionedRuntime = $false
$SrcVersion       = $null
if ($true) {  # always versioned (junction-free marker model; COPILOT_EXT_NO_VERSIONED retired)
    $pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyprojForVer) {
        $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    if ($SrcVersion) {
        $VersionedRuntime = $true
        $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
        $VenvPython  = Join-Path $VenvDir 'Scripts\python.exe'
        $VenvPythonw = Join-Path $VenvDir 'Scripts\pythonw.exe'
        $LinkDir = $VenvDir
        $LinkPython = $VenvPython
        $LinkPythonw = $VenvPythonw
    }
}

function Invoke-VersionedActivate {
    <# Health-gate the freshly-built slot, swap the stable `.venv` junction onto it,
       then gc old slots keeping current + previous-good. First migration: the
       periodic sync task's pythonw may briefly hold a legacy real `.venv`, so stop
       the task before the rename-aside (best-effort). Returns $false on failure.
       No-op ($true) in legacy mode. #>
    if (-not $VersionedRuntime) { return $true }
    if ((Test-Path $LinkDir) -and -not ((Get-Item $LinkDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
    }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    if (-not (Test-Path $py)) { return $true }
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $VenvPython -c 'import agent_logger' 2>$null
    $slotOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if (-not $slotOk) {
        Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
        return $false
    }
    Invoke-VersionedMarkComplete
    $prev = (& $py $vr --root $InstallDir --link-name '.venv' current 2>$null); $prev = ("$prev").Trim()
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
        ForEach-Object { Write-Step $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
        return $false
    }
    Write-Ok "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($prev) { $gcArgs += @('--keep', $prev) }
    & $LinkPython @gcArgs 2>&1 | ForEach-Object { Write-Step "gc: $_" }
    $ErrorActionPreference = $prevEAP
    return $true
}
# === end install-contract:v3 versioned-venv ===

# === install-contract:v3 strip-trampolines -- keep byte-identical across plugins ===
function Remove-ConsoleTrampolines {
    <# Strip the uv-regenerated Scripts\<name>.exe console-script trampolines from
       the venv after install. They are unsigned, zero-reputation PEs that Smart
       App Control blocks (CodeIntegrity 3077); nothing launches them (binstubs,
       services, and probes all use "python.exe -m <pkg>"), so remove every
       agent-*.exe. Best-effort -- rename a locked copy aside, then sweep stale
       stashes. Windows-only: POSIX console scripts are the sanctioned launch
       path and must be preserved. #>
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($env:OS -ne 'Windows_NT') { return }
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    if (-not (Test-Path $scriptsDir)) { return }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe') -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Remove-Item $_.FullName -Force -ErrorAction Stop
        } catch {
            try { Rename-Item $_.FullName "$($_.FullName).old-$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop } catch {}
        }
    }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}
# === end install-contract:v3 strip-trampolines ===

# agent-logger ships console scripts that do not match agent-*.exe (session-sync,
# collate-session, read-session-digest, prepare-session-log). They are likewise
# unsigned, never launched (binstubs use python -m), and SAC-blocked, so sweep
# them too. The shared block above stays byte-identical; this is an additive,
# plugin-specific cleanup.
function Remove-LoggerTrampolines {
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($env:OS -ne 'Windows_NT') { return }
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    if (-not (Test-Path $scriptsDir)) { return }
    foreach ($n in @('session-sync', 'collate-session', 'read-session-digest', 'prepare-session-log', 'ramp-up-session')) {
        $exe = Join-Path $scriptsDir "$n.exe"
        if (Test-Path $exe) {
            try { Remove-Item $exe -Force -ErrorAction Stop }
            catch { try { Rename-Item $exe "$exe.old-$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop } catch {} }
        }
    }
    Get-ChildItem (Join-Path $scriptsDir '*.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}

function Get-SignedBasePython {
    <# Return a SAC-trusted (Authenticode-signed) base Python (>=3.10), or $null.
       Smart App Control blocks the unsigned uv-managed Python and console-script
       trampoline; a venv built from a signed base with `--copies` has a signed
       python.exe that SAC allows. #>
    if ($env:OS -ne 'Windows_NT') { return $null }
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p }
        }
    }
    foreach ($c in ($cands | Select-Object -Unique)) {
        if (Test-Path $c) {
            try { if ((Get-AuthenticodeSignature $c).Status -eq 'Valid') { return $c } } catch {}
        }
    }
    return $null
}

function New-SignedVenv {
    <# Create or rebuild $VenvDir so its python.exe is SAC-trusted. Prefers a
       signed base Python via `--copies`; rebuilds an existing unsigned venv;
       falls back to uv (unsigned) when no signed Python exists. Returns $true
       if $VenvPython is present afterward. #>
    # #935: toss an INCOMPLETE prior slot first so we never build over a corpse.
    Invoke-VersionedSlotClean
    if ((Test-Path $VenvPython) -and ($env:OS -eq 'Windows_NT')) {
        $sig = try { (Get-AuthenticodeSignature $VenvPython).Status } catch { 'Unknown' }
        if ($sig -ne 'Valid' -and (Get-SignedBasePython)) {
            Write-Step 'Existing venv python is unsigned (Smart App Control-incompatible) -- rebuilding from signed Python'
            try { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop }
            catch { Write-Warn "Could not remove existing venv (in use?): $_" }
        }
    }
    if (Test-Path $VenvPython) { return $true }

    $signedBase = Get-SignedBasePython
    if ($signedBase) {
        & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        if (Test-Path $VenvPython) {
            Write-Ok "Venv created from signed Python ($signedBase)"
            return $true
        }
        Write-Warn 'Signed-Python venv creation failed -- falling back to uv'
    } elseif ($env:OS -eq 'Windows_NT') {
        Write-Warn 'No signed system Python found -- using uv (unsigned). On Smart App Control machines, install python.org Python 3.10+ and re-run.'
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv venv $VenvDir --python 3.10 --allow-existing 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { & uv venv $VenvDir --allow-existing 2>&1 | Out-Null }
    $ErrorActionPreference = $prevEAP
    return (Test-Path $VenvPython)
}

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        $dirtyOut = git -C $Path status --porcelain 2>$null
        if ($dirtyOut) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. `update` re-installs from whatever
# the recorded footprint is, because the same installer is invoked from the
# same place.
# === install-contract:v4 marker/toss helpers (#935) ===
function Get-BootstrapPython {
    <# A python to run the stdlib-only versioned_runtime.py helper (#935).
       Prefers the freshly-built slot venv python ($VenvDir, present at
       mark-complete before the link is swapped), then the active link's
       python, then a real base python via the `py` launcher -- avoiding the
       Windows Store 'python' alias stub. Returns $null if none. #>
    foreach ($d in @($VenvDir, $LinkDir)) {
        if ($d) { $p = Join-Path $d 'Scripts\python.exe'; if (Test-Path $p) { return $p } }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = (& py -3 -c 'import sys; print(sys.executable)' 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe }
    }
    foreach ($cand in 'python3', 'python') {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    return $null
}

function Get-PayloadHash {
    <# Cheap payload fingerprint for the completion marker (#935): sha256 of
       pyproject.toml + the vendored-lib version set. Never throws -> '' on error. #>
    try {
        $parts = @()
        $pp = Join-Path $PluginDir 'pyproject.toml'
        if (Test-Path $pp) { $parts += (Get-Content $pp -Raw) }
        $libs = Join-Path $PluginDir 'libs'
        if (Test-Path $libs) {
            Get-ChildItem $libs -Recurse -Filter 'pyproject.toml' -ErrorAction SilentlyContinue |
                Sort-Object FullName | ForEach-Object { $parts += (Get-Content $_.FullName -Raw) }
        }
        $joined = [string]::Join("`n", $parts)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))
        return (-join ($bytes | ForEach-Object { $_.ToString('x2') }))
    } catch { return '' }
}

function Invoke-VersionedSlotClean {
    <# Toss an INCOMPLETE prior slot before building so we never `uv venv
       --allow-existing` over a corpse (#935); the current/active slot is never
       tossed (link-name derived from $LinkDir so the guard works per plugin).
       No-op in legacy mode. #>
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return }
    & $py $vr --root $InstallDir --link-name (Split-Path -Leaf $LinkDir) slot $SrcVersion --clean-incomplete 2>&1 |
        ForEach-Object { Write-Host "  ...    $_" }
}

function Invoke-VersionedMarkComplete {
    <# Write the slot's completion marker AFTER its isolated health gate passed,
       so "marker present" == "healthy, complete build". A crashed / watchdog-
       killed install never reaches here, leaving its slot markerless and thus
       tossable + retryable (#935). No-op in legacy mode. #>
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return }
    $mcArgs = @($vr, '--root', $InstallDir, '--link-name', (Split-Path -Leaf $LinkDir), 'mark-complete', $SrcVersion)
    $ph = Get-PayloadHash
    if ($ph) { $mcArgs += @('--payload-hash', $ph) }
    & $py @mcArgs 2>&1 | ForEach-Object { Write-Host "  ...    $_" }
}
# === end install-contract:v4 marker/toss helpers ===

function Get-SourceKind {
    param([string]$PluginPath)
    # #935: when the installer self-staged out of the marketplace payload, its
    # live path is a throwaway stage dir, so infer the kind from the ORIGINAL
    # payload path the self-stage prologue recorded (else the current path).
    $__srcPath = if ($env:COPILOT_PLUGIN_STAGED_FROM) { $env:COPILOT_PLUGIN_STAGED_FROM } else { $PluginPath }
    if (($__srcPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

# Unified schema_version 3 manifest writer. Self-contained per plugin (no shared
# module -- plugins are pulled independently from the marketplace). Records the
# source footprint (local vs marketplace) and is written atomically (temp+move).
function Write-DeployManifestFor {
    param(
        [string]$Service,
        [string]$Plugin,
        [string]$InstallPath,
        [string]$PluginPath,
        [string]$VenvPath
    )
    $manifestPath = Join-Path $InstallPath 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginPath

    $ver = '0.0.0'
    $pyproj = Join-Path $PluginPath 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }

    # Git provenance only applies to a local checkout -- the marketplace vendor
    # copy is not a git repo.
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $gitInfo = Get-GitInfo -Path (Split-Path $PluginPath)
        $commit = $gitInfo.commit; $branch = $gitInfo.branch; $dirty = $gitInfo.dirty
    }

    $manifest = [ordered]@{
        schema_version = 3
        service        = $Service
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginPath -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = $Plugin
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($VenvPath -replace '\\', '/')
        runtime        = 'python'
    }

    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
}

function Write-DeployManifest {
    Write-DeployManifestFor -Service 'agent-logger' -Plugin 'agent-logger' `
        -InstallPath $InstallDir -PluginPath $PluginDir -VenvPath $LinkDir
}

function Publish-PayloadSnapshot {
    <# Publish one immutable payload snapshot and atomically point payload-dir
       and stamped-version at it. Existing complete same-version snapshots are
       reused, never removed beneath a running compatibility wrapper. #>
    foreach ($dir in @($InstallDir, (Join-Path $InstallDir 'snapshots'))) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    if (Test-Path $snapDir) {
        $complete = (Test-Path (Join-Path $snapDir 'plugin.json')) -and
            (Test-Path (Join-Path $snapDir 'bin\agent-logger.ps1'))
        if (-not $complete) {
            throw "Existing agent-logger snapshot is incomplete; refusing replacement: $snapDir"
        }
    } else {
        $snapTmp = "$snapDir.tmp-$PID"
        if (Test-Path $snapTmp) {
            Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
        $exclude = @(
            '.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist',
            '.pytest_cache', '.mypy_cache', 'tests'
        )
        Get-ChildItem -LiteralPath $PluginDir -Force |
            Where-Object { $exclude -notcontains $_.Name } |
            ForEach-Object {
                Copy-Item -LiteralPath $_.FullName `
                    -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
            }
        Move-Item -LiteralPath $snapTmp -Destination $snapDir
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($marker in @{
        'payload-dir'     = $snapDir
        'stamped-version' = $SrcVersion
    }.GetEnumerator()) {
        $path = Join-Path $InstallDir $marker.Key
        $tmp = "$path.tmp-$PID"
        [System.IO.File]::WriteAllText($tmp, [string]$marker.Value, $utf8NoBom)
        Move-Item -LiteralPath $tmp -Destination $path -Force
    }
    Write-Ok "Snapshot: $snapDir"
    return $snapDir
}

function Write-Binstubs {
    <# Compatibility alias retained for the install flow. Every auxiliary
       command delegates to its owning payload shim, so first-use provisioning
       and later updates preserve one attributable launch model. #>
    param([Parameter(Mandatory)][string]$PythonExe)
    Deploy-AuxiliaryCompatibilityBinstubs
}

function Install-Package {
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    if (-not (Test-Path $LocalBin))   { New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null }

    # Prerequisite: uv (venv + package management per the install contract).
    try { uv --version 2>&1 | Out-Null } catch {
        Write-Fail 'uv not found on PATH (required for venv + package management)'
        Write-Fail 'Install: https://docs.astral.sh/uv/getting-started/installation/'
        exit 1
    }

    # SAC-safe venv: prefer a signed base Python via --copies; rebuild unsigned.
    if (-not (New-SignedVenv)) {
        Write-Fail "Failed to create venv at $VenvDir"
        exit 1
    }

    # Pre-strip any locked console-script trampoline so uv can overwrite it
    # (Windows denies overwriting an in-use .exe -- os error 5).
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Remove-LoggerTrampolines -VenvDir $VenvDir

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Vendored config-schema-migration lib (agent-config-migrate / module
    # config_migrate): plugin-vendored (marketplace) or repo-root (git checkout).
    $cfgMigrateDir = Join-Path $PluginDir 'libs\config-migrate'
    if (-not (Test-Path (Join-Path $cfgMigrateDir 'pyproject.toml'))) {
        $cfgMigrateDir = Join-Path (Split-Path -Parent (Split-Path -Parent $PluginDir)) 'libs\config-migrate'
    }
    if (Test-Path (Join-Path $cfgMigrateDir 'pyproject.toml')) {
        & uv pip install --python $VenvPython --reinstall-package agent-config-migrate "$cfgMigrateDir" --quiet 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "config-migrate library install failed"
            exit 1
        }
    }
    $out = & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1
    $result = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($result -ne 0) {
        Write-Fail "Package install failed (exit $result)"
        if ($out) { Write-Host ($out | Out-String) }
        exit 1
    }
    Write-Ok "installed agent-logger package"

    # Strip the uv-regenerated console-script trampolines (SAC-blocked, unused).
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Remove-LoggerTrampolines -VenvDir $VenvDir

    # Versioned layout (#581): health-gate the slot + swap the `.venv` junction.
    # Everything below (binstubs, task, manifest) resolves through the link.
    if (-not (Invoke-VersionedActivate)) { exit 1 }

    # Binstubs: .ps1 primary + .cmd fallback that invoke `python -m`
    # (never the SAC-blocked console-script trampolines). Point at the stable
    # `.venv` link ($LinkPython), never a versions/<v> absolute a `gc` could remove.
    Publish-PayloadSnapshot | Out-Null
    Write-Binstubs -PythonExe $LinkPython
    # The primary `agent-logger` entrypoint is a SELF-PROVISIONING binstub
    # (deployed byte-identically at stamp + here) so it rebuilds the runtime on
    # first use on a stamped-only box; post-provision it fast-paths the slot.
    Deploy-SelfProvisioningBinstub

    # Machine-local config schema migration (idempotent + atomic). Non-fatal.
    try {
        $env:PYTHONUTF8 = '1'
        & $VenvPython -m agent_logger config-migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
    } catch {
        Write-Warn "config migration skipped: $_"
    }

    # Record the deploy footprint (source: local vs marketplace).
    Write-DeployManifest
}

function New-SyncTaskAction {
    # Prefer the windowless host so the task never flashes a console; fall back
    # to console python.exe only if pythonw.exe is somehow absent. Resolve through
    # the stable `.venv` link ($LinkPythonw), never a versions/<v> absolute a `gc`
    # could remove.
    $runHost = if (Test-Path $LinkPythonw) { $LinkPythonw } else { $LinkPython }
    # Resolve the active slot from the marker; tolerate either legacy .venv or
    # already-slot LinkDir when deriving the install root.
    $_root = Split-Path $LinkDir
    if ((Split-Path -Leaf $_root) -eq 'versions') { $_root = Split-Path $_root }
    $_ver = ''
    try { $_ver = ([IO.File]::ReadAllText((Join-Path $_root 'current-version'))).Trim() } catch {}
    if ($_ver) {
        $slot = Join-Path $_root ('versions\' + $_ver)
        $cand = Join-Path $slot 'Scripts\pythonw.exe'
        $runHost = if (Test-Path -LiteralPath $cand) { $cand } else { Join-Path $slot 'Scripts\python.exe' }
    }
    if (-not ($runHost -and (Test-Path -LiteralPath $runHost))) {
        $runHost = Get-ChildItem (Join-Path $_root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { $cand = Join-Path $_.FullName 'Scripts\pythonw.exe'; if (Test-Path -LiteralPath $cand) { $cand } else { Join-Path $_.FullName 'Scripts\python.exe' } } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Last 1
    }
    if (-not ($runHost -and (Test-Path -LiteralPath $runHost))) {
        return $null
    }
    return New-ScheduledTaskAction -Execute $runHost `
        -Argument '-m agent_logger.sync.engine run --prune'
}

function Register-SyncTask {
    $action = New-SyncTaskAction
    if (-not $action) {
        throw "Cannot register scheduled task: no provisioned agent-logger runtime"
    }
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(5) `
        -RepetitionInterval (New-TimeSpan -Hours 4)
    $trigger.Repetition.StopAtDurationEnd = $false
    # 30-min cap: the first sync cold-copies the whole session history (can take
    # 10+ min over a network/CIFS path); a 10-min limit killed it mid-copy.
    # Incremental runs finish in seconds.
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    # Interactive logon: runs as the current user when logged on, and -- unlike
    # an S4U principal -- registers without elevation. Right default for a
    # per-user roaming workstation. (Run-when-logged-off would need admin.)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal | Out-Null
        Write-Changed "scheduled task updated (every 4h)"
    } else {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal `
            -Description 'Agent Logger -- push Copilot session data to the configured target every 4 hours.' | Out-Null
        Write-Changed "scheduled task registered (every 4h)"
    }
}

function Update-SyncTaskBinding {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        $action = New-SyncTaskAction
        if (-not $action) {
            Write-Warn2 "scheduled task left unchanged: no provisioned runtime"
            return
        }
        Set-ScheduledTask -TaskName $TaskName -Action $action | Out-Null
        Write-Changed "scheduled task runtime updated"
    } else {
        Write-Ok "package updated (task not registered)"
    }
}

function Deploy-SelfProvisioningBinstub {
    # The primary `agent-logger` binstub (.ps1 primary + .cmd fallback), SELF-
    # PROVISIONING (#1393): fast-path the built versioned slot's python; if no
    # slot is built yet (a `stamp` deferred the venv), provision on first use by
    # running the slot-local snapshot's `scripts/install.ps1 provision`, then
    # dispatch. Opt out with AGENT_LOGGER_NO_SELFPROVISION=1. Deployed byte-
    # identically at stamp AND during Install-Package so a provision triggered by
    # this very binstub never rewrites the .cmd it is mid-execution.
    if (-not (Test-Path $LocalBin)) { New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    # Co-deploy the canonical resolvers so every launcher resolves identically
    # (uniform-runtime-resolution, #765).
    $binDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    foreach ($r in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $rSrc = Join-Path $PSScriptRoot $r
        if (Test-Path $rSrc) { Copy-Item $rSrc (Join-Path $binDir $r) -Force }
    }
    if ($env:OS -ne 'Windows_NT') {
        $stubPath = Join-Path $LocalBin 'agent-logger'
        $stubContent = @'
#!/usr/bin/env bash
export PYTHONUTF8=1
_root="$HOME/.agent-logger"
AGENT_RT_PY=""
if [ -f "$_root/bin/resolve-runtime.sh" ]; then AGENT_RT_ROOT="$_root"; . "$_root/bin/resolve-runtime.sh"; fi
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_logger "$@"
_i="$(cat "$_root/payload-dir" 2>/dev/null)/scripts/install.sh"
[ -f "$_i" ] || _i="$(ls "$HOME"/.copilot/installed-plugins/*/agent-logger/scripts/install.sh 2>/dev/null | head -n1)"
if [ -n "$_i" ] && [ -f "$_i" ]; then echo "[agent-logger] runtime not provisioned; run: bash \"$_i\" provision" >&2; else echo "[agent-logger] runtime not provisioned and the installer was not found; re-enable the plugin, then retry." >&2; fi
exit 1
'@
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
        Write-Ok "Binstub: $stubPath"
        return
    }
    $ps1Path = Join-Path $LocalBin 'agent-logger.ps1'
    $ps1Content = @'
$env:PYTHONUTF8 = '1'
$_root = Join-Path $env:USERPROFILE '.agent-logger'
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
function _Resolve-Py {
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $_resolver) { $env:AGENT_RT_ROOT = $_root; . $_resolver }
    return $AgentRtPy
}
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_logger @args; exit $LASTEXITCODE }
if ($env:AGENT_LOGGER_NO_SELFPROVISION) { [Console]::Error.WriteLine('[agent-logger] runtime not provisioned (AGENT_LOGGER_NO_SELFPROVISION set).'); exit 1 }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) { Join-Path $_snap 'scripts\install.ps1' } else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-logger] cannot self-provision: snapshot installer not found. Re-enable the plugin, then retry.'); exit 127 }
[Console]::Error.WriteLine('[agent-logger] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-logger eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
& $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_logger @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-logger] provisioning did not yield a runtime. See the log above; retry, or run the snapshot installer manually.')
exit 1
'@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

    $cmdPath = Join-Path $LocalBin 'agent-logger.cmd'
    # cmd fallback: delegate to the .ps1 binstub so resolution stays uniform with
    # the canonical resolve-runtime.ps1 chain and self-provisioning is shared.
    $cmdContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=%USERPROFILE%\.local\bin\agent-logger.ps1"
if not exist "%_PS1%" (echo [agent-logger] binstub not found: %_PS1%>&2 & exit /b 127)
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*) else (powershell -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*)
exit /b %ERRORLEVEL%
'@
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    Write-Ok "Binstub: $cmdPath (+ .ps1, self-provisioning)"
}

function Deploy-AuxiliaryCompatibilityBinstubs {
    <# Publish legacy/global fallbacks for every auxiliary payload command.

       The wrapper does not select the runtime itself. It resolves the durable
       payload marker written by stamp, then delegates to that payload's
       generated command shim so command ownership and first-use provisioning
       remain attributable to the plugin that supplied the capability. #>
    if (-not (Test-Path $LocalBin)) {
        New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($name in $BinstubNames | Where-Object { $_ -ne 'agent-logger' }) {
        $ps1Path = Join-Path $LocalBin "$name.ps1"
        $ps1Content = @'
$ErrorActionPreference = 'Stop'
$_command = '__COMMAND__'
$_root = Join-Path $env:USERPROFILE '.agent-logger'
$_payload = ''
try { $_payload = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_shim = if ($_payload) { Join-Path $_payload "bin\$($_command).ps1" } else { '' }
if (-not ($_shim -and (Test-Path -LiteralPath $_shim))) {
    [Console]::Error.WriteLine("[$_command] owning payload shim not found: $_shim")
    [Console]::Error.WriteLine("[$_command] re-enable/update agent-logger, then retry.")
    exit 127
}
$env:COPILOT_PLUGIN_ROOT = $_payload
& $_shim @args
exit $LASTEXITCODE
'@.Replace('__COMMAND__', $name)
        [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

        $cmdPath = Join-Path $LocalBin "$name.cmd"
        $cmdContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=%USERPROFILE%\.local\bin\__COMMAND__.ps1"
if not exist "%_PS1%" (echo [__COMMAND__] binstub not found: %_PS1%>&2 & exit /b 127)
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*) else (powershell -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*)
exit /b %ERRORLEVEL%
'@.Replace('__COMMAND__', $name)
        [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    }
    $auxiliaryCount = @(
        $BinstubNames | Where-Object { $_ -ne 'agent-logger' }
    ).Count
    Write-Ok "Auxiliary compatibility binstubs: $auxiliaryCount commands on PATH"
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE into
    # ~/.agent-logger/snapshots/<ver>/, record markers, and deploy ONLY the
    # self-provisioning `agent-logger` binstub -- deferring the venv build (and the
    # 5 auxiliary binstubs + scheduled task) to a provision on first use. No venv,
    # no uv; never holds the marketplace payload open (copies from the already
    # self-staged $PluginDir).
    Write-Host ''
    Write-Host '=== agent-logger stamp (defer runtime to first use) ===' -ForegroundColor Cyan
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Publish-PayloadSnapshot | Out-Null
    Deploy-SelfProvisioningBinstub
    Deploy-AuxiliaryCompatibilityBinstubs
    Write-Ok 'Stamped: agent-logger command family on PATH; runtime provisions on first use.'
}

switch ($Action) {
    'stamp' {
        if (-not $script:SkipStamp) { Invoke-Stamp }
    }
    'provision' {
        if (-not $script:SkipPackageInstall) { Install-Package }
        Write-Ok "runtime provisioned"
    }
    'install' {
        if (-not $script:SkipPackageInstall) { Install-Package }
        Register-SyncTask
        Write-Ok "install complete"
    }
    'update' {
        if (-not $script:SkipPackageInstall) { Install-Package }
        Update-SyncTaskBinding
    }
    'uninstall' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Changed "scheduled task removed (config at $InstallDir kept)"
        } else {
            Write-Warn2 "no scheduled task found"
        }
        foreach ($name in $BinstubNames) {
            foreach ($ext in 'ps1', 'cmd') {
                $f = Join-Path $LocalBin "$name.$ext"
                if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
            }
        }
        Write-Changed "binstubs removed from $LocalBin"
    }
    'status' {
        if (Test-Path $LinkPython) {
            Write-Ok ("installed: " + (& $LinkPython -m agent_logger version))
            & $LinkPython -m agent_logger.sync.engine status
        } else {
            Write-Warn2 "not installed (run: install.ps1 install)"
        }
        if (Test-Path $BinstubPs1) {
            Write-Ok "binstub present (session-sync.ps1)"
        } elseif (Test-Path $BinstubCmd) {
            Write-Warn2 "only the .cmd binstub is present (missing session-sync.ps1)"
        } else {
            Write-Warn2 "binstub not deployed"
        }
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Write-Ok "scheduled task present"
        } else {
            Write-Warn2 "scheduled task not registered"
        }
    }
}
