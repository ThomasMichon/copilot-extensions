<#
.SYNOPSIS
    agent-index installer / lifecycle manager. PS5+ compatible.
.DESCRIPTION
    Canonical installer for the agent-index runtime service shell.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'ensure', 'uninstall', 'engine', 'engine-update', 'register-tasks', 'stamp', 'provision', 'cell-provision', 'cell-recover', 'slot-provision', 'slot-validate', 'slot-complete', 'slot-completion-validate', 'slot-cutover')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$DurableHome,
    [string]$OriginPayloadRoot,
    [string]$ExpectedNamespaceGeneration,
    [string]$ExpectedInstallGeneration,
    [string]$ExpectedCurrentVersion,
    [string]$TargetPayloadRoot,
    [string]$TargetPayloadVersion,
    [string]$TargetSnapshotId,
    [string]$TargetRuntimeVersion,
    [switch]$ExpectCurrentAbsent,
    [switch]$NoService,
    [switch]$Purge,
    [switch]$Force,
    # Allow the task-scheduling STEP to self-elevate (UAC) when this machine
    # refuses scheduled-task CREATION without admin. Opt-in only; default is
    # user-mode with no elevation. Never elevates install/update -- only the
    # `register-tasks` action (see docs/install-contract.md § Hard rules).
    [switch]$AllowTaskElevation
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($InstallDir) {
    $InstallDir = [IO.Path]::GetFullPath($InstallDir)
    $PSBoundParameters['InstallDir'] = $InstallDir
}

# Preserve the invoking repository across the install-contract self-stage,
# whose child intentionally runs from a copied payload directory.
if (-not $env:AGENT_INDEX_REPO) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $candidate = @(& git rev-parse --show-toplevel 2>$null)
        if ($LASTEXITCODE -eq 0 -and $candidate.Count -gt 0) {
            $resolved = ("$($candidate[-1])").Trim()
            if ($resolved) { $env:AGENT_INDEX_REPO = $resolved }
        }
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

