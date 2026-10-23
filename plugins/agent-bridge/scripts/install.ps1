<#
.SYNOPSIS
    Agent Bridge -- plugin installer for Windows.

.DESCRIPTION
    Manages the agent-bridge service lifecycle: install, uninstall, start, stop,
    status, update.

    Runtime lives at ~/.agent-bridge/ (venv, config, DB, auth).
    Binstub goes to ~/.local/bin/agent-bridge.cmd.

    Run from the plugin directory or via the Copilot CLI plugin mechanism:
      pwsh -File plugins\agent-bridge\scripts\install.ps1 install
      pwsh -File plugins\agent-bridge\scripts\install.ps1 provision
      pwsh -File plugins\agent-bridge\scripts\install.ps1 status
      pwsh -File plugins\agent-bridge\scripts\install.ps1 update

    On first install, detects and migrates from a legacy project-service
    installer (services/agent-bridge/install.ps1) if present, preserving
    config, auth, and DB.

.PARAMETER Action
    Lifecycle action to perform.

.PARAMETER Purge
    On uninstall: also delete config, DB, and auth token.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'status', 'update', 'stamp', 'provision')]
    [string]$Action = 'status',

    [string]$InstallDir,

    [switch]$Purge,

    # Opt-in: run the daemon "whether the user is logged on or not" (a headless
    # boot-triggered S4U task) instead of the default at-logon task that only
    # runs while the user is interactively signed in. Useful for an always-on
    # workstation accessed over SSH/RDP with no persistent interactive session.
    # Can also be set via AGENT_BRIDGE_NONINTERACTIVE=1. Never forced; an
    # existing working non-interactive task is preserved across updates.
    [switch]$NonInteractive,

    # DEPRECATED / no-op (Thread B): the graceful ZDD cutover is now the DEFAULT on
    # `update` whenever a live daemon is running -- activation always cuts over
    # automatically (invariant #1), so this opt-in is no longer required. The switch
    # is still ACCEPTED (so existing callers, e.g. the launch-path reconciler, don't
    # break) but has no effect; it will be removed in a later cleanup.
    [switch]$ZeroDowntime,

    # Preview mode for the 'uninstall' action: print what WOULD be removed (or
    # preserved) without touching the filesystem, scheduled task, or registry.
    # No other action honors this switch.
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {
    if (-not [IO.File]::Exists((Join-Path $PSScriptRoot 'installer-human-output.ps1'))) {
        throw 'Required installer human-output initializer is unavailable.'
    }
    . (Join-Path $PSScriptRoot 'installer-human-output.ps1')
}
. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')

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
if (-not $env:COPILOT_PLUGIN_INSTALL_STAGED -or
    (($PSScriptRoot -replace '\\', '/') -match '/\.copilot/installed-plugins/')) {
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
                Set-Location -LiteralPath $env:USERPROFILE
                [System.IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
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
                $__selfStageArgs = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $__selfStagedEntry) + $__selfStageFwd |
                    ForEach-Object {
                        '"' + ([regex]::Replace(
                            [regex]::Replace([string]$_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1'
                        )) + '"'
                    }) -join ' '
                $__wdChild = Start-Process -FilePath $__selfStageExe -PassThru -NoNewWindow `
                    -WorkingDirectory $env:USERPROFILE `
                    -ArgumentList $__selfStageArgs
                # Windows PowerShell must retain the handle before waiting to preserve ExitCode.
                $null = $__wdChild.Handle
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
            } else {
                throw 'self-stage requires a plugin identity before leaving the payload'
            }
        }
    } catch {
        [Console]::Error.WriteLine("self-stage failed; refusing to run from the replaceable payload: $_")
        exit 1
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
            working_dir  = [System.IO.Directory]::GetCurrentDirectory()
            child_pid    = $PID
            grandchild_pid = $__smokeGrandPid
        } | ConvertTo-Json -Compress) | Set-Content -LiteralPath (Join-Path $__smokeHome 'smoke.json')
        Start-Sleep -Seconds $__smokeSleep
    } catch {}
    exit 0
}
# === end install-contract:v4 smoke seam ===

# -- Output helpers (PS5-safe) -----------------------------------------------

function Write-Ok   { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Step { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }

. (Join-Path $PSScriptRoot '..\..\..\libs\installer-engine\installer-engine.ps1')

Set-Item -Path Function:Ensure-UvShared -Value ${function:Ensure-Uv}
Set-Item -Path Function:Invoke-UvPipInstallResilientShared -Value ${function:Invoke-UvPipInstallResilient}
Set-Item -Path Function:Invoke-UvVenvResilientShared -Value ${function:Invoke-UvVenvResilient}
Set-Item -Path Function:New-SignedVenvShared -Value ${function:New-SignedVenv}
Set-Item -Path Function:Write-DeployManifestShared -Value ${function:Write-DeployManifest}

# Stable, documented contract (ThomasMichon/copilot-extensions#5066): a file
# containing a single epoch-seconds expiry, present for the duration of a
# live-service update/start lifecycle -- any downstream consumer (a local
# liveness watchdog, a diagnostic tool) can check its content without
# needing any plugin-specific caller-side wrapping. $UpdateMarker itself is
# set once $InstallDir is finalized below; these two functions only
# reference it at call time, so defining them here is safe.
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
# Windows -- `flock` has no equivalent here) makes increment/decrement
# atomic across processes.
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

# -- Paths -------------------------------------------------------------------

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$legacyInstallDir = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.agent-bridge'))
$InstallDir = if ($InstallDir) { $InstallDir } else { $legacyInstallDir }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$UpdateMarker = Join-Path $InstallDir 'update-in-progress'
$UpdateMarkerTtlDefault = 1200  # 20 min -- generous past any observed real cutover
$publishGlobalBinstubs = [StringComparer]::OrdinalIgnoreCase.Equals(
    $InstallDir,
    $legacyInstallDir
)
$serviceSuffix = if ($publishGlobalBinstubs) {
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
$env:AGENT_BRIDGE_INSTALL_DIR = $InstallDir
$env:AGENT_BRIDGE_CONFIG_DIR = $InstallDir
$env:AGENT_BRIDGE_CONNECT_LOG = Join-Path (Join-Path $InstallDir 'logs') 'connect.log'
$VenvDir    = Join-Path $InstallDir 'venv'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'
$BinstubCmd = Join-Path $LocalBin 'agent-bridge.cmd'
$BinstubPs1 = Join-Path $LocalBin 'agent-bridge.ps1'
$Binstub    = $BinstubPs1   # primary entry point (shown in summaries)
$PidFile    = Join-Path $InstallDir 'agent-bridge.pid'
$TaskName   = if ($publishGlobalBinstubs) { 'Agent Bridge' } else { "AgentBridge-$serviceSuffix" }
$ScheduledTaskHasNotRunResult = 267011  # 0x41303 / SCHED_S_TASK_HAS_NOT_RUN
$Port       = 9280
$RelayPort  = 9857   # integrated credential relay (in-process with the bridge)

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}

# === install-contract:v3 versioned-venv (agent-bridge: junction-free) ===
# Immutable per-version runtime (#581), JUNCTION-FREE. The venv ALWAYS builds into
# versions/<version> and the active version is published by a plain-text
# `current-version` marker (versioned_runtime.py) -- there is NO `venv` directory
# junction. The binstubs, scheduled-task launcher, and deploy manifest are pinned
# straight at the concrete slot python and REWRITTEN on every cutover, so nothing
# needs to traverse a reparse point (which RedirectionGuard blocks with WinError
# 448 on managed devices) and the legacy real-venv fork (COPILOT_EXT_NO_VERSIONED)
# is retired. A version bump builds a fresh slot beside the serving one (never
# mutates a live daemon's venv); rollback is "leave the marker on the previous
# slot"; gc prunes unreferenced slots.
#
# $VenvDir / $VenvPython -> the build+health-gate target (the versions/<v> slot).
# $LinkDir / $LinkPython -> the runtime-facing python the binstubs/task/manifest
#                           are pinned at. Junction-free, so these ARE the slot
#                           (Link == Venv); rewritten to the new slot on cutover.
$SrcVersion = $null
$SourceRuntimeIdentity = $null
$pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
if (Test-Path $pyprojForVer) {
    $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
    if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
}
if (-not $SrcVersion) {
    Write-Fail 'Cannot determine plugin version from pyproject.toml (required for the versioned runtime).'
    exit 1
}
$PackageVersion = $SrcVersion
$VersionedRuntime = $true
$VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
if ($env:OS -eq 'Windows_NT') { $VenvPython = Join-Path $VenvDir 'Scripts\python.exe' }
else { $VenvPython = Join-Path $VenvDir 'bin/python' }
$LinkDir    = $VenvDir
$LinkPython = $VenvPython
# === end install-contract:v3 versioned-venv ===

# -- Helpers -----------------------------------------------------------------

# === install-contract:v3 versioned-venv helpers (agent-bridge) ===
function Invoke-VersionedActivate {
    <# Publish this freshly-built slot as the active version (junction-free): the
       primitive writes the `current-version` marker and removes any stale legacy
       `venv` junction. Runs the stdlib-only primitive via the slot's own python.
       The caller MUST have already repointed the binstubs/task/manifest at the
       slot (and stopped/cut-over any daemon on the old slot). #>
    if (-not $VersionedRuntime) { return $true }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    & $py $vr --root $InstallDir --link-name 'venv' activate $SrcVersion --no-link 2>&1 |
        ForEach-Object { Write-Step $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to activate runtime version $SrcVersion (current-version marker)"
        return $false
    }
    Write-Ok "Runtime version $SrcVersion active (current-version -> $SrcVersion)"
    return $true
}

function Get-BootstrapPython {
    <# A python to run the stdlib-only versioned_runtime.py helper BEFORE the
       slot venv exists (e.g. the pre-build toss). Prefers a signed base python,
       then python3/python on PATH. Returns $null if none found. #>
    $sb = Get-SignedBasePython
    if ($sb) { return $sb }
    foreach ($cand in 'python3', 'python') {
        $c = Get-Command $cand -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1
        if ($c) { return $c.Source }
    }
    return $null
}

function Get-SourceRuntimeIdentity {
    param(
        [string]$Payload = $PluginDir,
        [string]$ExpectedVersion = '',
        [string]$FailureContext = 'Source runtime identity validation failed'
    )
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source runtime validation requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
        '--payload', $Payload, '--package-version', $PackageVersion,
        '--plugin', 'agent-bridge', '--format', 'json')
    if ($ExpectedVersion) { $arguments += @('--expected-runtime-version', $ExpectedVersion) }
    $nativeError = ''
    $detail = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "${FailureContext} (exit ${code}): $detail$nativeError" }
    return $detail.Trim()
}

function Initialize-SourceRuntime {
    $metadata = Join-Path $PluginDir 'runtime-source.json'
    try {
        [System.IO.File]::GetAttributes($metadata) | Out-Null
    } catch [System.IO.FileNotFoundException] {
        return
    } catch [System.IO.DirectoryNotFoundException] {
        return
    }
    $script:SourceRuntimeIdentity = Get-SourceRuntimeIdentity
    $script:SrcVersion = ($SourceRuntimeIdentity | ConvertFrom-Json).runtime_version
    $script:VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    $script:VenvPython = if ($env:OS -eq 'Windows_NT') {
        Join-Path $VenvDir 'Scripts\python.exe'
    } else {
        Join-Path $VenvDir 'bin/python'
    }
    $script:LinkDir = $VenvDir
    $script:LinkPython = $VenvPython
}

function Remove-PluginBuildArtifacts {
    param([string]$SourceDir = '')

    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
        (Join-Path $PluginDir 'build'), `
        (Join-Path $PluginDir '*.egg-info'), `
        (Join-Path (Join-Path $PluginDir 'src') '*.egg-info')
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
    if ($SourceDir -and $SourceDir -ne $PluginDir) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
            (Join-Path $SourceDir 'build'), `
            (Join-Path $SourceDir '*.egg-info'), `
            (Join-Path (Join-Path $SourceDir 'src') '*.egg-info')
    }
}

