<#
.SYNOPSIS
    Bootstrap the agent-containers runtime. PS5+ compatible.

.DESCRIPTION
    Creates the shared runtime at ~/.agent-containers/ -- a venv with the
    agent_containers package installed (via uv pip install) -- and deploys the
    `agent-containers` binstub into ~/.local/bin.

    Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-containers).

.PARAMETER Force
    Retry an incomplete runtime. Completed immutable slots are never rebuilt.

.PARAMETER Profile
    Persist base or provider-exec dependencies for this installation and future updates.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'init', 'stamp', 'provision')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [ValidateSet('base', 'provider-exec')]
    [string]$Profile,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($InstallDir) {
    $InstallDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($InstallDir)
    $PSBoundParameters['InstallDir'] = $InstallDir
}

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

. (Join-Path $PSScriptRoot 'installer-watchdog.ps1')

# === install-contract:v5 self-stage -- keep byte-identical across plugins ===
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
                $__wdBudget = Get-InstallWatchdogBudget -Payload $__selfStagedPayload -Name $__selfStageName
                $env:COPILOT_PLUGIN_INSTALL_PHASE_FILE = Join-Path $__selfStageDir 'phase.json'
                $env:COPILOT_PLUGIN_INSTALL_CUTOVER_BUDGET_SEC = [string]$__wdBudget.CutoverSec
                $__selfStageArgs = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $__selfStagedEntry) + $__selfStageFwd |
                    ForEach-Object {
                        '"' + ([regex]::Replace(
                            [regex]::Replace([string]$_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1'
                        )) + '"'
                    }) -join ' '
                $__wdStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
                $__wdChild = Start-Process -FilePath $__selfStageExe -PassThru -NoNewWindow `
                    -WorkingDirectory $env:USERPROFILE `
                    -ArgumentList $__selfStageArgs
                exit (Wait-InstallWatchdog -Child $__wdChild -Budget $__wdBudget `
                    -StartedAt $__wdStartedAt -PhaseFile $env:COPILOT_PLUGIN_INSTALL_PHASE_FILE `
                    -Name $__selfStageName -Stage $__selfStageDir `
                    -LogPath (Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") 'reconcile.err.log'))
            } else {
                throw 'self-stage requires a plugin identity before leaving the payload'
            }
        }
    } catch {
        [Console]::Error.WriteLine("self-stage failed; refusing to run from the replaceable payload: $_")
        exit 1
    }
}
# === end install-contract:v5 self-stage ===

# === install-contract:v5 smoke seam (test-only) -- keep byte-identical ===
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
                $__grandStart = [Diagnostics.ProcessStartInfo]::new()
                $__grandStart.FileName = (Get-Process -Id $PID).Path
                $__grandStart.Arguments = "-NoProfile -Command `"Start-Sleep -Seconds $([Math]::Max($__smokeSleep, 3600))`""
                $__grandStart.UseShellExecute = $false
                $__grandStart.CreateNoWindow = $true
                $__g = [Diagnostics.Process]::Start($__grandStart)
                $__smokeGrandPid = $__g.Id
            } catch { throw "Smoke grandchild launch failed: $_" }
        }
        ([ordered]@{
            ran_from     = $PSScriptRoot
            staged_from  = [string]$env:COPILOT_PLUGIN_STAGED_FROM
            staged       = [bool]$env:COPILOT_PLUGIN_INSTALL_STAGED
            working_dir  = [System.IO.Directory]::GetCurrentDirectory()
            child_pid    = $PID
            grandchild_pid = $__smokeGrandPid
        } | ConvertTo-Json -Compress) | Set-Content -LiteralPath (Join-Path $__smokeHome 'smoke.json')
        if ($env:COPILOT_PLUGIN_INSTALL_SMOKE_PHASE) {
            Enter-InstallPhase -Name $env:COPILOT_PLUGIN_INSTALL_SMOKE_PHASE
        }
        Start-Sleep -Seconds $__smokeSleep
    } catch {
        [Console]::Error.WriteLine("Smoke seam failed: $_")
        exit 1
    }
    exit 0
}
# === end install-contract:v5 smoke seam ===

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }


