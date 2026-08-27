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
    [switch]$ZeroDowntime
)

Set-StrictMode -Version 2.0
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

# -- Output helpers (PS5-safe) -----------------------------------------------

function Write-Ok   { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Step { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }

# -- Paths -------------------------------------------------------------------

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$InstallDir = Join-Path $env:USERPROFILE '.agent-bridge'
$VenvDir    = Join-Path $InstallDir 'venv'
$LocalBin   = Join-Path $env:USERPROFILE '.local\bin'
$BinstubCmd = Join-Path $LocalBin 'agent-bridge.cmd'
$BinstubPs1 = Join-Path $LocalBin 'agent-bridge.ps1'
$Binstub    = $BinstubPs1   # primary entry point (shown in summaries)
$PidFile    = Join-Path $InstallDir 'agent-bridge.pid'
$TaskName   = 'Agent Bridge'
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
$pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
if (Test-Path $pyprojForVer) {
    $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
    if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
}
if (-not $SrcVersion) {
    Write-Fail 'Cannot determine plugin version from pyproject.toml (required for the versioned runtime).'
    exit 1
}
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
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return $null
}

function Invoke-NativeCapture {
    param([Parameter(Mandatory)][scriptblock]$Command)

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = (& $Command 2>&1 | Out-String -Width 4096).Trim()
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }
}

function Ensure-Uv {
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
    if ($arch -eq 'AMD64') {
        $asset = 'uv-x86_64-pc-windows-msvc.zip'
    } elseif ($arch -eq 'ARM64') {
        $asset = 'uv-aarch64-pc-windows-msvc.zip'
    } else {
        Write-Fail "uv bootstrap does not support Windows architecture: $arch"
        return $false
    }

    New-Item -ItemType Directory -Path $toolDir -Force | Out-Null
    $urlTemplate = $env:AGENT_BRIDGE_UV_BOOTSTRAP_URL
    if (-not $urlTemplate) {
        $urlTemplate = 'https://github.com/astral-sh/uv/releases/latest/download/{asset}'
    }
    $url = $urlTemplate.Replace('{asset}', $asset)
    $archive = [IO.Path]::GetTempFileName()
    $staging = Join-Path $InstallDir ".uv-stage-$PID"
    try {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $client = New-Object Net.WebClient
        $client.Headers['User-Agent'] = 'agent-bridge-bootstrap'
        $client.DownloadFile($url, $archive)
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
        Write-Fail "Failed to vendor uv: $_"
        return $false
    } finally {
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    $result = Invoke-NativeCapture { & $uvPath --version }
    if ($result.ExitCode -ne 0) {
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Write-Fail "Vendored uv is not executable: $($result.Output)"
        return $false
    }
    $env:PATH = "$toolDir;$env:PATH"
    Write-Ok "Vendored uv into $toolDir"
    return $true
}

function Get-PayloadHash {
    <# Content fingerprint of the RUNTIME PAYLOAD (#935/#776/ce#811). sha256 over
       the sorted list of "<relpath>:<per-file sha256>" for pyproject.toml plus
       every file under src/ and libs/ (excluding caches/build artifacts). Unlike
       the old pyproject-only fingerprint, a src/-only edit WITHOUT a version bump
       changes this value, so the completion marker + the live-slot content guards
       detect real content drift, not just a pyproject/version change. The value
       is re-baselined by the accompanying version bump (a fresh install records
       the new-algorithm hash), so the algorithm change is self-healing. Never
       throws -> '' on error. #>
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $base = (Resolve-Path $PluginDir).Path
        $files = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
        $pp = Join-Path $base 'pyproject.toml'
        if (Test-Path $pp) { $files.Add((Get-Item $pp)) }
        foreach ($sub in @('src', 'libs')) {
            $d = Join-Path $base $sub
            if (Test-Path $d) {
                Get-ChildItem -Path $d -Recurse -File -Force -ErrorAction SilentlyContinue |
                    Where-Object {
                        $_.FullName -notmatch '[\\/](__pycache__|\.venv|venv|\.pytest_cache|\.mypy_cache|build|dist|[^\\/]+\.egg-info)[\\/]' -and
                        $_.Extension -ne '.pyc'
                    } | ForEach-Object { $files.Add($_) }
            }
        }
        $entries = foreach ($f in $files) {
            $rel = ($f.FullName.Substring($base.Length).TrimStart('\', '/')) -replace '\\', '/'
            $fh = [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($f.FullName))).Replace('-', '').ToLower()
            "${rel}:${fh}"
        }
        $joined = [string]::Join("`n", ($entries | Sort-Object))
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))
        return (-join ($bytes | ForEach-Object { $_.ToString('x2') }))
    } catch { return '' }
}