$probePayload = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
    [IO.Path]::GetFullPath($env:COPILOT_PLUGIN_STAGED_FROM)
} else {
    (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

# Refuse every mutating lifecycle action before self-staging touches the legacy
# runtime. Status remains read-only and does not need mutation authorization.
if ($Action -notin @(
    'status',
    'cell-provision',
    'cell-recover',
    'slot-provision',
    'slot-validate',
    'slot-complete',
    'slot-completion-validate',
    'slot-cutover'
)) {
    $probeLegacyRoot = if ($InstallDir) {
        [IO.Path]::GetFullPath($InstallDir)
    } else {
        Join-Path $env:USERPROFILE '.agent-index'
    }
    $legacyProbe = Join-Path $PSScriptRoot 'installation-context\legacy-entrypoint-probe.ps1'
    if (-not (Test-Path -LiteralPath $legacyProbe -PathType Leaf)) {
        Write-Host '  [FAIL] Legacy mutation probe is unavailable' -ForegroundColor Red
        exit 1
    }
    $probeHost = (Get-Process -Id $PID).Path
    & $probeHost -NoProfile -ExecutionPolicy Bypass -File $legacyProbe `
        -PayloadRoot $probePayload -LegacyRoot $probeLegacyRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# Status and dependency-light cell-slot actions do not enter the self-stage
# block that creates and reaps legacy staging directories.
if ($Action -in @('start', 'ensure', 'register-tasks')) {
    Write-Host '[agent-index] Host service lifecycle is managed by an already-running agent-dispatch supervisor; this installer cannot launch or register it.'
    exit $(if ($Action -eq 'ensure') { 0 } else { 2 })
}
$cellSlotAction = $Action -in @(
    'cell-provision',
    'cell-recover',
    'slot-provision',
    'slot-validate',
    'slot-complete',
    'slot-completion-validate',
    'slot-cutover'
)
if ($cellSlotAction) {
    Set-Location -LiteralPath $env:USERPROFILE
    [IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
}
$skipSelfStage = $Action -in @(
    'status',
    'cell-provision',
    'cell-recover',
    'slot-provision',
    'slot-validate',
    'slot-complete',
    'slot-completion-validate',
    'slot-cutover'
) -and
    -not $env:COPILOT_PLUGIN_INSTALL_STAGED
if ($skipSelfStage) {
    if ($Action -eq 'status') {
        $env:COPILOT_PLUGIN_INSTALL_STAGED = 'read-only-status'
    } else {
        $env:COPILOT_PLUGIN_INSTALL_STAGED = 'cell-slot-action'
    }
}

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
if ($skipSelfStage) { Remove-Item Env:COPILOT_PLUGIN_INSTALL_STAGED }

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


if ($env:AGENT_INDEX_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_index'
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE '.agent-index' }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$TaskName = 'agent-index'
$EnvFile = Join-Path $InstallDir 'service.env'
$Launcher = Join-Path $InstallDir 'service.ps1'

# === engine-daemon: durable, persistent embedding-engine runtime =============
# The heavy embedding stack (torch + transformers + sentence-transformers) lives
# in a DURABLE venv OUTSIDE the versioned service runtime, at
# AGENT_INDEX_ENGINE_HOME (default ~/.agent-index/engine). It is provisioned ONCE
# and preserved across service updates -- a routine `update` swaps only the
# versioned service runtime + junction and never rebuilds torch or restarts the
# warm engine daemon (effort agent-index-engine-daemon; vision §warm-durable-engine).
$EngineHome = if ($env:AGENT_INDEX_ENGINE_HOME) { $env:AGENT_INDEX_ENGINE_HOME } else { '~/.agent-index/engine' }
$EngineHome = $EngineHome -replace '^~', $env:USERPROFILE
$EngineHome = [System.IO.Path]::GetFullPath(($EngineHome -replace '/', '\'))
$EngineVenv       = Join-Path $EngineHome '.venv'
$EngineVenvPython = Join-Path $EngineVenv 'Scripts\python.exe'
$EngineTaskName   = 'agent-index-engine'
$EngineEnvFile    = Join-Path $EngineHome 'engine.env'
$EngineLauncher   = Join-Path $EngineHome 'engine.ps1'
# === end engine-daemon ======================================================

# === install-contract:v3 versioned-venv (agent-index: .venv-as-junction) ===
# Build each version into versions/<version> and make the historical `.venv`
# path a junction into the active slot. ALWAYS versioned -- the env opt-out
# (AGENT_INDEX_VERSIONED / COPILOT_EXT_NO_VERSIONED) is retired; the code below reads neither var.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
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
        $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
        $LinkDir = $VenvDir
        $LinkPython = $VenvPython
    }
}

if ($Action -in @('cell-provision', 'cell-recover', 'slot-cutover')) {
    if ([string]::IsNullOrWhiteSpace($Context)) {
        Write-Fail "$Action requires -Context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization"
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedMarketplaceId)) {
        Write-Fail "$Action requires -ExpectedMarketplaceId"
        exit 2
    }
    $cellRuntime = Join-Path $PSScriptRoot 'cell-runtime.py'
    if (-not (Test-Path -LiteralPath $cellRuntime -PathType Leaf)) {
        Write-Fail 'Installation-cell runtime coordinator is unavailable'
        exit 1
    }
    $cellPython = $null
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        if ($candidate -eq 'py') {
            $resolved = @(& $found.Source -3 -c 'import sys; print(sys.executable)' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $resolved.Count -gt 0) {
                $cellPython = ("$($resolved[-1])").Trim()
            }
        } else {
            $cellPython = $found.Source
        }
        if ($cellPython) { break }
    }
    if (-not $cellPython) {
        Write-Fail 'Python 3.10+ is required for installation-cell lifecycle actions'
        exit 1
    }
    $cellArgs = @(
        $cellRuntime,
        $Action,
        '--context', $Context,
        '--expected-marketplace-id', $ExpectedMarketplaceId
    )
    if ($DurableHome) {
        $cellArgs += @('--durable-home', $DurableHome)
    }
    if ($Action -eq 'cell-provision') {
        if ($OriginPayloadRoot) {
            $cellArgs += @('--origin-payload-root', $OriginPayloadRoot)
        }
    } elseif ($Action -eq 'slot-cutover') {
        if (
            [string]::IsNullOrWhiteSpace($ExpectedNamespaceGeneration) -or
            [string]::IsNullOrWhiteSpace($ExpectedInstallGeneration)
        ) {
            Write-Fail 'slot-cutover requires expected namespace and install generations'
            exit 2
        }
        if (
            [string]::IsNullOrWhiteSpace($TargetPayloadRoot) -or
            [string]::IsNullOrWhiteSpace($TargetPayloadVersion) -or
            [string]::IsNullOrWhiteSpace($TargetSnapshotId) -or
            [string]::IsNullOrWhiteSpace($TargetRuntimeVersion)
        ) {
            Write-Fail 'slot-cutover requires explicit target payload, snapshot, and runtime identity'
            exit 2
        }
        if (
            ($ExpectCurrentAbsent -and $ExpectedCurrentVersion) -or
            (-not $ExpectCurrentAbsent -and
                [string]::IsNullOrWhiteSpace($ExpectedCurrentVersion))
        ) {
            Write-Fail 'slot-cutover requires exactly one current-version expectation'
            exit 2
        }
        $cellArgs += @(
            '--expected-namespace-generation', $ExpectedNamespaceGeneration,
            '--expected-install-generation', $ExpectedInstallGeneration,
            '--target-payload-root', $TargetPayloadRoot,
            '--target-payload-version', $TargetPayloadVersion,
            '--target-snapshot-id', $TargetSnapshotId,
            '--target-runtime-version', $TargetRuntimeVersion
        )
        if ($ExpectCurrentAbsent) {
            $cellArgs += '--expect-current-absent'
        } else {
            $cellArgs += @('--expected-current-version', $ExpectedCurrentVersion)
        }
    }
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Set-Location -LiteralPath $PluginDir
    [IO.Directory]::SetCurrentDirectory($PluginDir)
    & $cellPython -I -X utf8 @cellArgs
    exit $LASTEXITCODE
}

if ($Action -in @(
    'slot-provision',
    'slot-validate',
    'slot-complete',
    'slot-completion-validate'
)) {
    if ([string]::IsNullOrWhiteSpace($Context)) {
        Write-Fail "$Action requires -Context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization"
        exit 2
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedMarketplaceId)) {
        Write-Fail "$Action requires -ExpectedMarketplaceId"
        exit 2
    }
    if (-not $SrcVersion) {
        Write-Fail 'Cannot determine plugin version from pyproject.toml'
        exit 1
    }
    $slotRunner = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    if (-not (Test-Path -LiteralPath $slotRunner -PathType Leaf)) {
        Write-Fail 'Installation-context runner is unavailable'
        exit 1
    }
    $slotArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $slotRunner,
        $Action,
        '-Context', $Context,
        '-ExpectedMarketplaceId', $ExpectedMarketplaceId,
        '-ExpectedPluginId', 'agent-index',
        '-ExpectedPayloadRoot', $PluginDir,
        '-ExpectedPayloadVersion', $SrcVersion,
        '-SnapshotId', $SrcVersion,
        '-RuntimeVersion', $SrcVersion
    )
    if ($DurableHome) { $slotArgs += @('-DurableHome', $DurableHome) }
    & (Get-Process -Id $PID).Path @slotArgs
    exit $LASTEXITCODE
}

function Test-VenvIsLink {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try { return [bool]((Get-Item $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) }
    catch { return $false }
}

function Test-RuntimeOrigin {
    param([string]$Python, [string]$Slot)
    if (
        -not (Test-Path -LiteralPath $Python -PathType Leaf) -or
        -not (Test-Path -LiteralPath $Slot -PathType Container)
    ) {
        return $false
    }
    $priorCwd = [IO.Directory]::GetCurrentDirectory()
    $priorLocation = Get-Location
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        Set-Location -LiteralPath $Slot
        [IO.Directory]::SetCurrentDirectory($Slot)
        $originOutput = @(
            & $Python -I -X utf8 -c (
                'from pathlib import Path; import agent_index; ' +
                'print(Path(agent_index.__file__).resolve())'
            ) 2>$null
        )
        if ($LASTEXITCODE -ne 0 -or $originOutput.Count -eq 0) {
            return $false
        }
        $origin = [IO.Path]::GetFullPath(("$($originOutput[-1])").Trim())
        $slotRoot = (Resolve-Path -LiteralPath $Slot).Path.TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        return (
            (Test-Path -LiteralPath $origin -PathType Leaf) -and
            $origin.StartsWith(
                $slotRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            )
        )
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousPreference
        Set-Location -LiteralPath $priorLocation
        [IO.Directory]::SetCurrentDirectory($priorCwd)
    }
}

function Invoke-VersionedActivate {
    if (-not $VersionedRuntime) { return $true }
    # Monotonic activation (dotfiles #1508): never flip the active runtime BACKWARD.
    # An install/ensure run from a STALE payload (older than the active
    # current-version marker -- e.g. a not-yet-reconciled marketplace snapshot, or a
    # different local/worktree deploy that activated a newer slot) must not
    # downgrade the running runtime. The current-version marker is authoritative
    # (#1504); keep it and skip activating the older slot (it stays built-but-
    # inactive) unless a downgrade is explicitly forced. This guards EVERY caller
    # (install, update, ...), not just the `update` action's Invoke-DowngradeGuard,
    # so a stale payload can't split-brain the service by re-activating an old slot.
    if (-not $Force -and $SrcVersion) {
        $curVer = ''
        try { $curVer = ([IO.File]::ReadAllText((Join-Path $InstallDir 'current-version'))).Trim() } catch {}
        if ($curVer -and (Test-VersionLt -A $SrcVersion -B $curVer)) {
            Write-Skip "Keeping active runtime $curVer -- not activating older $SrcVersion (monotonic; dotfiles #1508)"
            return $true
        }
    }
    if ((Test-Path $LinkDir) -and -not (Test-VenvIsLink $LinkDir)) {
        try { Invoke-Stop | Out-Null } catch {}
    }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = $VenvPython
    if (-not (Test-Path -LiteralPath $py -PathType Leaf)) {
        Write-Fail "Refusing to activate runtime slot without its target interpreter: $py"
        return $false
    }
    & $py $vr --root $InstallDir --link-name '.venv' is-complete $SrcVersion *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Refusing to activate incomplete runtime slot versions/$SrcVersion"
        return $false
    }
    if (-not (Test-RuntimeOrigin -Python $py -Slot $VenvDir)) {
        Write-Fail "Refusing to activate runtime slot versions/$SrcVersion because agent_index is not importable"
        return $false
    }
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
        ForEach-Object { Write-Host "  ...    $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
        return $false
    }
    Write-Ok "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
    return $true
}

function Get-VersionedCurrent {
    if (-not $VersionedRuntime) { return '' }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return '' }
    $out = & $py $vr --root $InstallDir --link-name '.venv' current 2>$null
    return ("$out").Trim()
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
    & $py @gcArgs 2>&1 | ForEach-Object { Write-Host "  ...    gc: $_" -ForegroundColor DarkGray }
    $ErrorActionPreference = $prevEAP
}
# === end install-contract:v3 versioned-venv ===

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

function Get-PayloadHash(
    [long]$MaxEntries = 100000,
    [long]$MaxPathBytes = 4096,
    [long]$MaxContentBytes = 4294967296
) {
    <# Match installation-context snapshot content hashing over the whole payload.
       All non-root entries, including root snapshot-provenance.json, count
       toward the limits; only its digest record is excluded. #>
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $root = Get-Item -LiteralPath $PluginDir -Force -ErrorAction Stop
    if (-not $root.PSIsContainer -or
        (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "Payload root must be an ordinary directory: $PluginDir"
    }
    $payloadIsWindows = $env:OS -eq 'Windows_NT'
    $statCommand = $null
    if (-not $payloadIsWindows) {
        $statCommand = Get-Command stat -CommandType Application -ErrorAction Stop |
            Select-Object -First 1
    }
    if ($payloadIsWindows -and -not ('CePayloadDirectoryState' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class CePayloadDirectoryState {
    private const uint SHARE_READ = 1, SHARE_WRITE = 2, SHARE_DELETE = 4;
    private const uint OPEN_EXISTING = 3;
    private const uint OPEN_REPARSE = 0x00200000;
    private const uint BACKUP_SEMANTICS = 0x02000000;
    private const uint DIRECTORY = 0x10, REPARSE = 0x400;
    [StructLayout(LayoutKind.Sequential)]
    private struct Info {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME Creation;
        public System.Runtime.InteropServices.ComTypes.FILETIME Access;
        public System.Runtime.InteropServices.ComTypes.FILETIME Write;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFile(
        string path, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out Info info);
    public static string Inspect(string path) {
        using (SafeFileHandle handle = CreateFile(
            path, 0, SHARE_READ | SHARE_WRITE | SHARE_DELETE, IntPtr.Zero,
            OPEN_EXISTING, OPEN_REPARSE | BACKUP_SEMANTICS, IntPtr.Zero)) {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            Info info;
            if (!GetFileInformationByHandle(handle, out info)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if ((info.Attributes & REPARSE) != 0 || (info.Attributes & DIRECTORY) == 0) {
                throw new IOException("path is not an ordinary directory");
            }
            return string.Format(
                "{0:x8}:{1:x8}{2:x8}|{3:x8}{4:x8}|{5:x8}{6:x8}|{7:x8}",
                info.Volume, info.IndexHigh, info.IndexLow,
                info.Write.dwHighDateTime, info.Write.dwLowDateTime,
                info.Creation.dwHighDateTime, info.Creation.dwLowDateTime,
                info.Attributes);
        }
    }
}
'@
    }
    function Get-PayloadKind($Entry, [string]$Relative) {
        if (($Entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Payload content may not contain symbolic links or reparse points: $Relative"
        }
        if ($payloadIsWindows) {
            return $(if ($Entry.PSIsContainer) { 'directory' } else { 'file' })
        }
        if ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                [Runtime.InteropServices.OSPlatform]::Linux
            )) {
            $kind = ("" + (& $statCommand.Source '--format=%F' '--' $Entry.FullName)).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Cannot inspect payload content: $Relative" }
            if ($kind -ceq 'directory') { return 'directory' }
            if ($kind -ceq 'regular file' -or $kind -ceq 'regular empty file') {
                return 'file'
            }
        } elseif ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                [Runtime.InteropServices.OSPlatform]::OSX
            )) {
            $kind = ("" + (& $statCommand.Source '-f' '%HT' $Entry.FullName)).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Cannot inspect payload content: $Relative" }
            if ($kind -ceq 'Directory') { return 'directory' }
            if ($kind -ceq 'Regular File') { return 'file' }
        } else {
            throw 'Payload content classification is unavailable on this platform'
        }
        throw "Payload content entries must be ordinary files or directories: $Relative"
    }
    function Get-PayloadDirectoryToken([string]$Path, [string]$Relative) {
        $entry = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ((Get-PayloadKind $entry $Relative) -cne 'directory') {
            throw "Payload content changed during hashing: $Relative"
        }
        if ($payloadIsWindows) {
            return [CePayloadDirectoryState]::Inspect($Path)
        }
        if ([Runtime.InteropServices.RuntimeInformation]::IsOSPlatform(
                [Runtime.InteropServices.OSPlatform]::Linux
            )) {
            $token = & $statCommand.Source '--format=%d|%i|%s|%y|%z' '--' $Path
        } else {
            $token = & $statCommand.Source '-f' '%d|%i|%z|%m|%c' $Path
        }
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect payload directory: $Relative" }
        return "" + $token
    }
    function Get-PayloadFileToken($Entry) {
        return (
            [string]$Entry.Length + '|' +
            [string]$Entry.LastWriteTimeUtc.Ticks + '|' +
            [string]$Entry.CreationTimeUtc.Ticks + '|' +
            [string][int64]$Entry.Attributes
        )
    }
    function Get-PayloadTreeState {
        $files = [Collections.Generic.SortedDictionary[string, object]]::new(
            [StringComparer]::Ordinal
        )
        $entries = [Collections.Generic.SortedDictionary[string, string]]::new(
            [StringComparer]::Ordinal
        )
        $directoryStates = [Collections.Generic.SortedDictionary[string, string]]::new(
            [StringComparer]::Ordinal
        )
        $directories = [Collections.Stack]::new()
        $directories.Push([pscustomobject]@{ path = $PluginDir; relative = '' })
        [long]$entryCount = 0
        [long]$totalBytes = 0
        while ($directories.Count -gt 0) {
            $directory = $directories.Pop()
            $directoryKey = if ($directory.relative) {
                ([BitConverter]::ToString(
                    $strictUtf8.GetBytes($directory.relative)
                )).Replace('-', '')
            } else { '' }
            $directoryStates.Add(
                $directoryKey,
                (Get-PayloadDirectoryToken $directory.path $directory.relative)
            )
            $enumerator = [IO.Directory]::EnumerateFileSystemEntries(
                $directory.path
            ).GetEnumerator()
            try {
                while ($enumerator.MoveNext()) {
                    $entryPath = [string]$enumerator.Current
                    $entryName = [IO.Path]::GetFileName($entryPath)
                    $relative = if ($directory.relative) {
                        $directory.relative + '/' + $entryName
                    } else { $entryName }
                    $pathBytes = $strictUtf8.GetBytes($relative)
                    if ($pathBytes.Length -gt $MaxPathBytes) {
                        throw "Payload content relative path exceeds the $MaxPathBytes-byte UTF-8 limit: $relative"
                    }
                    $entryCount++
                    if ($entryCount -gt $MaxEntries) {
                        throw "Payload content exceeds the $MaxEntries-entry limit"
                    }
                    $entry = Get-Item -LiteralPath $entryPath `
                        -Force -ErrorAction Stop
                    $kind = Get-PayloadKind $entry $relative
                    $sortKey = ([BitConverter]::ToString($pathBytes)).Replace('-', '')
                    $entries.Add($sortKey, $kind)
                    if ($kind -ceq 'directory') {
                        $directories.Push([pscustomobject]@{
                            path = $entry.FullName
                            relative = $relative
                        })
                        continue
                    }
                    [long]$length = $entry.Length
                    if ($length -gt ($MaxContentBytes - $totalBytes)) {
                        throw "Payload content exceeds the $MaxContentBytes-byte regular-file limit"
                    }
                    $totalBytes += $length
                    if ($directory.relative -or
                        $entry.Name -cne 'snapshot-provenance.json') {
                        $files.Add($sortKey, [pscustomobject]@{
                            path = $entry.FullName
                            relativeBytes = $pathBytes
                            token = Get-PayloadFileToken $entry
                        })
                    }
                }
            }
            finally {
                if ($enumerator -is [IDisposable]) {
                    $enumerator.Dispose()
                }
            }
        }
        return [pscustomobject]@{
            files = $files
            entries = $entries
            directories = $directoryStates
            entryCount = $entryCount
            totalBytes = $totalBytes
        }
    }
    function Assert-PayloadTreeState($Before, $After) {
        if ($Before.entryCount -ne $After.entryCount -or
            $Before.totalBytes -ne $After.totalBytes -or
            $Before.entries.Count -ne $After.entries.Count -or
            $Before.directories.Count -ne $After.directories.Count) {
            throw 'Payload content tree changed during hashing'
        }
        foreach ($pair in $Before.entries.GetEnumerator()) {
            if (-not $After.entries.ContainsKey($pair.Key) -or
                $After.entries[$pair.Key] -cne $pair.Value) {
                throw 'Payload content tree changed during hashing'
            }
        }
        foreach ($pair in $Before.directories.GetEnumerator()) {
            if (-not $After.directories.ContainsKey($pair.Key) -or
                $After.directories[$pair.Key] -cne $pair.Value) {
                throw 'Payload content tree changed during hashing'
            }
        }
    }
    $before = Get-PayloadTreeState
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $before.files.Values) {
            $entry = Get-Item -LiteralPath $file.path -Force -ErrorAction Stop
            if ($entry.PSIsContainer -or
                (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
                (Get-PayloadFileToken $entry) -cne $file.token) {
                throw "Payload content changed during hashing: $($entry.FullName)"
            }
            $stream = [IO.File]::Open(
                $file.path,
                [IO.FileMode]::Open,
                [IO.FileAccess]::Read,
                ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
            )
            $fileSha = [Security.Cryptography.SHA256]::Create()
            try {
                $fileDigest = ([BitConverter]::ToString(
                    $fileSha.ComputeHash($stream)
                )).Replace('-', '').ToLowerInvariant()
            }
            finally {
                $fileSha.Dispose()
                $stream.Dispose()
            }
            $entry = Get-Item -LiteralPath $file.path -Force -ErrorAction Stop
            if ($entry.PSIsContainer -or
                (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
                (Get-PayloadFileToken $entry) -cne $file.token) {
                throw "Payload content changed during hashing: $($entry.FullName)"
            }
            $prefix = [byte[]]@(0x46, 0x00)
            $separator = [byte[]]@(0x00)
            $digestBytes = [Text.Encoding]::ASCII.GetBytes($fileDigest)
            $newline = [byte[]]@(0x0A)
            [void]$sha.TransformBlock($prefix, 0, $prefix.Length, $null, 0)
            [void]$sha.TransformBlock(
                $file.relativeBytes,
                0,
                $file.relativeBytes.Length,
                $null,
                0
            )
            [void]$sha.TransformBlock($separator, 0, 1, $null, 0)
            [void]$sha.TransformBlock(
                $digestBytes,
                0,
                $digestBytes.Length,
                $null,
                0
            )
            [void]$sha.TransformBlock($newline, 0, 1, $null, 0)
        }
        Assert-PayloadTreeState $before (Get-PayloadTreeState)
        [void]$sha.TransformFinalBlock([byte[]]@(), 0, 0)
        return ([BitConverter]::ToString($sha.Hash)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
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
    if (-not $py) {
        throw 'Cannot locate Python to write the runtime completion marker'
    }
    $ph = Get-PayloadHash
    $mcArgs = @($vr, '--root', $InstallDir, '--link-name', (Split-Path -Leaf $LinkDir), 'mark-complete', $SrcVersion, '--payload-hash', $ph)
    & $py @mcArgs 2>&1 | ForEach-Object { Write-Host "  ...    $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime completion marker writer failed with exit code $LASTEXITCODE"
    }
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

# Resolve a vendored library path (libs\<LibName>) across multiple layouts.
# Returns the path string, or $null if not found.
function Resolve-VendoredLib {
    param([Parameter(Mandatory)][string]$LibName)
    # 1. Vendored inside agent-index (marketplace install layout)
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

# zero-downtime cutover primitives (module ``zdd``), extracted from agent-bridge.
function Resolve-Zdd { return (Resolve-VendoredLib -LibName 'zdd') }

# Check if the zdd cutover lib is already importable in the venv.
function Test-ZddInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from zdd.cutover import CutoverOrchestrator' 2>$null
    return $LASTEXITCODE -eq 0
}

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

function Get-InstalledVersion {
    if (-not (Test-Path $LinkPython)) { return $null }
    try {
        $v = & $LinkPython -c 'from importlib.metadata import version; print(version("agent-index"))' 2>$null
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
        $x = if ($i -lt $ta.Count) { $ta[$i] } else { 0 }
        $y = if ($i -lt $tb.Count) { $tb[$i] } else { 0 }
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
            Write-Warn "Downgrade $installed -> $source forced (-Force / AGENT_INDEX_ALLOW_DOWNGRADE)"
            return
        }
        Write-Fail "Refusing to downgrade agent-index: installed $installed > source $source"
        exit 1
    }
}

function Remove-ConsoleTrampolines {
    param([Parameter(Mandatory)][string]$VenvDir)
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

function Get-SignedBasePython {
    <# Return the path to an Authenticode-signed base Python (>=3.10), or $null.
       A `uv venv` builds the slot python.exe as a uv *trampoline* that resolves
       its base interpreter lazily; over a NON-interactive SSH logon that
       resolution fails with "uv trampoline failed to spawn Python child
       process (entity not found)". The agent-index CLI SSH transport runs
       `agent-index <sub>` (i.e. this slot python) on the indexer host over SSH,
       so the slot python MUST be spawnable there. Building the venv from a
       signed base with `python -m venv --copies` embeds a *real copied*
       python.exe (no trampoline; Authenticode survives the copy), which is BOTH
       SSH-invocable AND Smart-App-Control-allowed.

       Candidates are gathered from several sources because none is reliable on
       its own: the `py` launcher (absent on some Cloud PCs), the well-known
       all-users / per-user install roots, and any `python`/`python3` on PATH --
       skipping the WindowsApps App-Execution-Alias 0-byte reparse stub. Each
       candidate is verified to be a real interpreter >=3.10 (the plugin's
       requires-python floor) before its signature is checked. #>
    $cands = @()

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p }
        }
    }

    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA 'Programs\Python') }
    foreach ($root in $roots) {
        if (-not $root) { continue }
        Get-ChildItem -Path $root -Filter 'Python3*' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path $exe) { $cands += $exe }
            }
    }

    foreach ($name in 'python', 'python3') {
        Get-Command $name -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path -notmatch '\\WindowsApps\\' } |
            ForEach-Object { $cands += $_.Path }
    }

    foreach ($c in ($cands | Select-Object -Unique)) {
        if (-not (Test-Path $c)) { continue }
        $ver = (& $c -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null | Out-String).Trim()
        if (-not ($ver -match '^(\d+)\.(\d+)$')) { continue }
        if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 10)) { continue }
        try {
            if ((Get-AuthenticodeSignature $c).Status -eq 'Valid') { return $c }
        } catch {}
    }
    return $null
}