function Invoke-UvPipInstallResilient {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$SourceDir = ''
    )

    $result = Invoke-UvPipInstallResilientShared -Arguments $Arguments `
        -BeforeAttempt { Remove-PluginBuildArtifacts -SourceDir $SourceDir }
    if ($result.ExitCode -eq 0) { Remove-PluginBuildArtifacts -SourceDir $SourceDir }
    return $result
}

function Invoke-UvVenvResilient {
    param(
        [Parameter(Mandatory)][string]$VenvDir,
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$UvCommand = 'uv'
    )

    return (Invoke-UvVenvResilientShared -VenvDir $VenvDir -Arguments $Arguments -UvCommand $UvCommand)
}

function Ensure-Uv {
    if (-not ($env:AGENT_BRIDGE_UV_BOOTSTRAP_URL -or $env:AGENT_BRIDGE_UV_BOOTSTRAP_SHA256)) {
        return [bool](Ensure-UvShared -InstallRoot $InstallDir -ToolDirectory 'tool' -AcquireIfMissing $true)
    }

    $existing = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($existing) {
        $result = Invoke-NativeCapture { & $existing.Source --version }
        if ($result.ExitCode -eq 0) { return $true }
    }

    $toolDir = Join-Path $InstallDir 'tool'
    $uvPath = Join-Path $toolDir 'uv.exe'
    if (Test-Path -LiteralPath $uvPath) {
        $result = Invoke-NativeCapture { & $uvPath --version }
        if ($result.ExitCode -eq 0) {
            $env:PATH = "$toolDir;$env:PATH"
            return $true
        }
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $toolDir 'uvx.exe') `
            -Force -ErrorAction SilentlyContinue
    }

    $arch = if ($env:PROCESSOR_ARCHITEW6432) {
        $env:PROCESSOR_ARCHITEW6432
    } else {
        $env:PROCESSOR_ARCHITECTURE
    }
    $uvVersion = '0.12.6'
    if ($arch -eq 'AMD64') {
        $asset = 'uv-x86_64-pc-windows-msvc.zip'
        $expectedSha256 = 'df7cb9f243eae1621400d4fcf5b1b3d90f20e264ece91b64deb3b0078abca6ef'
    } elseif ($arch -eq 'ARM64') {
        $asset = 'uv-aarch64-pc-windows-msvc.zip'
        $expectedSha256 = '6dda514fbbe3152d980758e0f6347116060114d7d24932fc0ea5d8063f8b253a'
    } else {
        Write-Fail "uv bootstrap does not support Windows architecture: $arch"
        return $false
    }
    if ($env:AGENT_BRIDGE_UV_BOOTSTRAP_SHA256) {
        if ($env:AGENT_BRIDGE_UV_BOOTSTRAP_SHA256 -notmatch '^[0-9A-Fa-f]{64}$') {
            Write-Fail 'AGENT_BRIDGE_UV_BOOTSTRAP_SHA256 must be a 64-character hexadecimal SHA-256 digest.'
            return $false
        }
        $expectedSha256 = $env:AGENT_BRIDGE_UV_BOOTSTRAP_SHA256.ToLowerInvariant()
    }

    New-Item -ItemType Directory -Path $toolDir -Force | Out-Null
    $urlTemplate = $env:AGENT_BRIDGE_UV_BOOTSTRAP_URL
    if (-not $urlTemplate) {
        $urlTemplate = "https://github.com/astral-sh/uv/releases/download/$uvVersion/{asset}"
    }
    $url = $urlTemplate.Replace('{asset}', $asset)
    $archive = [IO.Path]::GetTempFileName()
    $staging = Join-Path $InstallDir ".uv-stage-$PID"
    $previousSecurityProtocol = [Net.ServicePointManager]::SecurityProtocol
    $client = $null
    try {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force `
                -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $client = New-Object Net.WebClient
        $client.Headers['User-Agent'] = 'agent-bridge-bootstrap'
        $client.DownloadFile($url, $archive)
        $archiveStream = [IO.File]::OpenRead($archive)
        try {
            $sha256 = [Security.Cryptography.SHA256]::Create()
            try {
                $digest = $sha256.ComputeHash($archiveStream)
                $actualSha256 = ([BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
            } finally {
                $sha256.Dispose()
            }
        } finally {
            $archiveStream.Dispose()
        }
        if ($actualSha256 -ne $expectedSha256) {
            throw "uv archive SHA-256 mismatch for $asset (expected $expectedSha256, got $actualSha256)"
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $staging)
        $uvSource = Get-ChildItem -LiteralPath $staging -Recurse -File -Filter 'uv.exe' |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $uvSource) { throw 'uv.exe was absent from the release archive' }
        $uvxSource = Get-ChildItem -LiteralPath $staging -Recurse -File -Filter 'uvx.exe' |
            Select-Object -First 1 -ExpandProperty FullName
        if ($uvxSource) {
            Move-Item -LiteralPath $uvxSource `
                -Destination (Join-Path $toolDir 'uvx.exe') -Force
        }
        Move-Item -LiteralPath $uvSource -Destination $uvPath -Force
    } catch {
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $toolDir 'uvx.exe') `
            -Force -ErrorAction SilentlyContinue
        Write-Fail "Failed to vendor uv from $url`: $_"
        Write-Fail 'Retry the installer, or install uv from https://docs.astral.sh/uv/getting-started/installation/.'
        return $false
    } finally {
        if ($client) { $client.Dispose() }
        [Net.ServicePointManager]::SecurityProtocol = $previousSecurityProtocol
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    $result = Invoke-NativeCapture { & $uvPath --version }
    if ($result.ExitCode -ne 0) {
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $toolDir 'uvx.exe') `
            -Force -ErrorAction SilentlyContinue
        Write-Fail "Vendored uv is not executable: $($result.Output)"
        Write-Fail 'Retry the installer, or install uv from https://docs.astral.sh/uv/getting-started/installation/.'
        return $false
    }
    $env:PATH = "$toolDir;$env:PATH"
    Write-Ok "Vendored uv into $toolDir"
    return $true
}

function Get-UvSourcePaths {
    <# The [tool.uv.sources] `path = "..."` dependencies one pyproject.toml
       declares, resolved relative to that project's own directory (the same
       resolution uv applies when it installs the project). Only directories
       that hold a pyproject.toml are returned. #>
    param([Parameter(Mandatory)][string]$PyprojectPath)
    $text = Get-Content -LiteralPath $PyprojectPath -Raw
    $table = [regex]::Match($text, '(?ms)^\[tool\.uv\.sources\](.*?)(?=^\[|\z)')
    if (-not $table.Success) { return @() }
    $base = Split-Path -Parent $PyprojectPath
    $out = @()
    foreach ($m in [regex]::Matches($table.Groups[1].Value, 'path\s*=\s*"([^"]*)"')) {
        $dir = Join-Path $base $m.Groups[1].Value
        if (Test-Path -LiteralPath (Join-Path $dir 'pyproject.toml')) {
            $out += (Resolve-Path -LiteralPath $dir).Path.TrimEnd('\', '/')
        }
    }
    return $out
}

function Get-PayloadLibRoots {
    <# Every shared-library directory OUTSIDE $PluginDir that this installer
       installs into the runtime slot: the libraries it installs explicitly
       (resolved through Resolve-VendoredLib, exactly as the build does) plus
       the transitive closure of [tool.uv.sources] path dependencies of the
       plugin and of those libraries (resolved live by `uv pip install`, e.g.
       "../../libs/<lib>" in a checkout). Directories under $PluginDir (a
       materialized/vendored libs/ copy) are already covered by the plugin
       scan in Get-PayloadHash and are not repeated here. #>
    $pluginReal = (Resolve-Path -LiteralPath $PluginDir).Path.TrimEnd('\', '/')
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @('ssh-manager', 'credential-relay', 'zdd', 'single-instance-lease', 'config-migrate')) {
        $dir = Resolve-VendoredLib -LibName $name
        if ($dir) { $candidates.Add(("$dir").Trim()) }
    }
    $pending = [System.Collections.Generic.List[string]]::new()
    $pending.Add((Join-Path $pluginReal 'pyproject.toml'))
    $localLibs = Join-Path $pluginReal 'libs'
    if (Test-Path -LiteralPath $localLibs) {
        Get-ChildItem -LiteralPath $localLibs -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { $pending.Add((Join-Path $_.FullName 'pyproject.toml')) }
    }
    foreach ($dir in @($candidates)) { $pending.Add((Join-Path $dir 'pyproject.toml')) }
    $visited = @{}
    for ($i = 0; $i -lt $pending.Count; $i++) {
        $pp = $pending[$i]
        if (-not (Test-Path -LiteralPath $pp)) { continue }
        $key = (Resolve-Path -LiteralPath $pp).Path.ToLowerInvariant()
        if ($visited.ContainsKey($key)) { continue }
        $visited[$key] = $true
        foreach ($dir in @(Get-UvSourcePaths -PyprojectPath $pp)) {
            $candidates.Add($dir)
            $pending.Add((Join-Path $dir 'pyproject.toml'))
        }
    }
    $seen = @{}
    $roots = [System.Collections.Generic.List[string]]::new()
    $sep = [System.IO.Path]::DirectorySeparatorChar
    foreach ($dir in $candidates) {
        $real = (Resolve-Path -LiteralPath $dir).Path.TrimEnd('\', '/')
        if ($real.Equals($pluginReal, [StringComparison]::OrdinalIgnoreCase) -or
            $real.StartsWith("$pluginReal$sep", [StringComparison]::OrdinalIgnoreCase)) { continue }
        $key = $real.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $roots.Add($real)
    }
    return $roots.ToArray()
}

function Get-PayloadHash {
    <# Content fingerprint of EVERY source this installer installs into the
       runtime slot (#935/#776/ce#811, #5472): pyproject.toml, src/, the
       plugin-local libs/, and each shared library resolved outside $PluginDir
       (Get-PayloadLibRoots). sha256 over the sorted "<label>:<per-file sha256>"
       list, where a plugin file's label is its $PluginDir-relative path and an
       external library's files are labeled "libs/<lib>/<relpath>" so the value
       does not depend on where the checkout lives. Caches and build artifacts
       (which the build itself writes into these trees) and each library's own
       top-level tests/ (never installed) are excluded, so building or testing
       never changes the value.

       A completed slot is reused only when this value matches its marker, so
       it is computed ONCE per transaction, before admission, and that same
       value is carried to admission, the completion marker and the deploy
       manifest. After a build it is recomputed only to VERIFY the sources did
       not change mid-build (Test-PayloadUnchangedSinceAdmission); a mismatch
       fails the install instead of publishing either value. Never throws ->
       '' on error (admission then fails closed for a completed slot). #>
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $base = (Resolve-Path -LiteralPath $PluginDir).Path.TrimEnd('\', '/')
        $excluded = '/(__pycache__|\.venv|venv|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.git|node_modules|build|dist|[^/]+\.egg-info)/'
        # (directory, label prefix, pattern of never-installed test trees)
        $sources = [System.Collections.Generic.List[object]]::new()
        $sources.Add(@((Join-Path $base 'src'), 'src', $null))
        $sources.Add(@((Join-Path $base 'libs'), 'libs', '^[^/]+/tests/'))
        foreach ($lib in @(Get-PayloadLibRoots)) {
            $sources.Add(@($lib, ('libs/' + (Split-Path -Leaf $lib)), '^tests/'))
        }
        $entries = [System.Collections.Generic.List[string]]::new()
        # pyproject.toml plus its declared `readme`, which setuptools embeds in
        # the installed distribution metadata.
        foreach ($meta in @('pyproject.toml', 'README.md', 'runtime-source.json')) {
            $mp = Join-Path $base $meta
            if (Test-Path -LiteralPath $mp) {
                $fh = [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($mp))).Replace('-', '').ToLower()
                $entries.Add("${meta}:${fh}")
            }
        }
        foreach ($source in $sources) {
            $dir, $label, $testsPattern = $source
            if (-not (Test-Path -LiteralPath $dir)) { continue }
            $dirReal = (Resolve-Path -LiteralPath $dir).Path.TrimEnd('\', '/')
            Get-ChildItem -LiteralPath $dirReal -Recurse -File -Force -ErrorAction Stop | ForEach-Object {
                $rel = $_.FullName.Substring($dirReal.Length).TrimStart('\', '/') -replace '\\', '/'
                if ("/$rel" -match $excluded -or $_.Extension -eq '.pyc') { return }
                if ($testsPattern -and $rel -match $testsPattern) { return }
                $fh = [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($_.FullName))).Replace('-', '').ToLower()
                $entries.Add("${label}/${rel}:${fh}")
            }
        }
        # Ordinal sort: Sort-Object's culture-aware order differs between
        # Windows PowerShell 5.1 and PowerShell 7, and either host may run
        # this installer against the same slot.
        $sorted = $entries.ToArray()
        [Array]::Sort($sorted, [StringComparer]::Ordinal)
        $joined = [string]::Join("`n", $sorted)
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))
        return (-join ($bytes | ForEach-Object { $_.ToString('x2') }))
    } catch { return '' }
}

function Test-PayloadUnchangedSinceAdmission {
    <# Post-build check that the slot holds the bytes admission hashed: the
       build reads the sources AFTER $AdmittedHash was taken, so an edit
       landing in between would put new bytes in the slot under a marker (and
       deploy manifest) naming the old hash, and a later install of the old
       sources would wrongly reuse it. Recomputes Get-PayloadHash and returns
       $false (after reporting it) when it differs; the caller must then fail
       without publishing the marker or manifest, or activating. Nothing to
       verify when no slot was built ('reuse') or no hash was admitted. #>
    param([string]$AdmittedHash = '', [string]$Admission = '', [switch]$SourceIdentityOnly)
    $selectedIdentity = Get-Variable -Name SourceRuntimeIdentity -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    if ($selectedIdentity -or (Test-Path -LiteralPath (Join-Path $PluginDir 'runtime-source.json'))) {
        $identity = Get-SourceRuntimeIdentity -ExpectedVersion $SrcVersion
        if ($identity -ne $selectedIdentity) {
            throw 'Source runtime identity changed after runtime selection; refusing publication'
        }
    }
    if ($SourceIdentityOnly) { return $true }
    if ($Admission -eq 'reuse' -or -not $AdmittedHash) { return $true }
    $fresh = Get-PayloadHash
    if ($fresh -eq $AdmittedHash) { return $true }
    $now = if ($fresh) { $fresh } else { 'unavailable' }
    Write-Fail "Install sources changed during the build (payload hash $AdmittedHash at admission, $now now); refusing to publish runtime slot versions/$SrcVersion -- re-run the install"
    return $false
}

function Invoke-VersionedSlotClean {
    param([string]$PayloadHash = '')
    if (-not $VersionedRuntime) { throw 'Construction requires a versioned runtime candidate' }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Construction requires a bootstrap Python interpreter' }
    if (-not (Get-Command Start-RuntimeConstruction -ErrorAction SilentlyContinue)) {
        $helpers = (& $py -I $vr --root $InstallDir construction-shell --shell ps1 | Out-String)
        if ($LASTEXITCODE -ne 0) { throw 'Canonical construction adapter is unavailable' }
        Invoke-Expression $helpers
    }
    $script:RuntimeConstructionReuse = Start-RuntimeConstruction `
        -Python $py -Helper $vr -Root $InstallDir -Version $SrcVersion `
        -PayloadHash $PayloadHash -LinkName (Split-Path -Leaf $LinkDir)
    return $true
}

function Invoke-VersionedMarkComplete {
    param([string]$PayloadHash = '')
    $actualHash = Get-PayloadHash
    if ($PayloadHash -and $PayloadHash -ne $actualHash) {
        throw 'Payload changed during construction; completion refused'
    }
    Complete-RuntimeConstruction -PayloadHash $actualHash
    return $true
}

function Get-VersionedCurrent {
    <# The version the `venv` link currently points at (empty for a legacy real
       venv or a fresh box). Used to remember the previous-good slot as a gc
       keep + rollback target. #>
    if (-not $VersionedRuntime) { return '' }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return '' }
    # versioned_runtime.py routinely writes a "no pyvenv.cfg" notice to stderr
    # when the 'venv' link is absent/legacy; under $ErrorActionPreference='Stop'
    # that becomes a terminating error even with a 2>$null redirect.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $py $vr --root $InstallDir --link-name 'venv' current 2>$null
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    return ("$out").Trim()
}

function Test-SlotContentCurrent {
    <# Strict same-content idempotency gate (ce#776/#777). Returns $true iff the
       versioned layout is active AND the target version ($SrcVersion) is ALREADY
       the current active slot AND that slot carries a valid completion marker
       whose recorded payload hash matches the payload we would install.

       When true, a (re)install/update MUST be a no-op: the target slot IS the
       live venv a running daemon pins, so rebuilding its byte-identical content
       in place would overwrite python.exe / site-packages mid-flight -- the
       `FileNotFoundError [WinError 2]` crash cascade (dotfiles#1612). Idempotency
       is keyed on CONTENT (the payload hash), not the mutable version label, so a
       reused dev label with changed content still fails the gate and rebuilds.

       The content decision is Get-SlotAdmission's 'reuse' verdict, never a
       separate marker probe: when the payload hash cannot be computed the
       shortcut is NOT taken, so the caller reaches the fail-closed admission
       refusal ('unverified') instead of accepting any valid marker.

       Fail-safe: any error (no python, helper missing, unparseable) returns
       $false, so the installer falls through to the admission check and a
       genuine install is never skipped. A deliberate force-rebuild remains a
       dedicated command (`versioned_runtime toss` + re-install / `service
       restart`), never a silent side effect of a routine same-version reconcile.
       $PayloadHash is the caller's once-computed Get-PayloadHash value. #>
    param([string]$PayloadHash = '')
    if (-not $VersionedRuntime) { return $false }
    try {
        $vr = Join-Path $ScriptDir 'versioned_runtime.py'
        # Prefer the slot's OWN python (an already-installed runtime always has it)
        # and fall back to a bootstrap python only when no slot exists yet -- a
        # box that only has the venv python would otherwise get $null from
        # Get-BootstrapPython and never no-op, defeating the gate (ce#788 review).
        # versioned_runtime is stdlib-only, so any python runs it.
        $py = if (Test-Path $LinkPython) { $LinkPython }
              elseif (Test-Path $VenvPython) { $VenvPython }
              else { Get-BootstrapPython }
        if (-not $py -or -not (Test-Path $vr)) { return $false }
        $cur = (& $py $vr --root $InstallDir --link-name 'venv' current 2>$null | Out-String).Trim()
        if (-not $cur -or $cur -ne $SrcVersion) { return $false }
        return ((Get-SlotAdmission -PayloadHash $PayloadHash) -eq 'reuse')
    } catch { return $false }
}

function Get-SlotAdmission {
    <# Classify the target numbered slot (versions/$SrcVersion) through the
       shared versioned_runtime `check-admission` primitive, BEFORE anything
       drains the daemon or touches the slot (#5472). Returns:
         'construct'              -- absent, or no completion marker: build as before
         'reuse'                  -- published with exactly this payload
         'content-conflict'       -- published with DIFFERENT payload content
         'health-repair-required' -- published evidence is malformed
         'unverified'             -- a completion marker exists but the payload
                                     hash / admission could not be computed
       Only 'construct' and 'reuse' may proceed: a published slot is immutable,
       so new content needs a new version, never an in-place rebuild. A helper
       failure with no completion marker on disk stays 'construct' so an
       incomplete slot is still rebuilt exactly as before. $PayloadHash is the
       caller's once-computed Get-PayloadHash value (empty = unavailable), the
       same value later published by Invoke-VersionedMarkComplete. #>
    param([string]$PayloadHash = '')
    if (-not $VersionedRuntime) { return 'construct' }
    $markerPresent = Test-Path -LiteralPath (Join-Path $VenvDir '.install-complete.json')
    $fallback = if ($markerPresent) { 'unverified' } else { 'construct' }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $ph = $PayloadHash
    if (-not $ph -or -not (Test-Path -LiteralPath $vr)) { return $fallback }
    $candidates = @()
    if (Test-Path -LiteralPath $VenvPython) { $candidates += $VenvPython }
    $bp = Get-BootstrapPython
    if ($bp) { $candidates += $bp }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        foreach ($py in $candidates) {
            try {
                $out = & $py $vr --root $InstallDir --link-name 'venv' check-admission $SrcVersion --payload-hash $ph 2>$null
                if ($LASTEXITCODE -eq 0) {
                    $decision = ("$out").Trim()
                    if ($decision) { return $decision }
                }
            } catch { }
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    return $fallback
}

function Write-SlotAdmissionRefusal {
    <# The actionable refusal for a published same-version slot (#5472). There
       is no override (matching the other runtime installers): repair or new
       content always requires a distinct, newly versioned generation. #>
    param([string]$Admission)
    if ($Admission -eq 'content-conflict') {
        Write-Fail "Runtime slot versions/$SrcVersion is already complete for different payload content; bump the plugin version instead of rebuilding an immutable slot in place"
    } else {
        Write-Fail "Runtime slot versions/$SrcVersion carries completion evidence that could not be verified ($Admission); bump the plugin version instead of rebuilding an immutable slot in place (repair requires a distinct generation)"
    }
    Write-Fail 'Refused before draining, stopping, or rebuilding anything; the running agent-bridge service was left untouched.'
}

$script:InstallLockHandle = $null

function Enter-InstallLock {
    <# Cross-process install serialization (ce#776/#777 follow-up: the "overlapping
       installers rebuild the venv over each other" footgun -- e.g. a same-version
       reconcile racing a manual/other-triggered install produced a live slot
       momentarily missing PyYAML, `ModuleNotFoundError: yaml`, dotfiles#1612).
       Opens $InstallDir/.install.lock with EXCLUSIVE (FileShare.None) access: a
       second installer's open throws a sharing violation until the holder
       releases. The OS drops the handle when the holding process dies, so there
       is NO stale-lock class (no PID reaping). Retries up to $TimeoutSec; returns
       $true when acquired (handle in $script:InstallLockHandle), $false when
       another install held it the whole window -- the caller then DEFERS (the
       in-flight install may land the version, but this run reports "blocked by
       lock" (exit 75), never success). A non-contention fault fails closed: it
       exits 1 rather than ever installing without the lock. #>
    param([int]$TimeoutSec = 300)
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    $lockPath = Join-Path $InstallDir '.install.lock'
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        try {
            $fs = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate,
                    [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            try {
                $stamp = [System.Text.Encoding]::UTF8.GetBytes("pid=$PID at=$((Get-Date).ToUniversalTime().ToString('o'))")
                $fs.SetLength(0); $fs.Write($stamp, 0, $stamp.Length); $fs.Flush()
            } catch { }
            $script:InstallLockHandle = $fs
            return $true
        } catch [System.IO.IOException] {
            # Only a sharing/lock violation means "another install holds it" ->
            # retry until the deadline: ERROR_SHARING_VIOLATION/ERROR_LOCK_VIOLATION
            # (32/33) on Windows, EWOULDBLOCK from the FileShare.None lock on
            # Linux (11) and macOS (35). Any OTHER IOException (disk full, FS
            # error) is a real fault: fail closed (exit 1) rather than masking it
            # as contention and spinning silently, and never install unlocked.
            $code = $_.Exception.HResult -band 0xFFFF
            $runtimeInfo = [System.Runtime.InteropServices.RuntimeInformation]
            $osPlatform = [System.Runtime.InteropServices.OSPlatform]
            $contention = if ($env:OS -eq 'Windows_NT') { $code -eq 32 -or $code -eq 33 } else {
                ($runtimeInfo::IsOSPlatform($osPlatform::Linux) -and $code -eq 11) -or
                ($runtimeInfo::IsOSPlatform($osPlatform::OSX) -and $code -eq 35)
            }
            if ($contention) {
                if ((Get-Date) -ge $deadline) { return $false }
                Start-Sleep -Milliseconds 750
            } else {
                Write-Fail "Install-lock IO error (not contention): $($_.Exception.Message) -- refusing to proceed unlocked."
                exit 1
            }
        } catch {
            Write-Fail "Install-lock error: $($_.Exception.Message) -- refusing to proceed unlocked."
            exit 1   # a lock fault fails closed: never install unlocked
        }
    }
}

function Exit-InstallLock {
    <# Release the install lock (also released by the OS on process exit, so an
       `exit`/crash never leaves a stale lock). #>
    if ($script:InstallLockHandle) {
        try { $script:InstallLockHandle.Close(); $script:InstallLockHandle.Dispose() } catch { }
        $script:InstallLockHandle = $null
    }
}

function Invoke-VersionedGc {
    <# Prune old version slots, keeping current + the given previous-good +
       any live-pid-pinned slot. No-op in legacy mode. Best-effort (warns only). #>
    param([string]$KeepPrev)
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return }
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', 'venv', 'gc', '--protect-pids')
    if ($KeepPrev) { $gcArgs += @('--keep', $KeepPrev) }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $py @gcArgs 2>&1 | ForEach-Object { Write-Step "gc: $_" }
    $ErrorActionPreference = $prevEAP
}
# === end install-contract:v3 versioned-venv helpers ===

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

# Resolve a vendored library path (libs\<LibName>) across multiple layouts.
# Returns the path string, or $null if not found.
function Resolve-VendoredLib {
    param([Parameter(Mandatory)][string]$LibName)
    # 1. Vendored inside agent-bridge (marketplace install layout)
    $candidate = Join-Path $PluginDir "libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    # 2. Relative path (git checkout layout)
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

# Resolve the ssh-manager / credential-relay vendored libs (thin wrappers).
function Resolve-SshManager { return (Resolve-VendoredLib -LibName 'ssh-manager') }
function Resolve-CredentialRelay { return (Resolve-VendoredLib -LibName 'credential-relay') }
# zero-downtime cutover primitives (module ``zdd``), extracted from this plugin.
function Resolve-Zdd { return (Resolve-VendoredLib -LibName 'zdd') }
# single-instance lease + supersession self-retire + reconcile-set reaper
# (module ``single_instance_lease``), extracted from this plugin.
function Resolve-SingleInstanceLease { return (Resolve-VendoredLib -LibName 'single-instance-lease') }
# config schema versioning + migration (module ``config_migrate``).
function Resolve-ConfigMigrate { return (Resolve-VendoredLib -LibName 'config-migrate') }

# Check if ssh-manager is already importable in the venv.
function Test-SshManagerInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from ssh_manager import SSHProfileSource, get_default_manager' 2>$null
    return $LASTEXITCODE -eq 0
}

# Check if credential-relay is already importable in the venv.
function Test-CredentialRelayInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from credential_relay import RelayBuilder' 2>$null
    return $LASTEXITCODE -eq 0
}

# Check if the zdd cutover lib is already importable in the venv.
function Test-ZddInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from zdd.cutover import CutoverOrchestrator' 2>$null
    return $LASTEXITCODE -eq 0
}