function Invoke-VersionedSlotClean {
    <# Ensure the target slot exists, tossing it first if a prior build left it
       INCOMPLETE (no completion marker) so we never `uv venv --allow-existing`
       over a corpse (#935). The current/active slot is never tossed. No-op in
       legacy mode. #>
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return }
    & $py $vr --root $InstallDir --link-name 'venv' slot $SrcVersion --clean-incomplete 2>&1 |
        ForEach-Object { Write-Step $_ }
}

function Invoke-VersionedMarkComplete {
    <# Write the slot's completion marker AFTER its isolated health gate passed,
       so the slot is provably a healthy, complete build. A crashed / watchdog-
       killed install never reaches here, leaving its slot markerless and thus
       tossable + retryable (#935). No-op in legacy mode. #>
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    $mcArgs = @($vr, '--root', $InstallDir, '--link-name', 'venv', 'mark-complete', $SrcVersion)
    $ph = Get-PayloadHash
    if ($ph) { $mcArgs += @('--payload-hash', $ph) }
    & $py @mcArgs 2>&1 | ForEach-Object { Write-Step $_ }
}

function Get-VersionedCurrent {
    <# The version the `venv` link currently points at (empty for a legacy real
       venv or a fresh box). Used to remember the previous-good slot as a gc
       keep + rollback target. #>
    if (-not $VersionedRuntime) { return '' }
    $vr = Join-Path $ScriptDir 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return '' }
    $out = & $py $vr --root $InstallDir --link-name 'venv' current 2>$null
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

       Fail-safe: any error (no python, helper missing, unparseable) returns
       $false, so the installer falls through to its existing behavior and a
       genuine install is never skipped. A deliberate force-rebuild remains a
       dedicated command (`versioned_runtime toss` + re-install / `service
       restart`), never a silent side effect of a routine same-version reconcile. #>
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
        $icArgs = @($vr, '--root', $InstallDir, '--link-name', 'venv', 'is-complete', $SrcVersion)
        $ph = Get-PayloadHash
        if ($ph) { $icArgs += @('--expect-hash', $ph) }
        & $py @icArgs 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Test-SlotIsLiveDifferentContent {
    <# True iff the target version IS the active slot, that slot is a COMPLETE
       build whose recorded content hash DIFFERS from the payload we would
       install, AND a daemon is currently running. In that state an Invoke-Install
       (which does NOT stop the daemon first) would rebuild the live slot in place
       and stomp the running runtime's python.exe/site-packages (ce#776/#777,
       WinError 2, dotfiles#1612). A version slot is immutable: new content must
       go under a NEW version (bump), or via `update` (which stops before it
       rebuilds). Fail-safe: any error returns $false (never blocks an install). #>
    if (-not $VersionedRuntime) { return $false }
    try {
        $vr = Join-Path $ScriptDir 'versioned_runtime.py'
        $py = if (Test-Path $LinkPython) { $LinkPython }
              elseif (Test-Path $VenvPython) { $VenvPython }
              else { Get-BootstrapPython }
        if (-not $py -or -not (Test-Path $vr)) { return $false }
        $cur = (& $py $vr --root $InstallDir --link-name 'venv' current 2>$null | Out-String).Trim()
        if (-not $cur -or $cur -ne $SrcVersion) { return $false }        # not the active slot -> normal path
        & $py $vr --root $InstallDir --link-name 'venv' is-complete $SrcVersion 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }                       # not complete -> toss+rebuild is safe
        $ph = Get-PayloadHash
        if (-not $ph) { return $false }                                  # no hash -> don't block
        & $py $vr --root $InstallDir --link-name 'venv' is-complete $SrcVersion --expect-hash $ph 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $false }                       # content MATCHES -> Test-SlotContentCurrent no-ops
        return ($null -ne (Get-RunningProcess))                          # differs + live daemon -> would stomp
    } catch { return $false }
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
       in-flight install lands the version). A non-contention error degrades to
       "proceed WITHOUT the lock" so a lock fault can never wedge the installer. #>
    param([int]$TimeoutSec = 150)
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
            # retry until the deadline. Any OTHER IOException (disk full, FS
            # error) is a real fault: warn + degrade to lock-free rather than
            # masking it as contention and spinning silently (ce#802 review).
            $win32 = $_.Exception.HResult -band 0xFFFF
            if ($win32 -eq 32 -or $win32 -eq 33) {   # ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION
                if ((Get-Date) -ge $deadline) { return $false }
                Start-Sleep -Milliseconds 750
            } else {
                Write-Warn "Install-lock IO error (not contention): $($_.Exception.Message) -- proceeding WITHOUT the lock."
                return $true
            }
        } catch {
            Write-Warn "Install-lock error: $($_.Exception.Message) -- proceeding WITHOUT the lock."
            return $true   # non-contention fault -> degrade to today's lock-free behavior
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
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            $result = Invoke-NativeCapture {
                & py "-$v" -c 'import sys;print(sys.executable)'
            }
            if ($result.ExitCode -eq 0 -and $result.Output) {
                $cands += $result.Output
            }
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
    # #935: toss an INCOMPLETE prior slot first so we never `uv venv
    # --allow-existing` over a half-built corpse (the current/active slot is
    # never tossed). No-op in legacy mode.
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
    $result = Invoke-NativeCapture {
        & uv venv $VenvDir --python 3.10 --allow-existing
    }
    if ($result.ExitCode -ne 0) {
        $result = Invoke-NativeCapture { & uv venv $VenvDir --allow-existing }
    }
    return (Test-Path $VenvPython)
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
# copy: the ACTIVE versioned runtime slot (``$VenvPython`` = versions/<v>) and the
# retired legacy top-level ``~/.agent-bridge/venv`` (the install-contract-v3
# leftover that agent-codespaces' old installer vendored into). We prune both:
# uv-uninstall first (clean dist-info removal), then belt-and-suspenders remove any
# raw-copied dirs, then probe importability from each venv's OWN interpreter.
function Remove-VendoredProviders {
    $modules = @('agent_codespaces', 'agent_containers')
    $pkgs    = @('agent-codespaces', 'agent-containers')
    $legacyPython = if ($env:OS -eq 'Windows_NT') {
        Join-Path $InstallDir 'venv\Scripts\python.exe'  # runtime-resolution: allow legacy-venv cleanup (prune, not launch)
    } else {
        Join-Path $InstallDir 'venv/bin/python'  # runtime-resolution: allow legacy-venv cleanup (prune, not launch)
    }
    $targets = @($VenvPython, $legacyPython) |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
    if (-not $targets) {
        Write-Step "No bridge venv yet -- nothing to prune (#1643)"
        return
    }
    foreach ($py in $targets) {
        foreach ($pkg in $pkgs) {
            & uv pip uninstall --python $py $pkg 2>&1 | Out-Null
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
    param([int]$TimeoutSec = 120)
    $bridgeExe = Join-Path $VenvDir 'Scripts\agent-bridge.exe'
    if (-not (Test-Path $bridgeExe)) { return }
    Write-Step "Draining in-flight sessions (up to ${TimeoutSec}s)..."
    try {
        & $bridgeExe drain --timeout $TimeoutSec --force 2>&1 | Out-Null
        Write-Ok 'Drain window complete'
    } catch {
        Write-Warn 'Drain reported busy sessions -- proceeding with swap'
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
    # The manifest `venv` field records the stable `venv` link ($LinkDir), never
    # a versions/<v> slot -- consumers resolve the runtime through the link.
    Write-DeployManifestFor -Service 'agent-bridge' -Plugin 'agent-bridge' `
        -InstallPath $InstallDir -PluginPath $PluginDir -VenvPath $LinkDir
}

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
            content_hash = (Get-PayloadHash)
        }
        venv           = ($VenvPath -replace '\\', '/')
        runtime        = 'python'
    }

    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
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
    $binDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    foreach ($r in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $rSrc = Join-Path $PSScriptRoot $r
        if (Test-Path $rSrc) { Copy-Item $rSrc (Join-Path $binDir $r) -Force }
    }
    $rootLit = $InstallDir -replace "'", "''"
    $ps1 = @'
$env:PYTHONUTF8 = '1'
$_root = '__ROOT__'
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
function _Resolve-Py {
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $_resolver) { $env:AGENT_RT_ROOT = $_root; . $_resolver }
    return $AgentRtPy
}
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_bridge @args; exit $LASTEXITCODE }
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
if ($_py) { & $_py -m agent_bridge @args; exit $LASTEXITCODE }
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

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE into
    # ~/.agent-bridge/snapshots/<ver>/, record markers, and deploy the self-
    # provisioning binstub -- deferring the heavy venv build (and the daemon/service
    # registration) to the binstub's first use. No venv, no uv; fits a sessionStart
    # grace window and NEVER holds the marketplace payload open.
    Write-Host ''; Write-Host '=== agent-bridge stamp (defer runtime to first use) ===' -ForegroundColor Cyan; Write-Host ''
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    $snapTmp = "$snapDir.tmp-$PID"
    if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
    $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
    Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
    }
    if (Test-Path $snapDir) { Remove-Item $snapDir -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $snapDir, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    Write-Ok "Snapshot: $snapDir"
    Write-Binstubs
    Write-Ok 'Stamped: agent-bridge binstub on PATH; runtime provisions on first use.'
}

function Invoke-Install {
    Write-Host ''
    Write-Host '=== agent-bridge install ===' -ForegroundColor Cyan
    Write-Host ''

    if (-not (Ensure-Uv)) { exit 1 }

    # Check for migration from old installer
    Invoke-MigrationCheck

    # Strict same-content no-op (ce#776/#777): if the target version is already
    # the active slot AND healthy with matching content, do NOT rebuild it -- the
    # slot IS the running daemon's live venv, and overwriting it in place is the
    # WinError-2 crash cascade (dotfiles#1612). Idempotency is keyed on content,
    # not the mutable version label. A force rebuild/restart stays a dedicated
    # command, never a routine-reconcile side effect.
    if (Test-SlotContentCurrent) {
        Write-Ok "agent-bridge $SrcVersion already installed and healthy (content match) -- no-op (no venv rebuild, no restart)."
        Write-DeployManifest   # re-assert the manifest so version drift clears and this never re-triggers every session
        return
    }

    # Serialize concurrent installers (ce#776/#777 follow-up): an overlapping
    # install of the same version must not rebuild the venv over an in-flight one
    # (the racing-rebuild that yields a DOA slot). Wait for any in-flight install;
    # on timeout DEFER -- it will land the version.
    if (-not (Enter-InstallLock)) {
        Write-Warn 'Another agent-bridge install is in progress -- deferring this run (the in-flight install lands the version). No-op.'
        return
    }
    # Re-check after acquiring: the install we waited on may have JUST landed this
    # exact content, so our rebuild would be redundant (and a live-slot stomp).
    if (Test-SlotContentCurrent) {
        Write-Ok "agent-bridge $SrcVersion already installed and healthy after lock wait -- no-op."
        Write-DeployManifest
        Exit-InstallLock
        return
    }

    # Never rebuild a LIVE slot in place with DIFFERENT content (ce#776/#777).
    # Invoke-Install does not stop the daemon before New-SignedVenv, so rebuilding
    # the active slot while a daemon runs from it overwrites its python.exe in
    # place (WinError 2, dotfiles#1612). Slots are immutable: deploy new content
    # under a NEW version (bump), or via `update` (which stops before rebuilding).
    # Only fires for that exact stomp shape; normal drift ($SrcVersion != current)
    # is unaffected.
    if (Test-SlotIsLiveDifferentContent) {
        Write-Warn "agent-bridge $SrcVersion is the ACTIVE slot but the payload content differs and a daemon is running from it -- refusing an in-place rebuild that would stomp the live runtime (dotfiles#1612). Bump the version to deploy new content, or run 'update' / stop the service first. No-op."
        Exit-InstallLock
        return
    }

    # Create directories
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }

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
        # #186 CodespaceConfigSource regression). NOTE the dist name is
        # `ssh-manager` (renamed from the old `agent-ssh-manager`); using the old
        # name here silently no-ops the reinstall. #177/#186
        $sshOut = & uv pip install --python $VenvPython "$SshManagerDir" --reinstall-package ssh-manager --refresh-package ssh-manager --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "ssh-manager install failed (exit $LASTEXITCODE)"
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
        $crOut = & uv pip install --python $VenvPython "$CredRelayDir" --reinstall-package agent-credential-relay --refresh-package agent-credential-relay --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "credential-relay install failed (exit $LASTEXITCODE)"
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
        $zddOut = & uv pip install --python $VenvPython "$ZddDir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "zdd install failed (exit $LASTEXITCODE)"
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
        $silOut = & uv pip install --python $VenvPython "$SilDir" --reinstall-package agent-single-instance-lease --refresh-package agent-single-instance-lease --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "single-instance-lease install failed (exit $LASTEXITCODE)"
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
        $cmOut = & uv pip install --python $VenvPython "$CfgMigrateDir" --reinstall-package agent-config-migrate --refresh-package agent-config-migrate --quiet 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "config-migrate install failed (exit $LASTEXITCODE)"
            if ($cmOut) { Write-Host ($cmOut | Out-String) }
            throw 'config-migrate install failed'
        }
    } elseif (Test-ConfigMigrateInstalled) {
        Write-Step 'config-migrate already installed in venv (marketplace layout)'
    } else {
        throw 'Cannot locate config-migrate library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
    }
    $bridgeOut = & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1
    $installResult = $LASTEXITCODE
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

    # Versioned layout (#581): health-gate the freshly-built slot IN ISOLATION,
    # then swap the stable `venv` junction onto it. Everything below resolves
    # through `venv` (the link), so the binstubs/task/manifest never change path
    # across versions. No-op in legacy mode (Link == Venv, nothing built a slot).
    if ($VersionedRuntime) {
        if (-not (Test-RuntimeHealthy $VenvPython)) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            throw 'Versioned slot health gate failed'
        }
        Invoke-VersionedMarkComplete
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
    Write-DeployManifest

    # Register scheduled task
    Register-ScheduledTask_

    # Ensure ~/.local/bin is on user PATH
    $userPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if ($userPath -and $userPath -notlike "*$LocalBin*") {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$userPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "Added $LocalBin to user PATH"
    }

    Write-Host ''
    Write-Ok 'agent-bridge installed'
    Write-Host "  Install dir: $InstallDir"
    Write-Host "  Binstub:     $Binstub"
    Write-Host "  Config:      agent-bridge config show"
    $__ep = Get-ActiveEndpoint
    Write-Host "  API:         http://$($__ep.Bind):$($__ep.Port)"

    # Start service and verify health
    Write-Host ''
    Write-Step 'Starting service after install...'
    Invoke-Start
}

function Invoke-Provision {
    Write-Host ''
    Write-Host '=== agent-bridge provision ===' -ForegroundColor Cyan
    Write-Host ''

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
    Write-Host ''

    Invoke-Stop

    # Remove scheduled task
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Scheduled task removed'
    }

    foreach ($stub in @($BinstubPs1, $BinstubCmd)) {
        if (Test-Path $stub) {
            Remove-Item -Force $stub
            Write-Ok "Binstub removed: $stub"
        }
    }

    Remove-SiblingBinstubs

    if (Test-Path $VenvDir) {
        Remove-Item -Recurse -Force $VenvDir
        Write-Ok 'Venv removed'
    }

    if ($Purge -and (Test-Path $InstallDir)) {
        Write-Warn 'Purging config, DB, and auth'
        Remove-Item -Recurse -Force $InstallDir
    } else {
        Write-Skip "Preserved config/DB at $InstallDir (use -Purge to remove)"
    }

    Write-Ok 'agent-bridge uninstalled'
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

    # Decide what to do about anything already serving.
    $proc = Get-RunningProcess
    if ($proc) {
        if ($Fresh) {
            Write-Step "Draining existing daemon (pid=$($proc.Id)) to start fresh..."
            Stop-DaemonProcesses | Out-Null
        } elseif (Test-HealthOnce) {
            Write-Warn "agent-bridge is already running (pid=$($proc.Id))"
            return
        } else {
            # Process exists but the port does not answer -- a wedged/zombie
            # daemon. Replace it rather than leaving the service unhealthy.
            Write-Warn "agent-bridge process found (pid=$($proc.Id)) but not responding -- restarting"
            Stop-DaemonProcesses | Out-Null
        }
    }

    $logFile = Join-Path $InstallDir 'agent-bridge.log'
    $errFile = Join-Path $InstallDir 'agent-bridge-err.log'

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

    # Start the service through a DETACHED, hidden pwsh launched via
    # ShellExecute (no -NoNewWindow / no redirection on THIS call, so handles
    # are NOT inherited from the installer). That inner pwsh does the redirected
    # Start-Process and records the pid. Without this indirection the long-lived
    # uvicorn server inherits the installer's std handles; when install.ps1 is
    # run with its output redirected or piped, the server holds that handle open
    # and the installer appears to hang after "Update complete".
    # #1376: pin both the inner python AND its conhost host to the runtime home,
    # never the installer's cwd (the installed-plugins payload dir). -NoNewWindow
    # keeps this conhost alive hosting the long-lived daemon, so without an
    # explicit working dir it would hold the payload folder open and a later
    # ``copilot plugin update agent-bridge`` would fail (os error 32) and empty it.
    $inner = @"
`$p = Start-Process -FilePath '$($LinkPython -replace "'", "''")' -ArgumentList '-m','agent_bridge','start' -WorkingDirectory '$($InstallDir -replace "'", "''")' -NoNewWindow -PassThru -RedirectStandardOutput '$($logFile -replace "'", "''")' -RedirectStandardError '$($errFile -replace "'", "''")'
Set-Content -Path '$($PidFile -replace "'", "''")' -Value `$p.Id
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($inner))
    $pwshForHeadless = Get-PwshPath

    # Spawn + health-gate, retrying a few times. A single slow/failed spawn must
    # NOT leave the service down (the previous single-attempt logic exited here,
    # killing the daemon mid-update). Each attempt drains any half-started
    # process before respawning so a stuck port-bind cannot wedge the retry.
    $maxAttempts = 3
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $label = if ($attempt -gt 1) { " (attempt $attempt/$maxAttempts)" } else { '' }
        Write-Step "Starting agent-bridge$label..."

        # Launch the detached, hidden pwsh through conhost --headless so Windows
        # Terminal (when configured as the default terminal app) cannot capture it
        # as a visible window/tab -- -WindowStyle Hidden alone is ignored by the
        # DefTerm handoff. ShellExecute (no -NoNewWindow / no redirection on THIS
        # call) is preserved so the long-lived python.exe (-m agent_bridge) does
        # not inherit the installer's std handles.
        Start-Process -FilePath 'conhost.exe' `
            -ArgumentList @('--headless', "`"$pwshForHeadless`"", '-NoProfile', '-WindowStyle', 'Hidden', '-EncodedCommand', $encoded) `
            -WorkingDirectory $InstallDir `
            -WindowStyle Hidden | Out-Null

        # Success == the port actually answers /health (not merely "a process
        # exists"). Single-shot probe per second so the loop paces at ~1s.
        for ($i = 0; $i -lt 25; $i++) {
            Start-Sleep -Seconds 1
            if (Test-HealthOnce) {
                $rp = Get-RunningProcess
                $pidTxt = if ($rp) { "pid=$($rp.Id), " } else { '' }
                Write-Ok ("agent-bridge started ({0}port={1})" -f $pidTxt, (Get-ActiveEndpoint).Port)
                return
            }
        }

        Write-Warn "agent-bridge did not become healthy within 25s$label -- draining and retrying"
        Stop-DaemonProcesses | Out-Null
    }

    Write-Fail 'agent-bridge failed to start -- check agent-bridge.log / agent-bridge-err.log'
    exit 1
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
        Write-Ok "Scheduled task: $($task.State)"
    } else {
        Write-Step 'No scheduled task registered'
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

function Invoke-Update {
    Write-Host ''
    Write-Host '=== agent-bridge update ===' -ForegroundColor Cyan
    Write-Host ''

    if (-not (Ensure-Uv)) { exit 1 }

    # Strict same-content no-op (ce#776/#777) -- checked BEFORE we stop the daemon
    # or touch the venv. Otherwise the classic same-version path below "downgrades
    # to stop-and-rebuild" IN PLACE over the live slot: it stops the running
    # daemon and rewrites its python.exe / site-packages, the WinError-2 crash
    # cascade (dotfiles#1612). If the active slot already matches the target
    # content there is nothing to do; a genuine force-restart is a dedicated
    # command (`service restart`), not an implicit effect of a reconcile.
    if (Test-SlotContentCurrent) {
        Write-Ok "agent-bridge $SrcVersion already installed and healthy (content match) -- no-op (no stop, no rebuild)."
        Write-DeployManifest
        return
    }

    # Serialize concurrent installers (see Invoke-Install): don't let an
    # overlapping install/update rebuild the venv over an in-flight one. Defer on
    # timeout; re-check the no-op after acquiring in case it just landed.
    if (-not (Enter-InstallLock)) {
        Write-Warn 'Another agent-bridge install/update is in progress -- deferring this run. No-op.'
        return
    }
    if (Test-SlotContentCurrent) {
        Write-Ok "agent-bridge $SrcVersion already active after lock wait -- no-op."
        Write-DeployManifest
        Exit-InstallLock
        return
    }

    # Stop running instance first -- a rebuild/repair of the venv (below) must
    # not race a live bridge holding python.exe open.
    $wasRunning = $null -ne (Get-RunningProcess)

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
        $useCutover = $wasRunning
        # Cutover onto the *same* slot is impossible (there is only one dir of that
        # name and the live daemon holds it). A same-version refresh downgrades to
        # the classic stop-and-rebuild.
        if ($useCutover -and $SrcVersion -eq $prevVersion) {
            Write-Step "Cutover skipped: version $SrcVersion is already active; using classic stop-and-rebuild"
            $useCutover = $false
        }
    } else {
        $useCutover = $wasRunning -and (Test-Path $VenvPython)
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
            Invoke-Drain -TimeoutSec $drainTimeout
            Invoke-Stop
        }

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
            # Dist renamed agent-ssh-manager -> ssh-manager; --refresh-package
            # busts uv's build cache so a same-version source change lands (#186).
            $sshOut = & uv pip install --python $VenvPython --reinstall-package ssh-manager --refresh-package ssh-manager `
                "$SshManagerDir" --quiet 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ErrorActionPreference = $prevEAP
                if ($sshOut) { Write-Host ($sshOut | Out-String) }
                throw "ssh-manager update failed (exit $LASTEXITCODE)"
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
            $crOut = & uv pip install --python $VenvPython --reinstall-package agent-credential-relay --refresh-package agent-credential-relay `
                "$CredRelayDir" --quiet 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ErrorActionPreference = $prevEAP
                if ($crOut) { Write-Host ($crOut | Out-String) }
                throw "credential-relay update failed (exit $LASTEXITCODE)"
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
            $zddOut = & uv pip install --python $VenvPython --reinstall-package agent-zdd --refresh-package agent-zdd `
                "$ZddDir" --quiet 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ErrorActionPreference = $prevEAP
                if ($zddOut) { Write-Host ($zddOut | Out-String) }
                throw "zdd update failed (exit $LASTEXITCODE)"
            }
        } elseif (Test-ZddInstalled) {
            Write-Step 'zdd already installed in venv (marketplace layout)'
        } else {
            throw 'Cannot locate zdd library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
        }
        # single-instance-lease: force-reinstall so a local code change propagates.
        $SilDir = Resolve-SingleInstanceLease
        if ($SilDir) {
            $silOut = & uv pip install --python $VenvPython --reinstall-package agent-single-instance-lease --refresh-package agent-single-instance-lease `
                "$SilDir" --quiet 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ErrorActionPreference = $prevEAP
                if ($silOut) { Write-Host ($silOut | Out-String) }
                throw "single-instance-lease update failed (exit $LASTEXITCODE)"
            }
        } elseif (Test-SingleInstanceLeaseInstalled) {
            Write-Step 'single-instance-lease already installed in venv (marketplace layout)'
        } else {
            throw 'Cannot locate single-instance-lease library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
        }
        # config-migrate: force-reinstall so a local code change propagates.
        $CfgMigrateDir = Resolve-ConfigMigrate
        if ($CfgMigrateDir) {
            $cmOut = & uv pip install --python $VenvPython --reinstall-package agent-config-migrate --refresh-package agent-config-migrate `
                "$CfgMigrateDir" --quiet 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ErrorActionPreference = $prevEAP
                if ($cmOut) { Write-Host ($cmOut | Out-String) }
                throw "config-migrate update failed (exit $LASTEXITCODE)"
            }
        } elseif (Test-ConfigMigrateInstalled) {
            Write-Step 'config-migrate already installed in venv (marketplace layout)'
        } else {
            throw 'Cannot locate config-migrate library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer.'
        }
        $bridgeOut = & uv pip install --python $VenvPython --reinstall-package agent-bridge `
            "$PluginDir" --quiet 2>&1
        $updateResult = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($updateResult -ne 0) {
            if ($bridgeOut) { Write-Host ($bridgeOut | Out-String) }
            throw "Package update failed (exit $updateResult)"
        }

        # Verify the freshly-installed runtime imports before declaring success.
        # Catches a half-installed venv (e.g. a wheel/dependency gap like #51)
        # while we can still roll back -- rather than starting a broken service.
        if (-not (Test-RuntimeHealthy $VenvPython)) {
            throw 'Post-install verification failed (agent_bridge / uvicorn / credential_relay not importable)'
        }
        # Machine-local config schema migration (idempotent + atomic). Non-fatal.
        try {
            $env:PYTHONUTF8 = '1'
            & $VenvPython -m agent_bridge config migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
        } catch {
            Write-Step "Config migration skipped: $_"
        }
        Write-Ok 'Package updated'
    }
    catch {
        Write-Fail "Update failed: $_"
        if ($VersionedRuntime) {
            # The `venv` link was never swapped (activate runs only after a healthy
            # build), so the previous slot is still active. Discard the half-built
            # new slot (unless it IS the active one -- a same-version refresh) and
            # restart the previous version if the classic path stopped it.
            if ($SrcVersion -and $SrcVersion -ne $prevVersion) {
                $failedSlot = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
                if (Test-Path $failedSlot) { Remove-Item -Recurse -Force $failedSlot -ErrorAction SilentlyContinue }
            }
            if ($wasRunning -and -not $useCutover) {
                Write-Step 'Restarting the previous version...'
                Invoke-Start
            }
            $prevLabel = if ($prevVersion) { "versions/$prevVersion" } else { 'the previous runtime' }
            Write-Warn "Update failed; kept the previous runtime (venv -> $prevLabel)."
        }
        elseif ($haveBackup) {
            Write-Step 'Rolling back to the previous venv...'
            if (Restore-Venv) {
                Write-Ok 'Previous venv restored'
                if ($wasRunning) {
                    Write-Step 'Restarting the previous service...'
                    Invoke-Start
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

    # Update sibling plugins (e.g. agent-codespaces for codespace: namespace)
    Install-SiblingPlugins -Reinstall

    # Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused);
    # also clears sibling agent-*.exe pulled into this venv by Install-SiblingPlugins.
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    # Versioned layout (#581): the new slot is built + verified; now atomically
    # swap the stable `venv` junction onto it. In cutover mode this makes `venv`
    # (which the scheduled-task launcher resolves through) point at the new slot,
    # so the daemon `agent-bridge deploy` brings up runs the new code -- while the
    # OLD daemon keeps serving from its own immutable slot until drained. No-op in
    # legacy mode.
    if ($VersionedRuntime) {
        Invoke-VersionedMarkComplete
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
    Write-DeployManifest

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
    if ($useCutover) {
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
            Invoke-Stop
            Invoke-Start
        }
    } else {
        Write-Step 'Starting service...'
        Invoke-Start
    }

    # Versioned layout: prune old version slots now that the new one is healthy
    # and active, keeping current + the previous-good (rollback) + any live-pid-
    # pinned slot (a daemon still draining mid-cutover). Best-effort.
    if ($VersionedRuntime) {
        Invoke-VersionedGc -KeepPrev $prevVersion
    }

    Write-Ok 'Update complete'
}

# -- Dispatch ----------------------------------------------------------------

try {
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
} finally {
    # Release the install lock on any exit path (the OS also drops it on process
    # death, so an `exit`/crash never strands it).
    Exit-InstallLock
}