function Deploy-SetupGatedBinstub {
    <# Deploy a stable machine-global redirector to the payload-owned lifecycle
       gate. The gate owns setup consent, runtime readiness, and provisioning. #>
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
    $payloadDirMarker = Join-Path $InstallDir 'payload-dir'
    [IO.File]::WriteAllText($payloadDirMarker, $probePayload, $utf8NoBom)

    $ps1Path = Join-Path $LocalBin 'agent-index.ps1'
    $ps1Content = @'
$_root = Join-Path $env:USERPROFILE '.agent-index'
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_gate = if ($_snap) { Join-Path $_snap 'scripts\runtime-gate.ps1' } else { '' }
if (-not ($_gate -and (Test-Path -LiteralPath $_gate -PathType Leaf))) {
    [Console]::Error.WriteLine('[agent-index] payload lifecycle gate not found. Re-enable the plugin, then retry.')
    exit 127
}
& $_gate @args
exit $LASTEXITCODE
'@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

    $cmdPath = Join-Path $LocalBin 'agent-index.cmd'
    # cmd fallback: delegate to the .ps1 binstub so resolution stays uniform with
    # the canonical resolve-runtime.ps1 chain and self-provisioning is shared.
    $cmdContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=%USERPROFILE%\.local\bin\agent-index.ps1"
if not exist "%_PS1%" (echo [agent-index] binstub not found: %_PS1%>&2 & exit /b 127)
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*) else (powershell -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*)
exit /b %ERRORLEVEL%
'@
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    Write-Ok "Binstub: $ps1Path (+ .cmd fallback, setup-gated)"
}