# Check if the single-instance-lease lib is already importable in the venv.
function Test-SingleInstanceLeaseInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from single_instance_lease import SingleInstance' 2>$null
    return $LASTEXITCODE -eq 0
}

# Check if config-migrate is already importable in the venv.
function Test-ConfigMigrateInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from config_migrate import migrate_file' 2>$null
    return $LASTEXITCODE -eq 0
}

function Get-SignedBasePython {
    <# Return a SAC-trusted (Authenticode-signed) base Python (>=3.10), or $null.
       Smart App Control blocks the unsigned uv-managed Python and console-script
       trampoline; a venv built from a signed base with `--copies` has a signed
       python.exe that SAC allows. #>
    if ($env:OS -ne 'Windows_NT') { return $null }
    $cached = Get-Variable -Name BridgeSignedBasePython -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    if ($cached -and (Test-Path -LiteralPath $cached)) {
        try {
            if ((Get-AuthenticodeSignature $cached).Status -eq 'Valid') { return $cached }
        } catch { Write-Verbose "Cached Python signature probe failed: $_" }
    }
    $script:BridgeSignedBasePython = $null
    $seen = @{}
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $launcher = (Get-Command py -ErrorAction Stop).Source
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            $probeError = ''
            $output = Invoke-Hidden -FilePath $launcher -ArgList @("-$v", '-c', 'import sys;print(sys.executable)') -ErrorOutput ([ref]$probeError)
            if ($LASTEXITCODE -eq 0 -and $output) {
                $candidate = $output.Trim()
                if ($seen.ContainsKey($candidate)) { continue }
                $seen[$candidate] = $true
                if (Test-Path -LiteralPath $candidate) {
                    try {
                        if ((Get-AuthenticodeSignature $candidate).Status -eq 'Valid') {
                            $script:BridgeSignedBasePython = $candidate
                            return $candidate
                        }
                    } catch { Write-Verbose "Python signature probe failed: $_" }
                }
            } elseif ($probeError) {
                Write-Verbose $probeError
            }
        }
    }
    return $null
}

function New-SignedVenv {
    return [bool](New-SignedVenvShared `
        -VenvDir $VenvDir `
        -VenvPython $VenvPython `
        -PythonVersion '3.10' `
        -UvCommand 'uv' `
        -RequireSignedBase ($env:OS -eq 'Windows_NT') `
        -AllowExisting $true)
}

# #1643: venue providers (agent-codespaces / agent-containers) are PURE
# providers.d markers -- the daemon drives their binstubs over a PROCESS BOUNDARY
# (the `agent-<sibling> namespace-* / relay-profile / relay-launch-env` CLIs, run
# from each sibling's OWN immutable venv) and NEVER imports a provider package. So
# a provider package inside the bridge venv is ALWAYS a stale vendored leftover
# (retired #892 vendoring model) that causes version skew and breaks dispatch
# (#1631/#1643). This actively prunes any such copy and GUARDS against one
# lingering, so the bridge venv stays provider-free.
# Sibling CLI binstubs remain owned by their own installers (~/.agent-<sibling>).
function Install-SiblingPlugins {
    param(
        [switch]$Reinstall
    )
    Write-Step "Sibling plugins not vendored -- process-boundary CLI seams (#892/#1643)"
    Remove-VendoredProviders
}

# Prune any stale venue-provider package from EVERY agent-bridge venv and fail if
# one is still importable afterward (the #1643 guard). Two venvs can carry a stale
# copy: the runtime slot being built (``$VenvPython`` = versions/<v>, still
# unpublished) and the retired legacy top-level ``~/.agent-bridge/venv`` (the
# install-contract-v3 leftover that agent-codespaces' old installer vendored
# into). We prune both: uv-uninstall first (clean dist-info removal), then
# belt-and-suspenders remove any raw-copied dirs, then probe importability from
# each venv's OWN interpreter. A ``venv`` junction/symlink is not a legacy venv:
# it targets an already-published slot, which is create-once (#5472) and must
# never be rewritten; activation removes the stale link instead.
function Remove-VendoredProviders {
    $modules = @('agent_codespaces', 'agent_containers')
    $pkgs    = @('agent-codespaces', 'agent-containers')
    $legacyPython = if ($env:OS -eq 'Windows_NT') {
        Join-Path $InstallDir 'venv\Scripts\python.exe'  # runtime-resolution: allow legacy-venv cleanup (prune, not launch)
    } else {
        Join-Path $InstallDir 'venv/bin/python'  # runtime-resolution: allow legacy-venv cleanup (prune, not launch)
    }
    $legacyItem = Get-Item -LiteralPath (Join-Path $InstallDir 'venv') -Force -ErrorAction SilentlyContinue
    if ($legacyItem -and ($legacyItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        $legacyPython = $null
    }
    $targets = @($VenvPython, $legacyPython) |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
    if (-not $targets) {
        Write-Step "No bridge venv yet -- nothing to prune (#1643)"
        return
    }
    foreach ($py in $targets) {
        # uv writes routine progress to stderr; under $ErrorActionPreference='Stop',
        # a 2>&1 redirect turns that into a terminating error, so relax it here.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            foreach ($pkg in $pkgs) {
                & uv pip uninstall --python $py $pkg 2>&1 | Out-Null
            }
        } finally {
            $ErrorActionPreference = $prevEAP
        }
        # Belt-and-suspenders: drop any raw-copied package dir / dist-info left
        # behind by the retired Install-PackageInto vendoring (no uv metadata).
        $purelib = (& $py -c "import sysconfig; print(sysconfig.get_paths()['purelib'])" 2>$null)
        if ($purelib -and (Test-Path $purelib)) {
            foreach ($mod in $modules) {
                $d = Join-Path $purelib $mod
                if (Test-Path $d) { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue }
                Get-ChildItem -Path $purelib -Filter "$($mod.Replace('_','?'))-*.dist-info" -Directory -ErrorAction SilentlyContinue |
                    ForEach-Object { Remove-Item -Recurse -Force $_.FullName -ErrorAction SilentlyContinue }
            }
        }
        # Guard: this venv's interpreter MUST NOT import a provider.
        $probe = "import importlib.util,sys; bad=[m for m in ['agent_codespaces','agent_containers'] if importlib.util.find_spec(m)]; sys.stdout.write(','.join(bad)); sys.exit(1 if bad else 0)"
        $leak = & $py -c $probe 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Provider package(s) still importable from $py after prune: $leak (#1643)"
            throw "agent-bridge venv must not import a venue provider ($leak) -- see #1643"
        }
    }
    Write-Ok "Bridge venv(s) provider-free (pure providers.d, #1643)"
}

# Sibling plugin binstubs (e.g. agent-codespaces) are owned by their own
# installer (~/.agent-codespaces), not by agent-bridge. Bridge uninstall must
# leave them in place. Kept as a no-op for clarity / future siblings.
function Remove-SiblingBinstubs {
    Write-Step "Leaving sibling CLI binstubs in place (owned by their own installers)"
}

function Get-RunningProcess {
    # Try PID file first
    if (Test-Path $PidFile) {
        $pid_ = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($pid_) {
            $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
            if ($proc) { return $proc }
            Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
        }
    }
    # Fallback: find by executable path. The service runs as the venv's
    # python.exe (`-m agent_bridge`); match that. In the versioned layout the
    # daemon is launched via the `venv` junction, so its image path may resolve
    # to either the junction ($LinkPython) or the slot ($VenvPython) -- match
    # both. Legacy installs that still ran the agent-bridge.exe trampoline are
    # also matched for clean migration. (Any miss here is caught by the port
    # fallback below, which finds the live daemon regardless of image path -- key
    # during an update where the old daemon runs a *different* slot.)
    $matchExes = @($VenvPython, $LinkPython, (Join-Path $VenvDir 'Scripts\agent-bridge.exe')) | Select-Object -Unique
    foreach ($exe in $matchExes) {
        if ($exe -and (Test-Path $exe)) {
            $proc = Get-Process | Where-Object { $_.Path -eq $exe } | Select-Object -First 1
            if ($proc) { return $proc }
        }
    }
    # Last resort: find by port binding (catches orphaned processes
    # whose PID file was lost or exe path changed during update). Resolve the
    # live port from active.json so a dynamic-port daemon is found too (#856).
    if (Test-ActiveIsForward) {
        return $null
    }
    $conn = Get-NetTCPConnection -LocalPort (Get-ActiveEndpoint).Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' } |
        Select-Object -First 1
    if ($conn) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) { return $proc }
    }
    return $null
}

function Get-ActiveEndpoint {
    <# Resolve the daemon's LIVE endpoint from the routing table
       (~/.agent-bridge/active.json). Post-#694 a primary daemon binds an
       OS-assigned ephemeral port and advertises the *actual* bound bind+port
       there -- the `agent-bridge` CLI client already resolves it the same way
       (BridgeClient.from_config). The installer's health probes + port-based
       process finder MUST do likewise: otherwise a dynamic-port daemon looks
       dead on the pinned $Port (9280), and a routine version-bump redeploy
       health-gate false-fails a perfectly healthy daemon and self-inflicts an
       outage (dotfiles #856). Falls back to the pinned $Port when there is no
       routing table yet (fresh install) or the deployment pins a fixed port. #>
    $bind = '127.0.0.1'
    $resolved = $Port
    $activeJson = Join-Path $InstallDir 'active.json'
    if (Test-Path $activeJson) {
        try {
            $aj = Get-Content $activeJson -Raw -ErrorAction Stop | ConvertFrom-Json
            $p = [int]($aj.active.port)
            if ($p -gt 0) {
                $resolved = $p
                $b = [string]$aj.active.bind
                if (-not [string]::IsNullOrWhiteSpace($b) -and $b -ne '0.0.0.0') { $bind = $b }
            }
        } catch { }
    }
    return @{ Bind = $bind; Port = $resolved }
}

function Test-ActiveIsForward {
    $activeJson = Join-Path $InstallDir 'active.json'
    if (-not (Test-Path $activeJson)) { return $false }
    try {
        $aj = Get-Content $activeJson -Raw -ErrorAction Stop | ConvertFrom-Json
        $active = $aj.active
        $p = [int]($active.port)
        if ($p -le 0) { return $false }
        if ($active.forwarded -eq $true) { return $true }
        $names = @($active.PSObject.Properties.Name)
        return (($names -notcontains 'pid') -and ($names -notcontains 'generation') -and ($names -notcontains 'bind'))
    } catch {
        return $false
    }
}

function Get-ActiveSignature {
    $activeJson = Join-Path $InstallDir 'active.json'
    if (-not (Test-Path $activeJson)) { return '' }
    try {
        $aj = Get-Content $activeJson -Raw -ErrorAction Stop | ConvertFrom-Json
        $active = $aj.active
        $p = [int]($active.port)
        if ($p -le 0) { return '' }
        $names = @($active.PSObject.Properties.Name)
        $legacyForward = (
            ($names -notcontains 'pid') -and
            ($names -notcontains 'generation') -and
            ($names -notcontains 'bind')
        )
        if (($active.forwarded -eq $true) -or $legacyForward) { return '' }
        $pidValue = if ($names -contains 'pid') { [string]$active.pid } else { '' }
        $generationValue = if ($names -contains 'generation') { [string]$active.generation } else { '' }
        $bindValue = if ($names -contains 'bind') { [string]$active.bind } else { '' }
        return "$bindValue|$p|$pidValue|$generationValue"
    } catch {
        return ''
    }
}

function Test-UpdateLifecycleStillTargetsPredecessor {
    # -AllowAbsent: after this update stopped the pinned predecessor, its graceful
    # shutdown clears its own route, so an absent route still means "ours".
    param([string]$Signature, [switch]$AllowAbsent)
    if (Test-ActiveIsForward) {
        Write-Step 'Forwarded host bridge route appeared during update -- skipping drain/stop/start'
        return $false
    }
    # An empty pin is the legacy fixed-port predecessor with no route: it must
    # stay empty. -AllowAbsent relaxes only a pinned predecessor that exited.
    $current = [string](Get-ActiveSignature)
    $pinned = [string]$Signature
    if ($AllowAbsent -and $pinned -and -not $current) { return $true }
    if ($current -ne $pinned) {
        Write-Step 'Active route changed during update -- skipping drain/stop/start'
        return $false
    }
    return $true
}

function Get-SignatureBaseUrl {
    # The validated predecessor's own endpoint (signature "bind|port|pid|generation";
    # empty = the fixed $Port daemon), so the drain targets exactly it and never
    # follows a route rewritten after validation -- e.g. to a venue forward.
    param([string]$Signature)
    $bind = '127.0.0.1'
    $p = $Port
    $parts = if ($Signature) { $Signature.Split('|') } else { @() }
    $parsed = 0
    if ($parts.Count -ge 2 -and [int]::TryParse($parts[1], [ref]$parsed) -and $parsed -gt 0) {
        $p = $parsed
        if ($parts[0] -eq '::') { $bind = '::1' }
        elseif ($parts[0] -and $parts[0] -ne '0.0.0.0') { $bind = $parts[0] }
    }
    if ($bind.Contains(':')) { return "http://[$bind]:$p" }
    return "http://${bind}:$p"
}

function Invoke-UpdateDrainStop {
    param([string]$Signature, [int]$TimeoutSec = 120)
    if (-not (Test-UpdateLifecycleStillTargetsPredecessor -Signature $Signature)) {
        return $false
    }
    Invoke-Drain -TimeoutSec $TimeoutSec -BaseUrl (Get-SignatureBaseUrl -Signature $Signature)
    # The drain can take minutes; a venue forward may publish or bind meanwhile.
    # Re-check right before stopping so the stop's port cleanup never kills it.
    if (-not (Test-UpdateLifecycleStillTargetsPredecessor -Signature $Signature -AllowAbsent)) {
        return $false
    }
    Invoke-Stop
    return $true
}

function Invoke-UpdateStart {
    param([string]$Signature, [string]$Message = 'Starting service...')
    if (-not (Test-UpdateLifecycleStillTargetsPredecessor -Signature $Signature -AllowAbsent)) {
        return $false
    }
    Write-Step $Message
    Invoke-Start
    return $true
}

function Test-HealthOnce {
    # Single-shot health probe (no retry/sleep). Used by readiness loops that do
    # their own pacing, so the loop interval is not multiplied by an inner retry.
    # Resolves the live endpoint from active.json (dynamic-port aware, #856).
    $ep = Get-ActiveEndpoint
    try {
        Invoke-RestMethod -Uri "http://$($ep.Bind):$($ep.Port)/health" `
            -TimeoutSec 2 -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-HealthCheck {
    $retries = 5
    for ($i = 1; $i -le $retries; $i++) {
        if (Test-HealthOnce) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Get-PortListeners {
    # Return the (unique) owning PIDs of every LISTEN socket on a port.
    param([int]$P)
    return @(
        Get-NetTCPConnection -LocalPort $P -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq 'Listen' } |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Stop-DaemonProcesses {
    # Drain EVERY agent-bridge daemon plus any occupant of the service/relay
    # ports, looping until both ports are free (or attempts exhausted). A single
    # Stop-Process is not enough: duplicate/orphaned daemons (e.g. a racer that
    # re-bound the port between stop and start, or a leftover from a botched
    # update) otherwise survive and defeat the restart, leaving the new code
    # unable to bind. Returns $true once $Port and $RelayPort are both free.
    param([int]$MaxAttempts = 10)

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $victims = New-Object 'System.Collections.Generic.HashSet[int]'

        # Every process running THIS venv's python (the `-m agent_bridge`
        # daemons). Match both the junction ($LinkPython) and the slot
        # ($VenvPython) image paths in the versioned layout; the port sweep below
        # is the backstop for any daemon on a different slot.
        foreach ($exe in (@($VenvPython, $LinkPython) | Select-Object -Unique)) {
            if ($exe -and (Test-Path $exe)) {
                foreach ($p in (Get-Process -ErrorAction SilentlyContinue |
                        Where-Object { $_.Path -eq $exe })) {
                    [void]$victims.Add([int]$p.Id)
                }
            }
        }
        # Every occupant of the service port and the (in-process) relay port.
        foreach ($pt in @($Port, $RelayPort)) {
            foreach ($procId in (Get-PortListeners $pt)) { [void]$victims.Add([int]$procId) }
        }

        if ($victims.Count -eq 0) { return $true }

        foreach ($procId in $victims) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 800
    }

    # Final verdict: both ports must be free.
    return ((Get-PortListeners $Port).Count -eq 0 -and (Get-PortListeners $RelayPort).Count -eq 0)
}

function Invoke-Drain {
    # Best-effort graceful drain before a stop: give in-flight turns a window to
    # settle so a routine update does not hard-kill an active session (the
    # Windows pre-stop hook -- Phase 1 zero-downtime). Bounded + forced so an
    # update never blocks indefinitely. Non-fatal; the Stop that follows is the
    # backstop against the Job Object force-kill on daemon exit.
    # -BaseUrl pins the drain to that endpoint (AGENT_BRIDGE_BASE_URL overrides the
    # routing table) instead of whatever active.json names by the time it runs.
    param([int]$TimeoutSec = 120, [string]$BaseUrl = '')
    $bridgeExe = Join-Path $VenvDir 'Scripts\agent-bridge.exe'
    if (-not (Test-Path $bridgeExe)) { return }
    Write-Step "Draining in-flight sessions (up to ${TimeoutSec}s)..."
    $prevBaseUrl = $env:AGENT_BRIDGE_BASE_URL
    try {
        if ($BaseUrl) { $env:AGENT_BRIDGE_BASE_URL = $BaseUrl }
        & $bridgeExe drain --timeout $TimeoutSec --force 2>&1 | Out-Null
        Write-Ok 'Drain window complete'
    } catch {
        Write-Warn 'Drain reported busy sessions -- proceeding with swap'
    } finally {
        $env:AGENT_BRIDGE_BASE_URL = $prevBaseUrl
    }
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

function Write-DeployManifest {
    # $PayloadHash: the transaction's once-computed Get-PayloadHash value, so
    # the manifest records the same content the slot was admitted/built for.
    param([string]$PayloadHash = '')
    $fields = @{}
    $selectedIdentity = Get-Variable -Name SourceRuntimeIdentity -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    if ($selectedIdentity) {
        $data = $selectedIdentity | ConvertFrom-Json
        foreach ($property in $data.PSObject.Properties) { $fields[$property.Name] = $property.Value }
    }
    $sourcePathOverride = if ($env:COPILOT_PLUGIN_STAGED_FROM) { $env:COPILOT_PLUGIN_STAGED_FROM } else { '' }
    Write-DeployManifestShared `
        -Service 'agent-bridge' `
        -Plugin 'agent-bridge' `
        -InstallPath $InstallDir `
        -PluginPath $PluginDir `
        -VenvPath $LinkDir `
        -GetSourceKind ${function:Get-SourceKind} `
        -GetGitInfo ${function:Get-GitInfo} `
        -SourcePathOverride $sourcePathOverride `
        -VersionOverride $PackageVersion `
        -AdditionalFields $fields `
        -PayloadHash $PayloadHash
}

function Get-ScheduledTaskLastResult {
    param([Parameter(Mandatory)][string]$Name)

    try {
        $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction Stop
        if ($null -eq $info) { return $null }
        return [int64]$info.LastTaskResult
    } catch {
        return $null
    }
}

function Get-ScheduledTaskRepairScript {
    if ($env:COPILOT_PLUGIN_STAGED_FROM) {
        return (Join-Path $env:COPILOT_PLUGIN_STAGED_FROM 'scripts\repair-scheduled-task.ps1')
    }
    return (Join-Path $PSScriptRoot 'repair-scheduled-task.ps1')
}

function Resolve-DaemonLogonMode {
    <# Decide whether the daemon's scheduled task runs non-interactively ("run
       whether the user is logged on or not", boot-triggered) or in the default
       at-logon interactive mode. Opt-in only, resolved from (in priority order):
         1. the -NonInteractive switch or AGENT_BRIDGE_NONINTERACTIVE env var;
         2. an existing task that is already non-interactive, unless an S4U task
            never launched (267011), in which case recover to the default;
         3. an interactive desktop install prompt (skipped over SSH/headless).
       Sets $Script:UseNonInteractive. Never forces the choice. #>
    $Script:UseNonInteractive = $false

    if ($NonInteractive -or ($env:AGENT_BRIDGE_NONINTERACTIVE -in @('1', 'true', 'yes', 'on'))) {
        $Script:UseNonInteractive = $true
        return
    }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Principal.LogonType -in @('S4U', 'Password'))) {
        # A requested S4U start can fail before the action launches when Windows
        # cannot acquire the user's logon token. Do not let that never-ran task
        # make headless mode sticky: absent an explicit opt-in above, recover to
        # the default interactive AtLogOn task. Password tasks and S4U tasks that
        # have run keep their existing intentional mode.
        $lastTaskResult = Get-ScheduledTaskLastResult -Name $TaskName
        if (($existing.Principal.LogonType -eq 'S4U') -and
            ($lastTaskResult -eq $ScheduledTaskHasNotRunResult)) {
            Write-Warn "Existing S4U scheduled task never launched (LastTaskResult=$lastTaskResult); selecting the default interactive AtLogOn mode"
            Write-Warn '  Re-run with -NonInteractive only if headless startup is still required.'
            return
        }

        $Script:UseNonInteractive = $true
        return
    }

    # Offer it on a fresh, genuinely interactive desktop install only. Skip the
    # prompt over SSH (no reliable TTY) AND whenever stdin is redirected -- e.g. a
    # background reconcile launched by bootstrap-check.ps1 with -RedirectStandard*.
    # Such a process still reports [Environment]::UserInteractive=$true on a
    # logged-in desktop, but its stdin is a redirected-but-open pipe on which
    # Read-Host BLOCKS forever (it does not throw, so the catch below never
    # fires) -- wedging the caller (and the CLI session-start hook that spawned
    # it). IsInputRedirected is the reliable "no usable console" signal here.
    $overSsh = [bool]($env:SSH_CONNECTION -or $env:SSH_CLIENT)
    $stdinRedirected = $true
    try { $stdinRedirected = [Console]::IsInputRedirected } catch { $stdinRedirected = $true }
    if ($Action -eq 'install' -and [Environment]::UserInteractive -and -not $overSsh -and -not $stdinRedirected) {
        try {
            Write-Host ''
            Write-Host '  Run agent-bridge whether you are logged on or not?' -ForegroundColor Cyan
            Write-Host '  (headless boot-start; needed for an always-on machine you reach over' -ForegroundColor DarkGray
            Write-Host '   SSH/RDP with no persistent interactive session). Default: No.' -ForegroundColor DarkGray
            $answer = Read-Host '  Enable non-interactive mode? [y/N]'
            if ($answer -match '^(y|yes)$') { $Script:UseNonInteractive = $true }
        } catch {
            # No interactive console available -- leave it at the default.
            $Script:UseNonInteractive = $false
        }
    }
}

