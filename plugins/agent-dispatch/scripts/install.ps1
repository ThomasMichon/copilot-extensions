<#
.SYNOPSIS
    agent-dispatch installer / lifecycle manager. PS5+ compatible.

.DESCRIPTION
    Canonical installer for the agent-dispatch runtime -- the same lifecycle
    shape as the agent-bridge installer (install|update|status|start|stop|
    uninstall), so the agent-worktrees plugin reconciler (runtimeScope:
    machine-gated) and `test-chamber services agent-dispatch <action>` both
    drive it.

    Creates the runtime at ~/.agent-dispatch/ (venv + package), a
    ~/.local/bin/agent-dispatch.cmd binstub, and -- on its deploy machines --
    an auto-starting Windows Scheduled Task running the FULL coordinator. The
    always-on Windows host owns the coordinator (Phase 2, issue #2818); it binds
    adaptively by WSL networking mode (mirrored -> 127.0.0.1; NAT -> the
    vEthernet(WSL) IP, resolved at startup, never 0.0.0.0/LAN). This reverses the
    #2777 model where WSL owned the coordinator and Windows was a client.

    On a coordinator host it ALSO installs the embody SUPERVISOR
    (Scheduled Task 'agent-dispatch-supervisor'), which runs
    `agent-dispatch supervise --all-repos` so dispatched, LABELED tasks are
    turned into host embody autopilots unattended -- the Windows peer of the
    Linux systemd supervisor unit (cross-platform-parity). It is label-gated
    for safety (a label-less supervisor would embody every queued task): the
    task is enabled only when AGENT_DISPATCH_SUPERVISE_LABELS is set in
    supervisor.env; with none set the task is registered but left DISABLED
    (inert), and the generated launcher hard-refuses a label-less run (#2869).

.PARAMETER Action
    install (default) | update | status | start | stop | uninstall.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-dispatch).

.PARAMETER NoService
    Install/update the client (venv + binstub) only; do NOT install/start the
    coordinator Scheduled Task (a deliberately client-only host). Also skips the
    embody supervisor (it needs a local coordinator).

.PARAMETER NoSupervisor
    Install everything EXCEPT the embody supervisor Scheduled Task (the
    coordinator still installs on an eligible host).

.PARAMETER Purge
    On uninstall: also delete config, DB, and the env file.

.PARAMETER Force
    On update: bypass the downgrade guard (deliberate rollback). Env:
    AGENT_DISPATCH_ALLOW_DOWNGRADE=1.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall', 'stamp', 'provision')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [switch]$NoService,
    [switch]$NoSupervisor,
    [switch]$Interactive,
    [switch]$Purge,
    [switch]$Force,

    # DEPRECATED / no-op (Thread B): the graceful zdd cutover
    # (Invoke-CoordinatorCutover) is already the DEFAULT on `update` whenever a
    # live, routed coordinator is running -- activation always cuts over
    # automatically, so this opt-in is not required. The switch is still
    # ACCEPTED (so a caller such as the launch-path reconciler, which appends
    # it whenever a plugin declares `"zeroDowntimeUpdate": true`, doesn't
    # break) but has no effect.
    [switch]$ZeroDowntime,

    # Preview mode for the 'uninstall' action: print what WOULD be removed
    # without touching the filesystem, scheduled task, autostart, or firewall.
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# === install-contract:test-persistent-environment -- keep byte-identical across installers ===
function Get-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    return [Environment]::GetEnvironmentVariable($Name, $effectiveTarget)
}

function Set-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][string]$Value,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    [Environment]::SetEnvironmentVariable($Name, $Value, $effectiveTarget)
}
# === end install-contract:test-persistent-environment ===

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

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }


if ($env:AGENT_DISPATCH_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

# -- Output helpers (PS5-safe) ------------------------------------------

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

# Stable, documented contract (ThomasMichon/copilot-extensions#5066): a file
# containing a single epoch-seconds expiry, present for the duration of a
# live-service update/start lifecycle -- any downstream consumer (a local
# liveness watchdog, a diagnostic tool) can check its content without needing
# any plugin-specific caller-side wrapping. $UpdateMarker itself is set once
# $InstallDir is finalized below; these two functions only reference it at
# call time, so defining them here is safe.
#
# Process-local: true once THIS invocation holds a refcount slot.
$script:UpdateMarkerHeld = $false

# Reference-counted, not single-owner: Invoke-Update holding the marker for
# a long cutover and a separate, brief Invoke-Start both legitimately want
# it live at once, and a single-owner/last-writer-wins scheme would let the
# SHORT invocation's own exit delete the marker while the LONG one is still
# mid-transition -- the marker must stay present until every concurrent
# holder has released its own slot, not just the most recent one. A named
# System.Threading.Mutex (the standard cross-process lock primitive on
# Windows) makes increment/decrement atomic across processes.
#
# `New-Item -Force` the parent directory first: on a fresh or deleted
# install root, $InstallDir itself may not exist yet at the point either
# live-service lifecycle starts (its own provisioning step is what would
# normally create it) -- the marker must not fail BEFORE that provisioning
# ever gets a chance to run.
function Get-UpdateMarkerMutex {
    $name = 'Local\' + ($UpdateMarker -replace '[^a-zA-Z0-9]', '_') + '_refcount'
    return New-Object System.Threading.Mutex($false, $name)
}
function Write-UpdateMarker {
    param([int]$TtlSeconds = $UpdateMarkerTtlDefault)
    if ($script:UpdateMarkerHeld) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path $UpdateMarker -Parent) | Out-Null
    $countPath = "$UpdateMarker.refcount"
    $mutex = Get-UpdateMarkerMutex
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne(10000)
        $count = 0
        if (Test-Path $countPath) {
            $raw = Get-Content -Path $countPath -Raw -ErrorAction SilentlyContinue
            if ($raw) { [int]::TryParse($raw.Trim(), [ref]$count) | Out-Null }
        }
        if ($count -eq 0) {
            # First holder: stamp a fresh marker. A later joiner deliberately
            # does NOT refresh the expiry -- the TTL is a safety-net backstop,
            # not a per-holder renewal lease.
            $expiry = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + $TtlSeconds
            $tmp = "$UpdateMarker.$([Guid]::NewGuid().ToString('N'))"
            Set-Content -Path $tmp -Value $expiry -NoNewline
            Move-Item -Path $tmp -Destination $UpdateMarker -Force
        }
        $count++
        $tmp2 = "$countPath.$([Guid]::NewGuid().ToString('N'))"
        Set-Content -Path $tmp2 -Value $count -NoNewline
        Move-Item -Path $tmp2 -Destination $countPath -Force
        $script:UpdateMarkerHeld = $true
    } finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
# Releases this invocation's own refcount slot; only the holder that takes
# the count to zero actually removes the marker file, so it stays present
# for the full duration of whichever concurrent transition runs longest.
function Clear-UpdateMarker {
    if (-not $script:UpdateMarkerHeld) { return }
    $script:UpdateMarkerHeld = $false
    $countPath = "$UpdateMarker.refcount"
    $mutex = Get-UpdateMarkerMutex
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne(10000)
        $count = 0
        if (Test-Path $countPath) {
            $raw = Get-Content -Path $countPath -Raw -ErrorAction SilentlyContinue
            if ($raw) { [int]::TryParse($raw.Trim(), [ref]$count) | Out-Null }
        }
        if ($count -gt 0) { $count-- }
        if ($count -le 0) {
            Remove-Item -Path $UpdateMarker -Force -ErrorAction SilentlyContinue
            Remove-Item -Path $countPath -Force -ErrorAction SilentlyContinue
        } else {
            $tmp = "$countPath.$([Guid]::NewGuid().ToString('N'))"
            Set-Content -Path $tmp -Value $count -NoNewline
            Move-Item -Path $tmp -Destination $countPath -Force
        }
    } finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

# -- Paths --------------------------------------------------------------

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_dispatch'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-dispatch'
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$UpdateMarker = Join-Path $InstallDir 'update-in-progress'
$UpdateMarkerTtlDefault = 1200  # 20 min -- generous past any observed real cutover
$legacyInstallDir = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.agent-dispatch'))
$publishLegacyNames = [StringComparer]::OrdinalIgnoreCase.Equals($InstallDir, $legacyInstallDir)
$serviceSuffix = if ($publishLegacyNames) {
    ''
} else {
    $serviceSha = [Security.Cryptography.SHA256]::Create()
    try {
        ([BitConverter]::ToString(
            $serviceSha.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes($InstallDir.ToLowerInvariant())
            )
        )).Replace('-', '').Substring(0, 12).ToLowerInvariant()
    } finally {
        $serviceSha.Dispose()
    }
}
$env:AGENT_DISPATCH_INSTALL_DIR = $InstallDir
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$TaskName = if ($publishLegacyNames) { 'agent-dispatch' } else { "agent-dispatch-$serviceSuffix" }
$SupervisorTaskName = if ($publishLegacyNames) {
    'agent-dispatch-supervisor'
} else {
    "agent-dispatch-supervisor-$serviceSuffix"
}
$SupervisorProfileDir = Join-Path $InstallDir 'supervisors'
$DefaultPort = 9847

# === install-contract:v3 versioned-venv (agent-dispatch: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction (Windows) / symlink (POSIX)
# into it, so the binstubs, the coordinator + supervisor task launchers, and the
# deploy-manifest -- all of which reference `.venv` -- resolve through the link
# unchanged. LinkDir/LinkPython is the stable `.venv` path (runtime-facing, never
# a versions/<v> absolute a `gc` could remove); VenvDir/VenvPython is the
# versions/<v> slot (build + health-gate + the firewall -Program, which needs the
# RESOLVED image path the running daemon reports). ALWAYS versioned -- the env
# opt-out (COPILOT_EXT_NO_VERSIONED / AGENT_DISPATCH_VERSIONED) and the legacy
# in-place fork are retired; the code below reads neither var.
# scripts/versioned_runtime.py owns the swap + migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
# Initialized here, at script scope, unconditionally -- NOT merely inside
# Invoke-VersionedActivate (which only runs at all when $VersionedRuntime
# ends up true below). Install-Runtime/Invoke-Install/Invoke-Update all read
# $script:ActivationSuperseded immediately after their own
# Install-Runtime/Invoke-VersionedActivate call regardless of whether
# versioning actually applies -- a legacy/no-resolved-version run (see
# New-PluginBuildSnapshot's own -BestEffort degrade path a few lines below)
# leaves $VersionedRuntime false, so Invoke-VersionedActivate is never even
# called, and this would otherwise stay a genuinely UNSET variable that
# Set-StrictMode -Version 2.0 throws on at the very first read.
$script:ActivationSuperseded = $false
if ($true) {  # always versioned (junction-free marker model; COPILOT_EXT_NO_VERSIONED retired)
    $pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyprojForVer) {
        $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    if ($SrcVersion) {
        $VersionedRuntime = $true
        $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
        if ($env:OS -eq 'Windows_NT') { $VenvPython = Join-Path $VenvDir 'Scripts\python.exe' }
        else { $VenvPython = Join-Path $VenvDir 'bin/python' }
        $LinkDir = $VenvDir
        $LinkPython = $VenvPython
    }
}
# === end install-contract:v3 versioned-venv ===

# === install-contract:v3 versioned-venv helpers (agent-dispatch) ===
function Test-VenvIsLink {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try { return [bool]((Get-Item $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) }
    catch { return $false }
}

function Invoke-VersionedActivate {
    <# Activate this version's freshly-built slot as the runtime (write the
       current-version marker; junction-free on Windows). No-op in legacy mode.

       First migration only: a genuine LEGACY real `.venv` dir -- a pre-versioned
       install whose coordinator + supervisor still execute from
       `.venv\Scripts\python.exe` -- must be released before the slot can take
       over, because Windows can't remove a dir a loaded python.exe locks; so stop
       BOTH daemons first. A normal version-bump swaps only the marker (the daemons
       run from their own immutable versions/<v> slot until Invoke-Update cycles
       them), so NO stop is needed.

       #689: gate on the ACTUAL `.venv` path, NOT $LinkDir. The versioned refactor
       repointed $LinkDir at the freshly-built versions/<v> slot -- ALWAYS a real,
       non-link dir -- so the old $LinkDir-based guard was true on EVERY update,
       force-stopping the coordinator + supervisor each time and defeating a
       non-elevated in-place refresh. In the junction-free marker model `.venv` is
       normally absent (the binstub, task launchers, and deploy-manifest all
       resolve the slot through the marker), so this guard is correctly false on a
       normal update.

       Version-ordering guard, UNDER a GLOBAL ($InstallDir-only, cross-version)
       lock: Install-Runtime's own buildMutex is deliberately VERSION-scoped (a
       wedged build for one version must never block an unrelated install of a
       different version), so two DIFFERENT versions can legitimately build and
       reach this activation call fully concurrently, each under its own
       independent lock. Without a cross-version check right here, a slower
       OLDER-version build that started first could still finish (and call
       activate) AFTER a faster NEWER-version build already activated, silently
       overwriting `current-version` with a regression -- versioned_runtime.py's
       own `activate` performs no version comparison of its own. Mirrors
       Invoke-Stamp's identical marker-publication guard (same lock scope,
       same Test-VersionLt comparison, same -Force/AGENT_DISPATCH_ALLOW_DOWNGRADE
       override for a deliberate rollback) -- read the currently-active version
       INSIDE the lock so this observes the true latest publish, not a stale
       snapshot read before acquiring it.

       Skipping the marker write alone is NOT enough: $script:ActivationSuperseded
       is a POINT-IN-TIME signal set here, at the moment this call either skips or
       succeeds -- it is NOT, by itself, a sufficient guarantee for a caller that
       acts LATER (manifest write, coordinator cutover): this invocation can
       genuinely WIN its own activation (nothing newer was current yet), release
       this lock, and only THEN have a separate, slower-to-build newer version
       activate before this invocation reaches its own downstream action -- this
       lock only serializes the compare-then-publish step itself, it cannot
       serialize everything a caller does afterward. Callers must re-validate LIVE
       immediately before acting via Test-ActivationSupersededNow (below) rather
       than trusting this variable alone -- Install-Runtime, Invoke-Install, and
       Invoke-Update all call it immediately before their own remaining steps
       (manifest/verify/PATH/pivot; coordinator cutover) rather than proceeding as
       if this invocation's own $VenvPython/$LinkPython were the one that's
       actually live -- otherwise a superseded (older) invocation would still
       publish its own manifest over the newer one's, or drive
       `Invoke-CoordinatorCutover`/`Confirm-CoordinatorRunning` from its own stale
       build, rolling back or misreporting the coordinator even though activation
       itself was correctly skipped (or was correctly accepted at the time, then
       overtaken). #>
    $script:ActivationSuperseded = $false
    if (-not $VersionedRuntime) { return $true }
    $activateMutex = Enter-PluginSnapshotLock -InstallDir $InstallDir
    try {
        $currentActive = Get-VersionedCurrent
        if ($currentActive -and (Test-VersionLt -A $SrcVersion -B $currentActive) -and -not $Force) {
            Write-Skip "Not activating: source $SrcVersion is older than already-active $currentActive (a newer build activated first; -Force to override)"
            $script:ActivationSuperseded = $true
            return $true
        }
        $legacyVenv = Join-Path $InstallDir '.venv'
        if ((Test-Path $legacyVenv) -and -not (Test-VenvIsLink $legacyVenv)) {
            Write-Step 'Releasing legacy .venv for versioned migration (stopping coordinator + supervisor)...'
            try { Stop-DispatchProcess -Subcommand serve | Out-Null } catch {}
            try { Retire-SupervisorProcesses | Out-Null } catch {}
        }
        $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
        $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
        & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
            ForEach-Object { Write-Step $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
            return $false
        }
        Write-Ok "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
        return $true
    } finally {
        [void]$activateMutex.ReleaseMutex()
        $activateMutex.Dispose()
    }
}

function Get-VersionedCurrent {
    if (-not $VersionedRuntime) { return '' }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return '' }
    $out = & $py $vr --root $InstallDir --link-name '.venv' current 2>$null
    return ("$out").Trim()
}

function Test-ActivationSupersededNow {
    <# True iff THIS invocation should NOT proceed with its own downstream
       publication/cutover -- re-validated LIVE at the moment of the call, not
       merely a replay of Invoke-VersionedActivate's own point-in-time
       $script:ActivationSuperseded snapshot. That snapshot alone is
       insufficient: this invocation can genuinely WIN its own (lock-protected)
       activation -- nothing newer was current yet -- release the lock, and
       only THEN have a separate, slower-to-build newer version activate before
       this invocation reaches Write-Manifest or Invoke-CoordinatorCutover.
       Re-reading Get-VersionedCurrent here catches that window too: if the
       live current version has since moved on to something other than this
       invocation's own $SrcVersion, treat it as superseded regardless of how
       the earlier activation call itself went. #>
    if (-not $VersionedRuntime) { return $false }
    if ($script:ActivationSuperseded) { return $true }
    $now = Get-VersionedCurrent
    return [bool]($now -and ($now -ne $SrcVersion))
}

function Invoke-VersionedGc {
    param([string]$KeepPrev)
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return }
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($KeepPrev) { $gcArgs += @('--keep', $KeepPrev) }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $py @gcArgs 2>&1 | ForEach-Object { Write-Step "gc: $_" }
    $ErrorActionPreference = $prevEAP
}
# === end install-contract:v3 versioned-venv helpers ===
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

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
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

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        if (git -C $Path status --porcelain 2>$null) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

# -- Version helpers + downgrade guard (parity with agent-bridge #1790) ------

function Get-InstalledVersion {
    if (-not (Test-Path $LinkPython)) { return $null }
    try {
        $v = & $LinkPython -c 'from importlib.metadata import version; print(version("agent-dispatch"))' 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return $v.Trim() }
    } catch {}
    return $null
}

function Get-SourceVersion {
    $manifest = Join-Path $PluginDir 'plugin.json'
    if (-not (Test-Path $manifest)) { return $null }
    $m = Select-String -Path $manifest -Pattern '"version"\s*:\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return ($m.Line -replace '.*"version"\s*:\s*"([^"]+)".*', '$1') }
    return $null
}

# Integer tuple from a version (extract every run of digits). [0,1,0,19] for
# 0.1.0-dev19. Compared element-wise so the devN build stream orders correctly.
function Get-VerTuple {
    param([string]$v)
    $nums = [regex]::Matches($v, '\d+') | ForEach-Object { [int]$_.Value }
    return , @($nums)
}

function Test-VersionLt {
    param([string]$A, [string]$B)
    if ($A -eq $B) { return $false }
    $ta = Get-VerTuple $A; $tb = Get-VerTuple $B
    $n = [Math]::Max($ta.Count, $tb.Count)
    for ($i = 0; $i -lt $n; $i++) {
        $aHas = $i -lt $ta.Count
        $bHas = $i -lt $tb.Count
        # A version that ran out of components here (e.g. "0.2.0" vs
        # "0.2.0-dev1") is the finished release; a release outranks any
        # devN pre-release build of the same prefix -- mirrors
        # install.sh's own _version_lt (#377's same false-positive class:
        # treating a missing component as 0 here would make "0.2.0" sort
        # as OLDER than "0.2.0-dev1", silently blocking a legitimate
        # dev-to-release promotion).
        if (-not $aHas) { return $false }
        if (-not $bHas) { return $true }
        $x = $ta[$i]; $y = $tb[$i]
        if ($x -lt $y) { return $true }
        if ($x -gt $y) { return $false }
    }
    return $false
}