# -- Output helpers (PS5-safe) ------------------------------------------

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

# -- Paths --------------------------------------------------------------

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_containers'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-containers'
}
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}

# === install-contract:v3 versioned-venv -- keep byte-identical across plugins ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and publish the active one via the `<root>/current-version` plain-text marker.
# On Windows there is NO junction at all -- a reparse point was blocked by
# RedirectionGuard (WinError 448) on managed devices -- so the version-pinned
# binstub + deploy-manifest resolve the active slot straight from the marker. On
# POSIX a `.venv` symlink (not a reparse point) still publishes the active slot,
# but the marker is authoritative. A version bump builds a new slot beside the old
# one and republishes the marker (never mutates a live venv). The
# COPILOT_EXT_NO_VERSIONED opt-out is fully retired -- always versioned.
# The scripts/versioned_runtime.py primitive owns the swap + migration.
$LinkDir = $VenvDir                       # stable path the binstub/manifest reference
$LinkPython = $VenvPython
$VersionedRuntime = $true  # always versioned (junction-free marker model; COPILOT_EXT_NO_VERSIONED retired)
$SrcVersion = $null
$pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
if (Test-Path $pyprojForVer) {
    $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
    if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
}
if ($SrcVersion) {
    $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    if ($env:OS -eq 'Windows_NT') { $VenvPython = Join-Path $VenvDir 'Scripts\python.exe' }
    else { $VenvPython = Join-Path $VenvDir 'bin/python' }
    $LinkDir = $VenvDir
    $LinkPython = $VenvPython
} else {
    $VersionedRuntime = $false
}
# === end install-contract:v3 versioned-venv ===

$utf8NoBom = New-Object System.Text.UTF8Encoding $false

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
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