function Remove-RegisteredTask {
    <# Best-effort hard purge of a scheduled task, tolerant of a Task Scheduler
       store that has desynced from its COM/CIM view. Tries the ScheduledTasks
       module first, then the schtasks.exe fallback -- which can delete an
       on-disk task (%WINDIR%\System32\Tasks\<name>) that Unregister-ScheduledTask
       reported as gone or that Get-ScheduledTask never surfaced. Never throws,
       so callers can use it unconditionally before a (re)register. #>
    param([Parameter(Mandatory)][string]$Name)

    try { Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop | Out-Null } catch {}

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & schtasks.exe /Delete /TN $Name /F *> $null } catch {}
    $ErrorActionPreference = $prevEAP
}

function Ensure-ScheduledTask {
    <# Routine-safe scheduled-task step for the `update` path (the decoupled
       model, dotfiles#227).

       The scheduled task is **write-once bootstrap infrastructure** whose action
       is version-stable: it invokes the `start-agent-bridge.ps1` supervisor,
       which resolves the live runtime from the `current-version` marker at boot.
       A version update therefore carries NO task change -- the new slot is picked
       up by the marker + supervisor (plain-file, no-elevation updates), never by
       rewriting the task. Rewriting an existing task (especially an S4U/boot task)
       needs elevation and, on failure, churns or destroys a working auto-start.

       So a routine `update`:
         * task PRESENT -> leave it entirely untouched (adopt whatever mode it has,
           never flip/Set/purge/re-register). Zero Task Scheduler writes, so no
           elevation is ever required and a healthy auto-start is never disturbed.
         * task ABSENT  -> provision it ONCE, in the default non-elevated
           interactive AtLogOn mode (first install, or after a manual removal).

       Deliberate (re)provisioning, mode flips (interactive <-> S4U), and repair of
       a broken/never-ran task are the explicit, elevation-aware `provision`
       action's job (Invoke-Provision -> Register-ScheduledTask_), never a routine
       update. #>
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        $mode = if ($existing.Principal.LogonType -in @('S4U', 'Password')) {
            'headless boot'
        } else { 'at logon' }
        Write-Ok "Scheduled task present ($mode) -- left untouched (version updates never rewrite it; run 'install.ps1 provision' elevated to change/repair it)"
        return
    }
    # No task yet -> create it once (default interactive mode; non-elevated).
    Register-ScheduledTask_
}

function Register-ScheduledTask_ {
    if (-not (Test-Path $LinkPython)) {
        Write-Warn "agent-bridge venv not found -- skipping scheduled task"
        return
    }

    Resolve-DaemonLogonMode

    # Create launcher script
    $launcherPath = Join-Path $InstallDir 'start-agent-bridge.ps1'
    $launcherBody = @"
# Start agent-bridge service -- called by scheduled task at logon.
# Launch via the venv's signed python (-m), never the unsigned console-script
# trampoline .exe -- Smart App Control blocks unsigned, zero-reputation exes.
# SINGLE routing point: resolve the active version from the `current-version`
# marker (the same source of truth the binstub uses), never a pinned path -- so a
# cutover only rewrites the marker and this launcher (written once) always starts
# the current slot. No junction/reparse is traversed (marker is a plain file;
# versions/<v> is a real dir), so RedirectionGuard/WinError 448 can't bite.
`$root = '$($InstallDir -replace "'", "''")'
`$launchPy = ''
try { `$_ver = ([IO.File]::ReadAllText((Join-Path `$root 'current-version'))).Trim(); if (`$_ver) { `$launchPy = Join-Path `$root ('versions\' + `$_ver + '\Scripts\python.exe') } } catch {}
if (-not (`$launchPy -and (Test-Path -LiteralPath `$launchPy))) {
    # #742: marker missing/stale -> prefer last-known-good (the last version
    # activate() published) over a raw newest-slot guess, which could bind a
    # still-installing/never-activated slot mid-swap.
    `$_lkg = ''
    try { `$_lkg = ([IO.File]::ReadAllText((Join-Path `$root 'last-known-good'))).Trim() } catch {}
    if (`$_lkg) { `$launchPy = Join-Path `$root ('versions\' + `$_lkg + '\Scripts\python.exe') }
    if (-not (`$launchPy -and (Test-Path -LiteralPath `$launchPy))) { `$launchPy = '' }
}
if (-not (`$launchPy -and (Test-Path -LiteralPath `$launchPy))) { `$launchPy = Get-ChildItem (Join-Path `$root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Join-Path `$_.FullName 'Scripts\python.exe' } | Where-Object { Test-Path -LiteralPath `$_ } | Select-Object -Last 1 }
`$pidFile = '$($PidFile -replace "'", "''")'
`$logFile = Join-Path (Split-Path `$pidFile) 'agent-bridge.log'
`$errFile = Join-Path (Split-Path `$pidFile) 'agent-bridge-err.log'

# #1376: keep this supervisor (pwsh) AND the worker python it spawns OUT of the
# installed-plugins payload dir. A process holding a directory as its CWD locks
# it on Windows, so anything sitting in the plugin folder blocks
# ``copilot plugin update agent-bridge`` (the replace fails and the payload dir
# is left emptied -- "installer not found" on the next update). The OPERATIVE
# guard is -WorkingDirectory on Start-Process below: Set-Location only moves
# PowerShell's `$PWD provider path, NOT the OS working directory a spawned child
# inherits. We set both, and the scheduled task pins -WorkingDirectory too.
`$runtimeHome = Split-Path `$pidFile
Set-Location -LiteralPath `$runtimeHome
[Environment]::SetEnvironmentVariable('AGENT_BRIDGE_INSTALL_DIR', `$runtimeHome, 'Process')
[Environment]::SetEnvironmentVariable('AGENT_BRIDGE_CONFIG_DIR', `$runtimeHome, 'Process')
[Environment]::SetEnvironmentVariable('AGENT_BRIDGE_CONNECT_LOG', (Join-Path (Join-Path `$runtimeHome 'logs') 'connect.log'), 'Process')

if (Test-Path `$pidFile) {
    `$existingPid = Get-Content `$pidFile -ErrorAction SilentlyContinue
    if (`$existingPid) {
        `$proc = Get-Process -Id `$existingPid -ErrorAction SilentlyContinue
        if (`$proc -and -not `$proc.HasExited) { exit 0 }
    }
}

`$proc = Start-Process -FilePath `$launchPy -ArgumentList '-m','agent_bridge','start' ``
    -WorkingDirectory `$runtimeHome ``
    -NoNewWindow -PassThru ``
    -RedirectStandardOutput `$logFile ``
    -RedirectStandardError `$errFile
Set-Content -Path `$pidFile -Value `$proc.Id
"@
    # Idempotent write: only rewrite the supervisor when its content actually
    # changes, so a routine update doesn't churn the file (and its mtime) on every
    # deploy. The supervisor is version-independent (it reads `current-version`),
    # so in practice it is written once and never changes across version updates.
    $existingBody = if (Test-Path $launcherPath) {
        Get-Content -Path $launcherPath -Raw -ErrorAction SilentlyContinue
    } else { $null }
    if ($existingBody -ne $launcherBody) {
        $launcherBody | Set-Content -Path $launcherPath -Encoding UTF8
    }

    $pwshPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
    if (-not (Test-Path $pwshPath)) {
        $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
        $pwshPath = if ($pwshCmd) { $pwshCmd.Source } else { 'pwsh.exe' }
    }

    # Use conhost --headless to prevent Windows Terminal from capturing the
    # task's pwsh as a visible window/tab when Terminal is the default terminal
    # app. -WindowStyle Hidden alone is ignored by Windows Terminal, so a bare
    # `pwsh -WindowStyle Hidden` task surfaces a real console window -- and
    # because the launcher spawns the long-lived python.exe (-m agent_bridge)
    # with -NoNewWindow, that window persists for the life of the service.
    $action = New-ScheduledTaskAction `
        -Execute 'conhost.exe' `
        -Argument "--headless `"$pwshPath`" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`"" `
        -WorkingDirectory $InstallDir

    # Non-interactive mode runs the daemon headless: a boot trigger (fires
    # without any logon) plus an S4U principal ("run whether the user is logged
    # on or not", no stored password). S4U is safe for agent-bridge because its
    # outbound SSH authenticates with key files, not the Windows network token.
    # Default mode keeps the at-logon trigger that only runs while signed in.
    if ($Script:UseNonInteractive) {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $trigger.Delay = 'PT15S'
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
            -LogonType S4U -RunLevel Limited
        $modeLabel = 'at startup, headless (run whether logged on or not)'
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $trigger.Delay = 'PT15S'
        $principal = $null
        $modeLabel = 'at logon, 15s delay'
    }

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew

    # A principal change (interactive <-> S4U) cannot be applied by
    # Set-ScheduledTask -- those go through a forced re-register so the logon
    # type sticks.
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $principalChange = $existing -and (
        ($Script:UseNonInteractive -and ($existing.Principal.LogonType -notin @('S4U', 'Password'))) -or
        (-not $Script:UseNonInteractive -and ($existing.Principal.LogonType -in @('S4U', 'Password')))
    )
    $canUpdateInPlace = $existing -and -not $principalChange

    $regArgs = @{
        TaskName    = $TaskName
        Action      = $action
        Trigger     = $trigger
        Settings    = $settings
        Description = 'Agent-Bridge -- inter-agent communication service on port 9280.'
    }
    if ($principal) { $regArgs['Principal'] = $principal }

    # Idempotent no-op when nothing changed. The task's action points at a STABLE,
    # self-provisioning launcher ($launcherPath) that resolves the live version from
    # the `current-version` marker at boot -- so the task's action/trigger/principal
    # are byte-identical across deploys and never need a rewrite. Re-applying an
    # unchanged S4U (boot) task via Set-ScheduledTask nonetheless requires
    # elevation, which a routine non-elevated `update` lacks; that failure then
    # falls into the purge-and-retry below, which would Unregister a perfectly good
    # boot task before a Register it also cannot perform -- risking the loss of a
    # working auto-start. So if an existing task already matches the desired action
    # AND mode (no principal change), leave it untouched -- no elevation required,
    # no misleading "not configured" warning, no purge of a healthy task.
    if ($existing -and -not $principalChange) {
        $curAct = @($existing.Actions)[0]
        $sameAction = $curAct -and
            ($curAct.Execute -eq $action.Execute) -and
            ($curAct.Arguments -eq $action.Arguments) -and
            (("" + $curAct.WorkingDirectory) -eq ("" + $action.WorkingDirectory))
        if ($sameAction) {
            Write-Ok "Scheduled task already configured ($modeLabel) -- unchanged, left as-is"
            return
        }
    }

    # Write the task resiliently. Windows Task Scheduler can desync its COM/CIM
    # view from the on-disk store: an Unregister may report success yet leave the
    # task XML, and Get-ScheduledTask may not surface a task whose file still
    # exists -- after which a plain Register-ScheduledTask throws "Cannot create
    # a file when that file already exists". So: prefer an in-place Set when only
    # trigger/action/settings change; otherwise Register -Force (idempotent
    # overwrite); and on ANY failure, hard-purge the registration and retry
    # Register -Force once. A scheduled task is only a boot/logon convenience, so
    # a terminal failure downgrades to a warning instead of aborting the whole
    # install -- under StrictMode + $ErrorActionPreference='Stop' an unhandled
    # throw here would take the entire module down as "exited 1" and skip the
    # remaining install steps (deploy manifest, PATH).
    try {
        if ($canUpdateInPlace) {
            $setArgs = @{ TaskName = $TaskName; Action = $action; Trigger = $trigger; Settings = $settings }
            if ($principal) { $setArgs['Principal'] = $principal }
            Set-ScheduledTask @setArgs | Out-Null
            Write-Ok "Scheduled task updated ($modeLabel)"
        } else {
            if ($principalChange) { Remove-RegisteredTask $TaskName }
            Register-ScheduledTask @regArgs -Force | Out-Null
            Write-Ok "Scheduled task registered ($modeLabel)"
        }
    } catch {
        # A write failure here is almost always "Access is denied" trying to
        # modify an existing elevated/S4U (boot) task from a non-elevated shell.
        # Do NOT purge a task we may be unable to recreate -- that would destroy a
        # working auto-start. Only purge-and-retry when the change is unavoidable
        # (a principal flip) AND we are elevated; otherwise leave the existing task
        # intact and tell the operator the one, explicit, elevation-aware fix.
        $isAdmin = $false
        try {
            $isAdmin = ([Security.Principal.WindowsPrincipal] `
                [Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        } catch {}
        if ($principalChange -and $isAdmin) {
            Write-Warn "Scheduled task write failed ($($_.Exception.Message.Trim())); purging stale registration and retrying (elevated)"
            Remove-RegisteredTask $TaskName
            try {
                Register-ScheduledTask @regArgs -Force | Out-Null
                Write-Ok "Scheduled task registered on retry ($modeLabel)"
            } catch {
                Write-Warn "Could not register the '$TaskName' scheduled task: $($_.Exception.Message.Trim())"
                Write-Warn "agent-bridge is installed, but auto-start ($modeLabel) is not configured -- start it now with 'agent-bridge start'."
            }
        } else {
            Write-Warn "Scheduled task write failed ($($_.Exception.Message.Trim())) -- the existing task was left intact."
            $__repair = Get-ScheduledTaskRepairScript
            Write-Warn "This change (switching an elevated/S4U task to interactive, or repairing a broken one) needs elevation. Run the self-elevating repair ONCE (it prompts for UAC and fixes the task without starting an elevated daemon):"
            Write-Warn "    pwsh -File `"$__repair`""
            Write-Warn "Routine updates do not touch the task; the daemon self-heals on demand meanwhile ('agent-bridge start' or any daemon-touching command)."
        }
    }
}

function Invoke-MigrationCheck {
    <# Detect and handle migration from a legacy project-service installer. #>
    $oldManifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $oldManifest)) { return }

    try {
        $manifest = Get-Content $oldManifest -Raw | ConvertFrom-Json
        if ($manifest.installer_path -and $manifest.installer_path -like '*services/agent-bridge*') {
            Write-Step "Migrating from legacy project-service installer"
            Write-Step "  Preserving config, auth, and DB"

            # Stop old instance if running
            $proc = Get-RunningProcess
            if ($proc) {
                Write-Step "  Stopping running instance (pid=$($proc.Id))"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
            }

            # Remove old scheduled task if it exists (it may have been registered
            # by the legacy project-service installer)
            $oldTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($oldTask) {
                Write-Step "  Re-registering scheduled task (plugin-owned)"
            }

            Write-Ok "Migration from legacy project-service installer detected"
        }
    } catch { }
}