function Install-Runtime {
    if (-not (Test-Path $PkgSrcDir)) { Write-Fail "Package source not found at $PkgSrcDir"; exit 1 }
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
    if (-not $pythonCmd) { Write-Fail 'Python not found on PATH (need 3.10+)'; exit 1 }
    Write-Ok "Python: $pythonCmd"

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    # Detach an invalid active marker before any rebuild. If provisioning fails
    # later, no success-shaped current-version pointer remains.
    $activeMarker = Join-Path $InstallDir 'current-version'
    $activeVersion = ''
    try { $activeVersion = ([IO.File]::ReadAllText($activeMarker)).Trim() } catch {}
    if ($VersionedRuntime -and $activeVersion) {
        $activeDir = Join-Path (Join-Path $InstallDir 'versions') $activeVersion
        $activePython = Join-Path $activeDir 'Scripts\python.exe'
        $activeReady = $false
        if (Test-Path -LiteralPath $activePython -PathType Leaf) {
            & $activePython (Join-Path $PSScriptRoot 'versioned_runtime.py') --root $InstallDir --link-name '.venv' is-complete $activeVersion *> $null
            if ($LASTEXITCODE -eq 0) {
                $activeReady = Test-RuntimeOrigin `
                    -Python $activePython `
                    -Slot (Join-Path (Join-Path $InstallDir 'versions') $activeVersion)
            }
        }
        if (-not $activeReady) {
            try { Invoke-Stop | Out-Null } catch {}
            Remove-Item -LiteralPath $activeMarker -Force -ErrorAction SilentlyContinue
            $lkgPath = Join-Path $InstallDir 'last-known-good'
            $lkgVersion = ''
            try { $lkgVersion = ([IO.File]::ReadAllText($lkgPath)).Trim() } catch {}
            if ($lkgVersion -ceq $activeVersion) {
                Remove-Item -LiteralPath $lkgPath -Force -ErrorAction SilentlyContinue
            }
            Write-Warn "Detached invalid active runtime marker for versions/$activeVersion"
        }
    }

    # A failed prior build may have left an interpreter-shaped target slot or,
    # on older installers, even an active marker. A slot is reusable only when
    # its completion marker is valid AND agent_index imports successfully.
    if ($VersionedRuntime -and (Test-Path -LiteralPath $VenvDir -PathType Container)) {
        $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
        $slotReady = $false
        if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
            & $VenvPython $vr --root $InstallDir --link-name '.venv' is-complete $SrcVersion *> $null
            if ($LASTEXITCODE -eq 0) {
                $slotReady = Test-RuntimeOrigin -Python $VenvPython -Slot $VenvDir
            }
        }
        if (-not $slotReady -or $env:AGENT_INDEX_REBUILD_CURRENT -eq '1') {
            $targetWasActive = $false
            foreach ($markerName in @('current-version', 'last-known-good')) {
                $markerPath = Join-Path $InstallDir $markerName
                $markerVersion = ''
                try { $markerVersion = ([IO.File]::ReadAllText($markerPath)).Trim() } catch {}
                if ($markerVersion -ceq $SrcVersion) {
                    if ($markerName -eq 'current-version') { $targetWasActive = $true }
                    Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
                }
            }
            if ($targetWasActive) { try { Invoke-Stop | Out-Null } catch {} }
            Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction Stop
            Write-Warn "Removed runtime slot versions/$SrcVersion before a clean role-aware rebuild"
        }
    }

    # Rebuild an existing slot venv whose python.exe is a uv trampoline /
    # unsigned (not spawnable over a non-interactive SSH logon, and Smart App
    # Control-blocked) when a signed base Python is available to rebuild from.
    # A re-deploy of the same version otherwise leaves the old trampoline in
    # place, defeating the CLI SSH transport.
    if (Test-Path $VenvPython) {
        $sigStatus = try { (Get-AuthenticodeSignature $VenvPython).Status } catch { 'Unknown' }
        if ($sigStatus -ne 'Valid' -and (Get-SignedBasePython)) {
            Write-Warn 'Existing slot python is a uv trampoline / unsigned (not SSH-invocable) -- rebuilding from signed Python'
            try { Invoke-Stop | Out-Null } catch {}
            try { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop }
            catch { Write-Fail "Could not remove the unsigned slot venv (in use?): $_ -- refusing to leave a non-SSH-invocable runtime in place"; exit 1 }
        }
    }

    if (-not (Test-Path $VenvPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        Invoke-VersionedSlotClean
        # Prefer a signed base Python via `python -m venv --copies`: the signed
        # python.exe is *copied* into the slot (no uv trampoline), so it is BOTH
        # spawnable over a non-interactive SSH logon AND SAC-allowed -- the
        # invariant the agent-index CLI SSH transport depends on. Fall back to
        # uv (unsigned trampoline) only when no signed base is present.
        $signedBase = Get-SignedBasePython
        $created = $false
        if ($signedBase) {
            # --clear so a leftover non-empty slot dir (partial prior build)
            # doesn't fail `venv` and force the uv (trampoline) fallback.
            & $signedBase -m venv --copies --clear $VenvDir 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0 -and (Test-Path $VenvPython)) {
                $created = $true
                Write-Ok "Venv created from signed Python ($signedBase)"
            } else {
                Write-Warn 'Signed-Python venv creation failed -- falling back to uv'
            }
        }
        if (-not $created) {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                # Run uv from a trusted CWD (SystemDrive root), never the profile
                # mount -- launching the WinGet uv.exe reparse shim with the
                # profile as CWD is blocked on SAC/profile-mount Cloud PCs.
                $prevLoc = Get-Location
                Set-Location "$env:SystemDrive\"
                try { & uv venv $VenvDir --allow-existing 2>&1 | Out-Null } finally { Set-Location $prevLoc }
            } else {
                & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
            }
        }
        $ErrorActionPreference = $prevEAP
        if (-not (Test-Path $VenvPython)) { Write-Fail "Venv creation failed -- $VenvPython not found"; exit 1 }
        Write-Ok 'Venv created'
    } else { Write-Skip 'Venv already exists' }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    $ZddDir = Resolve-Zdd
    if ($ZddDir) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $zddOut = & uv pip install --python $VenvPython "$ZddDir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet 2>&1
        } else {
            $zddOut = & $VenvPython -m pip install "$ZddDir" 2>&1
        }
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "zdd install failed (exit $LASTEXITCODE)"
            if ($zddOut) { Write-Host ($zddOut | Out-String) }
            exit 1
        }
    } elseif (Test-ZddInstalled) {
        Write-Skip 'zdd already installed in venv (marketplace layout)'
    } else {
        Write-Fail 'Cannot locate zdd library. Reinstall the agent-index plugin from the marketplace (copilot plugin install agent-index@copilot-extensions), then rerun this installer.'
        exit 1
    }
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    # Host dependencies belong exclusively to dispatch-owned managed cells.
    $pkgSpec = "$PluginDir"
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $out = & uv pip install --python $VenvPython $pkgSpec 2>&1 | Out-String
    } else {
        $out = & $VenvPython -m pip install $pkgSpec 2>&1 | Out-String
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'Failed to install agent-index package into venv'
        Write-Host $out
        $ErrorActionPreference = $prevEAP
        exit 1
    }
    $ErrorActionPreference = $prevEAP
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Write-Ok 'Package installed: agent-index'

    Deploy-SetupGatedBinstub

    $prevVersion = ''
    if ($VersionedRuntime) {
        $prevVersion = Get-VersionedCurrent
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $slotOk = Test-RuntimeOrigin -Python $VenvPython -Slot $VenvDir
        $ErrorActionPreference = $prevEAP
        if (-not $slotOk) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            exit 1
        }
        Invoke-VersionedMarkComplete
        & $VenvPython (Join-Path $PSScriptRoot 'versioned_runtime.py') --root $InstallDir --link-name '.venv' is-complete $SrcVersion *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Runtime completion marker was not published for versions/$SrcVersion -- not activating"
            exit 1
        }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
    }

    Write-Manifest

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $importOk = Test-RuntimeOrigin -Python $LinkPython -Slot $LinkDir
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    if ($VersionedRuntime) { Invoke-VersionedGc -KeepPrev $prevVersion }
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
        service        = 'agent-index'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-index'
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