function Deploy-SelfProvisioningBinstub {
    # Windows tool binstub (.ps1 primary + .cmd fallback), SELF-PROVISIONING
    # (#1393): fast-path the built versioned slot's python; if no slot is built
    # yet (a `stamp` deferred the venv), provision on first use by running the
    # slot-local snapshot's `scripts/init.ps1 provision`, then dispatch. Opt out
    # with AGENT_CONTAINERS_NO_SELFPROVISION=1. POSIX gets its sh shim.
    # Co-deploy the canonical resolvers so every launcher resolves identically
    # (uniform-runtime-resolution, #765).
    $binDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    foreach ($r in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $rSrc = Join-Path $PSScriptRoot $r
        if (Test-Path $rSrc) { Copy-Item $rSrc (Join-Path $binDir $r) -Force }
    }
    if ($env:OS -ne 'Windows_NT') {
        $stubPath = Join-Path $LocalBin 'agent-containers'
        $stubContent = @"
#!/usr/bin/env bash
export PYTHONUTF8=1
_root="`$HOME/.agent-containers"
AGENT_RT_PY=""
if [ -f "`$_root/bin/resolve-runtime.sh" ]; then AGENT_RT_ROOT="`$_root"; . "`$_root/bin/resolve-runtime.sh"; fi
[ -n "`$AGENT_RT_PY" ] && exec "`$AGENT_RT_PY" -m agent_containers "`$@"
_i="`$(cat "`$_root/payload-dir" 2>/dev/null)/scripts/init.sh"
[ -f "`$_i" ] || _i="`$(ls "`$HOME"/.copilot/installed-plugins/*/agent-containers/scripts/init.sh 2>/dev/null | head -n1)"
if [ -n "`$_i" ] && [ -f "`$_i" ]; then echo "[agent-containers] runtime not provisioned; run: bash \"`$_i\" provision" >&2; else echo "[agent-containers] runtime not provisioned and the installer was not found; re-enable the plugin, then retry." >&2; fi
exit 1
"@
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
        Write-Ok "Binstub: $stubPath"
        return
    }
    $ps1Path = Join-Path $LocalBin 'agent-containers.ps1'
    $ps1Content = @'
$env:PYTHONUTF8 = '1'
$_root = Join-Path $env:USERPROFILE '.agent-containers'
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
function _Resolve-Py {
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $_resolver) { $env:AGENT_RT_ROOT = $_root; . $_resolver }
    return $AgentRtPy
}
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_containers @args; exit $LASTEXITCODE }
if ($env:AGENT_CONTAINERS_NO_SELFPROVISION) { [Console]::Error.WriteLine('[agent-containers] runtime not provisioned (AGENT_CONTAINERS_NO_SELFPROVISION set).'); exit 1 }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) { Join-Path $_snap 'scripts\init.ps1' } else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-containers] cannot self-provision: snapshot installer not found. Re-enable the plugin, then retry.'); exit 127 }
[Console]::Error.WriteLine('[agent-containers] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-containers eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
& $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
$_py = _Resolve-Py
if ($_py) { & $_py -m agent_containers @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-containers] provisioning did not yield a runtime. See the log above; retry, or run the snapshot installer manually.')
exit 1
'@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

    $cmdPath = Join-Path $LocalBin 'agent-containers.cmd'
    # cmd fallback: delegate entirely to the .ps1 binstub so resolution stays
    # uniform with the canonical resolve-runtime.ps1 chain and self-provisioning
    # is shared (uniform-runtime-resolution, #765). PowerShell is always present
    # on Windows (this cmd already shelled to it to provision).
    $cmdContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=%USERPROFILE%\.local\bin\agent-containers.ps1"
if not exist "%_PS1%" (echo [agent-containers] binstub not found: %_PS1%>&2 & exit /b 127)
set "_PSHOST="
for /f "delims=" %%I in ('"%SystemRoot%\System32\where.exe" pwsh 2^>nul') do if not defined _PSHOST set "_PSHOST=%%I"
if not defined _PSHOST set "_PSHOST=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%_PSHOST%" -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*
exit /b %ERRORLEVEL%
'@
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    Write-Ok "Binstub: $ps1Path (+ .cmd fallback, self-provisioning)"
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE
    # into ~/.agent-containers/snapshots/<ver>/, record markers, and deploy the
    # self-provisioning binstub -- deferring the heavy venv build to first use.
    # No venv, no uv; never holds the marketplace payload open.
    Write-Host ''
    Write-Host '=== agent-containers stamp (defer runtime to first use) ===' -ForegroundColor Cyan
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
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
    Deploy-SelfProvisioningBinstub
    Write-Ok 'Stamped: agent-containers binstub on PATH; runtime provisions on first use.'
}

# === install-transaction lock (fail-closed) ===
# Serializes every mutating action of this runtime (stamp/install/update/
# provision): one exclusive, OS-held handle on $InstallDir\.install.lock for the
# whole build + publish transaction. A concurrent run waits up to the timeout
# (COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC, default 300s) and then FAILS -- it
# never proceeds unlocked. The OS drops the handle if this process dies.
$script:InstallLockHandle = $null

function Get-InstallLockTimeoutSec {
    $value = 0
    $raw = [string]$env:COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC
    if ($raw -and [int]::TryParse($raw, [ref]$value) -and $value -gt 0) { return $value }
    return 300
}