# -- Actions -----------------------------------------------------------------

function Write-Binstubs {
    <# Deploy the agent-bridge CLI binstubs into ~/.local/bin.

       Primary: agent-bridge.ps1. PowerShell resolves a .ps1 (ExternalScript)
       ahead of a .cmd (Application) in the same directory, and `@args`
       forwards the argument array to python verbatim -- quotes, &&, |, ;, and
       ! in `send` / `--remote-cmd` payloads arrive intact. A .cmd forwarding
       %* re-tokenizes the command line and mangles (and can inject) those
       metacharacters; setlocal/enabledelayedexpansion does not fix it.

       Fallback: agent-bridge.cmd, for non-PowerShell callers (cmd.exe or a
       bare CreateProcess/PATHEXT spawn) that cannot resolve a .ps1. It never
       shadows the .ps1 for PowerShell callers when both sit in the same dir.

       Both launch the venv's PSF-signed python via `-m`, never the unsigned
       console-script trampoline .exe that Smart App Control blocks (3077). #>
    param([string]$PythonExe)  # accepted for call-site compatibility; unused (marker-driven)

    # SINGLE dynamic router, now SELF-PROVISIONING (#1393). The binstub reads the
    # `current-version` marker at every invocation and redirects into
    # versions/<current>/Scripts/python.exe (version-AGNOSTIC, written once, so a
    # cutover only rewrites the marker). If NO slot is built yet (a `stamp`
    # deferred the venv), it provisions on first use from the slot-local snapshot,
    # then dispatches. Opt out with AGENT_BRIDGE_NO_SELFPROVISION=1. No junction is
    # traversed (marker is a plain file), so RedirectionGuard can't bite. Launches
    # the PSF-signed venv python via -m, never the SAC-blocked trampoline .exe.
    # Co-deploy the canonical resolvers so the binstub resolves the interpreter
    # the ONE uniform way (uniform-runtime-resolution, #765).
    function Publish-RuntimeHelperFile {
        param([string]$Source, [string]$Destination)
        $invalid = [IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint
        if (([IO.File]::GetAttributes($Source) -band $invalid) -ne 0) {
            throw "Runtime helper source must be an ordinary file: $Source"
        }
        $temporary = Join-Path (Split-Path -Parent $Destination) (
            '.' + (Split-Path -Leaf $Destination) + '.' + [Guid]::NewGuid().ToString('N'))
        $copied = $false
        try {
            [IO.File]::Copy($Source, $temporary, $false)
            $copied = $true
            if (Test-Path -LiteralPath $Destination) {
                if (([IO.File]::GetAttributes($Destination) -band $invalid) -ne 0) {
                    throw "Runtime helper destination must be an ordinary file: $Destination"
                }
                [IO.File]::Replace($temporary, $Destination, [NullString]::Value)
            } else {
                [IO.File]::Move($temporary, $Destination)
            }
        } finally {
            if ($copied -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        }
    }
    $binDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    foreach ($r in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $rSrc = Join-Path $PSScriptRoot $r
        if (Test-Path -LiteralPath $rSrc) { Publish-RuntimeHelperFile $rSrc (Join-Path $binDir $r) }
    }
    # Native service callers use the Python resolver without a payload or PATH.
    $scriptsDir = Join-Path $InstallDir 'scripts'
    New-Item -ItemType Directory -Path $scriptsDir -Force -ErrorAction Stop | Out-Null
    foreach ($r in @('versioned_runtime.py', 'versioned_runtime_leases.py', 'versioned_runtime_construction.py', 'versioned_runtime_source.py', 'versioned_runtime_publication.py')) {
        Publish-RuntimeHelperFile (Join-Path $PSScriptRoot $r) (Join-Path $scriptsDir $r)
    }
    if (-not $publishGlobalBinstubs) {
        Write-Ok 'Scoped install keeps the global compatibility binstub unchanged'
        return
    }
    $rootLit = $InstallDir -replace "'", "''"
    $ps1 = @'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'
$_root = '__ROOT__'
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
# Windows PowerShell marks -File's redirected stdin as pipeline input, but
# that invocation has no caller line. Defer reading object input so the raw
# native pipe is never eagerly decoded by a reference to the automatic variable.
$_pipelineInput = $MyInvocation.ExpectingInput -and $MyInvocation.Line
function _Resolve-Py {
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $_resolver) { $env:AGENT_RT_ROOT = $_root; . $_resolver }
    return $AgentRtPy
}
$_py = _Resolve-Py
if ($_py) { if ($_pipelineInput) { (Get-Variable input -ValueOnly) | & $_py -m agent_bridge @args } else { & $_py -m agent_bridge @args }; exit $LASTEXITCODE }
if ($env:AGENT_BRIDGE_NO_SELFPROVISION) { [Console]::Error.WriteLine('[agent-bridge] runtime not provisioned (AGENT_BRIDGE_NO_SELFPROVISION set).'); exit 1 }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) { Join-Path $_snap 'scripts\install.ps1' } else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-bridge] cannot self-provision: snapshot installer not found. Re-enable the plugin, then retry.'); exit 127 }
[Console]::Error.WriteLine('[agent-bridge] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-bridge eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
& $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
$_py = _Resolve-Py
if ($_py) { if ($_pipelineInput) { (Get-Variable input -ValueOnly) | & $_py -m agent_bridge @args } else { & $_py -m agent_bridge @args }; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-bridge] provisioning did not yield a runtime. See the log above; retry, or run the snapshot installer manually.')
exit 1
'@ -replace '__ROOT__', $rootLit
    [System.IO.File]::WriteAllText($BinstubPs1, $ps1, (New-Object System.Text.UTF8Encoding($false)))

    # cmd fallback: delegate entirely to the .ps1 binstub so resolution stays
    # uniform with the canonical resolve-runtime.ps1 chain and self-provisioning
    # is shared (uniform-runtime-resolution, #765).
    $cmd = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=__PS1__"
if not exist "%_PS1%" (echo [agent-bridge] binstub not found: %_PS1%>&2 & exit /b 127)
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*) else (powershell -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*)
exit /b %ERRORLEVEL%
'@ -replace '__PS1__', $BinstubPs1
    [System.IO.File]::WriteAllText($BinstubCmd, $cmd)

    Write-Ok "Binstub: $BinstubPs1 (+ .cmd fallback) -- marker-routed, self-provisioning"
}

function Resolve-SnapshotInstallerEngineSource {
    param([Parameter(Mandatory)][ValidateSet('ps1', 'sh')][string]$Ext)
    $localEngine = Join-Path $PSScriptRoot ("installer-engine.$Ext")
    if (Test-Path -LiteralPath $localEngine) { return $localEngine }
    return Join-Path (Join-Path $PSScriptRoot '..\..\..\libs\installer-engine') ("installer-engine.$Ext")
}

function Materialize-SnapshotVendoredLibs {
    param([Parameter(Mandatory)][string]$SnapshotDir)
    $libsDir = Join-Path $SnapshotDir 'libs'
    if (-not (Test-Path $libsDir)) { New-Item -ItemType Directory -Path $libsDir -Force | Out-Null }
    $sources = [ordered]@{
        'ssh-manager'          = (Resolve-SshManager)
        'credential-relay'     = (Resolve-CredentialRelay)
        'zdd'                  = (Resolve-Zdd)
        'single-instance-lease'= (Resolve-SingleInstanceLease)
        'config-migrate'       = (Resolve-ConfigMigrate)
        'agent-procutil'       = (Resolve-VendoredLib -LibName 'agent-procutil')
        'plugin-resolve'       = (Resolve-VendoredLib -LibName 'plugin-resolve')
        'dropin-registry'      = (Resolve-VendoredLib -LibName 'dropin-registry')
        'plugin-activation'    = (Resolve-VendoredLib -LibName 'plugin-activation')
        'remote-login-shell'   = (Resolve-VendoredLib -LibName 'remote-login-shell')
        'machine-transport'    = (Resolve-VendoredLib -LibName 'machine-transport')
        'worktree-identity'    = (Resolve-VendoredLib -LibName 'worktree-identity')
        'process-telemetry'    = (Resolve-VendoredLib -LibName 'process-telemetry')
    }
    foreach ($entry in $sources.GetEnumerator()) {
        $source = $entry.Value
        if (-not $source) {
            throw "Cannot materialize stamped snapshot: required vendored library '$($entry.Key)' is unresolved."
        }
        $destination = Join-Path $libsDir $entry.Key
        if ([System.IO.Path]::GetFullPath($source) -eq [System.IO.Path]::GetFullPath($destination)) {
            continue
        }
        Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
    }

    $snapshotPyproject = Join-Path $SnapshotDir 'pyproject.toml'
    if (Test-Path -LiteralPath $snapshotPyproject) {
        $pyprojectText = [System.IO.File]::ReadAllText($snapshotPyproject)
        $rewrites = [ordered]@{
            'agent-ssh-manager'          = 'ssh-manager'
            'agent-credential-relay'     = 'credential-relay'
            'agent-zdd'                  = 'zdd'
            'agent-single-instance-lease'= 'single-instance-lease'
            'agent-config-migrate'       = 'config-migrate'
            'agent-plugin-resolve'       = 'plugin-resolve'
            'agent-procutil'             = 'agent-procutil'
            'agent-dropin-registry'      = 'dropin-registry'
            'agent-plugin-activation'    = 'plugin-activation'
            'agent-remote-login-shell'   = 'remote-login-shell'
            'agent-machine-transport'    = 'machine-transport'
            'agent-worktree-identity'    = 'worktree-identity'
            'agent-process-telemetry'    = 'process-telemetry'
        }
        foreach ($entry in $rewrites.GetEnumerator()) {
            $oldLine = '{0} = {{ path = "../../libs/{1}", editable = true }}' -f $entry.Key, $entry.Value
            $newLine = '{0} = {{ path = "libs/{1}" }}' -f $entry.Key, $entry.Value
            $pyprojectText = $pyprojectText.Replace($oldLine, $newLine)
        }
        [System.IO.File]::WriteAllText($snapshotPyproject, $pyprojectText, $utf8NoBom)
    }

    foreach ($entry in $rewrites.GetEnumerator()) {
        $nestedPyproject = Join-Path (Join-Path $libsDir $entry.Value) 'pyproject.toml'
        if (-not (Test-Path -LiteralPath $nestedPyproject)) { continue }
        $nestedText = [System.IO.File]::ReadAllText($nestedPyproject)
        $nestedText = $nestedText.Replace(', editable = true }', ' }')
        [System.IO.File]::WriteAllText($nestedPyproject, $nestedText, $utf8NoBom)
    }
}

function Materialize-SnapshotInstallerEngine {
    param([Parameter(Mandatory)][string]$SnapshotDir)
    $scriptsDir = Join-Path $SnapshotDir 'scripts'
    if (-not (Test-Path $scriptsDir)) { New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null }
    foreach ($name in @('installer-engine.ps1', 'installer-engine.sh')) {
        $ext = [System.IO.Path]::GetExtension($name).TrimStart('.')
        $source = Resolve-SnapshotInstallerEngineSource -Ext $ext
        $destination = Join-Path $scriptsDir $name
        if ([System.IO.Path]::GetFullPath($source) -ne [System.IO.Path]::GetFullPath($destination)) {
            Copy-Item -LiteralPath $source -Destination $destination -Force
        }
    }
    $installSh = Join-Path $scriptsDir 'install.sh'
    if (Test-Path $installSh) {
        $shText = [System.IO.File]::ReadAllText($installSh)
        $shText = $shText.Replace('. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"', '. "$SCRIPT_DIR/installer-engine.sh"')
        [System.IO.File]::WriteAllText($installSh, $shText, $utf8NoBom)
    }
    $installPs1 = Join-Path $scriptsDir 'install.ps1'
    if (Test-Path $installPs1) {
        $ps1Text = [System.IO.File]::ReadAllText($installPs1)
        $ps1Text = $ps1Text.Replace('. (Join-Path $PSScriptRoot ''..\..\..\libs\installer-engine\installer-engine.ps1'')', '. (Join-Path $PSScriptRoot ''installer-engine.ps1'')')
        [System.IO.File]::WriteAllText($installPs1, $ps1Text, $utf8NoBom)
    }
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE into
    # ~/.agent-bridge/snapshots/<ver>/, record markers, and deploy the self-
    # provisioning binstub -- deferring the heavy venv build (and the daemon/service
    # registration) to the binstub's first use. No venv, no uv; fits a sessionStart
    # grace window and NEVER holds the marketplace payload open.
    Write-Step 'agent-bridge stamp; runtime deferred to first use'
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $dirs = @($InstallDir)
    if ($publishGlobalBinstubs) { $dirs += $LocalBin }
    foreach ($dir in $dirs) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $sourceQualified = [bool]$script:SourceRuntimeIdentity
    if ($sourceQualified) {
        [void](Test-PayloadUnchangedSinceAdmission -SourceIdentityOnly)
        $py = Get-BootstrapPython
        if (-not $py) { throw 'Source snapshot publication requires a bootstrap Python interpreter' }
        $nativeError = ''
        $snapshot = Invoke-Hidden -FilePath $py -ArgList @(
            '-I', '-B', (Join-Path $ScriptDir 'versioned_runtime_source.py'),
            '--payload', $PluginDir, '--package-version', $PackageVersion, '--plugin', 'agent-bridge',
            '--expected-runtime-version', $SrcVersion, '--expected-source-identity', $SourceRuntimeIdentity,
            '--snapshot-root', $InstallDir
        ) -ErrorOutput ([ref]$nativeError)
        if ($LASTEXITCODE -ne 0) {
            throw "Source snapshot publication failed (exit ${LASTEXITCODE}): $snapshot$nativeError"
        }
        if (-not $snapshot -or -not $snapshot.Trim()) {
            throw 'Source snapshot publisher returned no path'
        }
        $snapDir = $snapshot.Trim()
        [void](Test-PayloadUnchangedSinceAdmission -SourceIdentityOnly)
        $nativeError = ''
        [void](Invoke-Hidden -FilePath $py -ArgList @(
            '-I', '-B', (Join-Path $ScriptDir 'versioned_runtime.py'), '--root', $InstallDir,
            'publish-redirects', '--payload-dir', $snapDir, '--version', $SrcVersion,
            '--expected-source-identity', $SourceRuntimeIdentity
        ) -ErrorOutput ([ref]$nativeError))
        if ($LASTEXITCODE -ne 0) {
            throw "Source redirect publication failed (exit ${LASTEXITCODE}): $nativeError"
        }
    } else {
        $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
        $snapTmp = "$snapDir.tmp-$PID"
        if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
        $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
        Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
        }
        Materialize-SnapshotVendoredLibs -SnapshotDir $snapTmp
        Materialize-SnapshotInstallerEngine -SnapshotDir $snapTmp
        if (Test-Path $snapDir) { Remove-Item $snapDir -Recurse -Force -ErrorAction SilentlyContinue }
        $moveAttempt = 0
        while ($true) {
            try {
                Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force -ErrorAction Stop
                break
            } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
                $moveAttempt++
                if ($moveAttempt -ge 5) { throw }
                Start-Sleep -Milliseconds (200 * $moveAttempt)
            }
        }
        [System.IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $snapDir, $utf8NoBom)
        [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    }
    Write-Ok "Snapshot: $snapDir"
    Write-Binstubs
    if ($publishGlobalBinstubs) {
        Write-Ok 'Stamped: agent-bridge binstub on PATH; runtime provisions on first use.'
    } else {
        Write-Ok 'Stamped: scoped runtime metadata recorded; payload-local commands provision on first use.'
    }
}