function Get-MachineRole {
    # Explicit machine-level role used by bare management calls and to preserve
    # an already-configured host runtime during updates from another repo.
    if ($env:AGENT_INDEX_ROLE) {
        $r = ($env:AGENT_INDEX_ROLE).Trim().ToLower()
        if ($r -in @('host', 'client')) { return $r }
    }
    $cfg = if ($env:AGENT_INDEX_CONFIG) {
        $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
            $env:AGENT_INDEX_CONFIG
        )
    } else {
        Join-Path $InstallDir 'config.yaml'
    }
    if (Test-Path $cfg) {
        $rm = Select-String -Path $cfg -Pattern '^\s*(?:role|engine)\s*:\s*"?([A-Za-z]+)"?' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($rm) {
            $r = $rm.Matches[0].Groups[1].Value.ToLower()
            if ($r -in @('host', 'client')) { return $r }
            if ($r -in @('engine', 'server', 'indexer')) { return 'host' }
            if ($r -in @('none', 'consumer')) { return 'client' }
        }
    }
    return 'unconfigured'
}

function Get-ActivationRole {
    # Session/install activation is repo-scoped. A checked-out repository must
    # explicitly designate its indexer(s); no designation means unconfigured,
    # regardless of another repo's machine-global host role.
    $repoRoot = if ($env:AGENT_INDEX_REPO) {
        [IO.Path]::GetFullPath($env:AGENT_INDEX_REPO)
    } else {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $candidate = @(& git rev-parse --show-toplevel 2>$null)
            if ($LASTEXITCODE -eq 0 -and $candidate.Count -gt 0) {
                ("$($candidate[-1])").Trim()
            } else {
                ''
            }
        } finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    if (-not $repoRoot) { return Get-MachineRole }
    $repoConfig = Join-Path $repoRoot '.agent-index\config.yaml'
    if (-not (Test-Path -LiteralPath $repoConfig -PathType Leaf)) {
        return 'unconfigured'
    }
    $me = if ($env:AGENT_INDEX_MACHINE) {
        $env:AGENT_INDEX_MACHINE.Trim().ToLower()
    } else {
        [Environment]::MachineName.Trim().ToLower()
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $python) { return 'unconfigured' }
    $resolver = Join-Path $PSScriptRoot 'resolve-activation-role.py'
    $role = (& $python.Source $resolver --config $repoConfig --machine $me 2>$null |
        Select-Object -Last 1)
    $role = ("$role").Trim().ToLower()
    return $(if ($role -in @('host', 'client')) { $role } else { 'unconfigured' })
}

function Test-EnginePort {
    $eh = '127.0.0.1'; $ep = 8421
    if ($env:AGENT_INDEX_ENGINE_HOST) { $eh = $env:AGENT_INDEX_ENGINE_HOST }
    if ($env:AGENT_INDEX_ENGINE_PORT) { $ep = [int]$env:AGENT_INDEX_ENGINE_PORT }
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($eh, $ep, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2000)
        $connected = ($ok -and $c.Connected)
        $c.Close()
        return $connected
    } catch { return $false }
}