function Enter-InstallLock {
    param([int]$TimeoutSec = (Get-InstallLockTimeoutSec))
    try {
        if (-not (Test-Path -LiteralPath $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    } catch {
        Write-Fail "Install-lock error: $($_.Exception.Message)"
        return $false
    }
    $lockPath = Join-Path $InstallDir '.install.lock'
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        try {
            $fs = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            try {
                $stamp = [System.Text.Encoding]::UTF8.GetBytes("pid=$PID at=$((Get-Date).ToUniversalTime().ToString('o'))")
                $fs.SetLength(0)
                $fs.Write($stamp, 0, $stamp.Length)
                $fs.Flush()
            } catch { }
            $script:InstallLockHandle = $fs
            return $true
        } catch [System.IO.IOException] {
            # Win32 sharing/lock violation, or (non-Windows) the advisory lock
            # .NET takes for FileShare.None: another install holds the lock.
            $win32 = $_.Exception.HResult -band 0xFFFF
            if ($win32 -eq 32 -or $win32 -eq 33 -or $env:OS -ne 'Windows_NT') {
                if ((Get-Date) -ge $deadline) { return $false }
                Start-Sleep -Milliseconds 750
            } else {
                Write-Fail "Install-lock IO error: $($_.Exception.Message)"
                return $false
            }
        } catch {
            Write-Fail "Install-lock error: $($_.Exception.Message)"
            return $false
        }
    }
}

function Exit-InstallLock {
    if ($script:InstallLockHandle) {
        try { $script:InstallLockHandle.Close(); $script:InstallLockHandle.Dispose() } catch { }
        $script:InstallLockHandle = $null
    }
}
# === end install-transaction lock ===

if (-not (Enter-InstallLock)) {
    Write-Fail "Could not acquire the install transaction lock ($(Join-Path $InstallDir '.install.lock')) within $(Get-InstallLockTimeoutSec)s -- another install is still running; refusing to proceed unlocked"
    exit 1
}

try {
if ($Action -eq 'stamp') {
    if ($Profile) { throw 'Select a runtime profile with provision, not stamp' }
    Invoke-Stamp
    exit 0
}

# -- Preflight checks --------------------------------------------------

Write-Host ''
Write-Host '=== agent-containers init ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    Write-Host "  Are you running this from the correct plugin directory?"
    exit 1
}

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
            if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') {
                $pythonCmd = $found.Source
            }
        } catch { }
        $ErrorActionPreference = $prevEAP
        if ($pythonCmd) { break }
    }
}
if (-not $pythonCmd) {
    Write-Fail 'Python not found on PATH (need 3.10+)'
    Write-Host '  Install Python from https://python.org or via winget:' -ForegroundColor DarkGray
    Write-Host '    winget install Python.Python.3.13' -ForegroundColor DarkGray
    exit 1
}
Write-Ok "Python: $pythonCmd"

if (Get-Command docker -ErrorAction SilentlyContinue) {
    $dockerPath = (Get-Command docker -ErrorAction Stop).Source
    $dockerStart = [Diagnostics.ProcessStartInfo]::new()
    $dockerStart.FileName = $dockerPath
    if ($dockerStart.PSObject.Properties.Name -contains 'ArgumentList') {
        [void]$dockerStart.ArgumentList.Add('--version')
    } else {
        $dockerStart.Arguments = '--version'
    }
    $dockerStart.UseShellExecute = $false
    $dockerStart.CreateNoWindow = $true
    $dockerStart.RedirectStandardOutput = $true
    $dockerStart.RedirectStandardError = $false
    $dockerProcess = [Diagnostics.Process]::Start($dockerStart)
    $dockerVer = $dockerProcess.StandardOutput.ReadToEnd().Trim()
    $dockerProcess.WaitForExit()
    Write-Ok "Docker: $dockerVer"
} else {
    # Non-fatal: an installer must not fail on a missing prerequisite other than
    # Python/uv. Docker is only needed for *runtime* fleet operations; the CLI +
    # venv still install fine on a machine without it (matches init.sh's
    # `command -v docker` guard). Calling `docker` unguarded here throws a
    # CommandNotFoundException under ErrorActionPreference=Stop and aborts the
    # reconcile with exit 1 on every Docker-less box (e.g. box1).
    Write-Step 'docker CLI not found -- agent-containers fleet operations unavailable on this machine (non-fatal)'
}