function Invoke-InstallSlotBuild {
    <# Build the target slot from scratch for Invoke-Install: venv, vendored
       libs, the agent-bridge package, provider pruning. Only for an absent or
       incomplete slot -- a completed slot is never rebuilt in place (#5472). #>
    # Create venv (signed base python where available, so it is SAC-trusted)
    if (-not (Test-Path $VenvPython)) {
        Write-Step 'Creating venv...'
        if (-not (New-SignedVenv)) {
            Write-Fail "Failed to create venv at $VenvDir"
            exit 1
        }
        Write-Ok 'Venv created'
    } else {
        # Rebuild in place if the existing venv python is unsigned (SAC).
        if (-not (New-SignedVenv)) {
            Write-Fail "Venv unavailable at $VenvDir"
            exit 1
        }
        Write-Skip 'Venv ready'
    }

    # Install package via uv (ssh-manager library first, then agent-bridge)
    Write-Step 'Installing agent-bridge package...'
    # Pre-strip any locked console-script trampoline so uv can overwrite it
    # (Windows denies overwriting an in-use .exe -- os error 5).
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    $SshManagerDir = Resolve-SshManager
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($SshManagerDir) {
        # A vendored lib's version rarely bumps but its source changes, so force
        # a clean rebuild: --reinstall-package drops the installed dist and
        # --refresh-package busts uv's *build cache* (else uv serves a stale
        # cached wheel for the same version and new modules never land -- the
        # #186 CodespaceConfigSource regression). Both selectors must name the
        # `agent-ssh-manager` distribution declared by the vendored pyproject.
        $sshResult = Invoke-UvPipInstallResilient -SourceDir $SshManagerDir @('--python', $VenvPython, "$SshManagerDir", '--reinstall-package', 'agent-ssh-manager', '--refresh-package', 'agent-ssh-manager', '--quiet')
        $sshOut = $sshResult.Output
        if ($sshResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "ssh-manager install failed (exit $($sshResult.ExitCode))"
            if ($sshOut) { Write-Host ($sshOut | Out-String) }
            throw 'ssh-manager install failed'
        }
    } elseif (Test-SshManagerInstalled) {
        Write-Step 'ssh-manager already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # credential-relay (the relay framework agent-bridge runs in its daemon).
    $CredRelayDir = Resolve-CredentialRelay
    if ($CredRelayDir) {
        $crResult = Invoke-UvPipInstallResilient -SourceDir $CredRelayDir @('--python', $VenvPython, "$CredRelayDir", '--reinstall-package', 'agent-credential-relay', '--refresh-package', 'agent-credential-relay', '--quiet')
        $crOut = $crResult.Output
        if ($crResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "credential-relay install failed (exit $($crResult.ExitCode))"
            if ($crOut) { Write-Host ($crOut | Out-String) }
            throw 'credential-relay install failed'
        }
    } elseif (Test-CredentialRelayInstalled) {
        Write-Step 'credential-relay already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    $ZddDir = Resolve-Zdd
    if ($ZddDir) {
        $zddResult = Invoke-UvPipInstallResilient -SourceDir $ZddDir @('--python', $VenvPython, "$ZddDir", '--reinstall-package', 'agent-zdd', '--refresh-package', 'agent-zdd', '--quiet')
        $zddOut = $zddResult.Output
        if ($zddResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "zdd install failed (exit $($zddResult.ExitCode))"
            if ($zddOut) { Write-Host ($zddOut | Out-String) }
            throw 'zdd install failed'
        }
    } elseif (Test-ZddInstalled) {
        Write-Step 'zdd already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate zdd library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # single-instance-lease (one active daemon per host: lease + self-retire + reaper).
    $SilDir = Resolve-SingleInstanceLease
    if ($SilDir) {
        $silResult = Invoke-UvPipInstallResilient -SourceDir $SilDir @('--python', $VenvPython, "$SilDir", '--reinstall-package', 'agent-single-instance-lease', '--refresh-package', 'agent-single-instance-lease', '--quiet')
        $silOut = $silResult.Output
        if ($silResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "single-instance-lease install failed (exit $($silResult.ExitCode))"
            if ($silOut) { Write-Host ($silOut | Out-String) }
            throw 'single-instance-lease install failed'
        }
    } elseif (Test-SingleInstanceLeaseInstalled) {
        Write-Step 'single-instance-lease already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate single-instance-lease library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # config-migrate (config schema versioning + migration).
    $CfgMigrateDir = Resolve-ConfigMigrate
    if ($CfgMigrateDir) {
        $cmResult = Invoke-UvPipInstallResilient -SourceDir $CfgMigrateDir @('--python', $VenvPython, "$CfgMigrateDir", '--reinstall-package', 'agent-config-migrate', '--refresh-package', 'agent-config-migrate', '--quiet')
        $cmOut = $cmResult.Output
        if ($cmResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "config-migrate install failed (exit $($cmResult.ExitCode))"
            if ($cmOut) { Write-Host ($cmOut | Out-String) }
            throw 'config-migrate install failed'
        }
    } elseif (Test-ConfigMigrateInstalled) {
        Write-Step 'config-migrate already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate config-migrate library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # --refresh-package agent-procutil: agent-procutil has no dedicated
    # install call of its own (resolved transitively while installing
    # agent-bridge here), so it never gets an explicit cache-bust anywhere
    # else. uv's local-path build cache is keyed by source path, not source
    # content -- see the matching install.sh comment / #2863 for the
    # confirmed incident this class of gap caused.
    $bridgeResult = Invoke-UvPipInstallResilient @('--python', $VenvPython, "$PluginDir", '--reinstall-package', 'agent-bridge', '--refresh-package', 'agent-bridge', '--reinstall-package', 'agent-procutil', '--refresh-package', 'agent-procutil', '--reinstall-package', 'agent-plugin-resolve', '--refresh-package', 'agent-plugin-resolve', '--reinstall-package', 'agent-dropin-registry', '--refresh-package', 'agent-dropin-registry', '--reinstall-package', 'agent-plugin-activation', '--refresh-package', 'agent-plugin-activation', '--reinstall-package', 'agent-remote-login-shell', '--refresh-package', 'agent-remote-login-shell', '--reinstall-package', 'agent-machine-transport', '--refresh-package', 'agent-machine-transport', '--reinstall-package', 'agent-worktree-identity', '--refresh-package', 'agent-worktree-identity', '--reinstall-package', 'agent-process-telemetry', '--refresh-package', 'agent-process-telemetry', '--quiet')
    $bridgeOut = $bridgeResult.Output
    $installResult = $bridgeResult.ExitCode
    $ErrorActionPreference = $prevEAP
    if ($installResult -ne 0) {
        Write-Fail "Package install failed (exit $installResult)"
        if ($bridgeOut) { Write-Host ($bridgeOut | Out-String) }
        throw 'Package install failed'
    }
    Write-Ok 'Package installed'

    # Install sibling plugins (e.g. agent-codespaces for codespace: namespace)
    Install-SiblingPlugins

    # Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused);
    # also clears sibling agent-*.exe pulled into this venv by Install-SiblingPlugins.
    Remove-ConsoleTrampolines -VenvDir $VenvDir
}

function Invoke-Install {
    Write-Step 'agent-bridge install'

    if (-not (Ensure-Uv)) { exit 1 }

    # Check for migration from old installer
    Invoke-MigrationCheck

    # Strict same-content no-op (ce#776/#777): if the target version is already
    # the active slot AND healthy with matching content, do NOT rebuild it -- the
    # slot IS the running daemon's live venv, and overwriting it in place is the
    # WinError-2 crash cascade (dotfiles#1612). Idempotency is keyed on content,
    # not the mutable version label. A force rebuild/restart stays a dedicated
    # command, never a routine-reconcile side effect.
    $preLockHash = Get-PayloadHash
    if (-not (Get-Variable -Name SourceRuntimeIdentity -Scope Script -ValueOnly -ErrorAction SilentlyContinue) -and
        (Test-SlotContentCurrent -PayloadHash $preLockHash)) {
        Write-Ok "agent-bridge $SrcVersion already installed and healthy (content match) -- no-op (no venv rebuild, no restart)."
        Write-DeployManifest -PayloadHash $preLockHash   # re-assert the manifest so version drift clears and this never re-triggers every session
        return
    }

    # Serialize concurrent installers (ce#776/#777 follow-up): an overlapping
    # install of the same version must not rebuild the venv over an in-flight one
    # (the racing-rebuild that yields a DOA slot). Wait for any in-flight install;
    # on timeout DEFER -- it will land the version.
    if (-not (Enter-InstallLock)) {
        Write-Fail "Another agent-bridge install held the install lock ($(Join-Path $InstallDir '.install.lock')) for the whole wait -- this run changed nothing (blocked by lock); rerun once it finishes"
        exit 75
    }
    [void](Test-PayloadUnchangedSinceAdmission -SourceIdentityOnly)
    # The transaction's single payload fingerprint (#5472): computed once under
    # the lock, BEFORE admission, and carried unchanged through admission, the
    # completion marker and the deploy manifest.
    $payloadHash = Get-PayloadHash
    # Re-check after acquiring: the install we waited on may have JUST landed this
    # exact content, so our rebuild would be redundant (and a live-slot stomp).
    if (Test-SlotContentCurrent -PayloadHash $payloadHash) {
        Write-Ok "agent-bridge $SrcVersion already installed and healthy after lock wait -- no-op."
        Write-DeployManifest -PayloadHash $payloadHash
        Exit-InstallLock
        return
    }

    # Never rebuild a PUBLISHED same-version slot in place (#5472, ce#776/#777).
    # A completed numbered slot is immutable: its content may already be what a
    # running daemon executes, and rewriting it in place stomps that runtime
    # (WinError 2, dotfiles#1612). Different content must ship under a NEW
    # version. Refuse (non-zero, no override) before touching anything; an
    # absent/incomplete slot ('construct') still builds exactly as before.
    $admission = Get-SlotAdmission -PayloadHash $payloadHash
    if ($admission -ne 'construct' -and $admission -ne 'reuse') {
        Write-SlotAdmissionRefusal -Admission $admission
        Exit-InstallLock
        exit 1
    }
    [void](Invoke-VersionedSlotClean -PayloadHash $payloadHash)
    $admission = if ($script:RuntimeConstructionReuse) { 'reuse' } else { 'construct' }

    # Create directories
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }

    # A completed slot that already holds exactly this payload (just not the
    # active one) is activated as-is: never recreate its venv, reinstall into
    # it, or prune it (#5472). Only an absent/incomplete slot is built.
    if ($admission -eq 'reuse') {
        Write-Skip "Runtime slot versions/$SrcVersion is already complete for this payload -- activating it without rebuilding"
    } else {
        Invoke-InstallSlotBuild
    }

    # The build read the sources after $payloadHash was taken: publish nothing
    # (marker, manifest) and activate nothing unless they still hash to it.
    # Like a refused marker publication, the slot stays incomplete and the
    # next build's --clean-incomplete tosses it.
    if (-not (Test-PayloadUnchangedSinceAdmission -AdmittedHash $payloadHash -Admission $admission)) {
        throw 'Install sources changed during the build'
    }

    # Versioned layout (#581): health-gate the freshly-built slot IN ISOLATION,
    # then swap the stable `venv` junction onto it. Everything below resolves
    # through `venv` (the link), so the binstubs/task/manifest never change path
    # across versions. No-op in legacy mode (Link == Venv, nothing built a slot).
    if ($VersionedRuntime) {
        if (-not (Test-RuntimeHealthy $VenvPython)) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            throw 'Versioned slot health gate failed'
        }
        [void](Test-PayloadUnchangedSinceAdmission -SourceIdentityOnly)
        if (-not (Invoke-VersionedMarkComplete -PayloadHash $payloadHash)) { throw 'Versioned completion publication failed' }
        if (-not (Invoke-VersionedActivate)) { throw 'Versioned activate failed' }
    }

    # Create binstub -- launch via the venv's signed python (`-m`), never the
    # unsigned console-script trampoline .exe (Smart App Control blocks it).
    # Point it at the stable `venv` link ($LinkPython), never a versions/<v>
    # absolute path a later `gc` could remove.
    if (Test-Path $LinkPython) {
        Write-Binstubs -PythonExe $LinkPython
    }

    # Generate default config
    if (Test-Path $VenvPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c "from agent_bridge.config import load_config, write_default_config; write_default_config(load_config())" 2>$null
        # Machine-local config schema migration (idempotent + atomic). Non-fatal.
        $env:PYTHONUTF8 = '1'
        & $VenvPython -m agent_bridge config migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
        $ErrorActionPreference = $prevEAP
    }

    # Write the deploy manifest BEFORE registering the scheduled task. The
    # manifest is what clears the version drift that bootstrap-check.ps1 keys
    # on; writing it first means a failure/interruption in the (best-effort,
    # possibly-elevation-gated) task step can never leave drift permanently
    # unresolved -- which would otherwise re-trigger the reconcile every session.
    Write-DeployManifest -PayloadHash $payloadHash

    # Register scheduled task
    Register-ScheduledTask_

    # Ensure ~/.local/bin is on user PATH
    $userPath = Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'User'
    if ($publishGlobalBinstubs -and $userPath -and $userPath -notlike "*$LocalBin*") {
        Set-CopilotPersistentEnvironmentVariable -Name 'PATH' -Value "$LocalBin;$userPath" -Target 'User'
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "Added $LocalBin to user PATH"
    } elseif (-not $publishGlobalBinstubs) {
        Write-Ok 'Scoped install left the user PATH unchanged'
    }

    Write-Ok 'agent-bridge installed'
    Write-Host "  Install dir: $InstallDir"
    if ($publishGlobalBinstubs) {
        Write-Host "  Binstub:     $Binstub"
    } else {
        Write-Host '  Binstub:     global compatibility wrapper unchanged (scoped install)'
    }
    Write-Host "  Config:      agent-bridge config show"
    $__ep = Get-ActiveEndpoint
    Write-Host "  API:         http://$($__ep.Bind):$($__ep.Port)"

    # Start service and verify health
    Write-Step 'Starting service after install...'
    Invoke-Start
}

function Invoke-Provision {
    Write-Step 'agent-bridge provision'

    if (-not (Test-Path $LinkPython)) {
        # Preserve the self-provisioning binstub contract: `provision` is the
        # first-use full install when no runtime exists. The task-only path below
        # applies once a healthy runtime is already present.
        Invoke-Install
        return
    }

    # A never-ran S4U task cannot be flipped to the default interactive
    # principal from a normal shell. Route that exact recovery through the
    # existing self-elevating repair instead of rebuilding or restarting the
    # already-healthy runtime.
    $explicitNonInteractive = $NonInteractive -or
        ($env:AGENT_BRIDGE_NONINTERACTIVE -in @('1', 'true', 'yes', 'on'))
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $explicitNonInteractive -and $existing -and
        ($existing.Principal.LogonType -eq 'S4U') -and
        ((Get-ScheduledTaskLastResult -Name $TaskName) -eq $ScheduledTaskHasNotRunResult)) {
        $repair = Get-ScheduledTaskRepairScript
        if (-not (Test-Path -LiteralPath $repair)) {
            Write-Fail "Scheduled-task repair script not found: $repair"
            exit 1
        }

        Write-Step 'Repairing never-ran S4U scheduled task as the default interactive AtLogOn task...'
        $pwsh = Get-PwshPath
        & $pwsh -NoProfile -ExecutionPolicy Bypass -File $repair
        $repairExit = $LASTEXITCODE
        if ($repairExit -ne 0) {
            Write-Fail "Scheduled-task repair failed (exit $repairExit)"
            exit $repairExit
        }
        return
    }

    # Missing tasks, action/trigger drift, and explicitly requested S4U mode all
    # use the normal reconciliation path. This intentionally does not touch the
    # immutable runtime slot or daemon lifecycle.
    Register-ScheduledTask_
}

function Invoke-Uninstall {
    Write-Host ''
    Write-Host '=== agent-bridge uninstall ===' -ForegroundColor Cyan
    if ($DryRun) { Write-Host '(dry run -- nothing will be changed)' -ForegroundColor Yellow }
    Write-Host ''

    if ($DryRun) {
        $proc = Get-RunningProcess
        if ($proc) { Write-Host "[dry-run] would stop agent-bridge (pid=$($proc.Id))" }
        else { Write-Skip 'agent-bridge not running' }
    } else {
        Invoke-Stop
    }

    # Scheduled task
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        if ($DryRun) {
            Write-Host "[dry-run] would remove scheduled task: $TaskName"
        } else {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            Write-Ok 'Scheduled task removed'
        }
    } else {
        Write-Skip "Scheduled task not present: $TaskName"
    }

    if ($publishGlobalBinstubs) {
        foreach ($stub in @($BinstubPs1, $BinstubCmd)) {
            if (Test-Path $stub) {
                if ($DryRun) { Write-Host "[dry-run] would remove binstub: $stub" }
                else { Remove-Item -Force $stub; Write-Ok "Binstub removed: $stub" }
            }
        }
    } else {
        Write-Ok 'Scoped install left the legacy global binstub unchanged'
    }

    if (-not $DryRun) { Remove-SiblingBinstubs }

    if (Test-Path $VenvDir) {
        if ($DryRun) { Write-Host "[dry-run] would remove venv: $VenvDir" }
        else { Remove-Item -Recurse -Force $VenvDir; Write-Ok 'Venv removed' }
    } else {
        Write-Skip "Venv not present: $VenvDir"
    }

    if ($Purge -and (Test-Path $InstallDir)) {
        if ($DryRun) { Write-Host "[dry-run] would PURGE config/DB/auth at: $InstallDir" }
        else { Write-Warn 'Purging config, DB, and auth'; Remove-Item -Recurse -Force $InstallDir }
    } else {
        Write-Skip "Preserved config/DB at $InstallDir (use -Purge to remove)"
    }

    if ($DryRun) { Write-Host 'agent-bridge uninstall dry run complete -- nothing was changed' -ForegroundColor Yellow }
    else { Write-Ok 'agent-bridge uninstalled' }
}


function Get-PwshPath {
    $pwshPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
    if (-not (Test-Path $pwshPath)) {
        $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
        $pwshPath = if ($pwshCmd) { $pwshCmd.Source } else { 'powershell.exe' }
    }
    return $pwshPath
}

function Invoke-Start {
    # -Fresh: used by `update` -- never adopt a pre-existing daemon (it may be the
    # OLD version, or a racer that grabbed the port). Drain first, then spawn the
    # freshly-installed code and gate success on an actual health response.
    param([switch]$Fresh)

    if (-not (Test-Path $LinkPython)) {
        Write-Fail 'agent-bridge not installed. Run: install.ps1 install'
        exit 1
    }

    if (Test-ActiveIsForward) {
        Write-Skip 'agent-bridge: this machine reaches a host bridge through a forward (active.json); not starting a local daemon over it'
        return
    }

    # Resolve the already-healthy no-op FIRST, as its own early return --
    # nothing is disrupted on this path, so the marker below must never be
    # written for it (review finding on an earlier draft of this change:
    # publishing the marker only after a Stop-DaemonProcesses call left a
    # window where the daemon was already stopped without watchdog
    # suppression covering it).
    $proc = Get-RunningProcess
    if ($proc -and -not $Fresh -and (Test-HealthOnce)) {
        Write-Warn "agent-bridge is already running (pid=$($proc.Id))"
        return
    }

    # From here on every path genuinely disrupts something (a fresh drain, a
    # wedged-process replace, or a clean cold start) -- mark the lifecycle
        # before any of that begins. try/finally (not a trap, which PowerShell
        # only fires for terminating errors, not a clean `return`) covers every
        # remaining exit path uniformly.
        Write-UpdateMarker
        try {
    if ($proc) {
        if ($Fresh) {
            Write-Step "Draining existing daemon (pid=$($proc.Id)) to start fresh..."
            Stop-DaemonProcesses | Out-Null
        } else {
            # Process exists but the port does not answer -- a wedged/zombie
            # daemon. Replace it rather than leaving the service unhealthy.
            Write-Warn "agent-bridge process found (pid=$($proc.Id)) but not responding -- restarting"
            Stop-DaemonProcesses | Out-Null
        }
    }

    # Prefer the scheduled task to start the daemon whenever one is registered
    # -- for BOTH headless (S4U/Password, session 0) and at-logon (interactive)
    # tasks. The Task Scheduler owns the resulting process, so it is NOT parented
    # to the installer: a direct spawn here is a child of the installer (often an
    # SSH session) and Windows OpenSSH kills that whole process tree when the
    # session ends, taking the daemon down until the next boot/logon trigger.
    # Starting via Start-ScheduledTask makes the daemon survive the installer
    # session closing regardless of task type. (For an at-logon task this runs in
    # the logged-on user's interactive session; if nobody is logged on -- e.g. an
    # SSH install with no desktop session -- the task can't run, so we fall
    # through to a best-effort direct spawn and advise -NonInteractive.)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        $headless = $task.Principal.LogonType -in @('S4U', 'Password')
        $mode = if ($headless) { 'headless' } else { 'logon session' }
        Write-Step "Starting agent-bridge via scheduled task ($mode)..."
        Start-ScheduledTask -TaskName $TaskName
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            if (Test-HealthOnce) {
                $rp = Get-RunningProcess
                $pidTxt = if ($rp) { "pid=$($rp.Id), " } else { '' }
                Write-Ok ("agent-bridge started ({0}port={1}, {2})" -f $pidTxt, (Get-ActiveEndpoint).Port, $mode)
                return
            }
        }
        # Headless tasks have no interactive-session dependency, so a miss here
        # is a real failure -- report and stop. An at-logon task that didn't come
        # up is most likely "nobody logged on"; fall through to the direct spawn
        # as a last resort so the daemon isn't left down, and hint the durable fix.
        if ($headless) {
            $lastTaskResult = Get-ScheduledTaskLastResult -Name $TaskName
            if (($task.Principal.LogonType -eq 'S4U') -and
                ($lastTaskResult -eq $ScheduledTaskHasNotRunResult)) {
                Write-Warn "Task Scheduler accepted the start request but did not launch the action (LastTaskResult=$lastTaskResult / SCHED_S_TASK_HAS_NOT_RUN)"
                Write-Warn '  This likely means Windows could not acquire the S4U logon token at startup (for example, the account authority was unavailable).'
                Write-Warn '  Clear AGENT_BRIDGE_NONINTERACTIVE and run "install.ps1 provision" to replace it with the default interactive AtLogOn task.'
                return
            }
            Write-Warn 'agent-bridge did not become healthy within 30s via the scheduled task -- check agent-bridge-err.log'
            return
        }
        Write-Warn 'at-logon scheduled task did not yield a healthy daemon (no interactive session?) -- falling back to a direct start'
        Write-Warn '  For an always-on host reached over SSH, reinstall headless: install.ps1 update -NonInteractive'
    }

    # Start the service directly through the bridge's own job-surviving launch
    # (`service start --direct` -> agent_procutil.spawn_daemon): it breaks away
    # from the installer's Job (an SSH session's or an updater's), falls back to
    # the WMI broker when that Job forbids breakaway, roots the daemon at the
    # runtime home rather than the installer's cwd (#1376: never the plugin
    # payload), and gives it its own stdio files, so the long-lived server never
    # holds the installer's std handles open. A PowerShell process launch offers
    # none of those lifetime guarantees.

    # Spawn + health-gate, retrying a few times. A single slow/failed spawn must
    # NOT leave the service down (the previous single-attempt logic exited here,
    # killing the daemon mid-update). Each attempt drains any half-started
    # process before respawning so a stuck port-bind cannot wedge the retry.
    $maxAttempts = 3
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $label = if ($attempt -gt 1) { " (attempt $attempt/$maxAttempts)" } else { '' }
        Write-Step "Starting agent-bridge$label..."

        # The command writes to stderr when the daemon does not come up; under
        # $ErrorActionPreference='Stop' Windows PowerShell 5.1 would turn that
        # into a terminating error and skip the drain and remaining retries.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $LinkPython -m agent_bridge service start --direct 2>&1 | ForEach-Object { Write-Host "  $_" }
        } finally {
            $ErrorActionPreference = $prevEAP
        }

        # Success == the port actually answers /health (not merely "a process
        # exists"). Single-shot probe per second so the loop paces at ~1s.
        for ($i = 0; $i -lt 25; $i++) {
            if (Test-HealthOnce) {
                $rp = Get-RunningProcess
                $pidTxt = if ($rp) { "pid=$($rp.Id), " } else { '' }
                Write-Ok ("agent-bridge started ({0}port={1})" -f $pidTxt, (Get-ActiveEndpoint).Port)
                return
            }
            Start-Sleep -Seconds 1
        }

        Write-Warn "agent-bridge did not become healthy within 25s$label -- draining and retrying"
        Stop-DaemonProcesses | Out-Null
    }

    Write-Fail 'agent-bridge failed to start -- check agent-bridge.log / agent-bridge-err.log'
    exit 1
    } finally {
                Clear-UpdateMarker
    }
}

