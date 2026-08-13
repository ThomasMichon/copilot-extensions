<#
.SYNOPSIS
    agent-index installer / lifecycle manager. PS5+ compatible.
.DESCRIPTION
    Canonical installer for the agent-index runtime service shell.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall', 'engine', 'engine-update', 'register-tasks')]
    [string]$Action = 'install',
    [string]$InstallDir,
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


if ($env:AGENT_INDEX_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_index'
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE '.agent-index' }
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

function Test-VenvIsLink {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try { return [bool]((Get-Item $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) }
    catch { return $false }
}

function Invoke-VersionedActivate {
    if (-not $VersionedRuntime) { return $true }
    if ((Test-Path $LinkDir) -and -not (Test-VenvIsLink $LinkDir)) {
        try { Invoke-Stop | Out-Null } catch {}
    }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
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

    if (-not (Test-Path $VenvPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        Invoke-VersionedSlotClean
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
        } else {
            & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
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
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $out = & uv pip install --python $VenvPython $PluginDir 2>&1 | Out-String
    } else {
        $out = & $VenvPython -m pip install $PluginDir 2>&1 | Out-String
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

    $ps1Path = Join-Path $LocalBin 'agent-index.ps1'
    $ps1Content = @"
`$env:PYTHONUTF8 = '1'
`$_venv = "`$env:USERPROFILE\.agent-index\.venv"
# Resolve the .venv junction's target and launch the slot python DIRECTLY, never
# traversing the junction (reading its target is allowed) -- RedirectionGuard #637.
`$_py = Join-Path `$_venv 'Scripts\python.exe'
`$_root = Split-Path `$_venv
if ((Split-Path -Leaf `$_root) -eq 'versions') { `$_root = Split-Path `$_root }
`$_ver = ''
try { `$_ver = ([IO.File]::ReadAllText((Join-Path `$_root 'current-version'))).Trim() } catch {}
`$_py = if (`$_ver) { Join-Path `$_root ('versions\' + `$_ver + '\Scripts\python.exe') } else { '' }
if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) { `$_py = Get-ChildItem (Join-Path `$_root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Join-Path `$_.FullName 'Scripts\python.exe' } | Where-Object { Test-Path -LiteralPath `$_ } | Select-Object -Last 1 }
& `$_py -m agent_index @args
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)
    $cmdPath = Join-Path $LocalBin 'agent-index.cmd'
    $cmdContent = @"
@echo off
set "PYTHONUTF8=1"
set "_ROOT=%USERPROFILE%\.agent-index"
set "_VER="
if exist "%_ROOT%\current-version" set /p _VER=<"%_ROOT%\current-version"
set "_PY=%_ROOT%\versions\%_VER%\Scripts\python.exe"
"%_PY%" -m agent_index %*
"@
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    Write-Ok "Binstub: $ps1Path"

    $prevVersion = ''
    if ($VersionedRuntime) {
        $prevVersion = Get-VersionedCurrent
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import agent_index' 2>$null
        $slotOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEAP
        if (-not $slotOk) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            exit 1
        }
        Invoke-VersionedMarkComplete
        if (-not (Invoke-VersionedActivate)) { exit 1 }
    }

    Write-Manifest

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $LinkPython -c 'import agent_index' 2>$null
    $importOk = ($LASTEXITCODE -eq 0)
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

function Get-InstallRole {
    # host runs the engine; client is service-only. Precedence: AGENT_INDEX_ROLE
    # env, then the freshly-installed CLI resolver (config.yaml), else client.
    if ($env:AGENT_INDEX_ROLE) {
        $r = ($env:AGENT_INDEX_ROLE).Trim().ToLower()
        if ($r -in @('host', 'client')) { return $r }
    }
    if (Test-Path $LinkPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $out = & $LinkPython -m agent_index role 2>$null
        $ErrorActionPreference = $prevEAP
        if ($LASTEXITCODE -eq 0 -and $out) {
            $r = ("$out" -replace '\s', '').ToLower()
            if ($r -in @('host', 'client')) { return $r }
        }
    }
    return 'client'
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
    # Default PyPI torch is the CPU wheel; set AGENT_INDEX_TORCH_INDEX to a CUDA
    # wheel index (e.g. https://download.pytorch.org/whl/cu121) for a GPU host.
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $pipArgs = @('pip', 'install', '--python', $EngineVenvPython, "$PluginDir[engine]")
        if ($Upgrade) { $pipArgs += '--upgrade' }
        if ($env:AGENT_INDEX_TORCH_INDEX) { $pipArgs += @('--extra-index-url', $env:AGENT_INDEX_TORCH_INDEX) }
        $engOut = & uv @pipArgs 2>&1
    } else {
        $pipArgs = @('-m', 'pip', 'install', "$PluginDir[engine]")
        if ($Upgrade) { $pipArgs += '--upgrade' }
        if ($env:AGENT_INDEX_TORCH_INDEX) { $pipArgs += @('--extra-index-url', $env:AGENT_INDEX_TORCH_INDEX) }
        $engOut = & $EngineVenvPython @pipArgs 2>&1
    }
    $engRc = $LASTEXITCODE
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
    if (Get-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Start-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
        Write-Ok "Engine daemon restarted (new engine runtime loaded): $EngineTaskName"
    } else {
        Register-EngineDaemon
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
& '$($EngineVenvPython -replace "'","''")' -m agent_index engine run
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($EngineLauncher, $engLauncher, $utf8NoBom)
}

# ── Scheduled-task registration: user-mode by default; NEVER elevate the whole
#    installer (see docs/install-contract.md § Hard rules) ─────────────────────
# The installer process is never elevated. Task registration is:
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
# self-elevates ONLY that task-scheduling step -- never install/update.

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
        Action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherPath`""
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
`$_venv = '$($LinkDir -replace "'","''")'
`$_py = '$($LinkPython -replace "'","''")'
# Resolve the .venv junction's target and launch the slot python DIRECTLY (never
# traverse the junction; reading its target is allowed) -- RedirectionGuard #637.
`$_root = Split-Path `$_venv
if ((Split-Path -Leaf `$_root) -eq 'versions') { `$_root = Split-Path `$_root }
`$_ver = ''
try { `$_ver = ([IO.File]::ReadAllText((Join-Path `$_root 'current-version'))).Trim() } catch {}
`$_py = if (`$_ver) { Join-Path `$_root ('versions\' + `$_ver + '\Scripts\python.exe') } else { '' }
if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) { `$_py = Get-ChildItem (Join-Path `$_root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Join-Path `$_.FullName 'Scripts\python.exe' } | Where-Object { Test-Path -LiteralPath `$_ } | Select-Object -Last 1 }
& `$_py -m agent_index start
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

function Invoke-Status {
    if (Test-Path $LinkPython) { & $LinkPython -m agent_index status }
    else { Write-Skip "Runtime not installed: $InstallDir" }
}

function Invoke-Start {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Ok "Service task started: $TaskName"
    } elseif (Test-Path $LinkPython) {
        Start-Process -FilePath $LinkPython -ArgumentList @('-m', 'agent_index', 'start') -WorkingDirectory $InstallDir
        Write-Ok 'Service process started'
    } else { Write-Fail 'Runtime not installed'; exit 1 }
}

function Invoke-Stop {
    if (Test-Path $LinkPython) { & $LinkPython -m agent_index stop | Out-Host }
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
        Install-Runtime; Install-Service
        $role = Get-InstallRole
        if ($role -eq 'host') {
            Install-Engine | Out-Null
            Register-EngineDaemon
        } else {
            Write-Skip "Engine runtime skipped (role: $role) -- set 'role: host' in $InstallDir\config.yaml or AGENT_INDEX_ROLE=host to host the durable engine"
        }
    }
    'update' { Invoke-DowngradeGuard; Install-Runtime; Install-Service }  # engine venv + daemon left untouched by design
    'register-tasks' { Invoke-RegisterTasks }  # task-scheduling ONLY -- the sole action that may (opt-in) self-elevate that step
    'engine' { Install-Engine | Out-Null; Register-EngineDaemon }        # explicit host-side provisioning (role-independent)
    'engine-update' { if (Install-Engine -Upgrade) { Restart-EngineDaemon } }  # rebuild durable engine venv + restart daemon (decoupled from service update)
    'status' { Invoke-Status }
    'start' { Invoke-Start }
    'stop' { Invoke-Stop }
    'uninstall' { Invoke-Uninstall }
}