# Check for uv -- install via winget if missing
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

# -- 1. Create directories ---------------------------------------------

foreach ($dir in @($InstallDir, $LocalBin)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Directories: $InstallDir"

# -- 2. Provision the selected immutable profile (uv pip install or pip) --

$signedBase = $null
if ($env:OS -eq 'Windows_NT' -and (Get-Command py -ErrorAction SilentlyContinue)) {
    foreach ($v in '3.13', '3.12', '3.11') {
        $probePreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            $probeExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $probePreference
        }
        if ($probeExit -eq 0 -and $cand -and (Test-Path $cand)) {
            if ((Get-AuthenticodeSignature $cand).Status -eq 'Valid') { $signedBase = $cand; break }
        }
    }
}
if ($signedBase) { $pythonCmd = $signedBase }
$profileArgs = @(
    '-I',
    (Join-Path $PSScriptRoot 'runtime_profile.py'),
    '--root', $InstallDir, '--payload', $PluginDir, '--python', $pythonCmd
)
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) { $profileArgs += @('--uv', $uv.Source) }
if ($Profile) { $profileArgs += @('--profile', $Profile) }
$profileJson = & $pythonCmd @profileArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail 'Failed to provision the selected agent-containers runtime profile'
    exit 1
}
$profileResult = ($profileJson -join "`n") | ConvertFrom-Json
$VenvDir = [string]$profileResult.venv
$LinkDir = $VenvDir
$VenvPython = [string]$profileResult.python
Write-Ok "Runtime profile: $($profileResult.profile) ($($profileResult.runtimeVersion))"

# -- 4. Deploy binstub -------------------------------------------------

Deploy-SelfProvisioningBinstub

# -- 5. Write deploy manifest ------------------------------------------

# Unified schema_version 3 manifest (install-contract): records the source
# footprint (marketplace vs local) so deploys are auditable like the siblings.
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
    service        = 'agent-containers'
    deployed_at    = (Get-Date -Format 'o')
    deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
    source         = [ordered]@{
        kind    = $kind
        path    = ($PluginDir -replace '\\', '/')
        repo    = 'copilot-extensions'
        plugin  = 'agent-containers'
        version = $ver
        commit  = $commit
        branch  = $branch
        dirty   = $dirty
    }
    venv           = ($LinkDir -replace '\\', '/')
    runtime_version = [string]$profileResult.runtimeVersion
    runtime_profile = [string]$profileResult.profile
    runtime        = 'python'
}
$tmp = "$manifestPath.tmp"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
Move-Item -Force -Path $tmp -Destination $manifestPath
Write-Ok "Deploy manifest written (source: $kind)"

# -- Machine-local config schema migration (idempotent + atomic; never touches
# a repo/cwd containers.yaml -- that is an adopt concern). Non-fatal. --
try {
    $env:PYTHONUTF8 = '1'
    & $VenvPython -m agent_containers config-migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Step "Config migration skipped: $_"
}

# -- 6. Verify ----------------------------------------------------------

Write-Host ''
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$importOk = $false
for ($i = 0; $i -lt 3; $i++) {
    & $VenvPython -c 'import agent_containers' 2>$null
    if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
    Start-Sleep -Seconds 1
}
$ErrorActionPreference = $prevEAP
if ($importOk) {
    Write-Ok 'Verification: module imports successfully'
} else {
    Write-Fail 'Verification: module import failed'
    exit 1
}
& $VenvPython -c 'import credential_relay' 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Ok 'credential-relay: importable in venv'
} else {
    Write-Fail 'credential-relay not importable in venv'
    exit 1
}

# Ensure ~/.local/bin is on PATH
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

Write-Host ''
Write-Host '=== agent-containers init complete ===' -ForegroundColor Cyan
Write-Host '  Try: agent-containers version' -ForegroundColor DarkGray
exit 0
} finally {
    Exit-InstallLock
}