function Install-Engine {
    # Provision the DURABLE engine venv (agent-index[engine], the torch stack) at
    # AGENT_INDEX_ENGINE_HOME. Built ONCE and skipped if present (idempotent);
    # never rebuilt by a service `update`. Non-fatal -- a failure here leaves the
    # light, torch-free service fully functional. With -Upgrade, an existing venv
    # is upgraded in place (the explicit engine-runtime update path) rather than
    # skipped.
    param([switch]$Upgrade)
    if ($env:AGENT_INDEX_NO_ENGINE_DEPS -eq '1') {
        Write-Skip 'Engine runtime skipped (AGENT_INDEX_NO_ENGINE_DEPS=1)'
        return $false
    }
    if ((Test-Path $EngineVenvPython) -and -not $Upgrade) {
        Write-Skip "Engine runtime already provisioned (durable venv preserved): $EngineVenv"
        return $true
    }
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
    if (-not $pythonCmd) { Write-Warn 'Python not found -- cannot provision engine runtime'; return $false }

    Write-Host "  ...    $(if ($Upgrade) { 'Updating' } else { 'Provisioning' }) durable engine runtime (torch stack) -- may take a while" -ForegroundColor DarkGray
    if (-not (Test-Path $EngineHome)) { New-Item -ItemType Directory -Path $EngineHome -Force | Out-Null }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv venv $EngineVenv --allow-existing 2>&1 | Out-Null
    } else {
        & $pythonCmd -m venv $EngineVenv 2>&1 | Out-Null
    }
    if (-not (Test-Path $EngineVenvPython)) {
        $ErrorActionPreference = $prevEAP
        Write-Warn "Engine venv creation failed -- $EngineVenvPython not found"
        return $false
    }

    # zdd is a declared dependency of agent-index but is not on PyPI -- install it
    # from the vendored lib first so pip can satisfy the requirement.
    $ZddDir = Resolve-Zdd
    if ($ZddDir) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            & uv pip install --python $EngineVenvPython "$ZddDir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet 2>&1 |
                ForEach-Object { Write-Host "  ...    $_" -ForegroundColor DarkGray }
        } else {
            & $EngineVenvPython -m pip install "$ZddDir" 2>&1 |
                ForEach-Object { Write-Host "  ...    $_" -ForegroundColor DarkGray }
        }
    }

    # agent-index[engine] -- the heavy embedding stack into the DURABLE venv only.
    #
    # Torch install is TWO STEPS so a GPU host works even behind a managed/CFS
    # package feed:
    #   1. Install agent-index[engine] from the DEFAULT feed (governed mirror or
    #      public PyPI). This pulls the CPU torch wheel plus ALL of torch's
    #      pure-python deps (sympy, networkx, jinja2, ...) and the rest of the
    #      engine stack (transformers, sentence-transformers, numpy).
    #   2. If AGENT_INDEX_TORCH_INDEX is set (a CUDA wheel index, e.g.
    #      https://download.pytorch.org/whl/cu124), SWAP the torch wheel ONLY from
    #      that index with --no-deps. This is essential on a CFS box: the CUDA index
    #      links torch's pure-python deps to files.pythonhosted.org, which is often
    #      network-blocked -- so we take deps from the reachable default feed (step 1)
    #      and only the (reachable) CUDA torch wheel from the CUDA index here. The
    #      CUDA build's exact dep pins (e.g. sympy==1.13.1) are satisfied at runtime
    #      by the step-1 versions; --no-deps skips re-resolving them through the
    #      blocked host.
    $torchIdx = $env:AGENT_INDEX_TORCH_INDEX
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $pipArgs = @('pip', 'install', '--python', $EngineVenvPython, "$PluginDir[store,engine]")
        if ($Upgrade) { $pipArgs += '--upgrade' }
        $engOut = & uv @pipArgs 2>&1
        $engRc = $LASTEXITCODE
        if ($engRc -eq 0 -and $torchIdx) {
            Write-Host "  ...    Swapping in CUDA torch from the configured CUDA wheel index (wheel only, --no-deps)" -ForegroundColor DarkGray
            $torchOut = & uv pip install --python $EngineVenvPython --index-url $torchIdx --no-deps --reinstall-package torch torch 2>&1
            $engRc = $LASTEXITCODE
            $engOut = @($engOut) + @($torchOut)
        }
    } else {
        $pipArgs = @('-m', 'pip', 'install', "$PluginDir[store,engine]")
        if ($Upgrade) { $pipArgs += '--upgrade' }
        $engOut = & $EngineVenvPython @pipArgs 2>&1
        $engRc = $LASTEXITCODE
        if ($engRc -eq 0 -and $torchIdx) {
            Write-Host "  ...    Swapping in CUDA torch from the configured CUDA wheel index (wheel only, --no-deps)" -ForegroundColor DarkGray
            $torchOut = & $EngineVenvPython -m pip install --index-url $torchIdx --no-deps --force-reinstall torch 2>&1
            $engRc = $LASTEXITCODE
            $engOut = @($engOut) + @($torchOut)
        }
    }
    $ErrorActionPreference = $prevEAP
    if ($engRc -ne 0) {
        Write-Warn 'Engine runtime install failed (torch stack) -- light service unaffected; provision later with the "engine" action'
        Write-Host ($engOut | Out-String)
        return $false
    }
    & $EngineVenvPython -c 'import torch' 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Warn 'Engine venv built but torch import failed'; return $false }
    Write-Ok "Engine runtime $(if ($Upgrade) { 'updated' } else { 'provisioned' }) (durable venv): $EngineVenv"
    return $true
}

function Restart-EngineDaemon {
    # Restart the engine daemon so a freshly-updated durable venv is loaded. This
    # is the ONE place a restart is intended -- the explicit engine-runtime update
    # path, decoupled from the service `update` (which must never bounce the engine).
    if ($NoService) { Write-Skip 'Engine daemon restart skipped (-NoService)'; return }
    # If the operator opted into the advanced task tier, bounce the task; otherwise
    # restart the detached engine user-mode (NO scheduled task, NO elevation).
    if (Get-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Start-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
        Write-Ok "Engine daemon restarted via task: $EngineTaskName"
    } elseif (Test-Path $EngineVenvPython) {
        & $EngineVenvPython -I -X utf8 -m agent_index engine stop 2>&1 | Out-Host
        Start-Sleep -Seconds 1
        & $EngineVenvPython -I -X utf8 -m agent_index engine start 2>&1 | Out-Host
        Write-Ok 'Engine daemon restarted (user-mode, new engine runtime loaded)'
    } else {
        Write-Skip 'Engine runtime not provisioned -- restart skipped'
    }
}

function Write-EngineFiles {
    if (-not (Test-Path $EngineEnvFile)) {
        [System.IO.File]::WriteAllText($EngineEnvFile, "# agent-index engine daemon environment`nAGENT_INDEX_ENGINE_HOST=127.0.0.1`nAGENT_INDEX_ENGINE_PORT=8421`n", $utf8NoBom)
        Write-Ok "Engine env: $EngineEnvFile"
    } else { Write-Skip "Engine env already exists: $EngineEnvFile" }
    $engLauncher = @"
`$env:PYTHONUTF8 = '1'
`$env:AGENT_INDEX_ENGINE_HOME = '$($EngineHome -replace "'","''")'
`$envFile = '$($EngineEnvFile -replace "'","''")'
if (Test-Path `$envFile) {
    Get-Content `$envFile | ForEach-Object {
        `$line = `$_.Trim()
        if (`$line -and -not `$line.StartsWith('#') -and `$line.Contains('=')) {
            `$k, `$v = `$line.Split('=', 2)
            [Environment]::SetEnvironmentVariable(`$k.Trim(), `$v.Trim(), 'Process')
        }
    }
}
& '$($EngineVenvPython -replace "'","''")' -I -X utf8 -m agent_index engine run
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($EngineLauncher, $engLauncher, $utf8NoBom)
}

# ── Scheduled-task registration: OPT-IN ADVANCED TIER ONLY (never the default) ─
#    The DEFAULT lifecycle is user-mode: the daemon runs as a plain user process,
#    started/kept-alive by `Ensure-Running` (install/update/start) and the
#    sessionStart `ensure` safety net -- NO scheduled task, NO elevation. Scheduled
#    tasks are reachable ONLY via the explicit `register-tasks` action, for boxes
#    that want OS-level AtLogon persistence independent of a Copilot session.
#    (see docs/install-contract.md § Hard rules).
# The installer process is never elevated. When the opt-in task tier IS used,
# task registration is:
#   1. idempotent -- a task already in the desired state is left untouched;
#   2. in-place   -- an existing-but-drifted task is updated with Set-ScheduledTask,
#                    which (unlike Register-ScheduledTask -Force) modifies a task
#                    the user already owns WITHOUT admin -- so the common update
#                    path never elevates, even on boxes that forbid non-admin task
#                    CREATION (as some locked-down machines do);
#   3. create     -- a MISSING task is created with Register-ScheduledTask; if the
#                    machine refuses that without admin we do NOT elevate the
#                    installer -- we warn with remediation and continue.
# The ONLY elevation path is the explicit, opt-in `register-tasks` action, which
# self-elevates ONLY that task-scheduling step -- never install/update, and never
# the start/stop path.

function Test-Elevated {
    try {
        return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
            ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

function Test-TaskElevationOptIn {
    if ($AllowTaskElevation) { return $true }
    $v = $env:AGENT_INDEX_ALLOW_TASK_ELEVATION
    return [bool]($v -and ($v.Trim().ToLower() -notin @('0', 'false', 'no', 'off', '')))
}

function Test-AccessDenied {
    param($ErrorRecord)
    return ("$($ErrorRecord.Exception.Message)" -match '(?i)access is denied' `
            -or $ErrorRecord.Exception -is [UnauthorizedAccessException])
}