function Invoke-DowngradeGuard {
    $installed = Get-InstalledVersion
    if (-not $installed) { return }
    $source = Get-SourceVersion
    if (-not $source) {
        Write-Warn 'Could not read source version from plugin.json -- skipping downgrade guard'
        return
    }
    if (Test-VersionLt -A $source -B $installed) {
        if ($Force) {
            Write-Warn "Downgrade $installed -> $source forced (-Force / AGENT_DISPATCH_ALLOW_DOWNGRADE)"
            return
        }
        Write-Host ''
        Write-Fail "Refusing to downgrade agent-dispatch: installed $installed > source $source"
        Write-Fail 'This checkout is OLDER than the deployed runtime. Use the sanctioned path:'
        Write-Fail '    test-chamber services agent-dispatch update'
        Write-Fail 'Or override intentionally (deliberate rollback):'
        Write-Fail "    install.ps1 -Action $Action -Force"
        Write-Host ''
        exit 1
    }
}

# -- Runtime install (venv + package + binstub + manifest + verify + pivot) --

function Resolve-VendoredLib {
    param(
        [Parameter(Mandatory)][string]$LibName,
        # Where candidate 1 looks for libs/<name> -- the durable build
        # snapshot during a real install (see New-PluginBuildSnapshot),
        # defaulting to the live $PluginDir for any other caller.
        [string]$LibRoot = $PluginDir
    )
    # 1. Vendored inside agent-dispatch (marketplace install layout)
    $candidate = Join-Path $LibRoot "libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }
    # 2. Relative path (git checkout layout) -- always against the REAL
    # $PluginDir (not $LibRoot): a snapshot is a flat copy with no `../../libs`
    # sibling, so this candidate only ever makes sense against the true,
    # on-disk checkout location.
    $candidate = Join-Path $PluginDir "..\..\libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }
    # 3. Git repo registry (~/.git-repos) -- use Python for safe YAML parsing
    $gitRepos = Join-Path $env:USERPROFILE '.git-repos'
    if (Test-Path $gitRepos) {
        try {
            $result = & python3 -c @"
import pathlib, os
try:
    import yaml
except ImportError:
    raise SystemExit(1)
reg = yaml.safe_load(pathlib.Path.home().joinpath('.git-repos').read_text())
repo = (reg or {}).get('repos', {}).get('copilot-extensions', {})
if repo:
    p = repo.get('path', os.path.join(reg.get('srcroot', ''), 'copilot-extensions'))
    p = os.path.expanduser(p)
    lib = os.path.join(p, 'libs', '$LibName')
    if os.path.isfile(os.path.join(lib, 'pyproject.toml')):
        print(lib)
        raise SystemExit(0)
raise SystemExit(1)
"@ 2>$null
            if ($LASTEXITCODE -eq 0 -and $result) {
                return $result.Trim()
            }
        } catch { }
    }
    # 4. Common checkout path (repo exists but registry absent/stale)
    $candidate = Join-Path $env:USERPROFILE "src\copilot-extensions\libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }
    return $null
}

# zero-downtime graceful-cutover primitives (module ``zdd``).
function Resolve-Zdd {
    param([string]$LibRoot = $PluginDir)
    return (Resolve-VendoredLib -LibName 'zdd' -LibRoot $LibRoot)
}

# Check if the zdd cutover lib is already importable in the venv.
function Test-ZddInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from zdd.cutover import CutoverOrchestrator' 2>$null
    return $LASTEXITCODE -eq 0
}