function Invoke-Stop {
    $proc = Get-RunningProcess
    if (-not $proc) {
        Write-Skip 'agent-bridge not running'
        return
    }

    Write-Step "Stopping agent-bridge (pid=$($proc.Id))..."
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue

    # Wait up to 10s for process to exit and release the port
    $waited = 0
    while ($waited -lt 10) {
        Start-Sleep -Seconds 1
        $waited++
        $check = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $check -or $check.HasExited) { break }
    }

    $check = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if ($check -and -not $check.HasExited) {
        Write-Fail "Process did not stop cleanly"
        return
    }

    # Verify port is actually free (catches orphaned child processes)
    $portInUse = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' }
    if ($portInUse) {
        Write-Warn "Port $Port still in use after stop -- killing occupant (pid=$($portInUse.OwningProcess))"
        Stop-Process -Id $portInUse.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    # Also ensure the integrated credential relay is down. It runs in-process
    # with the bridge (so the kill above usually frees it), but free the port
    # explicitly to catch an orphaned relay.
    $relayInUse = Get-NetTCPConnection -LocalPort $RelayPort -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' }
    if ($relayInUse) {
        Write-Warn "Credential relay port $RelayPort still in use -- killing occupant (pid=$($relayInUse.OwningProcess))"
        Stop-Process -Id $relayInUse.OwningProcess -Force -ErrorAction SilentlyContinue
    }

    Remove-Item -Force $PidFile -ErrorAction SilentlyContinue
    Write-Ok 'agent-bridge stopped'
}

function Invoke-Status {
    $running = $false
    $proc = Get-RunningProcess
    if ($proc) {
        Write-Ok "agent-bridge is running (pid=$($proc.Id))"
        $running = $true

        if (Test-HealthCheck) {
            Write-Ok "Health check passed (port $Port)"
        } else {
            Write-Warn "Process running but health check failed"
        }
    } else {
        Write-Step 'agent-bridge is not running'
    }

    if (Test-Path $LinkPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $version = & $LinkPython -m agent_bridge version 2>$null
        $ErrorActionPreference = $prevEAP
        Write-Ok "Installed: $version"
    } else {
        Write-Step 'Not installed'
    }

    # Show runtime source footprint (local checkout vs marketplace)
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            if ($m.source) {
                $extra = ''
                if ($m.source.kind -eq 'local' -and $m.source.commit) {
                    $extra = " @ $($m.source.commit)$(if ($m.source.dirty) { '+dirty' })"
                }
                Write-Ok "Source: $($m.source.kind) ($($m.source.version))$extra"
            }
        } catch { }
    }

    # Show config summary
    if (Test-Path (Join-Path $InstallDir 'config.yaml')) {
        Write-Ok "Config: $(Join-Path $InstallDir 'config.yaml')"
    }

    # Show scheduled task
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Ok "Scheduled task: $($task.State) ($TaskName)"
    } else {
        Write-Step 'No scheduled task registered'
    }

    if (-not $publishGlobalBinstubs) {
        Write-Step 'Global compatibility binstub left unchanged for the legacy install'
    }

    # Exit non-zero when not installed (used by module update orchestrator)
    if (-not (Test-Path $VenvPython)) {
        exit 1
    }
}

function Test-RuntimeHealthy {
    <# True if the venv python can import the agent-bridge runtime + its key
       deps. Used to (a) decide whether the current venv is worth snapshotting
       and (b) verify a fresh install before declaring the update good (#52).
       Checks uvicorn + credential_relay too -- the exact modules that went
       missing in the observed broken-venv outage. #>
    param([string]$Python)
    if (-not (Test-Path $Python)) { return $false }
    & $Python -c 'import agent_bridge, uvicorn, credential_relay, zdd, single_instance_lease' 2>$null
    return $LASTEXITCODE -eq 0
}

function Backup-Venv {
    <# Snapshot $VenvDir to $VenvDir.bak so a failed update can roll back. Clears
       any stale backup first. Returns $true on success. #>
    $bak = "$VenvDir.bak"
    if (Test-Path $bak) { Remove-Item -Recurse -Force $bak -ErrorAction SilentlyContinue }
    try {
        Copy-Item -Recurse -Force $VenvDir $bak -ErrorAction Stop
        return $true
    } catch {
        Write-Warn "Could not snapshot venv for rollback: $_"
        return $false
    }
}

function Restore-Venv {
    <# Replace a broken $VenvDir with the snapshot at $VenvDir.bak. Returns $true
       on success. #>
    $bak = "$VenvDir.bak"
    if (-not (Test-Path $bak)) { return $false }
    try {
        if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop }
        Move-Item -Force $bak $VenvDir -ErrorAction Stop
        return $true
    } catch {
        Write-Warn "Restore-Venv failed: $_"
        return $false
    }
}

function Remove-VenvBackup {
    $bak = "$VenvDir.bak"
    if (Test-Path $bak) { Remove-Item -Recurse -Force $bak -ErrorAction SilentlyContinue }
}