function New-TaskSpec {
    # Single source of truth for the user-mode daemon task shape (both service
    # and engine). AtLogon auto-run as the current user, no elevation, unlimited
    # runtime, battery-safe, auto-restart, and start-when-available so a missed
    # logon trigger still recovers.
    param([string]$LauncherPath)
    @{
        # Launch through conhost --headless so Windows Terminal / the DefTerm
        # handoff cannot surface the daemon as a visible window -- -WindowStyle
        # Hidden alone is ignored by DefTerm (proven pattern; see agent-bridge).
        Action    = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$LauncherPath`""
        Trigger   = New-ScheduledTaskTrigger -AtLogOn
        Settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    }
}

function Test-TaskCurrent {
    # True when an existing task already matches the desired user-mode shape, so
    # registration can be skipped entirely (no churn, no elevation).
    param([string]$TaskName, $Spec)
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { return $false }
    $haveLogon = @($t.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' }).Count -gt 0
    if (-not $haveLogon) { return $false }
    if ("$($t.Settings.ExecutionTimeLimit)" -ne "$($Spec.Settings.ExecutionTimeLimit)") { return $false }
    if ([bool]$t.Settings.StartWhenAvailable -ne [bool]$Spec.Settings.StartWhenAvailable) { return $false }
    $ea = @($t.Actions)[0]
    if ($ea.Execute -ne $Spec.Action.Execute) { return $false }
    if (("$($ea.Arguments)").Trim() -ne ("$($Spec.Action.Arguments)").Trim()) { return $false }
    # Principal drift that matters for a user-mode auto-run task: logon type and
    # run level (a task escalated to RunLevel Highest, or flipped to a different
    # logon type, is not "current" and should be normalized in place).
    if ("$($t.Principal.LogonType)" -ne "$($Spec.Principal.LogonType)") { return $false }
    if ("$($t.Principal.RunLevel)" -ne "$($Spec.Principal.RunLevel)") { return $false }
    return $true
}

function Register-UserModeTask {
    <#
      Idempotent, user-mode task registration that never elevates the installer.
      Returns $true when the task ends up in the desired state, else $false.
    #>
    param([string]$TaskName, $Spec, [string]$Kind = 'service')
    if (Test-TaskCurrent -TaskName $TaskName -Spec $Spec) {
        Write-Skip "$Kind task already current -- not re-registering: $TaskName"
        return $true
    }
    # Update an existing task in place (no admin needed for a task the user owns).
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        try {
            Set-ScheduledTask -TaskName $TaskName -Action $Spec.Action -Trigger $Spec.Trigger -Settings $Spec.Settings -Principal $Spec.Principal -ErrorAction Stop | Out-Null
            Write-Ok "$Kind task updated in place: $TaskName"
            return $true
        } catch {
            if (-not (Test-AccessDenied $_)) { Write-Warn "Could not update $Kind task '$TaskName': $($_.Exception.Message)"; return $false }
            # else fall through to create/elevation handling
        }
    }
    # Create a missing task (may be refused without admin on locked-down boxes).
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $Spec.Action -Trigger $Spec.Trigger -Settings $Spec.Settings -Principal $Spec.Principal -Force -ErrorAction Stop | Out-Null
        Write-Ok "$Kind task registered: $TaskName"
        return $true
    } catch {
        if (-not (Test-AccessDenied $_)) { Write-Warn "Could not register $Kind task '$TaskName': $($_.Exception.Message)"; return $false }
        Write-Warn ("$Kind task '$TaskName' needs creation but this machine refuses scheduled-task creation without elevation. " +
            "Per policy the installer stays user-mode and does NOT elevate; any existing task keeps running. " +
            "To create/normalize it: run  agent-index-install register-tasks -AllowTaskElevation  (elevates ONLY the task step).")
        return $false
    }
}

function Invoke-ScopedElevation {
    # Re-run THIS installer elevated for a task-ONLY action (never install/update).
    param([string]$ElevAction)
    $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, $ElevAction, '-AllowTaskElevation')
    if ($InstallDir) { $childArgs += @('-InstallDir', $InstallDir) }
    try {
        Write-Host '  ...    Requesting elevation for the task-scheduling step ONLY (UAC)...'
        $p = Start-Process pwsh -Verb RunAs -ArgumentList $childArgs -PassThru -Wait -ErrorAction Stop
        if ($p.ExitCode -eq 0) { Write-Ok 'Task-scheduling step completed (elevated).'; return $true }
        Write-Warn "Elevated task-scheduling step exited with code $($p.ExitCode)."
        return $false
    } catch {
        Write-Warn "Elevation for the task-scheduling step was cancelled or failed: $($_.Exception.Message)"
        return $false
    }
}

function Invoke-RegisterTasks {
    # The ONLY task-scheduling entry point that may elevate -- and it elevates
    # ONLY itself (task registration), never a full install/update. Opt-in via
    # -AllowTaskElevation / AGENT_INDEX_ALLOW_TASK_ELEVATION=1.
    if ((Test-TaskElevationOptIn) -and -not (Test-Elevated)) {
        [void](Invoke-ScopedElevation -ElevAction 'register-tasks')
        return
    }
    Register-EngineDaemon
    Install-Service
}

function Register-EngineDaemon {
    # Register the persistent, platform-native daemon task that runs the warm
    # engine from the durable venv. A warm engine is left untouched (never
    # restarted) when it is already serving.
    if ($NoService) { Write-Skip 'Engine daemon task skipped (-NoService)'; return }
    if (-not (Test-Path $EngineVenvPython)) { Write-Skip 'Engine runtime not provisioned -- daemon task not registered'; return }
    Write-EngineFiles
    $spec = New-TaskSpec -LauncherPath $EngineLauncher
    if (Register-UserModeTask -TaskName $EngineTaskName -Spec $spec -Kind 'engine daemon') {
        if (Test-EnginePort) {
            Write-Skip "Engine daemon already serving -- leaving the warm engine untouched: $EngineTaskName"
        } else {
            Start-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
            Write-Ok "Engine daemon task active: $EngineTaskName"
        }
    }
}

function Write-ServiceFiles {
    if (-not (Test-Path $EnvFile)) {
        [System.IO.File]::WriteAllText($EnvFile, "# agent-index service environment`nAGENT_INDEX_HOST=127.0.0.1`n# AGENT_INDEX_PORT=0  # unset/0 = OS-assigned dynamic port advertised via rendezvous`n", $utf8NoBom)
        Write-Ok "Service env: $EnvFile"
    } else { Write-Skip "Service env already exists: $EnvFile" }
    $launcherContent = @"
`$env:PYTHONUTF8 = '1'
`$envFile = '$EnvFile'
if (Test-Path `$envFile) {
    Get-Content `$envFile | ForEach-Object {
        `$line = `$_.Trim()
        if (`$line -and -not `$line.StartsWith('#') -and `$line.Contains('=')) {
            `$k, `$v = `$line.Split('=', 2)
            [Environment]::SetEnvironmentVariable(`$k.Trim(), `$v.Trim(), 'Process')
        }
    }
}
`$_root = '$($InstallDir -replace "'","''")'
`$_resolver = Join-Path `$_root 'bin\resolve-runtime.ps1'
`$AgentRtPy = `$null
if (Test-Path -LiteralPath `$_resolver -PathType Leaf) {
    `$env:AGENT_RT_ROOT = `$_root
    . `$_resolver
}
`$_py = `$AgentRtPy
if (`$_py) {
    & `$_py -I -X utf8 -c 'import agent_index' *> `$null
    if (`$LASTEXITCODE -ne 0) { `$_py = `$null }
}
if (-not `$_py) { [Console]::Error.WriteLine('[agent-index] no complete, importable runtime slot is available.'); exit 1 }
& `$_py -I -X utf8 -m agent_index start
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($Launcher, $launcherContent, $utf8NoBom)
}

function Install-Service {
    if ($NoService) { Write-Skip 'Service skipped (-NoService)'; return }
    Write-ServiceFiles
    $spec = New-TaskSpec -LauncherPath $Launcher
    if (Register-UserModeTask -TaskName $TaskName -Spec $spec -Kind 'service') {
        Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
}

# ── DEFAULT lifecycle: user-mode auto-run (NO scheduled task, NO elevation) ────
# The default way the daemon runs is a plain user-mode process, started/kept
# alive by the user-mode CLI and the sessionStart `ensure` safety net. Scheduled
# tasks are an OPT-IN advanced tier (`register-tasks`) -- never the default, and
# never in the start/stop path. See docs/install-contract.md § Hard rules.
function Get-ActiveServicePort {
    $aj = Join-Path $InstallDir 'active.json'
    if (-not (Test-Path $aj)) { return $null }
    try { return [int]((Get-Content $aj -Raw | ConvertFrom-Json).active.port) } catch { return $null }
}

function Get-ActiveSlotPython {
    # The python of the ACTIVE (current-version marker) slot -- the single source
    # of truth for "which version serves". The sessionStart `ensure` runs the
    # INSTALLED-snapshot's install.ps1, whose $LinkPython/$SrcVersion may be OLDER
    # than the active marker (a newer version was activated by a different
    # install.ps1 -- a local/worktree deploy, or a not-yet-reconciled marketplace
    # update). Deploying $LinkPython there would drag the running service BACKWARD;
    # deploying the marker slot keeps the active version serving (dotfiles #1504).
    # Falls back to $LinkPython when the marker/slot is missing (e.g. first install,
    # before the marker is written).
    try {
        $ver = ([IO.File]::ReadAllText((Join-Path $InstallDir 'current-version'))).Trim()
    } catch { $ver = '' }
    if ($ver) {
        $p = Join-Path $InstallDir "versions\$ver\Scripts\python.exe"
        if (Test-Path -LiteralPath $p) { return $p }
    }
    # Marker missing/stale: match the binstub's resolution -- fall back to the
    # LATEST built slot under versions\* (a real installed runtime) before the
    # build's $LinkPython, so a present-but-unmarked runtime is still found.
    $latest = Get-ChildItem (Join-Path $InstallDir 'versions') -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name | ForEach-Object { Join-Path $_.FullName 'Scripts\python.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Last 1
    if ($latest) { return $latest }
    return $LinkPython
}

function Test-ServiceHealthy {
    # Health-gate on the LIVE routing endpoint (active.json), not a static port:
    # a stale active.json pointing at a dead pid correctly reads as unhealthy.
    $port = Get-ActiveServicePort
    if (-not $port) { return $false }
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Ensure-ServiceRunning {
    # Idempotent, user-mode, no-elevation. If a healthy daemon already serves the
    # routing endpoint, do nothing; otherwise start one via the ZDD cutover
    # primitive (`agent-index deploy`), which spawns a DETACHED user-mode daemon
    # and flips routing -- robust to a stale/dead active.json. Never a task.
    if ($NoService) { Write-Skip 'Service ensure skipped (-NoService)'; return }
    # A client runs NO local indexer daemon: its MCP/CLI reach the designated
    # host's service over the trusted SSH transport (config.client_url ->
    # configured endpoint). Only a host runs a local service. This mirrors the
    # engine gate in Ensure-Running and the single-host-service architecture
    # (docs/architecture.md: a fleet fans in SSH forwards to ONE host service,
    # never a fleet of listeners).
    $role = Get-ActivationRole
    if ($role -ne 'host') {
        Write-Skip "Local indexer service skipped (role: $role) -- only an explicitly configured host runs a local daemon"
        return
    }
    # Deploy the ACTIVE (current-version marker) slot -- NOT $LinkPython (this
    # install.ps1's own build): a sessionStart `ensure` from an older installed
    # snapshot must keep the active version serving, never drag it back (#1504).
    $activePy = Get-ActiveSlotPython
    if (-not (Test-Path $activePy)) { Write-Skip 'Runtime not installed -- service not ensured'; return }
    Write-ServiceFiles
    if (Test-ServiceHealthy) { Write-Skip 'Service already healthy (user-mode daemon serving)'; return }
    & $activePy -I -X utf8 -m agent_index deploy 2>&1 | Out-Host
    if (Test-ServiceHealthy) { Write-Ok 'Service ensured (user-mode daemon)' }
    else { Write-Warn 'Service ensure attempted -- endpoint not yet healthy (it may still be starting)' }
}

function Ensure-EngineRunning {
    # Host-side durable embedding engine, user-mode (NO scheduled task). Left warm
    # if already serving; else started detached from the durable venv.
    if ($NoService) { Write-Skip 'Engine ensure skipped (-NoService)'; return }
    if (-not (Test-Path $EngineVenvPython)) { Write-Skip 'Engine runtime not provisioned -- engine not ensured'; return }
    Write-EngineFiles
    if (Test-EnginePort) { Write-Skip 'Engine already serving -- leaving the warm engine untouched'; return }
    & $EngineVenvPython -I -X utf8 -m agent_index engine start 2>&1 | Out-Host
    Write-Ok 'Engine ensured (user-mode durable daemon)'
}

function Ensure-Running {
    # The DEFAULT user-mode lifecycle. No task, no elevation. Called by
    # install/update/start and the sessionStart `ensure`. A host runs the engine
    # then the local service; a client runs neither (Ensure-ServiceRunning
    # self-gates on role), so this is a no-op on a client.
    if ((Get-ActivationRole) -eq 'host') { Ensure-EngineRunning }
    Ensure-ServiceRunning
}

function Invoke-ServiceCutover {
    # Installer-driven zdd cutover on UPDATE (Thread B; docs/patterns/graceful-daemon-cutover.md).
    # Ensure-ServiceRunning only deploys when the service is UNHEALTHY, so a routine
    # update of a HEALTHY service would leave the old build serving stale code. On
    # update, drive the cutover explicitly: if a live service is serving, `deploy`
    # (the internal zdd active/passive seam) stands the new slot up passive, flips
    # routing, drains, and retires the old -- so activation moves the service to the
    # new version with no operator step (invariant #1). No live service -> start one.
    if ($NoService) { Write-Skip 'Service cutover skipped (-NoService)'; return }
    $role = Get-ActivationRole
    $maintainExistingHost = (
        $role -ne 'host' -and
        (Get-MachineRole) -eq 'host' -and
        (Test-ServiceHealthy)
    )
    if ($role -ne 'host' -and -not $maintainExistingHost) {
        Write-Skip "Service cutover skipped (role: $role) -- only an explicitly configured host or an already-running host service is updated"
        return
    }
    if (-not (Test-Path $LinkPython)) { Write-Skip 'Runtime not installed -- service not cut over'; return }
    Write-ServiceFiles
    if (Test-ServiceHealthy) {
        Write-Step 'Graceful cutover: moving the live service to the new build (zdd active/passive flip)...'
        & $LinkPython -I -X utf8 -m agent_index deploy 2>&1 | Out-Host
        if (Test-ServiceHealthy) { Write-Ok 'Service cut over to the new build (routing flipped; old drained + retired)' }
        else { Write-Warn 'Service cutover attempted -- endpoint not yet healthy (it may still be starting)' }
    } else {
        Ensure-ServiceRunning
    }
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE into
    # ~/.agent-index/snapshots/<ver>/, record markers, and deploy the setup-gated
    # binstub -- deferring the venv and durable engine until explicit setup.
    Write-Host ''; Write-Host '=== agent-index stamp (defer runtime to explicit setup) ===' -ForegroundColor Cyan; Write-Host ''
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $stampHash = [BitConverter]::ToString(
        [Security.Cryptography.SHA256]::Create().ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($InstallDir.ToLowerInvariant())
        )
    ).Replace('-', '').Substring(0, 24)
    $stampMutexName = if ($env:OS -eq 'Windows_NT') {
        "Local\CopilotExtensions.AgentIndex.Stamp.$stampHash"
    } else {
        "CopilotExtensions.AgentIndex.Stamp.$stampHash"
    }
    $stampMutex = New-Object Threading.Mutex($false, $stampMutexName)
    $stampLockHeld = $false
    try {
        try {
            $stampLockHeld = $stampMutex.WaitOne([TimeSpan]::FromSeconds(20))
        } catch [Threading.AbandonedMutexException] {
            $stampLockHeld = $true
        }
        if (-not $stampLockHeld) { throw 'Timed out waiting for the agent-index stamp lock.' }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $payloadDirMarker = Join-Path $InstallDir 'payload-dir'
    $payloadOriginMarker = Join-Path $InstallDir 'payload-origin'
    Remove-Item $payloadDirMarker, $payloadOriginMarker -Force -ErrorAction SilentlyContinue
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
    [System.IO.File]::WriteAllText($payloadOriginMarker, $probePayload, $utf8NoBom)
    [System.IO.File]::WriteAllText($payloadDirMarker, $snapDir, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    Write-Ok "Snapshot: $snapDir"
    Deploy-SetupGatedBinstub
    Write-Ok 'Stamped: agent-index binstub on PATH; runtime provisions after explicit setup.'
    } finally {
        if ($stampLockHeld) { [void]$stampMutex.ReleaseMutex() }
        $stampMutex.Dispose()
    }
}

function Invoke-Status {
    & (Join-Path $PSScriptRoot 'runtime-gate.ps1') status
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-Start {
    # Default user-mode start (no scheduled task). If the operator opted into the
    # task tier the task auto-runs the daemon anyway; this still ensures it.
    if (-not (Test-Path $LinkPython)) { Write-Fail 'Runtime not installed'; exit 1 }
    Ensure-Running
}

function Invoke-Stop {
    if (Test-Path $LinkPython) { & $LinkPython -I -X utf8 -m agent_index stop | Out-Host }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Ok "Service task stopped: $TaskName"
    }
}

function Invoke-Uninstall {
    Invoke-Stop
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    if (Get-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $EngineTaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    Remove-Item (Join-Path $LocalBin 'agent-index.ps1') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $LocalBin 'agent-index.cmd') -Force -ErrorAction SilentlyContinue
    if ($Purge) {
        if (Test-Path $EngineHome) { Remove-Item $EngineHome -Recurse -Force -ErrorAction SilentlyContinue }
        if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    }
    Write-Ok 'agent-index uninstalled'
}

switch ($Action) {
    'install' {
        Install-Runtime
        Write-Skip 'Host service is dispatch-managed; independent embedding engine unchanged'
    }
    'update' {
        Invoke-DowngradeGuard
        Install-Runtime
        Write-Skip 'Host service is dispatch-managed; independent embedding engine unchanged'
    }
    'ensure' { Ensure-Running }  # user-mode auto-run safety net (sessionStart hook) -- start if not already healthy
    'register-tasks' { Invoke-RegisterTasks }  # OPT-IN advanced tier (scheduled tasks) -- the sole action that may (opt-in) self-elevate that ONE step
    'engine' { Install-Engine | Out-Null; Ensure-EngineRunning }        # explicit host-side provisioning (role-independent), user-mode
    'engine-update' { if (Install-Engine -Upgrade) { Restart-EngineDaemon } }  # rebuild durable engine venv + restart daemon (decoupled from service update)
    'status' { Invoke-Status }
    'start' { Invoke-Start }
    'stop' { Invoke-Stop }
    'uninstall' { Invoke-Uninstall }
    'stamp' { Invoke-Stamp }
    'provision' {
        Install-Runtime
        Write-Skip 'Only the lightweight client runtime was provisioned; host service is dispatch-managed'
    }
}