function Remove-PluginBuildArtifacts {
    <# Installing FROM the pristine payload directory ($PluginDir, under the
       plugin install root) leaves setuptools' own build/ + *.egg-info staging
       behind IN that tree. Left in place, a stale build/ can silently shadow
       fresh src/ on a later install if setuptools' incremental-build mtime
       check decides nothing "changed" -- confirmed live on POSIX
       (copilot-extensions#3444): a truncated recipes_cli.py shipped this way
       and crash-looped a production daemon for ~8h.

       Every vendored `[tool.uv.sources]` workspace path dep under
       libs/<name>/ (agent-procutil, zdd, dropin-registry, ...) is its OWN
       independent setuptools build root and accumulates the identical
       residue -- confirmed live: a stale libs/agent-procutil/build/lib
       silently shipped a version of agent_procutil missing a since-added
       function even after a fully clean `uv cache clean` + a forced
       `--reinstall-package`/`--refresh-package` rebuild, because every
       rebuild kept reading the stale build/lib copy instead of the fresh
       src/ underneath it (those flags bust uv's resolution/build cache, not
       a stale artifact sitting directly in the source tree uv builds FROM).

       Shared by BOTH the standalone zdd pre-install (which runs before the
       main package install below) and that main install's own scrub --
       the zdd pre-install builds from the exact same libs/zdd/ tree and is
       equally vulnerable if it runs first without this.

       -ExtraDir (optional): Resolve-Zdd/Resolve-VendoredLib can also
       resolve to a checkout/registry path OUTSIDE $PluginDir entirely (a
       sibling copilot-extensions checkout, not the marketplace-installed
       payload) -- $PluginDir/libs/* scrubbing never reaches that tree, so
       the standalone zdd pre-install passes its own resolved $ZddDir here
       too. #>
    param([string]$PluginDir, [string]$ExtraDir)
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
        (Join-Path $PluginDir 'build'), `
        (Join-Path $PluginDir '*.egg-info'), `
        (Join-Path (Join-Path $PluginDir 'src') '*.egg-info')
    # Directory names under libs/ don't map 1:1 to package names (e.g.
    # agent-zdd -> libs/zdd), so glob every immediate child rather than
    # trying to enumerate them.
    $libsDir = Join-Path $PluginDir 'libs'
    if (Test-Path -LiteralPath $libsDir) {
        Get-ChildItem -LiteralPath $libsDir -Directory -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
                    (Join-Path $_.FullName 'build'), `
                    (Join-Path $_.FullName '*.egg-info'), `
                    (Join-Path (Join-Path $_.FullName 'src') '*.egg-info')
            }
    }
    if ($ExtraDir -and $ExtraDir -ne $PluginDir) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
            (Join-Path $ExtraDir 'build'), `
            (Join-Path $ExtraDir '*.egg-info'), `
            (Join-Path (Join-Path $ExtraDir 'src') '*.egg-info')
    }
}

function Publish-FileAtomically {
    <# Atomically replace (or first-create) a marker file -- ported from
       agent-machines' own scripts/init.ps1 (same helper, same semantics).
       Invoke-Stamp's payload-dir/stamped-version markers are read by the
       self-provisioning binstub OUTSIDE any lock, so a plain WriteAllText
       could be observed mid-write (a torn, partially-written file) by a
       racing reader. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Content,
        [Parameter(Mandatory)]$Encoding
    )
    # File.Replace requires fully-qualified paths -- a relative path throws
    # "The path is not of a legal form."
    $fullPath = [IO.Path]::GetFullPath($Path)
    $tmp = "$fullPath.tmp-$PID"
    [System.IO.File]::WriteAllText($tmp, $Content, $Encoding)
    for ($attempt = 1; $true; $attempt++) {
        try {
            if (Test-Path -LiteralPath $fullPath) {
                $backup = "$fullPath.bak-$PID"
                [System.IO.File]::Replace($tmp, $fullPath, $backup)
                Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
            } else {
                # Two racing first-time installs can both pass the Test-Path
                # check above before either publishes. Omit -Force here: a
                # plain Move-Item throws if a concurrent writer created the
                # destination in that gap, instead of silently -Force
                # deleting+recreating the very no-file window this helper
                # exists to prevent. Caught below and retried, which
                # re-checks Test-Path and takes the safe Replace() branch.
                Move-Item -LiteralPath $tmp -Destination $fullPath
            }
            return
        } catch {
            if ($attempt -ge 20) { throw }
            Start-Sleep -Milliseconds 15
        }
    }
}

function Enter-PluginSnapshotLock {
    <# Serializes a critical section against concurrent invocations (two
       near-simultaneous install/stamp actions) under one named mutex keyed
       by $InstallDir (and, for the build-lifecycle scope, $Version too) --
       mirrors agent-machines' own Enter-StampLock
       (plugins/agent-machines/scripts/init.ps1) in a distinct namespace.

       Two DISTINCT locks share this one implementation (different keys,
       each independently thread-affine reentrant within its own scope):

       - VERSION-SCOPED (pass -Version): New-PluginBuildSnapshot's own
         (short) check/copy/publish sequence, and Install-Runtime's WIDER
         span from snapshot resolution through the end of the actual
         package install, both keyed by $InstallDir + $Version together.
         Without the first, two callers could both observe no valid
         snapshot, each finish their own temp copy, and the LATER
         publisher's rename-aside step would retire the snapshot the
         EARLIER caller already returned and may still be actively
         building from. Without the second, two same-version callers could
         both take the snapshot-reuse fast path and then concurrently
         scrub/rebuild PEP 517 artifacts (build/, *.egg-info) under the
         SAME shared $BuildSrcDir. Scoping this lock BY VERSION (not just
         $InstallDir) is deliberate: a wedged build backend for one
         version -- the exact failure mode this whole mechanism tolerates
         -- must never block an unrelated install of a DIFFERENT,
         newer version from acquiring its own independent lock.

       - GLOBAL, $InstallDir-only (omit -Version): Invoke-Stamp's own
         outer span covering BOTH marker publications. This one genuinely
         needs cross-version visibility -- its whole purpose is comparing
         against whatever stamped-version is CURRENTLY published,
         regardless of which version last held it, to stop a
         delayed/preempted OLDER stamp from overwriting a NEWER one's
         markers. This span is always short (file writes only, no venv/uv
         build work), so cross-version contention here is a non-issue.

       -TimeoutSeconds defaults to 20s (comfortably covers both the short
       snapshot-only and marker-publication scopes); Install-Runtime's own
       wider, version-scoped acquisition passes a much larger value, since
       a real venv+package build this lock can now span takes ~30-120s on
       its own (see Deploy-SelfProvisioningBinstub's own first-use
       provisioning estimate) -- the default would almost certainly time
       out under any genuinely concurrent SAME-version build otherwise. #>
    param(
        [Parameter(Mandatory)][string]$InstallDir,
        [string]$Version,
        [int]$TimeoutSeconds = 20
    )
    $key = if ($Version) { "$($InstallDir.ToLowerInvariant())|$Version" } else { $InstallDir.ToLowerInvariant() }
    $hash = [BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($key)
        )
    ).Replace('-', '').Substring(0, 24)
    $mutexName = if ($env:OS -eq 'Windows_NT') {
        "Local\CopilotExtensions.AgentDispatch.Snapshot.$hash"
    } else {
        "CopilotExtensions.AgentDispatch.Snapshot.$hash"
    }
    $mutex = New-Object Threading.Mutex($false, $mutexName)
    $held = $false
    try {
        $held = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
    } catch [Threading.AbandonedMutexException] {
        $held = $true
    }
    if (-not $held) { $mutex.Dispose(); throw 'Timed out waiting for the agent-dispatch snapshot lock.' }
    return $mutex
}

function New-PluginBuildSnapshot {
    <# Copy $PluginDir into a durable, version-pinned snapshot under
       $InstallDir/snapshots/<ver>/ and return that path -- so every build
       subprocess this installer spawns (uv's PEP 517 build backend included)
       sets ITS OWN cwd inside a folder `copilot plugin update` never touches.
       An update only ever deletes/replaces the live, swappable marketplace
       payload (~/.copilot/installed-plugins/.../agent-dispatch); a snapshot
       under $InstallDir is immutable once written and is removed only once
       nothing references it. Confirmed live -- a stuck `uv pip install` build (its PEP 517 backend
       cwd'd into libs/agent-procutil, directly inside the live payload) and
       the init.ps1 installer process that spawned it both sat there for
       hours, and `copilot plugin update agent-dispatch@copilot-extensions`
       failed outright with os error 32 (ERROR_SHARING_VIOLATION) the entire
       time.

       A no-op (returns $PluginDir unchanged) when $PluginDir is ALREADY
       under $InstallDir -- e.g. this install.ps1 is itself running from a
       previously-made snapshot, as the self-provisioning binstub's
       first-use `provision` dispatch does -- avoiding a redundant
       copy-of-a-copy on the already-safe path.

       Also a no-op for a LOCAL dev checkout (Get-SourceKind returns
       anything other than 'marketplace'): a checkout's pyproject.toml
       declares its `[tool.uv.sources]` workspace path deps relative to the
       monorepo root (e.g. `../../libs/zdd`), which only resolves from the
       checkout's own location -- copying just $PluginDir's own tree into a
       flat snapshot would orphan those relative paths, breaking the
       documented direct-from-worktree install path local testing relies
       on. Only a marketplace payload (whose packaged pyproject.toml
       already references its OWN co-located `libs/`) is both safe to
       snapshot and actually exposed to `copilot plugin update`'s locking
       hazard -- a local checkout is subject to neither.

       -BestEffort (Install-Runtime's own call site): on ANY copy failure
       (disk full, permissions) logs a warning and returns $PluginDir
       unchanged -- an acceptable degraded outcome, since the only
       consequence is building from the live payload again rather than
       aborting the whole install. Without -BestEffort (Invoke-Stamp's call
       site): a copy failure THROWS instead. Invoke-Stamp persists this
       function's return value as the self-provisioning binstub's durable
       `payload-dir`/`stamped-version` marker and reports success
       regardless of what it received back -- a swallowed failure there
       would silently publish a marker pointing at the wrong (transient,
       or merely unchanged) directory while claiming the stamp succeeded,
       instead of failing it outright. A missing source version always
       degrades (no version to snapshot under), regardless of -BestEffort. #>
    param(
        [Parameter(Mandatory)][string]$PluginDir,
        [Parameter(Mandatory)][string]$InstallDir,
        [string]$Version,
        [switch]$BestEffort
    )
    if ((Get-SourceKind -PluginPath $PluginDir) -ne 'marketplace') {
        return $PluginDir
    }
    # Containment root is $InstallDir/snapshots specifically, NOT $InstallDir
    # itself -- $InstallDir also hosts the self-stage area
    # ($InstallDir/.install-stage/<ts>-<pid>/, see the self-stage relocation
    # near the top of this script) that a normal marketplace stamp/install
    # run commonly passes as $PluginDir. That stage is exactly as transient
    # as the marketplace payload this function exists to avoid -- treating
    # it as already-safe would skip snapshot creation entirely and leave
    # Invoke-Stamp publishing a payload-dir marker that a later installer
    # invocation reaps once its owner PID exits.
    $snapshotsRoot = Join-Path $InstallDir 'snapshots'
    $installRoot = Resolve-Path -LiteralPath $snapshotsRoot -ErrorAction SilentlyContinue
    if ($installRoot) {
        # Platform-portable, non-wildcard containment check: this script
        # also runs under pwsh on Linux/macOS, where a hardcoded '\' never
        # matches and a case-sensitive filesystem makes case-insensitive
        # comparison wrong -- compare with the real separator and the
        # platform's own case sensitivity, as a literal prefix (no `-like`
        # globbing, which would mis-match a path containing '[', ']', or '*').
        # $IsWindows is undefined under Windows PowerShell 5.1 (this script
        # is PS5+ compatible, see its own synopsis) and would throw under
        # Set-StrictMode -- use the script's existing PS5-safe OS check.
        $sep = [IO.Path]::DirectorySeparatorChar
        $prefix = $installRoot.Path.TrimEnd('/\') + $sep
        $pluginNorm = $PluginDir.TrimEnd('/\') + $sep
        $cmp = if ($env:OS -eq 'Windows_NT') { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
        if ($pluginNorm.StartsWith($prefix, $cmp)) {
            return $PluginDir
        }
    }
    if (-not $Version) {
        Write-Skip 'No source version resolved -- building from the live payload (snapshot skipped)'
        return $PluginDir
    }
    # Lock-free fast path, BEFORE acquiring any mutex: a published snapshot is
    # immutable (see the rename-aside/immutable-publish comment below), so
    # reading it needs no lock at all -- exactly how the binstub's own
    # first-use read already works. Without this check up front, a `stamp`
    # invocation (this function's only other caller besides Install-Runtime)
    # would queue behind Install-Runtime's OWN acquisition of this identical
    # version-scoped mutex -- which Install-Runtime can hold for its full
    # 30-120+ second package build, not just this function's short
    # check/copy/publish sequence -- and time out on this function's much
    # shorter default lock timeout even though the snapshot it actually
    # needs is already valid and sitting on disk. Checked again, inside the
    # lock, below: a concurrent publisher could still be mid-build right
    # now, so this early check is a (correct, since immutable) optimization,
    # never a substitute for the authoritative check.
    $snapDirFast = Join-Path (Join-Path $InstallDir 'snapshots') $Version
    if (Test-Path (Join-Path $snapDirFast 'pyproject.toml')) {
        Write-Ok "Reusing existing build snapshot: $snapDirFast"
        return $snapDirFast
    }
    try {
        # Initialized before any failure-prone step (mutex acquisition
        # included) so the outer catch below can safely check it under this
        # script's Set-StrictMode without an "uninitialized variable" error
        # when a failure happens before $snapTmp is ever assigned.
        $snapTmp = $null
        # Serialize the whole check/copy/publish sequence below against a
        # concurrent caller racing the same $InstallDir (see
        # Enter-PluginSnapshotLock's own docstring for the exact race this
        # closes). A lock-acquisition timeout flows through this same catch,
        # so -BestEffort still governs whether that degrades gracefully.
        $snapMutex = Enter-PluginSnapshotLock -InstallDir $InstallDir -Version $Version
        try {
            if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
            $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $Version
            # Same-version idempotent fast path (mirrors agent-machines'
            # scripts/init.ps1 stamp mutex): a snapshot for this EXACT version
            # that already looks valid would be rebuilt byte-identical anyway,
            # so skip the whole copy -- also closes the replacement race below
            # for the common case (nothing to publish means nothing to race).
            $snapValid = Test-Path (Join-Path $snapDir 'pyproject.toml')
            if ($snapValid) {
                Write-Ok "Reusing existing build snapshot: $snapDir"
                return $snapDir
            }
            $snapTmp = "$snapDir.tmp-$PID"
            if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
            New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
            # Copy everything needed to `uv pip install .` (src, libs, scripts,
            # pyproject, plugin.json, hooks, README); skip VCS/build/test junk --
            # same exclusion list Invoke-Stamp uses for its own snapshot.
            $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
            Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
            }
            # A published snapshot is immutable: Invoke-Stamp's payload-dir
            # marker, and this function's own return value, can be read by a
            # CONCURRENT first-use binstub invocation outside this mutex (the
            # binstub is a reader, never a publisher, so it never takes this
            # lock). Deleting $snapDir before moving the replacement in would
            # leave NO snapshot at all for the entire remainder of this copy
            # -- a race reading that window sees a missing directory, not
            # merely a stale one. Rename the old copy aside first (a
            # directory rename is metadata-only, so the window where
            # $snapDir doesn't exist shrinks to the Move-Item's own atomic
            # rename) rather than deleting the still-advertised snapshot
            # outright.
            if (Test-Path $snapDir) {
                $snapStale = "$snapDir.stale-$PID"
                # Terminating (no -ErrorAction SilentlyContinue): if this
                # rename fails (e.g. Windows still has $snapDir open), letting
                # execution continue to Move-Item below would find $snapDir
                # STILL present -- PowerShell then treats it as a destination
                # CONTAINER and moves $snapTmp INSIDE it instead of replacing
                # it, silently returning a root that lacks pyproject.toml at
                # the expected path. Let this throw into the outer catch,
                # which cleans up $snapTmp and -- on the -BestEffort path --
                # correctly degrades to the live payload instead.
                Rename-Item -LiteralPath $snapDir -NewName (Split-Path -Leaf $snapStale)
            }
            Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
            Get-ChildItem -LiteralPath (Split-Path -Parent $snapDir) -Directory -Filter "$(Split-Path -Leaf $snapDir).stale-*" -ErrorAction SilentlyContinue |
                ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
            Write-Ok "Building from durable snapshot: $snapDir (never locks the marketplace payload)"
            return $snapDir
        } finally {
            [void]$snapMutex.ReleaseMutex()
            $snapMutex.Dispose()
        }
    } catch {
        # A failure mid-copy/move (disk full, permissions) can leave this
        # attempt's own $snapTmp behind. Every attempt uses a fresh PID
        # suffix, so on the -BestEffort path (which swallows this and lets
        # the caller retry on its next invocation) nothing else would ever
        # remove it -- a repeatedly-failing degrade would accumulate a
        # distinct, never-reaped partial tree on every single attempt.
        if ($snapTmp -and (Test-Path $snapTmp)) {
            Remove-Item -LiteralPath $snapTmp -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (-not $BestEffort) { throw }
        Write-Warn "Could not create build snapshot ($($_.Exception.Message)) -- building from the live payload"
        return $PluginDir
    }
}

function Get-AdoptedProjects {
    <# The adopted-project names from agent-worktrees' adoption registry (empty
       when the registry is absent). Used only to give `agent-worktrees get
       machine` a project so it resolves the machine registry from any CWD. #>
    $reg = Join-Path $HOME '.agent-worktrees/projects.yaml'
    if (-not (Test-Path $reg)) { return @() }
    $names = @()
    $inProjects = $false
    foreach ($line in [System.IO.File]::ReadAllLines($reg)) {
        if ($line -match '^projects:\s*$') { $inProjects = $true; continue }
        if ($line -match '^[^\s#]') { $inProjects = $false }
        if ($inProjects -and $line -match '^  ([A-Za-z0-9._-]+):\s*$') {
            $names += $Matches[1]
        }
    }
    return $names
}

function Deploy-SelfProvisioningBinstub {
    <# Deploy the agent-dispatch CLI binstubs into ~/.local/bin, SELF-PROVISIONING
       (#1393): fast-path the built versioned slot's python; if no slot is built
       yet (a `stamp` deferred the venv), provision on first use by running the
       slot-local snapshot's `scripts/install.ps1 provision`, then dispatch. Opt
       out with AGENT_DISPATCH_NO_SELFPROVISION=1.

       Primary agent-dispatch.ps1 (ExternalScript, resolved ahead of the .cmd in
       the same dir; forwards argv verbatim via @args) + agent-dispatch.cmd
       fallback for non-PowerShell callers. Both launch the signed venv python via
       -m, never the SAC-blocked console-script trampoline .exe. #>
    if (-not (Test-Path $LocalBin)) {
        New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $machine = $env:AGENT_DISPATCH_SUPERVISE_MACHINE
    if (-not $machine) {
        try {
            $aw = Get-Command agent-worktrees -ErrorAction Stop # marketplace-isolation: allow installer-management
            $machine = (& $aw.Source get machine 2>$null | Select-Object -First 1)
            # `get machine` resolves the machine registry THROUGH a project and
            # discovers context from the CWD, so it yields nothing when the
            # installer runs outside an adopted repo/worktree. Falling straight
            # through to the OS name then pins an identity that need not equal the
            # registry key the Picker substitutes for `{machine}` (a key may be
            # decoupled from COMPUTERNAME). Every adopted project resolves the
            # same identity, so retry with an explicit --project first.
            if (-not $machine) {
                foreach ($p in (Get-AdoptedProjects)) {
                    $machine = (& $aw.Source --project $p get machine 2>$null | Select-Object -First 1) # marketplace-isolation: allow installer-management
                    if ($machine) { break }
                }
            }
        } catch {}
    }
    if (-not $machine) { $machine = [Environment]::MachineName.ToLowerInvariant() }
    if ($machine) {
        [System.IO.File]::WriteAllText(
            (Join-Path $InstallDir 'machine'),
            $machine.Trim().ToLowerInvariant(),
            $utf8NoBom
        )
    }

    # Co-deploy the canonical resolvers so every launcher resolves identically
    # (uniform-runtime-resolution, #765).
    $binDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    foreach ($r in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $rSrc = Join-Path $PSScriptRoot $r
        if (Test-Path $rSrc) { Copy-Item $rSrc (Join-Path $binDir $r) -Force }
    }

    $ps1Path = Join-Path $LocalBin 'agent-dispatch.ps1'
    $ps1Content = @'
$env:PYTHONUTF8 = '1'
$_root = Join-Path $env:USERPROFILE '.agent-dispatch'
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
function _Resolve-Py {
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $_resolver) { $env:AGENT_RT_ROOT = $_root; . $_resolver }
    return $AgentRtPy
}
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_dispatch @args; exit $LASTEXITCODE }
if ($env:AGENT_DISPATCH_NO_SELFPROVISION) { [Console]::Error.WriteLine('[agent-dispatch] runtime not provisioned (AGENT_DISPATCH_NO_SELFPROVISION set).'); exit 1 }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) { Join-Path $_snap 'scripts\install.ps1' } else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-dispatch] cannot self-provision: snapshot installer not found. Re-enable the plugin, then retry.'); exit 127 }
[Console]::Error.WriteLine('[agent-dispatch] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-dispatch eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
& $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_dispatch @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-dispatch] provisioning did not yield a runtime. See the log above; retry, or run the snapshot installer manually.')
exit 1
'@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

    $stubPath = Join-Path $LocalBin 'agent-dispatch.cmd'
    # cmd fallback: delegate to the .ps1 binstub so resolution stays uniform with
    # the canonical resolve-runtime.ps1 chain and self-provisioning is shared.
    $stubContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=%USERPROFILE%\.local\bin\agent-dispatch.ps1"
if not exist "%_PS1%" (echo [agent-dispatch] binstub not found: %_PS1%>&2 & exit /b 127)
set "_PSHOST="
for /f "delims=" %%I in ('"%SystemRoot%\System32\where.exe" pwsh 2^>nul') do if not defined _PSHOST set "_PSHOST=%%I"
if not defined _PSHOST set "_PSHOST=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%_PSHOST%" -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*
exit /b %ERRORLEVEL%
'@
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    $boardStubPath = Join-Path $LocalBin 'agent-dispatch-board.cmd'
    $boardStubContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_ROOT=%USERPROFILE%\.agent-dispatch"
set "_VER="
if exist "%_ROOT%\current-version" set /p _VER=<"%_ROOT%\current-version"
set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
if exist "%_PY%" (
  "%_PY%" -m agent_dispatch.board_cli %*
  exit /b %ERRORLEVEL%
)
agent-dispatch inbox %* --board
exit /b %ERRORLEVEL%
'@
    [System.IO.File]::WriteAllText($boardStubPath, $boardStubContent, $utf8NoBom)
    Write-Ok "Binstub: $stubPath (self-provisioning)"
    Write-Ok "Fast board binstub: $boardStubPath"
}

function Install-Runtime {
    if (-not (Test-Path $PkgSrcDir)) {
        Write-Fail "Package source not found at $PkgSrcDir"
        exit 1
    }

    # Build from a durable, version-pinned snapshot -- never from the live,
    # swappable marketplace payload ($PluginDir) -- so a uv/setuptools build
    # subprocess's own cwd can never block `copilot plugin update`. See
    # New-PluginBuildSnapshot's docstring for the confirmed incident.
    # -BestEffort: a failed snapshot here only means building from the live
    # payload again (the prior, only-ever behavior) -- not fatal.
    #
    # The OUTER lock below (same $InstallDir + $SrcVersion key
    # New-PluginBuildSnapshot's own inner acquisition reenters harmlessly)
    # spans snapshot resolution through the end of the actual package
    # install: two same-version callers both taking the snapshot-reuse fast
    # path would otherwise concurrently scrub/rebuild PEP 517 artifacts
    # (build/, *.egg-info) under the SAME shared $BuildSrcDir, each able to
    # delete the other's in-progress build output. Scoped BY VERSION (not
    # just $InstallDir): a wedged build backend for one version must never
    # block an unrelated, newer-version install's own independent lock. A
    # real build can run ~30-120s on its own (see
    # Deploy-SelfProvisioningBinstub's own first-use estimate), so this
    # acquisition passes a much longer timeout than the short snapshot-only
    # default -- `exit` (used throughout the build steps below on failure)
    # still runs this `finally`, confirmed: PowerShell unwinds pending
    # `finally` blocks before an `exit` actually terminates the process.
    $buildMutex = Enter-PluginSnapshotLock -InstallDir $InstallDir -Version $SrcVersion -TimeoutSeconds 180
    try {
    $BuildSrcDir = New-PluginBuildSnapshot -PluginDir $PluginDir -InstallDir $InstallDir -Version $SrcVersion -BestEffort

    $hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

    # Find a Python interpreter (skip Windows Store aliases that aren't real)
    $pythonCmd = $null
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $testOut = & $found.Source --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') { $pythonCmd = $found.Source }
            } catch { }
            $ErrorActionPreference = $prevEAP
            if ($pythonCmd) { break }
        }
    }
    if (-not $pythonCmd) {
        Write-Fail 'Python not found on PATH (need 3.10+)'
        Write-Host '    winget install Python.Python.3.13' -ForegroundColor DarkGray
        exit 1
    }
    Write-Ok "Python: $pythonCmd"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if ($hasWinget) {
            Write-Step 'uv not found -- installing via winget...'
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            $ErrorActionPreference = $prevEAP
            $env:PATH = (Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'Machine') + ';' + (Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'User')
            if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Ok 'uv installed' }
        }
    }

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    # -- venv (SAC-trusted signed base python preferred; then uv; then venv) --
    if (-not (Test-Path $VenvPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $signedBase = $null
        if ($env:OS -eq 'Windows_NT' -and (Get-Command py -ErrorAction SilentlyContinue)) {
            foreach ($v in '3.13', '3.12', '3.11') {
                $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
                if ($LASTEXITCODE -eq 0 -and $cand -and (Test-Path $cand)) {
                    try { if ((Get-AuthenticodeSignature $cand).Status -eq 'Valid') { $signedBase = $cand; break } } catch {}
                }
            }
        }
        if ($signedBase -and -not (Test-Path $VenvPython)) {
            & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        }
        if (-not (Test-Path $VenvPython)) {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                Write-Step 'Creating venv via uv...'
                Invoke-VersionedSlotClean
                & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    Write-Step 'uv venv failed -- falling back to python -m venv'
                    & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
                }
            } else {
                Write-Step 'Creating venv via python -m venv...'
                & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
            }
        }
        $ErrorActionPreference = $prevEAP
        if (-not (Test-Path $VenvPython)) {
            Write-Fail "Venv creation failed -- $VenvPython not found"
            exit 1
        }
        Write-Ok 'Venv created'
    } else {
        Write-Skip 'Venv already exists'
    }

    # -- install package (uv pip install; [mcp] extra with graceful fallback) --
    # The [mcp] extra pulls `mcp` -> `pyjwt[crypto]` -> `cryptography`, which has
    # no prebuilt wheel on some platforms (notably win-arm64) and needs a Rust +
    # MSVC toolchain to build from source. Per the plugin-services vision's
    # `degrade-gracefully` behavior, a build failure of the OPTIONAL MCP server
    # surface must not abort the whole install: fall back to the base package so
    # the coordinator CLI still deploys; only `agent-dispatch mcp` stays dark
    # until the toolchain is present.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    # zdd (zero-downtime graceful-cutover primitives: routing table + orchestrator).
    # Declared as `agent-zdd` in pyproject but NOT on PyPI, so `uv pip install .`
    # cannot resolve it -- install it from the vendored lib FIRST so the package
    # install below finds the requirement already satisfied.
    $ZddDir = Resolve-Zdd -LibRoot $BuildSrcDir
    if ($ZddDir) {
        # Scrub BEFORE this standalone pre-install too, not just the main
        # install below -- this build reads from the exact same libs/zdd/
        # tree (or, via Resolve-VendoredLib's registry/checkout fallback, a
        # sibling checkout entirely OUTSIDE $PluginDir) and is equally
        # vulnerable to stale build/lib residue shadowing a fresh source
        # change if it runs first without this -- pass $ZddDir explicitly
        # so an external resolved path is reached too, not just libs/*.
        Remove-PluginBuildArtifacts -PluginDir $BuildSrcDir -ExtraDir $ZddDir
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $zddOut = & uv pip install --python $VenvPython "$ZddDir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet 2>&1
        } else {
            $zddOut = & $VenvPython -m pip install "$ZddDir" 2>&1
        }
        Remove-PluginBuildArtifacts -PluginDir $BuildSrcDir -ExtraDir $ZddDir
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "zdd install failed (exit $LASTEXITCODE)"
            if ($zddOut) { Write-Host ($zddOut | Out-String) }
            exit 1
        }
        Write-Ok 'zdd (graceful-cutover primitives) installed'
    } elseif (Test-ZddInstalled) {
        Write-Skip 'zdd already installed in venv (marketplace layout)'
    } else {
        Write-Fail 'Cannot locate zdd library. Reinstall the agent-dispatch plugin from the marketplace (copilot plugin install agent-dispatch@copilot-extensions), then rerun this installer.'
        exit 1
    }

    # Every OTHER `[tool.uv.sources]` workspace path dep that is a `uv`-
    # editable canonical reference (vendor-pointer-generalization effort,
    # Phase 1: no local copy in a dev checkout at all) needs the exact same
    # standalone pre-install as zdd above, and for the same reason: when
    # `uv` is unavailable the fallback below uses bare `python -m pip`
    # install, which does NOT honor `[tool.uv.sources]` at all -- without a
    # local copy AND without this pre-install, that non-uv path cannot
    # resolve the dependency and may instead try (and fail) to resolve a
    # same-named index package. `plugin-activation` is installed LAST since
    # it imports `dropin_registry`/`plugin_resolve` at module load time.
    foreach ($lib in @(
        @{ Dir = 'dropin-registry'; Pkg = 'agent-dropin-registry'; Display = 'dropin-registry' },
        @{ Dir = 'plugin-resolve'; Pkg = 'agent-plugin-resolve'; Display = 'plugin-resolve' },
        @{ Dir = 'single-instance-lease'; Pkg = 'agent-single-instance-lease'; Display = 'single-instance-lease' },
        @{ Dir = 'agent-procutil'; Pkg = 'agent-procutil'; Display = 'agent-procutil' },
        @{ Dir = 'plugin-activation'; Pkg = 'agent-plugin-activation'; Display = 'plugin-activation' }
    )) {
        $libDir = Resolve-VendoredLib -LibName $lib.Dir -LibRoot $BuildSrcDir
        if (-not $libDir) {
            Write-Fail "Cannot locate $($lib.Display) library. Reinstall the agent-dispatch plugin from the marketplace (copilot plugin install agent-dispatch@copilot-extensions), then rerun this installer."
            exit 1
        }
        Remove-PluginBuildArtifacts -PluginDir $BuildSrcDir -ExtraDir $libDir
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $libOut = & uv pip install --python $VenvPython "$libDir" --reinstall-package $lib.Pkg --refresh-package $lib.Pkg --quiet 2>&1
        } else {
            $libOut = & $VenvPython -m pip install "$libDir" 2>&1
        }
        Remove-PluginBuildArtifacts -PluginDir $BuildSrcDir -ExtraDir $libDir
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "$($lib.Display) install failed (exit $LASTEXITCODE)"
            if ($libOut) { Write-Host ($libOut | Out-String) }
            exit 1
        }
        Write-Ok "$($lib.Display) installed"
    }

    # -ReinstallPackage/-RefreshPackage (uv only): this installs from a local
    # PATH source (not a registry), and uv's local-path build cache is keyed
    # by source path, not source content. A version string that was ever
    # built before -- at this exact path, or a different one -- can silently
    # serve a stale cached wheel instead of rebuilding from what's actually
    # on disk right now. This is the confirmed root cause behind
    # ThomasMichon/copilot-extensions#2863: a deployed package was missing a
    # function its own import site required, even though every verified copy
    # of that release's actual source (git history and the exact snapshot
    # used for the install alike) defined it correctly. Force a fresh
    # build/install every time so a stale cache entry can never silently ship
    # again -- covering agent-dispatch itself AND its own local
    # `[tool.uv.sources]` workspace path deps (agent-procutil, agent-zdd,
    # agent-dropin-registry, agent-plugin-activation, agent-plugin-resolve,
    # agent-single-instance-lease), which are equally local PATH sources and
    # equally vulnerable. #2863 also flagged a second, same-class ImportError
    # in the self-update fallback (`from agent_procutil import ...`) --
    # agent-procutil is exactly one of these.
    $StaleCacheRefreshPackages = @(
        'agent-dispatch',
        'agent-procutil',
        'agent-zdd',
        'agent-dropin-registry',
        'agent-plugin-activation',
        'agent-plugin-resolve',
        'agent-single-instance-lease'
    )
    $installPkg = {
        param([string]$Spec)
        # Installing FROM $BuildSrcDir (the durable per-version snapshot, NOT
        # the live payload -- see New-PluginBuildSnapshot) leaves setuptools'
        # own build/lib + *.egg-info staging behind IN that tree -- pip's
        # build isolation covers the *environment* the build runs in, not
        # where the legacy build_meta backend writes intermediate files. Left
        # in place, a stale build/lib/ (or a src-layout package's
        # src/*.egg-info, one level deeper than a root-level glob reaches)
        # can silently shadow fresh src/ on a later install if setuptools'
        # incremental-build mtime check decides nothing "changed". Scrub on
        # every attempt (success or not) so the snapshot directory stays the
        # pristine copy it's supposed to be -- mirrors the POSIX installer's
        # cleanup in install.sh. Also reaches every vendored
        # `[tool.uv.sources]` workspace path dep under libs/<name>/, which
        # is its own independent build root and equally vulnerable -- see
        # Remove-PluginBuildArtifacts's own docstring for the confirmed
        # live incident.
        $scrubArtifacts = { Remove-PluginBuildArtifacts -PluginDir $BuildSrcDir }
        # Scrub BEFORE installing too, not just after: residue already
        # sitting in $BuildSrcDir the moment this call starts (an earlier
        # failed attempt, a re-stamped snapshot, a concurrent process) is
        # what shadows THIS build -- an after-only scrub only protects the
        # NEXT install, not this one. Confirmed live (2026-09-23,
        # copilot-extensions#3444): a truncated recipes_cli.py shipped this
        # way on POSIX and crash-looped a production daemon for ~8h; the
        # Windows path had the identical gap.
        & $scrubArtifacts
        try {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                $refreshFlags = @()
                foreach ($pkg in $StaleCacheRefreshPackages) {
                    $refreshFlags += @('--reinstall-package', $pkg, '--refresh-package', $pkg)
                }
                $out = & uv pip install --python $VenvPython @refreshFlags $Spec 2>&1 | Out-String
            } else {
                # The refresh pre-pass's own exit status must gate the real
                # install: if it fails, letting the plain install below run
                # anyway could silently succeed from a cached/existing wheel,
                # defeating the whole stale-wheel guard.
                $preOut = & $VenvPython -m pip install --force-reinstall --no-deps $Spec 2>&1 | Out-String
                if ($LASTEXITCODE -ne 0) {
                    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = $preOut }
                }
                # The pre-pass above can itself recreate build/egg-info
                # residue before the real install's own build starts below --
                # re-scrub between the two calls so that build isn't shadowed
                # by residue the pre-pass just left behind.
                & $scrubArtifacts
                $out = & $VenvPython -m pip install $Spec 2>&1 | Out-String
            }
            [pscustomobject]@{ Code = $LASTEXITCODE; Output = $out }
        } finally {
            & $scrubArtifacts
        }
    }

    $mcpResult = & $installPkg "$($BuildSrcDir)[mcp]"
    if ($mcpResult.Code -eq 0) {
        Write-Ok 'Package installed: agent-dispatch [mcp]'
    } else {
        Write-Warn 'Could not install the [mcp] extra (its native deps may not build on this platform) -- falling back to a base install without the MCP server surface'
        $baseResult = & $installPkg "$BuildSrcDir"
        if ($baseResult.Code -ne 0) {
            Write-Fail 'Failed to install agent-dispatch package into venv'
            Write-Host $baseResult.Output
            $ErrorActionPreference = $prevEAP
            exit 1
        }
        Write-Ok 'Package installed: agent-dispatch (base -- `agent-dispatch mcp` server unavailable on this platform)'
    }
    $ErrorActionPreference = $prevEAP
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    # -- stamp build provenance (version from pyproject -- the single source of
    # truth -- plus git commit/branch) into the deployed package, so the runtime
    # reports its version without importlib.metadata. Best-effort; mirrors
    # agent-worktrees' stamp_build_info. --
    try {
        $pkgDir = & $VenvPython -c "import agent_dispatch, os; print(os.path.dirname(agent_dispatch.__file__))" 2>$null
        if ($pkgDir -and (Test-Path $pkgDir)) {
            $repoRoot = (Resolve-Path (Join-Path $PluginDir '..\..')).Path
            & $VenvPython (Join-Path $PSScriptRoot 'stamp_build_info.py') `
                --package-dir $pkgDir --plugin-dir $PluginDir --git-dir $repoRoot 2>&1 |
                ForEach-Object { Write-Step $_ }
        }
    } catch {
        Write-Warn "Build-info stamp skipped: $($_.Exception.Message)"
    }

    # -- binstub (self-provisioning; #1393) --
    Deploy-SelfProvisioningBinstub

    # Versioned layout (#581): health-gate the freshly-built slot in isolation,
    # then swap the stable `.venv` link onto it. Everything below (manifest, task
    # launchers, binstub) resolves through `.venv` (the link). No-op in legacy
    # mode. Remember the previously-active version as the gc keep target (a
    # not-yet-cycled daemon may still run it).
    $prevVersion = ''
    # `embody` is only ever imported lazily, inside spawn_factories.
    # make_headless_spawn -- so a bare `import agent_dispatch` never touches
    # it, and a slot that is broken ONLY at that import (as in #2863) would
    # otherwise sail through this gate and only fail on the first real spawn
    # attempt, invisibly to `agent-dispatch health`/`daemon-status`.
    # `__main__` is the CLI entry point (argparse wiring, e.g. the
    # `recipes_cli.register_recipes_commands` import) -- a bare `import
    # agent_dispatch` never touches it either, so a slot whose package
    # content got truncated/corrupted in a way that only breaks `__main__`'s
    # own top-level imports (as in #3419: a partially-written source file
    # produced a published payload silently missing a whole function) would
    # otherwise sail through this gate and only surface on the very next
    # `agent-dispatch <anything>` invocation -- including inside the
    # coordinator's own service, which then crash-loops. Import both
    # explicitly here so each class of defect is caught before a slot is
    # ever activated.
    if ($VersionedRuntime) {
        $prevVersion = Get-VersionedCurrent
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import agent_dispatch, agent_dispatch.embody, agent_dispatch.__main__' 2>$null
        $slotOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEAP
        if (-not $slotOk) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            exit 1
        }
        Invoke-VersionedMarkComplete
        if (-not (Invoke-VersionedActivate)) { exit 1 }
        if (Test-ActivationSupersededNow) {
            # This invocation's own build lost the cross-version activation
            # race -- either caught immediately inside Invoke-VersionedActivate,
            # or only discovered HERE via the LIVE re-check (this invocation
            # could have WON its own activation and released the lock, only
            # for a newer version to activate before reaching this exact
            # point -- see Test-ActivationSupersededNow's own docstring).
            # Everything below (manifest, verify, PATH, pivot, gc) resolves
            # or reports through THIS invocation's own $VenvPython/$LinkPython
            # -- publishing it now would overwrite the newer build's already-
            # correct manifest with this older build's stale one. Stop here;
            # the already-active newer slot remains fully installed and
            # untouched. The buildMutex is still released normally via the
            # enclosing `finally` below.
            Write-Skip "Build $SrcVersion superseded by a newer concurrent build -- skipping remaining publication (manifest, verification, PATH, pivot) for this invocation"
            return
        }
    }
    } finally {
        # Held through the health gate, completion marker, AND activation
        # above (not just the package build/install): a second same-version
        # installer that only waited out the earlier, narrower release
        # point could force-reinstall into this exact versions/<version>
        # venv while THIS process is still validating the slot or has only
        # just activated it, exposing a partially modified "current" runtime
        # to any daemon/CLI invocation racing the swap. Release only once
        # this process's own slot is fully published and the stable `.venv`
        # link (if versioned) has been swapped onto it.
        [void]$buildMutex.ReleaseMutex()
        $buildMutex.Dispose()
    }

    Write-Manifest

    # -- verify (through the stable `.venv` link) --
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $importOk = $false
    for ($i = 0; $i -lt 3; $i++) {
        & $LinkPython -c 'import agent_dispatch, agent_dispatch.embody, agent_dispatch.__main__' 2>$null
        if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
        Start-Sleep -Seconds 1
    }
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    # Versioned layout: prune old slots, keeping current + the previous-good (the
    # slot a not-yet-cycled daemon may still run from) + any live-pid-pinned slot.
    if ($VersionedRuntime) { Invoke-VersionedGc -KeepPrev $prevVersion }

    # -- PATH --
    $pathDirs = $env:PATH -split ';'
    if ($pathDirs -contains $LocalBin) {
        Write-Ok "PATH: $LocalBin is on PATH"
    } else {
        $currentUserPath = Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'User'
        if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
            Set-CopilotPersistentEnvironmentVariable -Name 'PATH' -Value "$LocalBin;$currentUserPath" -Target 'User'
            $env:PATH = "$LocalBin;$env:PATH"
            Write-Ok "PATH: Added $LocalBin to User PATH"
        }
    }

    Register-PickerPivot
}

function Write-Manifest {
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PluginDir)
        $git = Get-GitInfo -Path $repoRoot
        $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-dispatch'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-dispatch'
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($LinkDir -replace '\\', '/')
        runtime        = 'python'
    }
    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
}

function Register-PickerPivot {
    $pivotSrc = Join-Path $PluginDir 'pivots\agent-dispatch.json'
    $pivotDir = Join-Path $env:USERPROFILE '.agent-worktrees\pivots'
    if (Test-Path $pivotSrc) {
        try {
            if (-not (Test-Path $pivotDir)) { New-Item -ItemType Directory -Force -Path $pivotDir | Out-Null }
            Copy-Item -Force $pivotSrc (Join-Path $pivotDir 'agent-dispatch.json')
            Write-Ok "Picker pivot registered: $pivotDir\agent-dispatch.json"
        } catch {
            Write-Skip 'Could not register picker pivot (agent-worktrees runtime root not writable)'
        }
    } else {
        Write-Skip "Picker pivot manifest not found at $pivotSrc"
    }
}

# -- Coordinator Scheduled Task (default-on on deploy machines) --------------

function Remove-CoordinatorTask {
    # Returns 'removed' | 'blocked' | 'absent'. Unregister-ScheduledTask may need
    # elevation (Access denied) for a task in the root folder. Used by the
    # -NoService client path and by uninstall.
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        return 'absent'
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        return 'blocked'
    }
    return 'removed'
}

function Get-ServiceMode {
    # Resolve the service auto-start mode for this host:
    #   'interactive' -- an interactive (RDP/console) logon is required before the
    #                    box is usable (verified: dev6/cloud1/box1 must be
    #                    RDP-kicked before SSH works). Such a logon ALWAYS precedes
    #                    dispatch, so the non-elevated HKCU logon auto-start is the
    #                    first-class coordinator service -- no elevated boot task.
    #   'boot'        -- default: a true always-on boot service via an elevated S4U
    #                    Scheduled Task (for a genuinely headless box). Degrades to
    #                    the logon auto-start when registration needs elevation.
    # Precedence: the -Interactive switch > the persisted marker > 'boot'.
    if ($Interactive) { return 'interactive' }
    $marker = Join-Path $InstallDir 'service-mode'
    if (Test-Path $marker) {
        try {
            $m = ((Get-Content $marker -Raw -ErrorAction Stop)).Trim().ToLowerInvariant()
            if ($m -eq 'interactive') { return 'interactive' }
        } catch { }
    }
    return 'boot'
}

function Save-ServiceMode {
    # Persist the service mode so a later `update` (which may not re-pass
    # -Interactive) keeps installing the logon auto-start instead of reverting to
    # the elevated boot task. Kept across a non-purge uninstall (it is config).
    param([Parameter(Mandatory)][ValidateSet('interactive', 'boot')][string]$Mode)
    $marker = Join-Path $InstallDir 'service-mode'
    try { [System.IO.File]::WriteAllText($marker, $Mode, $utf8NoBom) } catch { }
}

function Set-ServiceEnvLoopback {
    # Pin the coordinator to loopback (127.0.0.1) in service.env for interactive
    # mode. Interactive mode targets the Windows-interactive user and ignores the
    # WSL guest, so the NAT vEthernet(WSL) bind -- which needs an elevation-gated
    # firewall rule to be reachable -- is unwanted. Loopback is reachable from the
    # host session with NO elevation on both mirrored and NAT boxes (verified: a
    # NAT box's coordinator was unreachable on its vEthernet IP without the
    # firewall rule, but answered immediately on 127.0.0.1). Idempotent: drops any
    # existing AGENT_DISPATCH_HOST line (active or commented) and appends one
    # ACTIVE pin. The loopback-strip migration above is skipped in interactive
    # mode so this pin survives re-runs.
    $envFile = Join-Path $InstallDir 'service.env'
    $pin = 'AGENT_DISPATCH_HOST=127.0.0.1'
    $kept = @()
    if (Test-Path $envFile) {
        foreach ($l in @(Get-Content $envFile)) {
            if ($l -notmatch '^\s*#?\s*AGENT_DISPATCH_HOST\s*=') { $kept += $l }
        }
    }
    $kept += $pin
    [System.IO.File]::WriteAllText($envFile, (($kept -join "`r`n") + "`r`n"), $utf8NoBom)
    Write-Ok 'Coordinator pinned to loopback 127.0.0.1 (interactive mode: no firewall/elevation; WSL ignored)'
}

function Test-CoordinatorHealthy {
    # True when a coordinator already answers on its rendezvous endpoint. Used to
    # keep the non-elevated fallback idempotent (never start a second instance).
    try {
        $null = & $VenvPython -m agent_dispatch health 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Stop-DispatchProcess {
    # Terminate any RUNNING agent_dispatch process for the given subcommand
    # ('serve' = coordinator, 'supervise' = embody supervisor), regardless of how
    # it was launched. The launcher runs under `conhost.exe --headless`, which
    # DETACHES the powershell+python from the Scheduled Task's tracked process
    # tree, so `Stop-ScheduledTask` does NOT kill the actual `python -m
    # agent_dispatch <subcommand>` process -- a stop/update otherwise leaves the
    # OLD build running and still holding the rendezvous endpoint (#3602). Resolve
    # the live coordinator PID from the rendezvous file (serve only) AND match the
    # `-m agent_dispatch <subcommand>` command line, kill each, then (serve) clear
    # the stale endpoint file so a client doesn't chase a dead endpoint and the
    # fresh coordinator writes a clean one. Returns the count terminated.
    param([Parameter(Mandatory)][ValidateSet('serve', 'supervise')][string]$Subcommand)
    if ($env:OS -ne 'Windows_NT') { return 0 }

    $pidsToKill = New-Object System.Collections.Generic.List[int]
    $endpointFile = Join-Path (Join-Path $InstallDir 'run') 'endpoint.json'

    # 1) The rendezvous-advertised PID (the coordinator that owns the endpoint).
    if ($Subcommand -eq 'serve' -and (Test-Path $endpointFile)) {
        try {
            $ep = Get-Content $endpointFile -Raw | ConvertFrom-Json
            if (($ep.PSObject.Properties.Name -contains 'pid') -and $ep.pid) {
                [void]$pidsToKill.Add([int]$ep.pid)
            }
        } catch { }
    }
    # 2) Any matching `-m agent_dispatch <subcommand>` process (the detached child
    #    + orphans not reflected in the rendezvous file). '\bserve\b' never matches
    #    'supervise' (and vice-versa), so the two never cross-terminate.
    try {
        $needle = '\b' + [regex]::Escape($Subcommand) + '\b'
        Get-CimInstance Win32_Process -Filter "Name LIKE 'python%.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                ($_.CommandLine -match 'agent_dispatch') -and
                ($_.CommandLine -match $needle)
            } |
            ForEach-Object { [void]$pidsToKill.Add([int]$_.ProcessId) }
    } catch { }

    $killed = 0
    foreach ($procId in ($pidsToKill | Select-Object -Unique)) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            $killed++
        } catch { }
    }
    if ($Subcommand -eq 'serve' -and (Test-Path $endpointFile)) {
        Remove-Item $endpointFile -Force -ErrorAction SilentlyContinue
    }
    return $killed
}

function Retire-SupervisorProcesses {
    <# Retire every Windows supervisor service generation: the conhost/PowerShell
       wrapper, the singleton master or direct lane, each registrar child
       (schedule/emitter/evaluator/lane), and descendants.  Scheduled Tasks and
       HKCU Run do not own these detached trees, so killing only
       `agent_dispatch supervise` leaves autonomous children from old runtime
       slots alive after an update.  The freshly-installed runtime provides the
       pure, unit-tested process classifier; this installer owns when to invoke
       it. Returns the count retired. #>
    if ($env:OS -ne 'Windows_NT') { return 0 }
    $py = $null
    try {
        $marker = Join-Path $InstallDir 'current-version'
        if (Test-Path -LiteralPath $marker) {
            $current = ([IO.File]::ReadAllText($marker)).Trim()
            if ($current) {
                $candidate = Join-Path $InstallDir "versions\$current\Scripts\python.exe"
                if (Test-Path -LiteralPath $candidate) { $py = $candidate }
            }
        }
    } catch { }
    if (-not $py -and (Test-Path $VenvPython)) { $py = $VenvPython }
    if (-not $py -and (Test-Path $LinkPython)) { $py = $LinkPython }
    if (-not $py) {
        return (Retire-SupervisorProcessesFallback)
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $raw = & $py -m agent_dispatch _retire-supervisors --install-dir $InstallDir 2>&1 | Out-String
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    try {
        $result = $raw | ConvertFrom-Json
        $retired = @($result.retired).Count
        foreach ($message in @($result.errors)) {
            if ($message) { Write-Warn "Supervisor generation retirement: $message" }
        }
        $enumerationFailed = @($result.errors | Where-Object {
            [string]$_ -like 'process enumeration failed:*'
        }).Count -gt 0
        # A partial termination failure is already classified precisely by the
        # Python helper. Do not run a second, broader classifier over a changed
        # process inventory; report the errors and keep its retired count.
        if ($rc -eq 0 -or -not $enumerationFailed) { return $retired }
    } catch { }
    Write-Warn 'Full supervisor generation inventory failed; using native CIM supervisor-tree retirement'
    return (Retire-SupervisorProcessesFallback)
}

function Retire-SupervisorProcessesFallback {
    <# Native fallback when no installed runtime can run the Python classifier.
       Select only supervise-service wrappers and `agent_dispatch supervise`
       roots, registrar children carrying a supervisor-owned materialized spec,
       then their descendants. Standalone producers are preserved. #>
    if ($env:OS -ne 'Windows_NT') { return 0 }
    try {
        $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $launcher = [IO.Path]::GetFullPath((Join-Path $InstallDir 'supervise-service.ps1'))
        $runtimeRun = if ($env:AGENT_DISPATCH_RUN_DIR) {
            $env:AGENT_DISPATCH_RUN_DIR
        } else {
            Join-Path $InstallDir 'run'
        }
        $supervisorRun = (
            [IO.Path]::GetFullPath((Join-Path $runtimeRun 'supervisor')).TrimEnd('\') + '\'
        )
        $selected = [System.Collections.Generic.HashSet[int]]::new()
        foreach ($row in $rows) {
            $cmd = [string]$row.CommandLine
            if (-not $cmd) { continue }
            $isWrapper = $cmd.Replace('"', '').IndexOf(
                $launcher,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0
            $exe = $null
            $underRoot = $false
            if ($row.ExecutablePath) {
                try {
                    $exe = [IO.Path]::GetFullPath([string]$row.ExecutablePath)
                    $underRoot = $exe.StartsWith(
                        ([IO.Path]::GetFullPath($InstallDir).TrimEnd('\') + '\'),
                        [StringComparison]::OrdinalIgnoreCase
                    )
                } catch { }
            }
            $isSupervisor = $underRoot -and (
                $cmd -match '(?i)(agent_dispatch|agent-dispatch)(?:\.exe)?["'']?\s+supervise(?=\s*(?:$|serve(?:\s|$)|-))'
            )
            $isRegistrarChild = $underRoot -and (
                $cmd -match '(?i)(agent_dispatch|agent-dispatch)(?:\.exe)?["'']?\s+(emitter\s+serve(?:\s|$)|schedule\s+serve(?:\s|$)|webhook(?:\s|$))'
            ) -and (
                $cmd.Replace('"', '').IndexOf(
                    $supervisorRun,
                    [StringComparison]::OrdinalIgnoreCase
                ) -ge 0
            )
            if ($isWrapper -or $isSupervisor -or $isRegistrarChild) {
                [void]$selected.Add([int]$row.ProcessId)
            }
        }
        $changed = $true
        while ($changed) {
            $changed = $false
            foreach ($row in $rows) {
                if (
                    $selected.Contains([int]$row.ParentProcessId) -and
                    $selected.Add([int]$row.ProcessId)
                ) {
                    $changed = $true
                }
            }
        }
        $depth = @{}
        foreach ($pid in $selected) {
            $value = 0
            $cursor = $rows | Where-Object ProcessId -eq $pid | Select-Object -First 1
            $seen = [System.Collections.Generic.HashSet[int]]::new()
            while ($cursor -and $selected.Contains([int]$cursor.ParentProcessId)) {
                if (-not $seen.Add([int]$cursor.ParentProcessId)) { break }
                $value++
                $cursor = $rows | Where-Object ProcessId -eq $cursor.ParentProcessId | Select-Object -First 1
            }
            $depth[$pid] = $value
        }
        $killed = 0
        # Stop roots first so a still-live master cannot replace a child while
        # the snapshot is being retired.
        foreach ($pid in @($selected | Sort-Object { $depth[$_] })) {
            if ($pid -eq $PID) { continue }
            try {
                Stop-Process -Id $pid -Force -ErrorAction Stop
                $killed++
            } catch { }
        }
        return $killed
    } catch {
        Write-Warn "Native supervisor generation retirement failed: $_"
        return (Stop-DispatchProcess -Subcommand supervise)
    }
}

function Confirm-CoordinatorRunning {
    # After a start/update, verify a coordinator actually answers AND runs the
    # just-installed build -- catching the "reports success but the old detached
    # build is still serving the rendezvous endpoint" drift (#3602). Compares the
    # running coordinator's health version against the freshly-installed package
    # __version__ (identical string source, so no normalization mismatch). Polls
    # briefly to let a fresh coordinator bind + advertise.
    if (-not (Test-Path $VenvPython)) { return }
    $installed = $null
    try {
        $installed = (& $VenvPython -c 'import agent_dispatch; print(agent_dispatch.__version__)' 2>$null).Trim()
    } catch { $installed = $null }

    $running = $null
    for ($i = 0; $i -lt 12; $i++) {
        try {
            $out = & $VenvPython -m agent_dispatch health 2>$null
            if (($LASTEXITCODE -eq 0) -and $out) {
                $h = ($out | Out-String) | ConvertFrom-Json
                if ($h.PSObject.Properties.Name -contains 'version') { $running = $h.version }
                if ($running) { break }
            }
        } catch { }
        Start-Sleep -Seconds 1
    }

    if (-not $running) {
        Write-Warn 'Coordinator did not answer health after start -- check serve-service.log'
        return
    }
    if ($installed -and ($running -ne $installed)) {
        Write-Warn "Coordinator is serving $running but $installed is installed -- a stale build may still hold the endpoint. Re-run: install.ps1 -Action stop; then -Action start"
    } else {
        Write-Ok "Coordinator running (version $running)"
    }
}

function Remove-CoordinatorAutostart {
    # Remove the non-elevated logon auto-start (HKCU Run) if present. Returns
    # $true when one was removed. Idempotent.
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    try {
        if (Get-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue
            return $true
        }
    } catch { }
    return $false
}

function Start-CoordinatorNonElevatedFallback {
    # Non-elevated coordinator service: start it NOW as a detached background
    # process (idempotent via a health probe) and register an HKCU Run key so it
    # (re)starts at each interactive (RDP/console) logon.
    #
    # This is used two ways:
    #   (a) FIRST-CLASS on an -Interactive host (`-Primary`): the box requires an
    #       RDP/console logon before SSH works, so a logon always precedes
    #       dispatch and the Run key always fires -- no boot task, no elevation.
    #   (b) FALLBACK in boot mode when the elevated Scheduled Task can't be
    #       registered (Access denied non-elevated; dotfiles #525).
    # `-Primary` selects the interactive-mode messaging (intended service, not a
    # degraded fallback) and suppresses the "needs elevation" warning.
    param(
        [Parameter(Mandatory)][string]$Launcher,
        [switch]$Primary
    )
    if (-not $Primary) {
        Write-Warn "Coordinator Scheduled Task not registered (registration needs elevation on this host)."
    }
    $taskArgs = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""

    if (Test-CoordinatorHealthy) {
        Write-Ok 'Coordinator already running (health ok) -- not starting a second instance'
    } else {
        try {
            Start-Process -FilePath 'conhost.exe' -ArgumentList $taskArgs -WindowStyle Hidden | Out-Null
            Start-Sleep -Seconds 2
            if (Test-CoordinatorHealthy) {
                Write-Ok 'Coordinator started as a detached background process (health ok)'
            } else {
                Write-Ok 'Coordinator process launched (detached) -- give it a moment; see serve-service.log for bind status'
            }
        } catch {
            Write-Warn "Could not start coordinator process: $($_.Exception.Message)"
        }
    }

    # Durable, non-elevated auto-start at each logon (HKCU Run).
    try {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        New-ItemProperty -Path $runKey -Name $TaskName -Value "conhost.exe $taskArgs" `
            -PropertyType String -Force | Out-Null
        Write-Ok "Logon auto-start registered (HKCU Run '$TaskName') -- durable without elevation"
    } catch {
        Write-Warn "Could not register logon auto-start (HKCU Run): $($_.Exception.Message)"
    }

    if ($Primary) {
        Write-Ok 'Coordinator installed as an interactive logon service (no elevation; starts at each RDP/console logon)'
    } else {
        Write-Step 'This box looks interactive-required. Re-run with -Interactive to make the logon auto-start the intended mode (silences the warning above).'
        Write-Step "Or for a true boot service, run ONCE elevated:  powershell -File `"$PSCommandPath`" -Action install"
    }
}

function Install-CoordinatorTask {
    # Windows OWNS the coordinator (Phase 2, issue #2818): the always-on Windows
    # host runs the full coordinator and the WSL guest is a client. This reverses
    # the #2777 model (WSL-owned, Windows client). Explicit -NoService still forces
    # a client-only host (e.g. a box that intentionally has no coordinator).
    #
    # -NoStart: register/refresh the boot task but do NOT start the coordinator NOW
    # -- used after a graceful cutover already brought the new coordinator up, so a
    # Start-ScheduledTask here would race a SECOND coordinator (Thread B).
    param([switch]$NoStart)
    if ($NoService) {
        # Remove a coordinator task left from a prior full install so a host asked
        # to be client-only stops running one. Removal may be blocked without
        # elevation -- log and continue.
        switch (Remove-CoordinatorTask) {
            'removed' { Write-Ok   'Removed local coordinator Scheduled Task (-NoService: client-only host)' }
            'blocked' { Write-Skip 'Coordinator task present but not removable without elevation (-NoService) -- run elevated to remove it' }
            default   { Write-Skip 'Coordinator service skipped (-NoService: client-only host)' }
        }
        return
    }
    if ($env:OS -ne 'Windows_NT') { return }
    $haveSchedMod = [bool](Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)
    if (-not $haveSchedMod -and (Get-ServiceMode) -ne 'interactive') {
        Write-Skip 'ScheduledTasks module unavailable -- skipping service (run: agent-dispatch serve)'
        return
    }
    $envFile = Join-Path $InstallDir 'service.env'
    if (-not (Test-Path $envFile)) {
        $envDefault = @"
# agent-dispatch coordinator service environment.
# Edit, then: Start-ScheduledTask -TaskName agent-dispatch
# AGENT_DISPATCH_HOST is resolved dynamically at startup by serve-service.ps1
# (mirrored -> 127.0.0.1; NAT -> the vEthernet(WSL) IP). Uncomment only to pin it.
# AGENT_DISPATCH_HOST=127.0.0.1
# AGENT_DISPATCH_PORT=$DefaultPort  # unset = OS-assigned dynamic port (Stage C), advertised via the rendezvous file; uncomment to pin
# AGENT_DISPATCH_DB=%USERPROFILE%\.agent-dispatch\tasks.db   # default; uncomment to override
# AGENT_DISPATCH_TOKEN=                                       # set to require bearer auth
# AGENT_DISPATCH_CONTROL_TOKEN=                               # required to manage producer scopes (and to register evaluators)
# AGENT_DISPATCH_CONTROL_TOKEN_COMMAND=                       # or fetch it on demand (e.g. a vault CLI) instead of a raw value above
# refuse task creation against an unregistered repo lane; register every real lane first with 'agent-dispatch registrar add-pointer'
# AGENT_DISPATCH_ENFORCE_REGISTERED_REPOS=1
"@
        [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
        Write-Ok "Service env: $envFile (defaults; edit to pin the bind host / add a token)"
    } else {
        # Migrate stale old-default pins so dynamic resolution/binding takes over,
        # leaving any operator-chosen value untouched:
        #  - AGENT_DISPATCH_HOST=127.0.0.1 (#2888): early (dev39) installs pinned an
        #    ACTIVE loopback host. On a NAT box that pin makes the coordinator bind
        #    loopback -- unreachable from WSL -- because both the launcher and `serve`
        #    skip bind-host resolution whenever AGENT_DISPATCH_HOST is set. The host
        #    is now resolved at startup (mirrored -> 127.0.0.1; NAT -> vEthernet(WSL) IP).
        #  - AGENT_DISPATCH_PORT=9847 (durable-service-transport Stage C): the fixed
        #    default port pin defeats the dynamic bind. The coordinator now binds an
        #    OS-assigned port and advertises it via the rendezvous file, so drop the
        #    old-default pin (discovery-capable clients follow the dynamic port).
        $envLines = Get-Content $envFile
        $migrations = @()
        $newEnvLines = foreach ($envLine in $envLines) {
            if ($envLine -match '^\s*AGENT_DISPATCH_HOST\s*=\s*127\.0\.0\.1\s*$' -and (Get-ServiceMode) -ne 'interactive') {
                $migrations += 'AGENT_DISPATCH_HOST=127.0.0.1 (#2888)'
                '# AGENT_DISPATCH_HOST=127.0.0.1  # migrated (#2888): now resolved dynamically at startup (mirrored -> 127.0.0.1; NAT -> vEthernet(WSL) IP)'
            } elseif ($envLine -match '^\s*AGENT_DISPATCH_PORT\s*=\s*9847\s*$') {
                $migrations += 'AGENT_DISPATCH_PORT=9847 (Stage C)'
                '# AGENT_DISPATCH_PORT=9847  # migrated (Stage C): unset = OS-assigned dynamic port advertised via the rendezvous file; uncomment to pin'
            } else {
                $envLine
            }
        }
        if ($migrations.Count -gt 0) {
            [System.IO.File]::WriteAllText($envFile, (($newEnvLines -join "`r`n") + "`r`n"), $utf8NoBom)
            Write-Ok ("Service env: migrated stale pin(s) -> dynamic: " + ($migrations -join ', '))
        } else {
            Write-Skip "Service env already exists: $envFile"
        }
    }

    $launcher = Join-Path $InstallDir 'serve-service.ps1'
    $launcherBody = @"
# agent-dispatch coordinator launcher (generated by install.ps1). Do not edit;
# edit service.env instead. Loads service.env, then runs `serve`. `serve`
# resolves the bind host per WSL networking mode (mirrored -> 127.0.0.1; NAT ->
# the dynamic vEthernet(WSL) IP, re-resolved on each start) with a bounded retry
# that rides out the logon-before-WSL race on NAT (#2889). All output is teed to
# serve-service.log so a NAT bind failure / retry is diagnosable -- the Scheduled
# Task runs headless (conhost --headless), so console output is otherwise lost.
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
`$env:AGENT_DISPATCH_INSTALL_DIR = '$($InstallDir -replace "'","''")'
Set-Location -LiteralPath `$PSScriptRoot
`$envFile = Join-Path `$PSScriptRoot 'service.env'
if (Test-Path `$envFile) {
    foreach (`$line in Get-Content `$envFile) {
        `$t = `$line.Trim()
        if (`$t -eq '' -or `$t.StartsWith('#')) { continue }
        `$kv = `$t -split '=', 2
        if (`$kv.Count -eq 2) {
            [Environment]::SetEnvironmentVariable(`$kv[0].Trim(), [Environment]::ExpandEnvironmentVariables(`$kv[1].Trim()), 'Process')
        }
    }
}
# Resolve the .venv junction's target and launch the slot python DIRECTLY -- never
# *traverse* the junction (a RedirectionGuard task context is blocked from that,
# though it may still *read* the target) -- dotfiles #637. Plain-dir keeps `$_py.
# Resolved up front so the version (`$_slot leaf) is available for the log fallback.
`$_venv = '$($LinkDir -replace "'","''")'
`$_py = '$($LinkPython -replace "'","''")'
`$_slot = ''
`$_root = Split-Path `$_venv
if ((Split-Path -Leaf `$_root) -eq 'versions') { `$_root = Split-Path `$_root }
`$_ver = ''
try { `$_ver = ([IO.File]::ReadAllText((Join-Path `$_root 'current-version'))).Trim() } catch {}
`$_slot = if (`$_ver) { Join-Path `$_root ('versions\' + `$_ver) } else { '' }
`$_py = if (`$_slot) { Join-Path `$_slot 'Scripts\python.exe' } else { '' }
if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) {
    # #742: marker missing/stale -> prefer last-known-good (the last version
    # activate() published) over a raw newest-slot guess, which could bind a
    # still-installing/never-activated slot mid-swap.
    `$_lkg = ''
    try { `$_lkg = ([IO.File]::ReadAllText((Join-Path `$_root 'last-known-good'))).Trim() } catch {}
    if (`$_lkg) { `$_slot = Join-Path `$_root ('versions\' + `$_lkg); `$_py = Join-Path `$_slot 'Scripts\python.exe' }
    if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) { `$_py = ''; `$_slot = '' }
}
if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) { `$_py = Get-ChildItem (Join-Path `$_root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Join-Path `$_.FullName 'Scripts\python.exe' } | Where-Object { Test-Path -LiteralPath `$_ } | Select-Object -Last 1; `$_slot = if (`$_py) { Split-Path (Split-Path `$_py) } else { '' } }
# A busy/locked log must NEVER block the coordinator launch. Prefer the canonical
# serve-service.log; if it cannot be opened for append (a stale or concurrent
# process still holds the handle), fall back to a VERSION- and pid-aware file so
# startup always has a writable log. (Previously a locked log threw under
# -ErrorActionPreference Stop and killed the launch before serve ever ran.)
function Resolve-WritableLog([string]`$primary, [string]`$slot) {
    `$ver = if (`$slot) { Split-Path -Leaf `$slot } else { 'unknown' }
    `$alt = (`$primary -replace '\.log`$', "-`$ver-`$PID.log")
    foreach (`$cand in @(`$primary, `$alt)) {
        try { `$fs = [System.IO.File]::Open(`$cand, 'Append', 'Write', 'ReadWrite'); `$fs.Close(); return `$cand } catch { }
    }
    return `$alt
}
`$logFile = Resolve-WritableLog (Join-Path `$PSScriptRoot 'serve-service.log') `$_slot
try {
    if ((Test-Path `$logFile) -and ((Get-Item `$logFile).Length -gt 1MB)) {
        Move-Item -Force `$logFile "`$logFile.1"
    }
} catch { }
`$pinned = if (`$env:AGENT_DISPATCH_HOST) { `$env:AGENT_DISPATCH_HOST } else { 'auto (resolved by serve)' }
`$portShown = if (`$env:AGENT_DISPATCH_PORT) { `$env:AGENT_DISPATCH_PORT } else { 'default' }
# Banner write is best-effort -- a logging hiccup must not be fatal (wrapped so a
# late lock on the resolved path still can't kill startup).
try {
    "[`$(Get-Date -Format o)] agent-dispatch coordinator launch (host=`$pinned port=`$portShown log=`$logFile)" |
        Out-File -FilePath `$logFile -Append -Encoding utf8
} catch { }
# Tee every stream (stdout/stderr/warning/info) to the log while still writing
# through, so the retry lines from serve's bind-host resolution are captured.
# serve logs via uvicorn to STDERR; under `$ErrorActionPreference = 'Stop'`
# PowerShell wraps a native command's stderr as a terminating NativeCommandError
# and would kill the long-lived coordinator on its very first log line (observed
# on Anomalous-Potato: task launched, banner written, no listener). Drop to
# 'Continue' for the serve invocation so stderr is captured, never fatal.
`$ErrorActionPreference = 'Continue'
try {
    & `$_py -m agent_dispatch serve 2>&1 | Out-File -FilePath `$logFile -Append -Encoding utf8
} catch {
    # Teeing to the log failed (e.g. it got locked after resolution) -- keep the
    # coordinator alive without the tee rather than let logging kill the service.
    & `$_py -m agent_dispatch serve *> `$null
}
"@
    [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)

    # -- Interactive-required host: prefer an Interactive-logon Scheduled Task --
    # Same desktop access as the HKCU Run auto-start it replaces (an interactive
    # logon always precedes dispatch on this class of host), PLUS Task
    # Scheduler's own RestartCount/RestartInterval crash-restart policy, which
    # HKCU Run cannot provide (copilot-extensions#2172). Degrade to the
    # non-elevated logon auto-start only when elevation is unavailable (first
    # install, non-elevated) -- see the interactive-service-mode design.
    if ((Get-ServiceMode) -eq 'interactive') {
        if ($Interactive) { Save-ServiceMode 'interactive' }
        Set-ServiceEnvLoopback
        if ($haveSchedMod -and (Install-InteractiveScheduledTask -Name $TaskName -Launcher $launcher `
                -Description 'agent-dispatch -- portable agent task-queue coordinator (interactive)' -NoStart:$NoStart)) {
            if (Remove-CoordinatorAutostart) {
                Write-Step "Removed the non-elevated logon fallback -- the Scheduled Task supersedes it"
            }
            return
        }
        if ($haveSchedMod) {
            switch (Remove-CoordinatorTask) {
                'removed' { Write-Step 'Removed prior boot Scheduled Task (interactive mode: logon auto-start owns startup)' }
                'blocked' {
                    # The prior boot task was registered by an ELEVATED install, so its
                    # task-file ACL denies a non-elevated Unregister. Left in place it
                    # keeps an -AtLogOn trigger whose action is a bare
                    # `powershell.exe -WindowStyle Hidden` -- which DefTerm/Windows
                    # Terminal ignore, so it FLASHES a console at every logon (the
                    # headless HKCU Run auto-start below is the real service). A routine
                    # non-elevated `update` can't remove it, so surface an actionable
                    # remediation instead of degrading silently. See copilot-extensions#920.
                    Write-Warn "A prior ELEVATED boot Scheduled Task '$TaskName' remains and cannot be removed without elevation -- it flashes a console window at each logon. The headless logon auto-start below supersedes it; to remove the stale task run this ONCE in an elevated PowerShell: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
                }
                default   { }
            }
        }
        Start-CoordinatorNonElevatedFallback -Launcher $launcher -Primary
        return
    }

    # Use conhost --headless to prevent Windows Terminal from capturing the
    # task's powershell as a visible window/tab when Terminal is the default
    # terminal app. -WindowStyle Hidden alone is ignored by Windows Terminal, so
    # a bare `powershell -WindowStyle Hidden` task surfaces a real console window
    # -- and because the launcher runs the long-lived `-m agent_dispatch serve`
    # in-process, that window persists for the life of the coordinator.
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`"" `
        -WorkingDirectory $InstallDir
    # Two triggers: -AtStartup makes the coordinator a true always-on service that
    # comes up at boot with NO interactive login (essential for a headless box
    # accessed only over SSH); -AtLogOn additionally (re)starts it
    # when the operator logs in (covers a manually-stopped task on a
    # console-driven box). The dev58 bounded bind-host retry
    # (_resolve_bind_host_resilient) rides out the boot-before-WSL race on NAT,
    # where the vEthernet(WSL) IP isn't up yet the instant -AtStartup fires.
    $trigger = @(
        (New-ScheduledTaskTrigger -AtStartup),
        (New-ScheduledTaskTrigger -AtLogOn)
    )
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # LogonType S4U ("run whether the user is logged on or not", no stored
    # password): the coordinator must run headless. The prior Interactive logon
    # type only ran while the user had an interactive console session, so on a
    # headless SSH-only box the task registered but never fired (observed on
    # a headless SSH-only host: State=Ready, LastRunTime=never). S4U runs it in a non-interactive
    # session at boot; validated binding the vEthernet(WSL) IP on a NAT-mode WSL host and
    # loopback on mirrored hosts. Set-ScheduledTask/Register with S4U succeeds
    # non-elevated (unlike a password-backed Password logon). NOTE: the supervisor
    # task below deliberately stays Interactive -- it spawns embody CLI sessions
    # that need an interactive session, which S4U's non-interactive station lacks.
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType S4U -RunLevel Limited

    # Idempotent no-op when nothing changed (mirrors agent-bridge). The coordinator
    # boot task points at a STABLE, self-provisioning launcher ($launcher) that
    # resolves the live version at boot, so its action/trigger/principal are
    # byte-identical across deploys and never need a rewrite. Overwriting an
    # already-S4U task via `Register -Force` still requires elevation -- which a
    # routine non-elevated `update` lacks -- and the failure then starts a *second*,
    # logon-scoped coordinator via the fallback, racing the surviving boot task. So
    # if an existing task already matches the desired action AND is already S4U,
    # leave it untouched (and clear any stale logon fallback so nothing races it).
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask -and ($existingTask.Principal.LogonType -in @('S4U', 'Password'))) {
        $curAct = @($existingTask.Actions)[0]
        $sameAction = $curAct -and
            ($curAct.Execute -eq $action.Execute) -and
            ($curAct.Arguments -eq $action.Arguments) -and
            (("" + $curAct.WorkingDirectory) -eq ("" + $action.WorkingDirectory))
        if ($sameAction) {
            Write-Ok "Coordinator boot task already configured (Scheduled Task '$TaskName') -- unchanged, left as-is"
            if (Remove-CoordinatorAutostart) {
                Write-Step "Removed the non-elevated logon fallback -- the Scheduled Task supersedes it"
            }
            return
        }
    }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Register-ScheduledTask raises a TERMINATING "Access is denied" on a
    # non-elevated host, which would abort the whole installer with a non-zero
    # exit even though the client (venv + binstub + manifest) is already fully
    # deployed above. Per the plugin-services vision's `degrade-gracefully`
    # behavior, a client-only host (e.g. a field terminal that is not a
    # coordinator) must still complete: trap the failure into the existing
    # non-fatal $regOk path instead of terminating.
    $regOk = $false
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description 'agent-dispatch -- portable agent task-queue coordinator' | Out-Null
        $regOk = $?
    } catch {
        $regOk = $false
    }
    if ($regOk) { if (-not $NoStart) { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } }
    $ErrorActionPreference = $prevEAP

    if ($regOk) {
        if ($NoStart) {
            Write-Ok "Coordinator boot task registered (Scheduled Task '$TaskName'); not started -- the graceful cutover already brought the new coordinator up"
        } else {
            Write-Ok "Coordinator service installed + started (Scheduled Task '$TaskName')"
        }
        # The Scheduled Task now owns startup -- drop any non-elevated logon
        # fallback from a prior unprivileged install so two coordinators don't race.
        if (Remove-CoordinatorAutostart) {
            Write-Step "Removed the non-elevated logon fallback -- the Scheduled Task supersedes it"
        }
    } else {
        Start-CoordinatorNonElevatedFallback -Launcher $launcher
    }
}

# -- Embody supervisor Scheduled Task (Windows; label-gated) ----------------
#
# The Windows peer of the Linux systemd supervisor unit (cross-platform-parity).
# Runs `agent-dispatch supervise --all-repos`, turning queued LABELED tasks into
# host embody autopilots. `--all-repos` avoids the lane-scoping gotcha (a short
# `--repo owner/name` filters every task out), which makes the label opt-in the
# ONLY thing between the supervisor and embodying *every* queued task -- so the
# task is enabled only when supervisor.env sets AGENT_DISPATCH_SUPERVISE_LABELS.
# See #2869.

function Test-SupervisorLabelsConfigured {
    param([string]$EnvFile = (Join-Path $InstallDir 'supervisor.env'))
    # True when the env file declares a non-empty AGENT_DISPATCH_SUPERVISE_LABELS.
    if (-not (Test-Path $EnvFile)) { return $false }
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^\s*AGENT_DISPATCH_SUPERVISE_LABELS\s*=\s*(.+?)\s*$') {
            $val = $Matches[1].Trim().Trim('"').Trim("'")
            $val = ($val -replace '[\s,]', '')
            if ($val -ne '') { return $true }
        }
    }
    return $false
}

function Test-SupervisorProfileName {
    param([Parameter(Mandatory)][string]$Name)
    return ($Name -match '^[A-Za-z0-9_-]+$')
}

# The supervisor MODE: 'serve' runs the single master registrar daemon
# (`supervise serve --legacy-env`, reconciling declared pools + legacy env
# profiles, each in its own subprocess); anything else (blank/'legacy') runs the
# classic direct `supervise --label...` embody loop.
function Get-SupervisorMode {
    param([string]$EnvFile = (Join-Path $InstallDir 'supervisor.env'))
    if (Test-Path $EnvFile) {
        foreach ($line in Get-Content $EnvFile) {
            if ($line -match '^\s*AGENT_DISPATCH_SUPERVISE_MODE\s*=\s*(.+?)\s*$') {
                if ($Matches[1].Trim().Trim('"').Trim("'") -eq 'serve') { return 'serve' }
            }
        }
    }
    return 'legacy'
}

function Get-SupervisorTaskName {
    param([string]$ProfileName)
    if ([string]::IsNullOrEmpty($ProfileName)) { return $SupervisorTaskName }
    return "$SupervisorTaskName-$ProfileName"
}

function Get-SupervisorProfileEnvFile {
    param([Parameter(Mandatory)][string]$ProfileName)
    return (Join-Path $SupervisorProfileDir "$ProfileName.env")
}

function Get-SupervisorProfileFiles {
    if (-not (Test-Path $SupervisorProfileDir)) { return @() }
    $files = @(Get-ChildItem -Path $SupervisorProfileDir -Filter '*.env' -File -ErrorAction SilentlyContinue)
    foreach ($f in $files) {
        if (Test-SupervisorProfileName -Name $f.BaseName) {
            $f
        } else {
            Write-Warn "Skipping unsafe supervisor profile name: $($f.BaseName)"
        }
    }
}

function Remove-SupervisorTask {
    param([string]$Name = $SupervisorTaskName)
    # Returns 'removed' | 'blocked' | 'absent'. Mirrors Remove-CoordinatorTask.
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) { return 'absent' }
    if (-not (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue)) {
        return 'absent'
    }
    Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        return 'blocked'
    }
    return 'removed'
}

function Remove-SupervisorAutostart {
    param([string]$Name = $SupervisorTaskName)
    # Remove the supervisor's non-elevated logon auto-start (HKCU Run) if present.
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    try {
        if (Get-ItemProperty -Path $runKey -Name $Name -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $runKey -Name $Name -ErrorAction SilentlyContinue
            return $true
        }
    } catch { }
    return $false
}

function Remove-AllSupervisorTasks {
    switch (Remove-SupervisorTask -Name $SupervisorTaskName) {
        'removed' { Write-Step "Removed supervisor task '$SupervisorTaskName'" }
        default   { }
    }
    [void](Remove-SupervisorAutostart -Name $SupervisorTaskName)
    if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
        $tasks = @(Get-ScheduledTask -TaskName "$SupervisorTaskName-*" -ErrorAction SilentlyContinue)
        foreach ($task in $tasks) {
            $name = $task.TaskName
            switch (Remove-SupervisorTask -Name $name) {
                'removed' { Write-Step "Removed supervisor profile task '$name'" }
                'blocked' { Write-Skip "Supervisor profile task '$name' present but not removable without elevation -- run elevated to remove it" }
                default   { }
            }
        }
    }
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    try {
        $props = Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue
        if ($props) {
            foreach ($p in $props.PSObject.Properties) {
                if ($p.Name -like "$SupervisorTaskName-*") {
                    Remove-ItemProperty -Path $runKey -Name $p.Name -ErrorAction SilentlyContinue
                    Write-Step "Removed supervisor profile logon auto-start '$($p.Name)'"
                }
            }
        }
    } catch { }
}

function Remove-OrphanSupervisorProfiles {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) { return }
    $tasks = @(Get-ScheduledTask -TaskName "$SupervisorTaskName-*" -ErrorAction SilentlyContinue)
    foreach ($task in $tasks) {
        $name = $task.TaskName
        $profile = $name.Substring($SupervisorTaskName.Length + 1)
        $envFile = Get-SupervisorProfileEnvFile -ProfileName $profile
        if ((-not (Test-SupervisorProfileName -Name $profile)) -or (-not (Test-Path $envFile))) {
            switch (Remove-SupervisorTask -Name $name) {
                'removed' { Write-Ok "Removed orphan supervisor profile task: $name" }
                'blocked' { Write-Skip "Orphan supervisor profile task '$name' present but not removable without elevation" }
                default   { }
            }
        }
    }
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    try {
        $props = Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue
        if ($props) {
            foreach ($p in $props.PSObject.Properties) {
                if ($p.Name -like "$SupervisorTaskName-*") {
                    $profile = $p.Name.Substring($SupervisorTaskName.Length + 1)
                    $envFile = Get-SupervisorProfileEnvFile -ProfileName $profile
                    if ((-not (Test-SupervisorProfileName -Name $profile)) -or (-not (Test-Path $envFile))) {
                        Remove-ItemProperty -Path $runKey -Name $p.Name -ErrorAction SilentlyContinue
                        Write-Ok "Removed orphan supervisor profile logon auto-start: $($p.Name)"
                    }
                }
            }
        }
    } catch { }
}

function Remove-ProfileSupervisorTasks {
    # MODE=serve: the master daemon runs each legacy profile itself (--legacy-env),
    # so every per-profile task (agent-dispatch-supervisor-<name>) is redundant.
    # Retire them all; their .env files stay for the daemon to read. The "-*" glob
    # requires a char after the dash, so the primary task is never matched.
    if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
        $tasks = @(Get-ScheduledTask -TaskName "$SupervisorTaskName-*" -ErrorAction SilentlyContinue)
        foreach ($task in $tasks) {
            $name = $task.TaskName
            switch (Remove-SupervisorTask -Name $name) {
                'removed' { Write-Ok "Retired per-profile task (MODE=serve; the daemon runs it): $name" }
                'blocked' { Write-Skip "Per-profile task '$name' present but not removable without elevation" }
                default   { }
            }
        }
    }
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    try {
        $props = Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue
        if ($props) {
            foreach ($p in $props.PSObject.Properties) {
                if ($p.Name -like "$SupervisorTaskName-*") {
                    Remove-ItemProperty -Path $runKey -Name $p.Name -ErrorAction SilentlyContinue
                    Write-Ok "Retired per-profile logon auto-start (MODE=serve): $($p.Name)"
                }
            }
        }
    } catch { }
}

function Install-InteractiveScheduledTask {
    <#
      Idempotent Interactive-logon-type Scheduled Task installer shared by the
      coordinator and supervisor on an interactive-service-mode host. An
      -AtLogOn task with LogonType Interactive/RunLevel Limited gets the SAME
      desktop access as the historical HKCU Run auto-start it replaces (embody
      CLI/mux sessions need an interactive station -- S4U's non-interactive
      station, used by the headless boot-mode coordinator, cannot provide one),
      but ALSO gets Task Scheduler's own RestartCount/RestartInterval
      crash-restart policy, which HKCU Run cannot: closing the "who restarts
      the watchdog after it dies mid-session" gap (copilot-extensions#2172).

      Register-ONCE model, same as the boot-mode supervisor task below: a
      non-elevated call that finds the task already registered (as an
      Interactive-logon task -- a mismatched leftover, e.g. a stale S4U boot
      task, is NOT treated as a match) must never re-register (Register -Force
      needs elevation); it just cycles (stop/start) the task onto the freshly
      activated launcher slot, which resolves the current version itself. First
      registration needs ONE elevated run.

      Returns $true when the task ends up registered+enabled (whether by this
      call or a prior one); $false when elevation is unavailable (first
      install, non-elevated) and the caller should fall back to its own
      non-elevated logon auto-start path -- unchanged, degrade-gracefully
      behavior for a host where elevation truly cannot be obtained. Also
      returns $false (never throws) when the ScheduledTasks cmdlets themselves
      are unavailable, so a caller that skips the `$haveSchedMod` guard still
      degrades gracefully instead of crashing the install.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Launcher,
        [Parameter(Mandatory)][string]$Description,
        [string]$WorkingDirectory = $InstallDir,
        [switch]$NoStart
    )

    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        return $false
    }

    $existingTask = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    $matchingType = $existingTask -and ($existingTask.Principal.LogonType -eq 'Interactive')
    if ($matchingType -and (-not (Test-Elevated))) {
        Enable-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue | Out-Null
        if ($NoStart) {
            # The caller explicitly asked not to (re)start anything right now
            # (e.g. the coordinator's graceful-cutover path, where a NEW
            # coordinator has already been brought up out-of-band and
            # stopping/restarting this task would race or kill it) -- leave an
            # already-running task's process completely untouched.
            Write-Ok "$Description already registered (Scheduled Task '$Name'; left running as-is, -NoStart)"
        } else {
            # Start-ScheduledTask on an already-Running task is a no-op in Task
            # Scheduler, so picking up a freshly activated launcher slot needs
            # an explicit stop before the restart.
            Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
            Write-Ok "$Description refreshed in place (Scheduled Task '$Name'; restarted onto the new build -- no re-register, no elevation)"
        }
        return $true
    }

    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`"" `
        -WorkingDirectory $WorkingDirectory
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $regOk = $false
    try {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description $Description | Out-Null
        $regOk = $?
    } catch {
        $regOk = $false
    }
    $ErrorActionPreference = $prevEAP
    if (-not $regOk) {
        return $false
    }
    Enable-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue | Out-Null
    if (-not $NoStart) { Start-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue }
    Write-Ok "$Description installed + started (Scheduled Task '$Name'; interactive logon, self-restarting)"
    return $true
}

function Install-SupervisorLogonAutostart {
    # Interactive-mode supervisor: start it now (detached) and register an HKCU
    # Run key so it (re)starts at each interactive logon. An interactive logon
    # station is actually the RIGHT fit for the supervisor (it spawns embody CLI
    # sessions that need one), so this is a clean first-class path, not a fallback.
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Launcher,
        [Parameter(Mandatory)][string]$EnvFile
    )
    $taskArgs = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`" -EnvFile `"$EnvFile`""
    # Install-SupervisorTask retires every prior wrapper/master/child generation
    # ONCE before reconciling all primary/profile launchers. Do not repeat a
    # process-wide stop here: doing so would kill siblings started earlier in the
    # same profile pass.
    try {
        Start-Process -FilePath 'conhost.exe' -ArgumentList $taskArgs -WindowStyle Hidden | Out-Null
    } catch {
        Write-Warn "Could not start supervisor process '$Name': $($_.Exception.Message)"
    }
    try {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        New-ItemProperty -Path $runKey -Name $Name -Value "conhost.exe $taskArgs" `
            -PropertyType String -Force | Out-Null
        Write-Ok "Embody supervisor installed as an interactive logon service (HKCU Run '$Name'; no elevation)"
    } catch {
        Write-Warn "Could not register supervisor logon auto-start '$Name' (HKCU Run): $($_.Exception.Message)"
    }
}

function Write-SupervisorDefaultEnv {
    $envFile = Join-Path $InstallDir 'supervisor.env'
    if (-not (Test-Path $envFile)) {
        $envDefault = @"
# agent-dispatch embody supervisor environment.
# Edit, then: Start-ScheduledTask -TaskName agent-dispatch-supervisor
#
# SAFETY: the supervisor turns queued tasks into AUTONOMOUS embody sessions. It
# runs with --all-repos, so it is GATED by an explicit label opt-in: only queued
# tasks carrying one of these labels are embodied. With NO labels set, the task
# is left DISABLED -- a label-less supervisor would embody EVERY queued task
# (handoffs, interactive worktree-pinned tasks, ...), which is unsafe.
#
# Opt-in labels, comma- or space-separated (REQUIRED to enable the task):
AGENT_DISPATCH_SUPERVISE_LABELS=
# Poll interval, seconds (default 30):
AGENT_DISPATCH_SUPERVISE_INTERVAL=30
# Max concurrent in-flight embodies (default 1 = max-one-active):
AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT=1
# Max failed spawn attempts before a task is dead-lettered (default 3; 0=disable):
AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS=3
# Per-label overrides of MAX_ATTEMPTS (space- or comma-separated LABEL=N pairs),
# e.g. "code-review=3 nightly-scan=1" so raising one
# label's bound never revives another label's stale tasks (N=0 = retry forever):
AGENT_DISPATCH_SUPERVISE_LABEL_MAX_ATTEMPTS=
# Default embody backend: 'headless' (default) embodies each claimed task as a
# headless agent-bridge ACP session (no mux, no CLI-start-prompt); 'cli' makes the
# lane a CLI-backed autopilot worktree session. Leave blank for the headless default.
AGENT_DISPATCH_SUPERVISE_EMBODY_BACKEND=
# Per-label overrides (comma/space list; each must also be in SUPERVISE_LABELS):
#   CLI_LABELS      -- force these labels to a CLI autopilot (opt-out on a headless lane)
#   HEADLESS_LABELS -- force these labels headless (opt-in when EMBODY_BACKEND=cli)
AGENT_DISPATCH_SUPERVISE_CLI_LABELS=
AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS=
# agent-bridge agent name used for headless embody bodies (default: task-worker):
AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT=
# Extra raw flags appended to the invocation (advanced; e.g. fleet mode:
#   --pool host-a,host-b --origin anomalous-potato):
AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=
# Supervisor MODE (migration opt-in; default: the classic direct embody loop):
#   serve -- run the single MASTER registrar daemon (supervise serve --legacy-env)
#            instead of the direct loop. It reconciles declared pools (registrar
#            pointers) PLUS this host's legacy profiles (supervisor.env +
#            supervisors/*.env, via --legacy-env), each in its own subprocess, and
#            is self-gating (only labeled units run). In this mode the installer
#            stops creating per-profile tasks and retires any it created.
# Leave blank for the classic per-host direct supervisor.
AGENT_DISPATCH_SUPERVISE_MODE=
# MODE=serve only: explicit machine scope for this host's daemon. Recommended in a
# service context -- CWD-based identity resolution can fail there, and without a
# machine the daemon SKIPS every machine-pinned declaration (the downstream tracker).
# Leave blank to fall back to the host node name at runtime; set to this host's
# alias to pin it explicitly.
AGENT_DISPATCH_SUPERVISE_MACHINE=
"@
        [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
        Write-Ok "Supervisor env: $envFile (no labels -> task stays inert; add a label to enable)"
    } else {
        Write-Skip "Supervisor env already exists: $envFile"
    }
    return $envFile
}

function Write-SupervisorLauncher {
    # Launcher: loads supervisor.env, builds the supervise argv (labels -> repeated
    # --label flags), and hard-refuses a label-less run (defense-in-depth: the
    # registration below leaves it disabled without labels, but a hand-enable must
    # not embody everything). supervise logs to STDERR, so -- as with the
    # coordinator launcher -- drop to 'Continue' for the invocation so native
    # stderr is captured, not a terminating NativeCommandError.
    $launcher = Join-Path $InstallDir 'supervise-service.ps1'
    $launcherBody = @"
param([string]`$EnvFile = (Join-Path `$PSScriptRoot 'supervisor.env'))
# agent-dispatch embody supervisor launcher (generated by install.ps1; #2869).
# Do not edit; edit supervisor.env or supervisors/<name>.env instead.
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
`$env:AGENT_DISPATCH_INSTALL_DIR = '$($InstallDir -replace "'","''")'
Set-Location -LiteralPath `$PSScriptRoot
`$envFile = `$EnvFile
`$labels = ''
`$interval = '30'
`$maxConcurrent = '1'
`$maxAttempts = '3'
`$labelMaxAttempts = ''
`$embodyBackend = ''
`$cliLabels = ''
`$headlessLabels = ''
`$headlessAgent = ''
`$extra = ''
`$mode = ''
`$sMachine = ''
if (Test-Path `$envFile) {
    foreach (`$line in Get-Content `$envFile) {
        `$t = `$line.Trim()
        if (`$t -eq '' -or `$t.StartsWith('#')) { continue }
        `$kv = `$t -split '=', 2
        if (`$kv.Count -ne 2) { continue }
        `$k = `$kv[0].Trim(); `$v = `$kv[1].Trim()
        switch (`$k) {
            'AGENT_DISPATCH_SUPERVISE_LABELS'         { `$labels = `$v }
            'AGENT_DISPATCH_SUPERVISE_INTERVAL'       { if (`$v) { `$interval = `$v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT' { if (`$v) { `$maxConcurrent = `$v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS'   { if (`$v) { `$maxAttempts = `$v } }
            'AGENT_DISPATCH_SUPERVISE_LABEL_MAX_ATTEMPTS' { `$labelMaxAttempts = `$v }
            'AGENT_DISPATCH_SUPERVISE_EMBODY_BACKEND'  { `$embodyBackend = `$v }
            'AGENT_DISPATCH_SUPERVISE_CLI_LABELS'      { `$cliLabels = `$v }
            'AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS' { `$headlessLabels = `$v }
            'AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT'  { `$headlessAgent = `$v }
            'AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS'     { `$extra = `$v }
            'AGENT_DISPATCH_SUPERVISE_MODE'           { `$mode = `$v }
            'AGENT_DISPATCH_SUPERVISE_MACHINE'        { `$sMachine = `$v }
        }
    }
}
if (`$mode -eq 'serve') {
    # MODE=serve (opt-in): run the single MASTER registrar daemon instead of the
    # classic direct embody loop. The daemon reconciles declared pools (discovered
    # pointers) + this host's legacy env profiles (--legacy-env: supervisor.env +
    # supervisors/*.env), each in its own subprocess. It is self-gating (only
    # labeled declarations/profiles run), so it needs no label opt-in here.
    `$argsList = @('supervise', 'serve', '--legacy-env', '--interval', `$interval)
    # Explicit machine scope (recommended for a service context, where CWD-based
    # identity resolution can fail and leave the daemon unable to scope
    # machine-pinned declarations -- the downstream tracker). Falls back to the host
    # node name at runtime when unset.
    if (`$sMachine) { `$argsList += @('--machine', `$sMachine) }
    if (`$extra) { `$argsList += (`$extra -split '\s+') }
} else {
`$argsList = @('supervise', '--all-repos', '--interval', `$interval,
    '--max-concurrent', `$maxConcurrent, '--max-attempts', `$maxAttempts)
`$haveLabel = `$false
foreach (`$l in (`$labels -split '[\s,]+')) {
    if (`$l) { `$argsList += @('--label', `$l); `$haveLabel = `$true }
}
if (-not `$haveLabel) {
    Write-Error "agent-dispatch-supervisor: refusing to run with no opt-in label. A label-less supervisor would embody EVERY queued task. Set AGENT_DISPATCH_SUPERVISE_LABELS in `$envFile."
    exit 78  # EX_CONFIG
}
foreach (`$lm in (`$labelMaxAttempts -split '[\s,]+')) {
    if (`$lm) { `$argsList += @('--label-max-attempts', `$lm) }
}
if (`$embodyBackend) { `$argsList += @('--embody-backend', `$embodyBackend) }
foreach (`$cl in (`$cliLabels -split '[\s,]+')) {
    if (`$cl) { `$argsList += @('--cli-label', `$cl) }
}
foreach (`$hl in (`$headlessLabels -split '[\s,]+')) {
    if (`$hl) { `$argsList += @('--headless-label', `$hl) }
}
if (`$headlessAgent) { `$argsList += @('--headless-agent', `$headlessAgent) }
if (`$extra) { `$argsList += (`$extra -split '\s+') }
}
# Resolve the .venv junction's target and launch the slot python DIRECTLY (never
# traverse the junction; reading its target is allowed) -- RedirectionGuard #637.
# Resolved up front so the version (`$_slot leaf) is available for the log fallback.
`$_venv = '$($LinkDir -replace "'","''")'
`$_py = '$($LinkPython -replace "'","''")'
`$_slot = ''
`$_root = Split-Path `$_venv
if ((Split-Path -Leaf `$_root) -eq 'versions') { `$_root = Split-Path `$_root }
`$_ver = ''
try { `$_ver = ([IO.File]::ReadAllText((Join-Path `$_root 'current-version'))).Trim() } catch {}
`$_slot = if (`$_ver) { Join-Path `$_root ('versions\' + `$_ver) } else { '' }
`$_py = if (`$_slot) { Join-Path `$_slot 'Scripts\python.exe' } else { '' }
if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) {
    # #742: marker missing/stale -> prefer last-known-good (the last version
    # activate() published) over a raw newest-slot guess, which could bind a
    # still-installing/never-activated slot mid-swap.
    `$_lkg = ''
    try { `$_lkg = ([IO.File]::ReadAllText((Join-Path `$_root 'last-known-good'))).Trim() } catch {}
    if (`$_lkg) { `$_slot = Join-Path `$_root ('versions\' + `$_lkg); `$_py = Join-Path `$_slot 'Scripts\python.exe' }
    if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) { `$_py = ''; `$_slot = '' }
}
if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) { `$_py = Get-ChildItem (Join-Path `$_root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Join-Path `$_.FullName 'Scripts\python.exe' } | Where-Object { Test-Path -LiteralPath `$_ } | Select-Object -Last 1; `$_slot = if (`$_py) { Split-Path (Split-Path `$_py) } else { '' } }
# A busy/locked log must NEVER block the supervisor launch -- prefer the canonical
# supervise-service.log, else a VERSION- and pid-aware fallback (see serve-service).
function Resolve-WritableLog([string]`$primary, [string]`$slot) {
    `$ver = if (`$slot) { Split-Path -Leaf `$slot } else { 'unknown' }
    `$alt = (`$primary -replace '\.log`$', "-`$ver-`$PID.log")
    foreach (`$cand in @(`$primary, `$alt)) {
        try { `$fs = [System.IO.File]::Open(`$cand, 'Append', 'Write', 'ReadWrite'); `$fs.Close(); return `$cand } catch { }
    }
    return `$alt
}
`$logFile = Resolve-WritableLog (Join-Path `$PSScriptRoot 'supervise-service.log') `$_slot
try {
    if ((Test-Path `$logFile) -and ((Get-Item `$logFile).Length -gt 1MB)) {
        Move-Item -Force `$logFile "`$logFile.1"
    }
} catch { }
try {
    "[`$(Get-Date -Format o)] agent-dispatch supervisor launch (env=`$envFile labels=`$labels interval=`$interval log=`$logFile)" |
        Out-File -FilePath `$logFile -Append -Encoding utf8
} catch { }
`$ErrorActionPreference = 'Continue'
try {
    & `$_py -m agent_dispatch @argsList 2>&1 | Out-File -FilePath `$logFile -Append -Encoding utf8
} catch {
    # Teeing to the log failed -- keep the supervisor alive without the tee.
    & `$_py -m agent_dispatch @argsList *> `$null
}
"@
    [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)
    return $launcher
}

function Restart-SupervisorTaskInPlace {
    <# Cycle an ALREADY-REGISTERED supervisor Scheduled Task onto the freshly-
       activated slot, NON-ELEVATED. The task's action points at a STABLE launcher
       path that resolves the active slot via the current-version marker, so the
       task DEFINITION never changes across updates -- only the launcher content +
       the active slot do. Re-registering would need elevation; restarting does
       not. The conhost --headless launcher detaches the daemon from the task's
       tracked tree. Install-SupervisorTask therefore retires the full
       detached wrapper/master/child inventory once before it reconciles every
       primary/profile task; this per-task helper only resets and starts its own
       registration so sibling profiles are not killed mid-pass. #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$EnvFile,
        [Parameter(Mandatory)][string]$Mode,
        [Parameter(Mandatory)][string]$DisplayName
    )
    if ($Mode -eq 'serve' -or (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)) {
        Enable-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue | Out-Null
        Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        Write-Ok "$DisplayName refreshed in place (Scheduled Task '$Name'; restarted onto the new build -- no re-register, no elevation)"
    } else {
        Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue | Out-Null
        Write-Ok "$DisplayName INERT (no opt-in label; task disabled)"
    }
}

function Install-SupervisorTaskInstance {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$EnvFile,
        [Parameter(Mandatory)][string]$Launcher,
        [Parameter(Mandatory)][string]$DisplayName
    )

    # MODE=serve runs the self-gating master daemon, so it is enabled
    # unconditionally (no label opt-in); the direct loop stays label-gated.
    $mode = Get-SupervisorMode -EnvFile $EnvFile

    # Interactive-required host: prefer an Interactive-logon Scheduled Task (same
    # desktop access as the HKCU Run auto-start it replaces, PLUS Task
    # Scheduler's own crash-restart policy -- copilot-extensions#2172); degrade
    # to the non-elevated logon auto-start only when elevation is unavailable.
    # Only when a label opt-in is configured (else stay inert, like the
    # disabled-task case).
    if ((Get-ServiceMode) -eq 'interactive') {
        if ($mode -eq 'serve' -or (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)) {
            if (Install-InteractiveScheduledTask -Name $Name -Launcher $Launcher `
                    -Description 'agent-dispatch -- embody + companion supervisor (interactive)') {
                if (Remove-SupervisorAutostart -Name $Name) {
                    Write-Step "Removed the non-elevated logon fallback -- the Scheduled Task supersedes it"
                }
                return
            }
            # Elevation unavailable (first install, non-elevated): degrade to the
            # non-elevated logon auto-start, exactly as before this task-based
            # path existed.
            switch (Remove-SupervisorTask -Name $Name) {
                'removed' { Write-Step "Removed prior supervisor Scheduled Task '$Name' (interactive mode)" }
                default   { }
            }
            Install-SupervisorLogonAutostart -Name $Name -Launcher $Launcher -EnvFile $EnvFile
            return
        }
        switch (Remove-SupervisorTask -Name $Name) {
            'removed' { Write-Step "Removed prior supervisor Scheduled Task '$Name' (interactive mode)" }
            default   { }
        }
        if (Remove-SupervisorAutostart -Name $Name) { Write-Step "Removed supervisor logon auto-start '$Name' (no opt-in label)" }
        Write-Ok "$DisplayName INERT (no opt-in label). Set AGENT_DISPATCH_SUPERVISE_LABELS in $EnvFile + re-run update to enable."
        return
    }

    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`" -EnvFile `"$EnvFile`"" `
        -WorkingDirectory $InstallDir

    # Register-ONCE model (#689 / #1837): re-registering (Register -Force, which
    # needs elevation) is reserved for first install or an ACTUAL task-definition
    # change -- never a routine update, elevated or not. Compare the
    # already-registered task's action against the one this update would
    # produce; the launcher/env-file/install-dir paths are all stable across
    # ordinary updates, so a match here means "already correct", regardless of
    # the caller's elevation state. Only a genuine drift (a real migration, e.g.
    # an install-dir move) falls through to re-register.
    $existingTask = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($existingTask) {
        $existingAction = $existingTask.Actions | Select-Object -First 1
        $matchesDesired = $existingAction -and
            $existingAction.Execute -eq $action.Execute -and
            $existingAction.Arguments -eq $action.Arguments -and
            $existingAction.WorkingDirectory -eq $action.WorkingDirectory
        if ($matchesDesired) {
            Restart-SupervisorTaskInPlace -Name $Name -EnvFile $EnvFile -Mode $mode -DisplayName $DisplayName
            return
        }
        if (-not (Test-Elevated)) {
            # A genuine definition drift, but we cannot re-register without
            # elevation -- keep the existing (stale) task registration rather
            # than losing it, and just cycle the running process onto the new
            # slot; say so instead of silently no-op'ing the migration.
            Write-Warn "$DisplayName task definition changed but elevation is unavailable to migrate it -- run elevated once to update the task; refreshing the running process onto the new slot meanwhile"
            Restart-SupervisorTaskInPlace -Name $Name -EnvFile $EnvFile -Mode $mode -DisplayName $DisplayName
            return
        }
    }

    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $regOk = $false
    try {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description 'agent-dispatch -- embody spawn supervisor (labeled queued tasks -> host embody autopilots)' | Out-Null
        $regOk = $?
    } catch {
        $regOk = $false
    }
    if (-not $regOk) {
        $ErrorActionPreference = $prevEAP
        Write-Warn "$DisplayName not registered (first-time task registration needs elevation) -- run elevated ONCE to install the task; subsequent updates refresh it in place, no elevation"
        return
    }

    if ($mode -eq 'serve' -or (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)) {
        Enable-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue | Out-Null
        Start-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        $ErrorActionPreference = $prevEAP
        if ($mode -eq 'serve') {
            Write-Ok "$DisplayName installed + started (Scheduled Task '$Name' -- MODE=serve: master registrar daemon)"
        } else {
            Write-Ok "$DisplayName installed + started (Scheduled Task '$Name')"
        }
    } else {
        # No opt-in label -> leave the task registered but DISABLED (inert), the
        # Windows analogue of an installed-but-not-enabled systemd unit.
        Disable-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue | Out-Null
        $ErrorActionPreference = $prevEAP
        Write-Ok "$DisplayName installed (INERT: no opt-in label; task disabled). To enable: set"
        Write-Step "AGENT_DISPATCH_SUPERVISE_LABELS in $EnvFile, then re-run update"
        Write-Step "(or: Enable-ScheduledTask -TaskName $Name; Start-ScheduledTask -TaskName $Name)"
    }
}

function Install-SupervisorTask {
    # Install only where the full coordinator lives (a client-only host has no
    # local coordinator for the supervisor to talk to). -NoSupervisor opts a full
    # host out; -NoService (client-only) skips it too. Remove stale primary and
    # profile tasks in either case so a host that became client-only stops supervising.
    if ($env:OS -eq 'Windows_NT') {
        # Stop service-manager roots first so restart-on-failure cannot race the
        # inventory reap, then retire all detached generations exactly once.
        Invoke-SupervisorsStop
        $retired = Retire-SupervisorProcesses
        if ($retired -gt 0) {
            Write-Step "Retired $retired prior supervisor wrapper/master/child process(es)"
        }
    }
    if ($NoSupervisor -or $NoService) {
        Remove-AllSupervisorTasks
        Write-Skip 'Embody supervisor skipped (client-only / -NoSupervisor)'
        return
    }
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Skip 'ScheduledTasks module unavailable -- run "agent-dispatch supervise --all-repos --label <L>" manually'
        return
    }

    if (-not (Test-Path $SupervisorProfileDir)) {
        New-Item -ItemType Directory -Force -Path $SupervisorProfileDir | Out-Null
    }
    $envFile = Write-SupervisorDefaultEnv
    $launcher = Write-SupervisorLauncher

    Install-SupervisorTaskInstance -Name $SupervisorTaskName -EnvFile $envFile `
        -Launcher $launcher -DisplayName 'Embody supervisor'

    if ((Get-SupervisorMode -EnvFile $envFile) -eq 'serve') {
        # MODE=serve: the master daemon runs the legacy profiles itself
        # (--legacy-env), so the per-profile tasks are redundant and would
        # double-run -- retire them (their .env files stay; the daemon reads them).
        Remove-ProfileSupervisorTasks
    } else {
        foreach ($profile in @(Get-SupervisorProfileFiles)) {
            $name = Get-SupervisorTaskName -ProfileName $profile.BaseName
            Install-SupervisorTaskInstance -Name $name -EnvFile $profile.FullName `
                -Launcher $launcher -DisplayName "Embody supervisor profile '$($profile.BaseName)'"
        }
        Remove-OrphanSupervisorProfiles
    }
}

function Invoke-SupervisorStart {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$EnvFile,
        [Parameter(Mandatory)][string]$Label
    )
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($task -and $task.State -ne 'Disabled') {
        Start-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        Write-Ok "$Label started"
        return
    }
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $auto = Get-ItemProperty -Path $runKey -Name $Name -ErrorAction SilentlyContinue
    $launcher = Join-Path $InstallDir 'supervise-service.ps1'
    if ($auto -and (Test-Path $launcher) -and (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)) {
        Install-SupervisorLogonAutostart -Name $Name -Launcher $launcher -EnvFile $EnvFile
    }
}

function Invoke-SupervisorsStart {
    Invoke-SupervisorStart -Name $SupervisorTaskName -EnvFile (Join-Path $InstallDir 'supervisor.env') -Label 'Embody supervisor'
    foreach ($profile in @(Get-SupervisorProfileFiles)) {
        $name = Get-SupervisorTaskName -ProfileName $profile.BaseName
        Invoke-SupervisorStart -Name $name -EnvFile $profile.FullName -Label "Embody supervisor profile '$($profile.BaseName)'"
    }
}

function Invoke-SupervisorsStop {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) { return }
    $tasks = @()
    $primary = Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    if ($primary) { $tasks += $primary }
    $tasks += @(Get-ScheduledTask -TaskName "$SupervisorTaskName-*" -ErrorAction SilentlyContinue)
    foreach ($task in $tasks) {
        Stop-ScheduledTask -TaskName $task.TaskName -ErrorAction SilentlyContinue
    }
}

function Write-SupervisorStatus {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$EnvFile,
        [Parameter(Mandatory)][string]$Label
    )
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($task) {
        if (Test-SupervisorLabelsConfigured -EnvFile $EnvFile) {
            Write-Ok "$Label task: $Name $($task.State)"
        } else {
            Write-Ok "$Label task: $Name $($task.State) (INERT: no opt-in label set)"
        }
    } else {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $auto = Get-ItemProperty -Path $runKey -Name $Name -ErrorAction SilentlyContinue
        if ($auto) {
            if (Test-SupervisorLabelsConfigured -EnvFile $EnvFile) {
                Write-Ok "${Label}: $Name interactive logon auto-start (HKCU Run)"
            } else {
                Write-Ok "${Label}: $Name interactive logon auto-start (HKCU Run -- INERT: no opt-in label set)"
            }
        } else {
            Write-Skip "No $Label task: $Name"
        }
    }
}

function Write-SupervisorsStatus {
    Write-SupervisorStatus -Name $SupervisorTaskName -EnvFile (Join-Path $InstallDir 'supervisor.env') -Label 'Embody supervisor'
    foreach ($profile in @(Get-SupervisorProfileFiles)) {
        $name = Get-SupervisorTaskName -ProfileName $profile.BaseName
        Write-SupervisorStatus -Name $name -EnvFile $profile.FullName -Label "Embody supervisor profile '$($profile.BaseName)'"
    }
}

# -- Port reservation (Windows) ---------------------------------------------

function Test-Elevated {
    # True when the current process holds the Administrators role. Windows-only.
    if ($env:OS -ne 'Windows_NT') { return $false }
    try {
        $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($id)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

# -- Coordinator port reservation: RETIRED (durable-service-transport Stage D) --
# The netsh 9847 excludedportrange reservation (Test-PortExcluded / Add-PortReservation)
# existed only to stop the Windows dynamic-port allocator from stealing the FIXED
# coordinator port (#2818). Stage C flipped the coordinator to an OS-assigned dynamic
# port advertised via the rendezvous file, so there is no fixed port to protect and the
# reservation is retired. Any leftover live 9847 exclusion is harmless (it only keeps
# 9847 out of the ephemeral pool) and can be cleared elevated:
#   netsh int ipv4 delete excludedportrange protocol=tcp startport=9847 numberofports=1

# -- Coordinator firewall (Windows, NAT mode only) --------------------------

function Remove-CoordinatorFirewallRule {
    # #640: agent-dispatch exposes NO firewall ports. Machines reach a remote
    # coordinator over SSH or a central tunnel-broker, and the coordinator binds
    # loopback; even the local WSL guest reaches the host over loopback/SSH. So no
    # inbound rule is ever added. Proactively sweep any rule a prior version left
    # (idempotent; needs elevation to remove -- degrades to a logged skip).
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) { return }
    $ruleName = 'agent-dispatch coordinator (WSL)'
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) { return }
    if (-not (Test-Elevated)) {
        Write-Skip "Legacy coordinator firewall rule present -- needs elevation to remove (agent-dispatch no longer uses firewall ports; #640)"
        return
    }
    $existing | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Step "Removed legacy coordinator firewall rule (agent-dispatch uses no exposed ports; #640)"
}

# -- Actions ----------------------------------------------------------------

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE
    # into a per-version snapshot under ~/.agent-dispatch/snapshots/<ver>/, record
    # markers, and deploy the self-provisioning binstub -- deferring the heavy venv
    # build (and the coordinator/supervisor service install) to the binstub's
    # first use. No venv, no uv; fits a sessionStart grace window and NEVER holds
    # the marketplace payload open (it copies from the already self-staged
    # $PluginDir, freeing the singleton immediately). Shares its copy logic with
    # New-PluginBuildSnapshot (Install-Runtime's own build-isolation snapshot).
    Write-Host ''; Write-Host '=== agent-dispatch stamp (defer runtime to first use) ===' -ForegroundColor Cyan; Write-Host ''
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    # Snapshot build happens with NO global lock held: New-PluginBuildSnapshot
    # takes its OWN version-scoped lock (keyed $InstallDir+$SrcVersion)
    # entirely internally, acquiring and releasing it before this function
    # ever touches the global lock below. This ordering is deliberate, not
    # incidental: Install-Runtime holds that SAME version-scoped lock as a
    # wide OUTER scope spanning its full build+install+activate span, and
    # (via Invoke-VersionedActivate) acquires the global, $InstallDir-only
    # lock NESTED inside it for a short version-ordering check. If this
    # function instead held the global lock OUTER and let
    # New-PluginBuildSnapshot acquire the version-scoped lock NESTED inside
    # it -- the previous shape here, and the exact opposite nesting order --
    # a concurrent `stamp` and `install` for the same version could deadlock:
    # stamp holding global and waiting on version, install holding version
    # and waiting on global. Keeping the two acquisitions here strictly
    # SEQUENTIAL (never nested) makes that AB-BA cycle impossible, regardless
    # of which lock Install-Runtime nests inside the other.
    $snapDir = New-PluginBuildSnapshot -PluginDir $PluginDir -InstallDir $InstallDir -Version $SrcVersion

    # Hold ONE lock across the version-ordering guard AND both marker writes
    # (not narrower, independent acquisitions for each): two overlapping
    # stamps for DIFFERENT versions could otherwise each pass the guard, then
    # race the writes below -- an older, slower invocation could publish its
    # markers AFTER a newer one already finished, pointing
    # payload-dir/stamped-version at stale content, or leaving the two
    # markers naming different versions.
    $stampMutex = Enter-PluginSnapshotLock -InstallDir $InstallDir
    try {
        # Version-ordering guard, UNDER this same lock: the mutex only
        # serializes overlapping writes, it does not guarantee ARRIVAL
        # order. A delayed/preempted older-version invocation could still
        # acquire this lock AFTER a newer one already published, and
        # (without this check) would unconditionally overwrite both
        # markers with its own stale version. Compare against whatever
        # stamped-version is CURRENTLY on disk (read inside the lock, so
        # this observes the true latest publish) and skip rather than
        # downgrade, mirroring Invoke-DowngradeGuard's own
        # -Force/AGENT_DISPATCH_ALLOW_DOWNGRADE override for a deliberate
        # rollback.
        $stampedVersionMarker = Join-Path $InstallDir 'stamped-version'
        $currentStamped = if (Test-Path $stampedVersionMarker) {
            (Get-Content -LiteralPath $stampedVersionMarker -Raw -ErrorAction SilentlyContinue).Trim()
        } else { $null }
        if ($currentStamped -and (Test-VersionLt -A $SrcVersion -B $currentStamped) -and -not $Force) {
            Write-Skip "Not publishing: source $SrcVersion is older than already-stamped $currentStamped (a newer stamp arrived first; -Force to override)"
            return
        }
        # Publish-FileAtomically guards the binstub's self-provisioning read
        # (which never takes this mutex) against observing a torn,
        # partially-written marker file.
        Publish-FileAtomically -Path (Join-Path $InstallDir 'payload-dir') -Content $snapDir -Encoding $utf8NoBom
        Publish-FileAtomically -Path $stampedVersionMarker -Content $SrcVersion -Encoding $utf8NoBom
        Write-Ok "Snapshot: $snapDir"
        # Deployed INSIDE the lock (mirrors agent-machines' own stamp,
        # plugins/agent-machines/scripts/init.ps1:2061-2065): otherwise an
        # older, delayed invocation could resume AFTER a newer one already
        # published AND deployed, then overwrite the resolver/binstub files
        # from its own (older) $PSScriptRoot -- pairing fresh markers with a
        # stale launcher surface, the exact same ordering hazard the
        # version guard above exists to prevent for the markers themselves.
        Deploy-SelfProvisioningBinstub
        Write-Ok 'Stamped: agent-dispatch binstub on PATH; runtime provisions on first use.'
    } finally {
        [void]$stampMutex.ReleaseMutex()
        $stampMutex.Dispose()
    }
}


function Invoke-Install {
    Write-Host ''; Write-Host '=== agent-dispatch install ===' -ForegroundColor Cyan; Write-Host ''
    Install-Runtime
    if (Test-ActivationSupersededNow) {
        # A concurrent, newer install already activated -- either caught at
        # Install-Runtime's own return, or only now via the LIVE re-check
        # (see Test-ActivationSupersededNow's own docstring: this invocation
        # could have WON its own activation and only been overtaken
        # afterward). Install-CoordinatorTask/Install-SupervisorTask are
        # themselves version-agnostic (they register generic, marker-
        # resolving launchers), but Confirm-CoordinatorRunning compares the
        # running coordinator's reported version against THIS invocation's
        # own (older, losing) $VenvPython build -- proceeding would
        # misreport a perfectly healthy, already-newer coordinator as
        # serving a stale build. Nothing else to do for this invocation.
        Write-Host ''; Write-Host '=== agent-dispatch install complete (superseded by a newer concurrent build) ===' -ForegroundColor Cyan
        return
    }
    Install-CoordinatorTask
    Remove-CoordinatorFirewallRule
    Install-SupervisorTask
    if (-not $NoService) { Confirm-CoordinatorRunning }
    Write-Host ''; Write-Host '=== agent-dispatch install complete ===' -ForegroundColor Cyan
}

function Test-CoordinatorRouted {
    <# True when a live coordinator has published the zdd routing table (i.e. it is
       a Thread-B build with a /drain seam that can be gracefully cut over). A
       pre-Thread-B coordinator serves the rendezvous endpoint but has no routing
       entry and no /drain -- it must be stop-and-swapped once (invariant #2). #>
    $py = if (Test-Path $VenvPython) { $VenvPython } elseif (Test-Path $LinkPython) { $LinkPython } else { $null }
    if (-not $py) { return $false }
    & $py -c 'import sys; from zdd.routing import read_active_endpoint; from agent_dispatch.config import routing_dir; sys.exit(0 if read_active_endpoint(routing_dir()) else 1)' 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-CoordinatorCutover {
    <# Graceful installer-driven cutover (Thread B; docs/patterns/graceful-daemon-cutover.md).
       Runs the zdd cutover IN-PROCESS from the freshly-built NEW slot python:
       spawn the new coordinator PASSIVE on a fresh port -> health-gate -> flip the
       routing table -> drain the OLD coordinator at the safe point (between task
       claims) -> retire it. In-flight task workers are never killed; they outlive
       the swap and re-adopt the new coordinator via the durable queue DB +
       routing table. The supervisor service is reconciled separately after the
       coordinator cutover so stale wrapper/master/child generations cannot keep
       autonomous units alive.
       Returns $true when the cutover brought up the new coordinator (so the
       caller skips a normal task-start), $false to fall back to a normal start. #>
    $py = if (Test-Path $VenvPython) { $VenvPython } elseif (Test-Path $LinkPython) { $LinkPython } else { $null }
    if (-not $py) { return $false }
    Write-Step 'Graceful cutover: standing the new coordinator up beside the old, then flipping routing...'
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & $py -m agent_dispatch _cutover --json 2>&1 | Out-String
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    $ok = $false
    try {
        # The JSON result is the last brace-balanced object on stdout; steps go to stderr.
        $jsonLine = ($out -split "`n" | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
        if ($jsonLine) { $ok = [bool]((ConvertFrom-Json $jsonLine).ok) }
    } catch { $ok = $false }
    if ($ok -or $rc -eq 0) {
        Write-Ok 'Coordinator cut over to the new build (no in-flight work killed; supervisor/workers re-adopt via the queue DB + routing table)'
        return $true
    }
    Write-Warn 'Graceful cutover did not complete -- falling back to a normal restart'
    if ($out.Trim()) { Write-Step ($out.Trim()) }
    return $false
}

function Invoke-Update {
    Write-Host ''; Write-Host '=== agent-dispatch update ===' -ForegroundColor Cyan; Write-Host ''
    # Mark the live-service update lifecycle as in-progress (ce#5066) so a
    # local liveness watchdog never force-restarts a daemon that is
    # legitimately, briefly mid-cutover. try/finally (not a trap, which
    # PowerShell only fires for terminating errors) covers every exit path.
    Write-UpdateMarker
    try {
    Invoke-DowngradeGuard
    Install-Runtime
    if (Test-ActivationSupersededNow) {
        # This invocation's own build lost the cross-version activation race
        # -- either caught immediately inside Install-Runtime, or only
        # discovered HERE via the LIVE re-check (this invocation could have
        # WON its own activation and released the lock, only for a newer
        # version to activate before reaching this exact point -- see
        # Test-ActivationSupersededNow's own docstring for why a one-time
        # snapshot right after activation is not sufficient). The cutover
        # logic below spawns the new coordinator from THIS invocation's own
        # $VenvPython/$LinkPython -- proceeding would cut the ALREADY-newer,
        # already-active coordinator OVER to this invocation's older build,
        # a silent rollback. Nothing to update: the newer build's own update
        # already did (or will do) the real cutover.
        Write-Skip 'Update superseded by a newer concurrent build that already activated -- skipping coordinator cutover/task refresh for this invocation'
        return
    }
    # Thread B (graceful daemon cutover): a version update must NEVER kill
    # in-flight, non-resumable work. Install-Runtime built + activated the new
    # slot WITHOUT stopping the running daemon; now, if a live coordinator is
    # serving the old slot, stand the new slot up passive, flip the routing table,
    # drain the old coordinator at its safe cutover point (between task claims),
    # and retire it -- in-process, automatic, no operator step. Spawned task
    # workers outlive the coordinator swap. Install-SupervisorTask then retires
    # every prior supervisor service generation and starts exactly the configured
    # current generation, preventing old autonomous emitters/lanes from surviving
    # a runtime update. Falls back to a normal start only when no live coordinator
    # exists to cut over from, or the cutover cannot run.
    if (-not $NoService) {
        $didCutover = $false
        if (Test-CoordinatorHealthy) {
            # Hold the SAME global (version-independent) lock
            # Invoke-VersionedActivate itself uses to publish current-version,
            # across BOTH the live re-check AND the entire cutover call below
            # -- not just the re-check alone. A re-check immediately before
            # calling Invoke-CoordinatorCutover still leaves a gap: this
            # invocation's own cutover subprocess has its own internal
            # cross-version cutover lease, and a NEWER invocation could
            # activate and complete its ENTIRE cutover while this (older)
            # invocation is merely queued waiting on that internal lease --
            # once it finally acquires it, it would route the coordinator
            # back to this invocation's own stale build. Holding this lock
            # across the whole span means a newer invocation's own
            # Invoke-VersionedActivate call (which needs this identical
            # lock) cannot even START publishing its activation until this
            # invocation's cutover attempt has fully finished and released
            # it -- closing the window rather than merely narrowing it.
            #
            # -TimeoutSeconds: the default (20s) comfortably covers a bare
            # marker-publish, but this critical section now also spans a
            # REAL Invoke-CoordinatorCutover call, whose own zdd.cutover
            # defaults allow up to health_timeout(60s) + drain_timeout(300s)
            # + 60s slack for its OWN internal cutover-lease wait (see
            # zdd.cutover.CutoverOrchestrator.run's own lock_timeout
            # default) -- a 420s worst case. A concurrent newer installer
            # waiting on THIS lock must outlast that, not time out and fail
            # outright during a normal long cutover.
            $cutoverMutex = Enter-PluginSnapshotLock -InstallDir $InstallDir -TimeoutSeconds 450
            try {
                if (Test-ActivationSupersededNow) {
                    Write-Skip 'Update superseded by a newer concurrent build that already activated -- skipping coordinator cutover/task refresh for this invocation'
                    return
                }
                if (Test-CoordinatorRouted) {
                    # A Thread-B coordinator (routed, /drain seam) -> graceful cutover.
                    $didCutover = Invoke-CoordinatorCutover
                } else {
                    # Pre-Thread-B coordinator (unrouted, no /drain): one-time
                    # stop-and-swap (invariant #2 fallback). Every future update from a
                    # Thread-B build is graceful. The supervisor is left running.
                    $stopped = Stop-DispatchProcess -Subcommand serve
                    if ($stopped -gt 0) {
                        Write-Step "Stopped $stopped pre-cutover coordinator process(es) -- one-time transition to graceful cutover"
                    }
                }
            } finally {
                [void]$cutoverMutex.ReleaseMutex()
                $cutoverMutex.Dispose()
            }
        }
        Remove-CoordinatorFirewallRule
        # Refresh the boot task definition either way; -NoStart avoids launching a
        # SECOND coordinator when the cutover already brought the new one up.
        Install-CoordinatorTask -NoStart:$didCutover
        Install-SupervisorTask
        if (-not $didCutover) { Confirm-CoordinatorRunning }
    } else {
        Install-CoordinatorTask
        Install-SupervisorTask
    }
    Write-Host ''; Write-Host '=== agent-dispatch update complete ===' -ForegroundColor Cyan
    } finally {
        Clear-UpdateMarker
    }
}

function Invoke-Start {
    # Mark the live-service start lifecycle as in-progress (ce#5066).
    Write-UpdateMarker
    try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Ok 'Coordinator started'
    } else {
        # Non-elevated host: no Scheduled Task was registrable, so the coordinator
        # runs from the detached fallback launcher. Start that rather than hard-
        # failing (the client is installed; #3602).
        $launcher = Join-Path $InstallDir 'serve-service.ps1'
        if (Test-Path $launcher) {
            Start-CoordinatorNonElevatedFallback -Launcher $launcher -Primary:((Get-ServiceMode) -eq 'interactive')
        } else {
            Write-Fail "No coordinator task or launcher installed -- run: install.ps1 -Action install"; exit 1
        }
    }
    # Start every supervisor that is enabled (label-gated). Disabled/inert
    # primary/profile supervisors are left alone.
    Invoke-SupervisorsStart
    Confirm-CoordinatorRunning
    } finally {
        Clear-UpdateMarker
    }
}

function Invoke-Stop {
    # Supervisor first (it spawns work), then the coordinator. Stop the Scheduled
    # Task AND terminate the detached process -- Stop-ScheduledTask alone leaves the
    # `conhost --headless`-detached python alive (#3602).
    Invoke-SupervisorsStop
    $killedSup = Retire-SupervisorProcesses
    if ($killedSup -gt 0) { Write-Ok "Embody supervisor stopped ($killedSup process(es))" }

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    $killedCoord = Stop-DispatchProcess -Subcommand serve
    if ($killedCoord -gt 0) {
        Write-Ok "Coordinator stopped ($killedCoord process(es) terminated; rendezvous cleared)"
    } else {
        Write-Skip 'No running coordinator process found'
    }
}

function Invoke-Status {
    Write-Host ''; Write-Host '=== agent-dispatch status ===' -ForegroundColor Cyan
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-Ok "Deployed: $($m.source.version) (source: $($m.source.kind))"
        } catch { Write-Skip 'Deploy manifest unreadable' }
    } else {
        Write-Skip 'No deploy manifest -- not installed?'
    }
    Write-Ok "Service mode: $(Get-ServiceMode)"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Ok "Coordinator task: $($task.State)"
    } else {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $auto = Get-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue
        if ($auto) {
            Write-Ok 'Coordinator: interactive logon auto-start (HKCU Run; no boot task)'
        } else {
            Write-Skip 'No coordinator task (client-only host)'
        }
    }
    Write-SupervisorsStatus
}

function Invoke-Uninstall {
    Write-Host ''; Write-Host '=== agent-dispatch uninstall ===' -ForegroundColor Cyan
    if ($DryRun) { Write-Host '(dry run -- nothing will be changed)' -ForegroundColor Yellow }
    Write-Host ''

    if ($DryRun) {
        Write-Host '[dry-run] would stop supervisors + retire supervisor processes'
        if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
            $supervisorTasks = @(Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue) +
                @(Get-ScheduledTask -TaskName "$SupervisorTaskName-*" -ErrorAction SilentlyContinue)
            foreach ($t in $supervisorTasks) { Write-Host "[dry-run] would remove supervisor task: $($t.TaskName)" }
        }
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        if (Get-ItemProperty -Path $runKey -Name $SupervisorTaskName -ErrorAction SilentlyContinue) {
            Write-Host "[dry-run] would remove supervisor autostart (HKCU Run): $SupervisorTaskName"
        }
    } else {
        Invoke-SupervisorsStop
        $retired = Retire-SupervisorProcesses
        if ($retired -gt 0) { Write-Ok "Embody supervisor stopped ($retired process(es))" }
        Remove-AllSupervisorTasks
        Write-Ok 'Embody supervisor tasks removed'
    }

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        if ($DryRun) {
            Write-Host "[dry-run] would remove scheduled task: $TaskName"
        } else {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            Write-Ok 'Coordinator task removed'
        }
    }

    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    if (Get-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue) {
        if ($DryRun) {
            Write-Host "[dry-run] would remove coordinator logon auto-start (HKCU Run): $TaskName"
        } elseif (Remove-CoordinatorAutostart) {
            Write-Ok 'Coordinator logon auto-start (HKCU Run) removed'
        }
    }

    if (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue) {
        $fwRule = 'agent-dispatch coordinator (WSL)'
        if (Get-NetFirewallRule -DisplayName $fwRule -ErrorAction SilentlyContinue) {
            if ($DryRun) {
                Write-Host "[dry-run] would remove firewall rule: $fwRule"
            } else {
                Remove-NetFirewallRule -DisplayName $fwRule -ErrorAction SilentlyContinue
                Write-Ok 'Coordinator firewall rule removed'
            }
        }
    }

    foreach ($n in @(
        'agent-dispatch.cmd', 'agent-dispatch.ps1', 'agent-dispatch',
        'agent-dispatch-board.cmd'
    )) {
        $p = Join-Path $LocalBin $n
        if (Test-Path $p) {
            if ($DryRun) { Write-Host "[dry-run] would remove binstub: $p" }
            else { Remove-Item $p -Force -ErrorAction SilentlyContinue }
        }
    }
    if (-not $DryRun) { Write-Ok 'Binstub removed' }

    $pivot = Join-Path $env:USERPROFILE '.agent-worktrees\pivots\agent-dispatch.json'
    if (Test-Path $pivot) {
        if ($DryRun) { Write-Host "[dry-run] would remove pivot: $pivot" }
        else { Remove-Item $pivot -Force -ErrorAction SilentlyContinue }
    }

    if ($Purge) {
        if (Test-Path $InstallDir) {
            if ($DryRun) { Write-Host "[dry-run] would PURGE (config + DB): $InstallDir" }
            else { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue; Write-Ok "Runtime purged: $InstallDir (config + DB deleted)" }
        }
    } else {
        # Remove the runtime venv. Versioned: the `.venv` link + the versions/
        # tree; otherwise the single real venv dir.
        if ($VersionedRuntime) {
            if ($DryRun) {
                if ((Test-VenvIsLink $LinkDir) -or (Test-Path $LinkDir)) { Write-Host "[dry-run] would remove: $LinkDir" }
                $verRoot = Join-Path $InstallDir 'versions'
                if (Test-Path $verRoot) { Write-Host "[dry-run] would remove: $verRoot" }
            } else {
                if (Test-VenvIsLink $LinkDir) { & cmd /c rmdir "$LinkDir" 2>$null }
                elseif (Test-Path $LinkDir) { Remove-Item -Recurse -Force $LinkDir -ErrorAction SilentlyContinue }
                $verRoot = Join-Path $InstallDir 'versions'
                if (Test-Path $verRoot) { Remove-Item -Recurse -Force $verRoot -ErrorAction SilentlyContinue }
            }
        } elseif (Test-Path $VenvDir) {
            if ($DryRun) { Write-Host "[dry-run] would remove venv: $VenvDir" }
            else { Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue }
        }
        if ($DryRun) {
            Write-Host "[dry-run] config + DB at $InstallDir would be kept (-Purge to delete)"
        } else {
            Write-Ok 'Venv removed (config + DB kept; -Purge to delete)'
        }
    }

    if ($DryRun) { Write-Host 'agent-dispatch uninstall dry run complete -- nothing was changed' -ForegroundColor Yellow }
}

switch ($Action) {
    'install'   { Invoke-Install }
    'update'    { Invoke-Update }
    'start'     { Invoke-Start }
    'stop'      { Invoke-Stop }
    'status'    { Invoke-Status }
    'uninstall' { Invoke-Uninstall }
    'stamp'     { Invoke-Stamp }
    'provision' { Invoke-Install }
}
exit 0