function Invoke-UpdateSlotBuild {
    <# Build/repair the target slot and (re)install every package into it for
       Invoke-Update. Throws on failure so the caller's rollback runs. Only for
       an absent/incomplete slot -- a completed slot is never rebuilt in place
       (#5472). #>
    param([bool]$UseCutover)
    # Repair venv if python binary is missing (or rebuild if unsigned for SAC).
    # Skipped in cutover mode: the running daemon is holding this venv, so a
    # rebuild would break it -- an in-place package update is enough.
    #
    # Versioned layout: $VenvDir is a FRESH per-version slot (never the running
    # daemon's), so we always build it -- cutover included -- and the immutable
    # guarantee holds by construction.
    if ($VersionedRuntime) {
        if (-not (New-SignedVenv)) { throw "Venv build failed (versions/$SrcVersion)" }
        if (-not (Test-Path $VenvPython)) { throw "Venv build failed (versions/$SrcVersion)" }
        Write-Ok "Built runtime slot versions/$SrcVersion"
    }
    elseif ((-not $useCutover) -and ((-not (Test-Path $VenvPython)) -or ($env:OS -eq 'Windows_NT'))) {
        if ((Test-Path $VenvDir) -or (Get-SignedBasePython)) {
            if (-not (Test-Path $VenvPython)) { Write-Step 'Repairing venv (python binary missing)...' }
            if (-not (New-SignedVenv)) {
                throw 'Venv repair failed'
            }
            if (-not (Test-Path $VenvPython)) {
                throw 'Venv repair failed'
            }
            Write-Ok 'Venv repaired'
        } else {
            throw 'agent-bridge not installed. Run: install.ps1 install'
        }
    }

    # Reinstall package via uv (ssh-manager + credential-relay + agent-bridge)
    Write-Step 'Updating agent-bridge package...'
    # Pre-strip any locked console-script trampoline so uv can overwrite it
    # (Windows denies overwriting an in-use .exe -- os error 5).
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    $SshManagerDir = Resolve-SshManager
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($SshManagerDir) {
        # Refresh the vendored agent-ssh-manager distribution's build cache
        # so a same-version source change lands (#186).
        $sshResult = Invoke-UvPipInstallResilient -SourceDir $SshManagerDir @('--python', $VenvPython, '--reinstall-package', 'agent-ssh-manager', '--refresh-package', 'agent-ssh-manager', "$SshManagerDir", '--quiet')
        $sshOut = $sshResult.Output
        if ($sshResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            if ($sshOut) { Write-Host ($sshOut | Out-String) }
            throw "ssh-manager update failed (exit $($sshResult.ExitCode))"
        }
    } elseif (Test-SshManagerInstalled) {
        Write-Step 'ssh-manager already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # credential-relay: force-reinstall so a local code change propagates even
    # without a version bump (uv otherwise skips a same-version path dep).
    $CredRelayDir = Resolve-CredentialRelay
    if ($CredRelayDir) {
        $crResult = Invoke-UvPipInstallResilient -SourceDir $CredRelayDir @('--python', $VenvPython, '--reinstall-package', 'agent-credential-relay', '--refresh-package', 'agent-credential-relay', "$CredRelayDir", '--quiet')
        $crOut = $crResult.Output
        if ($crResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            if ($crOut) { Write-Host ($crOut | Out-String) }
            throw "credential-relay update failed (exit $($crResult.ExitCode))"
        }
    } elseif (Test-CredentialRelayInstalled) {
        Write-Step 'credential-relay already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # zdd: force-reinstall so a local code change propagates even without a
    # version bump (uv otherwise skips a same-version path dep).
    $ZddDir = Resolve-Zdd
    if ($ZddDir) {
        $zddResult = Invoke-UvPipInstallResilient -SourceDir $ZddDir @('--python', $VenvPython, '--reinstall-package', 'agent-zdd', '--refresh-package', 'agent-zdd', "$ZddDir", '--quiet')
        $zddOut = $zddResult.Output
        if ($zddResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            if ($zddOut) { Write-Host ($zddOut | Out-String) }
            throw "zdd update failed (exit $($zddResult.ExitCode))"
        }
    } elseif (Test-ZddInstalled) {
        Write-Step 'zdd already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate zdd library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # single-instance-lease: force-reinstall so a local code change propagates.
    $SilDir = Resolve-SingleInstanceLease
    if ($SilDir) {
        $silResult = Invoke-UvPipInstallResilient -SourceDir $SilDir @('--python', $VenvPython, '--reinstall-package', 'agent-single-instance-lease', '--refresh-package', 'agent-single-instance-lease', "$SilDir", '--quiet')
        $silOut = $silResult.Output
        if ($silResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            if ($silOut) { Write-Host ($silOut | Out-String) }
            throw "single-instance-lease update failed (exit $($silResult.ExitCode))"
        }
    } elseif (Test-SingleInstanceLeaseInstalled) {
        Write-Step 'single-instance-lease already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate single-instance-lease library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # config-migrate: force-reinstall so a local code change propagates.
    $CfgMigrateDir = Resolve-ConfigMigrate
    if ($CfgMigrateDir) {
        $cmResult = Invoke-UvPipInstallResilient -SourceDir $CfgMigrateDir @('--python', $VenvPython, '--reinstall-package', 'agent-config-migrate', '--refresh-package', 'agent-config-migrate', "$CfgMigrateDir", '--quiet')
        $cmOut = $cmResult.Output
        if ($cmResult.ExitCode -ne 0) {
            $ErrorActionPreference = $prevEAP
            if ($cmOut) { Write-Host ($cmOut | Out-String) }
            throw "config-migrate update failed (exit $($cmResult.ExitCode))"
        }
    } elseif (Test-ConfigMigrateInstalled) {
        Write-Step 'config-migrate already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate config-migrate library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    # --refresh-package agent-procutil: see the matching comment in the
    # initial-install path above.
    $bridgeResult = Invoke-UvPipInstallResilient @('--python', $VenvPython, '--reinstall-package', 'agent-bridge', '--refresh-package', 'agent-bridge', '--reinstall-package', 'agent-procutil', '--refresh-package', 'agent-procutil', '--reinstall-package', 'agent-plugin-resolve', '--refresh-package', 'agent-plugin-resolve', '--reinstall-package', 'agent-dropin-registry', '--refresh-package', 'agent-dropin-registry', '--reinstall-package', 'agent-plugin-activation', '--refresh-package', 'agent-plugin-activation', '--reinstall-package', 'agent-remote-login-shell', '--refresh-package', 'agent-remote-login-shell', '--reinstall-package', 'agent-machine-transport', '--refresh-package', 'agent-machine-transport', '--reinstall-package', 'agent-worktree-identity', '--refresh-package', 'agent-worktree-identity', '--reinstall-package', 'agent-process-telemetry', '--refresh-package', 'agent-process-telemetry', "$PluginDir", '--quiet')
    $bridgeOut = $bridgeResult.Output
    $updateResult = $bridgeResult.ExitCode
    $ErrorActionPreference = $prevEAP
    if ($updateResult -ne 0) {
        if ($bridgeOut) { Write-Host ($bridgeOut | Out-String) }
        throw "Package update failed (exit $updateResult)"
    }

    # Update sibling plugins (e.g. agent-codespaces for codespace: namespace),
    # then strip the uv-regenerated console-script trampoline(s) (SAC-blocked,
    # unused; also clears sibling agent-*.exe pulled into this venv). Both
    # mutate the slot, so they run here -- before the caller's final health
    # check, payload verification, publication and activation, and inside its
    # rollback -- never on an already-published slot (#5472).
    Install-SiblingPlugins -Reinstall
    Remove-ConsoleTrampolines -VenvDir $VenvDir
}

function Invoke-ServingRuntimeReconcile {
    & $VenvPython -m agent_bridge deploy --reconcile-serving --probe-serving --health-timeout 180
    if ($LASTEXITCODE -eq 0) { return }
    if ($LASTEXITCODE -ne 10) {
        throw "Serving runtime reconcile failed (probe exit $LASTEXITCODE); completed slot left unchanged."
    }
    Write-UpdateMarker
    try {
        & $VenvPython -m agent_bridge deploy --reconcile-serving --health-timeout 180
        if ($LASTEXITCODE -ne 0) {
            throw "Serving runtime reconcile failed (exit $LASTEXITCODE); completed slot left unchanged. Resolve the reported ownership/health/cutover failure before retrying."
        }
    } finally {
        Clear-UpdateMarker
    }
}

function Invoke-Update {
    Write-Step 'agent-bridge update'

    if (-not (Ensure-Uv)) { exit 1 }

    # Serialize concurrent installers (see Invoke-Install): don't let an
    # overlapping install/update race serving convergence or rebuild the venv.
    if (-not (Enter-InstallLock)) {
        Write-Fail "Another agent-bridge install held the install lock ($(Join-Path $InstallDir '.install.lock')) for the whole wait -- this run changed nothing (blocked by lock); rerun once it finishes"
        exit 75
    }
    [void](Test-PayloadUnchangedSinceAdmission -SourceIdentityOnly)
    # The transaction's single payload fingerprint (see Invoke-Install).
    $payloadHash = Get-PayloadHash
    if (Test-SlotContentCurrent -PayloadHash $payloadHash) {
        try {
            # Slot/import health proves reusable bytes, not the serving version.
            # This guarded seam never rebuilds, cold-starts or falls back to stop.
            Invoke-ServingRuntimeReconcile
            Write-DeployManifest -PayloadHash $payloadHash
            Write-Ok "agent-bridge $SrcVersion content matched; serving runtime reconciled without rebuilding."
        } finally {
            Exit-InstallLock
        }
        return
    }

    # Refuse to rebuild a published same-version slot in place (#5472) --
    # checked BEFORE the update marker, the drain, or any stop, so a refused
    # update never disturbs the live daemon. See Invoke-Install.
    $admission = Get-SlotAdmission -PayloadHash $payloadHash
    if ($admission -ne 'construct' -and $admission -ne 'reuse') {
        Write-SlotAdmissionRefusal -Admission $admission
        Exit-InstallLock
        exit 1
    }
    [void](Invoke-VersionedSlotClean -PayloadHash $payloadHash)
    $admission = if ($script:RuntimeConstructionReuse) { 'reuse' } else { 'construct' }

    # Mark the live-service update lifecycle as in-progress (ce#5066) so a
    # local liveness watchdog never force-restarts a daemon that is
    # legitimately, briefly down for drain/stop/stage/start or a zero-downtime
    # cutover -- ONLY when a local daemon transition can actually happen.
    # Resolve the forward-route check FIRST: that branch never drains/
    # stops/starts anything local, so marking it would advertise a live
    # transition where none occurs (review finding on an earlier draft).
    # Cleared in the existing try/finally below alongside Exit-InstallLock,
    # so every exit path (success, a cutover-then-fallback, a failed
    # update's rollback, or an unhandled terminating error) covers it
    # uniformly -- PowerShell's `finally` runs even on `exit` within the
    # same call stack, exactly like this codebase already relies on for the
    # install lock.
    $activeForward = Test-ActiveIsForward
    if ($activeForward) {
        Write-Step 'Forwarded host bridge route detected -- updating runtime files without draining/stopping/starting a local daemon'
    }
    if (-not $activeForward) {
        Write-UpdateMarker
    }
    try {

    $predecessorSignature = if ($activeForward) { '' } else { Get-ActiveSignature }

    # Stop running instance first -- a rebuild/repair of the venv (below) must
    # not race a live bridge holding python.exe open.
    $wasRunning = (-not $activeForward) -and ($null -ne (Get-RunningProcess))

    # Thread B: the ZDD active/passive cutover is now the DEFAULT whenever a live
    # daemon is running -- activation always cuts over automatically (no opt-in).
    # It hands off via the installer-internal `agent_bridge deploy` seam (new
    # daemon on a fresh port -> flip routing -> drain + retire the old), so a live
    # dispatch is never collapsed; it falls back to the classic stop-and-swap only
    # when the cutover can't run or fails.
    #
    # Legacy layout: the venv is updated IN PLACE, so cutover requires the venv to
    # already exist (we must not rebuild python.exe under a running daemon).
    # Versioned layout (#581): the new version builds into its OWN slot beside the
    # serving one, so the old daemon's files are never touched -- cutover no longer
    # needs the slot to pre-exist, and we can always build fresh. Remember the
    # currently-active version as the rollback + gc-keep target.
    $prevVersion = ''
    if ($VersionedRuntime) {
        $prevVersion = Get-VersionedCurrent
        $useCutover = $wasRunning -and (-not $activeForward)
        # Cutover onto the *same* slot is impossible (there is only one dir of that
        # name and the live daemon holds it). A same-version refresh downgrades to
        # the classic stop-and-rebuild. Only an active slot that never published
        # completion can reach this: a published one was a no-op or refused above.
        if ($useCutover -and $SrcVersion -eq $prevVersion) {
            Write-Step "Cutover skipped: version $SrcVersion is already active; using classic stop-and-rebuild"
            $useCutover = $false
        }
    } else {
        $useCutover = $wasRunning -and (-not $activeForward) -and (Test-Path $VenvPython)
    }
    if ($useCutover) {
        Write-Step 'Graceful cutover: building the new runtime; will cut over (no stop)'
    }

    # Snapshot the current healthy venv so a failed install can roll back to the
    # previous-good runtime instead of leaving the service DOWN with a broken/
    # empty venv (#52). Only snapshot a venv that actually works -- no point
    # backing up an already-broken one. Skipped in the versioned layout: rollback
    # there is "leave the `venv` link on the previous slot" (the link is only
    # swapped after a healthy build), so no copy is needed.
    $haveBackup = $false
    if ((-not $VersionedRuntime) -and (Test-RuntimeHealthy $VenvPython)) {
        $haveBackup = Backup-Venv
    }

    try {
        if ($wasRunning -and -not $useCutover) {
            $drainTimeout = if ($env:AGENT_BRIDGE_DRAIN_TIMEOUT) {
                [int]$env:AGENT_BRIDGE_DRAIN_TIMEOUT
            } else { 120 }
            if (-not (Invoke-UpdateDrainStop -Signature $predecessorSignature -TimeoutSec $drainTimeout)) {
                $wasRunning = $false
            }
        }

        # A completed slot that already holds exactly this payload (just not
        # the active one) is activated as-is: never recreate its venv or
        # reinstall into it (#5472). Only an absent/incomplete slot is built.
        if ($admission -eq 'reuse') {
            Write-Skip "Runtime slot versions/$SrcVersion is already complete for this payload -- activating it without rebuilding"
        } else {
            Invoke-UpdateSlotBuild -UseCutover $useCutover
        }

        # Verify the freshly-installed runtime imports before declaring success.
        # Catches a half-installed venv (e.g. a wheel/dependency gap like #51)
        # while we can still roll back -- rather than starting a broken service.
        if (-not (Test-RuntimeHealthy $VenvPython)) {
            throw 'Post-install verification failed (agent_bridge / uvicorn / credential_relay not importable)'
        }
        # The build read the sources after $payloadHash was taken: if they no
        # longer hash to it, fail through the rollback below (the never-
        # published slot is discarded and the previous runtime restarted)
        # before the marker, manifest or activation.
        if (-not (Test-PayloadUnchangedSinceAdmission -AdmittedHash $payloadHash -Admission $admission)) {
            throw 'Install sources changed during the build'
        }
        # Machine-local config schema migration (idempotent + atomic). Non-fatal.
        try {
            $env:PYTHONUTF8 = '1'
            & $VenvPython -m agent_bridge config migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
        } catch {
            Write-Step "Config migration skipped: $_"
        }
        Write-Ok 'Package updated'
        # Completion is still part of the protected build: a late source
        # identity failure must restart a predecessor stopped above.
        if ($VersionedRuntime) {
            [void](Test-PayloadUnchangedSinceAdmission -SourceIdentityOnly)
            if (-not (Invoke-VersionedMarkComplete -PayloadHash $payloadHash)) { throw 'Versioned completion publication failed' }
        }
    }
    catch {
        Write-Fail "Update failed: $_"
        if ($VersionedRuntime) {
            # The `venv` link was never swapped (activate runs only after a healthy
            # build), so the previous slot is still active. Discard the half-built
            # new slot -- only one this run constructed: a reused slot was already
            # published (create-once, #5472) and is never deleted here -- and
            # restart the previous version if the classic path stopped it.
            # The attached construction owner reclaims only its own unfinished
            # candidate after rollback settles; no unguarded directory deletion.
            if ($wasRunning -and -not $useCutover -and -not $activeForward) {
                if (-not (Invoke-UpdateStart -Signature $predecessorSignature -Message 'Restarting the previous version...')) { $wasRunning = $false }
            }
            $prevLabel = if ($prevVersion) { "versions/$prevVersion" } else { 'the previous runtime' }
            Write-Warn "Update failed; kept the previous runtime (venv -> $prevLabel)."
        }
        elseif ($haveBackup) {
            Write-Step 'Rolling back to the previous venv...'
            if (Restore-Venv) {
                Write-Ok 'Previous venv restored'
                if ($wasRunning -and -not $activeForward) {
                    if (-not (Invoke-UpdateStart -Signature $predecessorSignature -Message 'Restarting the previous service...')) { $wasRunning = $false }
                }
            } else {
                Write-Fail 'Rollback failed -- run "install.ps1 install" to rebuild the runtime'
            }
        } else {
            Write-Warn 'No healthy venv snapshot to roll back to -- run "install.ps1 install" to rebuild the runtime'
        }
        exit 1
    }

    # Success: discard the rollback snapshot.
    Remove-VenvBackup

    # Versioned layout (#581): the new slot is built + verified; now atomically
    # swap the stable `venv` junction onto it. In cutover mode this makes `venv`
    # (which the scheduled-task launcher resolves through) point at the new slot,
    # so the daemon `agent-bridge deploy` brings up runs the new code -- while the
    # OLD daemon keeps serving from its own immutable slot until drained. No-op in
    # legacy mode.
    if ($VersionedRuntime) {
        if (-not (Invoke-VersionedActivate)) { throw 'Versioned activate failed' }
    }

    # Update binstub -- launch via the venv's signed python (`-m`), never the
    # unsigned console-script trampoline .exe (Smart App Control blocks it).
    # Point it at the stable `venv` link ($LinkPython), never a versions/<v>
    # absolute a later `gc` could remove.
    if (Test-Path $LinkPython) {
        Write-Binstubs -PythonExe $LinkPython
    }

    # Update deploy manifest BEFORE the scheduled task (see the install path):
    # clearing drift must not depend on the best-effort task step succeeding.
    Write-DeployManifest -PayloadHash $payloadHash

    # Scheduled task: routine updates NEVER rewrite it. The task is version-stable
    # bootstrap infrastructure (its action points at the start-agent-bridge.ps1
    # supervisor, which resolves the live runtime from the `current-version`
    # marker at boot), so a version update carries zero task changes. Rewriting an
    # existing (esp. S4U/boot) task needs elevation and churns/breaks a working
    # auto-start -- so `update` only *creates* the task when it is absent and
    # otherwise leaves it entirely untouched. Deliberate (re)provisioning and
    # repair are the explicit, elevation-aware `provision` action's job.
    Ensure-ScheduledTask

    # Bring the new build into service. The zero-downtime path hands off via the
    # ZDD cutover (`agent-bridge deploy`: new daemon on a fresh port -> flip the
    # routing table -> drain + retire the old one), so a live dispatch is never
    # collapsed. The classic path just (re)starts -- the old daemon was already
    # stopped above. Launch via the `venv` link ($LinkPython) so the process
    # resolves through the junction (never a versions/<v> absolute).
    if ($activeForward) {
        Write-Step 'Forwarded host bridge route still active -- not starting a local daemon'
    }
    elseif ($useCutover) {
        # Warm the freshly-built slot before the timed cutover (#864): the slot's
        # first full-app start is cold -- Python compiles the whole app graph and,
        # on a managed Windows box, Defender / Smart App Control scans the freshly
        # written venv files on first *execute*. A full-app import here pays that
        # one-time cost up front (pycache + AV scan) so the cutover's passive
        # becomes healthy well within the health window instead of timing out and
        # rolling back to a stop-restart. Best-effort; never fails the deploy.
        Write-Step 'Warming the new runtime slot before cutover...'
        & $LinkPython -c 'import agent_bridge.app, agent_bridge.__main__, agent_bridge.session_manager' 2>&1 | Out-Null

        Write-Step 'Cutting over to the new build (zero-downtime)...'
        # Give the cutover a generous health window: a cold fresh-build first start
        # (even after the warm-up above) can outlast the 60s deploy default, which
        # is tuned for warm standalone cutovers -- see #864.
        & $LinkPython -m agent_bridge deploy --force --health-timeout 180 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -eq 0) {
            Write-Ok 'Cutover complete (zero-downtime)'
        } else {
            Write-Warn 'Cutover failed -- falling back to a stop-and-restart swap'
            if (Invoke-UpdateDrainStop -Signature $predecessorSignature -TimeoutSec 30) {
                Invoke-UpdateStart -Signature $predecessorSignature | Out-Null
            }
        }
    } else {
        Invoke-UpdateStart -Signature $predecessorSignature | Out-Null
    }

    # Versioned layout: prune old version slots now that the new one is healthy
    # and active, keeping current + the previous-good (rollback) + any live-pid-
    # pinned slot (a daemon still draining mid-cutover). Best-effort.
    if ($VersionedRuntime) {
        Invoke-VersionedGc -KeepPrev $prevVersion
    }

    Write-Ok 'Update complete'
    } finally {
            Clear-UpdateMarker
    }
}

# -- Dispatch ----------------------------------------------------------------

function Write-InstallerLifecycle {
    param([string]$Outcome)

    $savedExit = Get-Variable LASTEXITCODE -Scope Global -ValueOnly -ErrorAction SilentlyContinue
    try {
        $python = Get-BootstrapPython
        $recorder = Join-Path $PluginDir 'libs/zdd/src/zdd/lifecycle.py'
        if (-not (Test-Path -LiteralPath $recorder -PathType Leaf)) {
            $recorder = Join-Path $PluginDir '../../libs/zdd/src/zdd/lifecycle.py'
        }
        if (-not $python -or -not (Test-Path -LiteralPath $recorder -PathType Leaf)) {
            [Console]::Error.WriteLine('[WARN] Lifecycle record unavailable: bootstrap Python or payload recorder is missing')
            return
        }
        $diagnostic = ''
        $arguments = @('-I', $recorder, 'record', '--config-dir', $InstallDir,
            '--service', 'agent-bridge', '--action', $script:InstallerLifecycleAction,
            '--outcome', $Outcome, '--pid', "$PID", '--version', "$SrcVersion")
        $null = Invoke-Hidden -FilePath $python -ArgList $arguments -ErrorOutput ([ref]$diagnostic)
        if ($diagnostic) { [Console]::Error.Write($diagnostic) }
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine('[WARN] Lifecycle recorder failed; installer outcome is unchanged')
        }
    } catch {
        [Console]::Error.WriteLine("[WARN] Lifecycle recorder unavailable: $($_.Exception.Message)")
    } finally {
        $global:LASTEXITCODE = $savedExit
    }
}

$script:InstallerLifecycleAction = if ($Action -in @('install', 'provision')) { 'install' }
    elseif ($Action -in @('update', 'start', 'stop')) { $Action } else { '' }
$script:InstallerLifecycleOutcome = 'fail'
if ($script:InstallerLifecycleAction) { Write-InstallerLifecycle begin }
try {
    Initialize-SourceRuntime
    switch ($Action) {
        'install'   { Invoke-Install }
        'uninstall' { Invoke-Uninstall }
        'start'     { Invoke-Start }
        'stop'      { Invoke-Stop }
        'status'    { Invoke-Status }
        'update'    { Invoke-Update }
        'stamp'     { Invoke-Stamp }
        'provision' { Invoke-Provision }
    }
    $script:InstallerLifecycleOutcome = 'ok'
} finally {
    try {
        try {
            if (Test-Path -LiteralPath 'Function:\Stop-RuntimeConstruction') {
                Stop-RuntimeConstruction
            }
        } finally {
            # Release the install lock even when construction cleanup fails.
            Exit-InstallLock
        }
    } catch {
        $script:InstallerLifecycleOutcome = 'fail'
        throw
    } finally {
        if ($script:InstallerLifecycleAction) {
            Write-InstallerLifecycle $script:InstallerLifecycleOutcome
        }
    }
}
