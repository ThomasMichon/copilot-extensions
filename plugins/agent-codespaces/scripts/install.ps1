<#
.SYNOPSIS
    Agent Codespaces - standardized installer interface.

.DESCRIPTION
    Manages the agent-codespaces infrastructure lifecycle: install, uninstall,
    status, update.

    Runtime (venv, package, ssh-manager) lives at ~/.agent-codespaces/.
    Binstub goes to ~/.local/bin/.

    Run from the repo root:
      pwsh -File plugins\agent-codespaces\scripts\install.ps1 install
      pwsh -File plugins\agent-codespaces\scripts\install.ps1 status

.PARAMETER Action
    Lifecycle action to perform.

.PARAMETER Force
    Overwrite without confirmation.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status', 'update', 'stamp', 'provision')]
    [string]$Action = 'status',

    [switch]$Force
)

Set-StrictMode -Version Latest
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


# -- Load shared utilities (if available) --------------------------------

$serviceUtilsPath = Join-Path $PSScriptRoot 'service-utils.ps1'
$hasServiceUtils = Test-Path $serviceUtilsPath
if ($hasServiceUtils) {
    . $serviceUtilsPath
} else {
    # Inline minimal helpers when service-utils.ps1 is not present
    function Write-ServiceOk      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
    function Write-ServiceChanged { param([string]$Msg) Write-Host "  [->]   $Msg" -ForegroundColor Yellow }
    function Write-ServiceSkipped { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
    function Write-ServiceWarn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
    function Write-ServiceErr     { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
    function Write-ServiceHeader  { param([string]$Name) Write-Host "`n=== $Name ===" -ForegroundColor Cyan }
    function Ensure-InstallDir    { param([string]$Dir) if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null } }
}

# -- Metadata -------------------------------------------------------------

$ServiceName     = 'Agent Codespaces'
$InstallDir      = Join-Path $env:USERPROFILE '.agent-codespaces'
$LocalBin        = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir       = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$RepoRoot        = (Resolve-Path (Join-Path $PluginDir '..\..')).Path

$VenvDir         = Join-Path $InstallDir '.venv'
$VenvPython      = Join-Path $VenvDir 'Scripts\python.exe'

# === install-contract:v3 versioned-venv (agent-codespaces: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction into it, so the binstubs and
# deploy-manifest resolve through the link unchanged. agent-codespaces is a CLI
# (its SSH ControlMasters are ssh.exe, not python -- they don't lock the venv), so
# no process to drain. LinkDir/LinkPython is the stable `.venv` path;
# VenvDir/VenvPython is the versions/<v> slot (build + health-gate). ALWAYS
# versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED / AGENT_CODESPACES_VERSIONED)
# and the legacy in-place fork are retired; the code below reads neither var.
# scripts/versioned_runtime.py owns the swap + migration + gc.
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

function Invoke-VersionedActivate {
    <# CLI (no daemon): health-gate the freshly-built slot, swap the stable `.venv`
       junction onto it (first migration moves a legacy real `.venv` aside), then
       gc old slots keeping current + the previous-good. Returns $false on failure.
       No-op ($true) in legacy mode. #>
    if (-not $VersionedRuntime) { return $true }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    if (-not (Test-Path $py)) {
        Write-ServiceErr "Fresh runtime slot has no interpreter (versions/$SrcVersion)"
        return $false
    }
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $VenvPython -c 'import agent_codespaces' 2>$null
    $slotOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if (-not $slotOk) {
        Write-ServiceErr "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
        return $false
    }
    if (-not (Invoke-VersionedMarkComplete)) { return $false }
    $prev = (& $py $vr --root $InstallDir --link-name '.venv' current 2>$null); $prev = ("$prev").Trim()
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
        ForEach-Object { Write-ServiceChanged $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-ServiceErr "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
        return $false
    }
    Write-ServiceOk "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($prev) { $gcArgs += @('--keep', $prev) }
    & $LinkPython @gcArgs 2>&1 | ForEach-Object { Write-ServiceChanged "gc: $_" }
    $ErrorActionPreference = $prevEAP
    return $true
}
# === end install-contract:v3 versioned-venv ===
# Note: the Windows console-script exe (Scripts\agent-codespaces.exe) is
# deliberately stripped post-install (SAC-blocked); all invocation goes through
# "$VenvPython -m agent_codespaces". Do not reintroduce an exe-path dependency.
# ssh-manager dir (contains pyproject.toml): plugin-vendored (marketplace
# layout) or repo-root (git checkout layout).
$SshMgrDir       = Join-Path $PluginDir 'libs\ssh-manager'
if (-not (Test-Path (Join-Path $SshMgrDir 'pyproject.toml'))) {
    $SshMgrDir   = Join-Path $RepoRoot 'libs\ssh-manager'
}
# credential-relay dir (vendored like ssh-manager): plugin-vendored or repo-root.
$CredRelayDir    = Join-Path $PluginDir 'libs\credential-relay'
if (-not (Test-Path (Join-Path $CredRelayDir 'pyproject.toml'))) {
    $CredRelayDir = Join-Path $RepoRoot 'libs\credential-relay'
}
# config-migrate dir (vendored like ssh-manager): plugin-vendored or repo-root.
$CfgMigrateDir   = Join-Path $PluginDir 'libs\config-migrate'
if (-not (Test-Path (Join-Path $CfgMigrateDir 'pyproject.toml'))) {
    $CfgMigrateDir = Join-Path $RepoRoot 'libs\config-migrate'
}

$DeploySourcePaths = @('plugins/agent-codespaces/')
$InstallerRelPath  = 'plugins/agent-codespaces/scripts/install.ps1'

# -- Helpers ---------------------------------------------------------------

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

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
# === install-contract:v4 marker/toss helpers (#935) ===
function Get-BootstrapPython {
    <# A python to run the stdlib-only versioned_runtime.py helper (#935).
       Prefers a real base Python via the `py` launcher or PATH -- avoiding the
       Windows Store alias stub -- before considering a completed active slot.
       Returns $null if none. #>
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

function Invoke-VersionedRuntime {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($py) {
            $output = (& $py $vr @Arguments 2>&1 | Out-String -Width 4096).Trim()
            return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }
        }
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        if ($uv) {
            $output = (& $uv.Source run --no-project --python 3.11 $vr @Arguments 2>&1 |
                Out-String -Width 4096).Trim()
            return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }
        }
        return [pscustomobject]@{ ExitCode = 127; Output = 'no bootstrap Python or uv is available' }
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
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

function Test-PythonVenv {
    param(
        [Parameter(Mandatory)][string]$Dir,
        [Parameter(Mandatory)][string]$Python
    )
    if (-not (Test-Path -LiteralPath (Join-Path $Dir 'pyvenv.cfg') -PathType Leaf)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        return $false
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $probe = @(& $Python -c 'import os, sys; print(os.path.normcase(os.path.abspath(sys.prefix))); print("1" if sys.prefix != sys.base_prefix else "0")' 2>$null)
        if ($LASTEXITCODE -ne 0 -or $probe.Count -lt 2 -or "$($probe[1])".Trim() -ne '1') {
            return $false
        }
        $actual = [IO.Path]::GetFullPath("$($probe[0])".Trim()).TrimEnd('\', '/')
        $expected = [IO.Path]::GetFullPath($Dir).TrimEnd('\', '/')
        return [StringComparer]::OrdinalIgnoreCase.Equals($actual, $expected)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $prevEAP
    }
}

function Invoke-VersionedSlotClean {
    <# Delegate incomplete-slot cleanup and live-process protection to the
       canonical versioned-runtime primitive. #>
    if (-not $VersionedRuntime -or -not (Test-Path -LiteralPath $VenvDir)) {
        return $true
    }
    $result = Invoke-VersionedRuntime -Arguments @(
        '--root', $InstallDir, '--link-name', (Split-Path -Leaf $LinkDir),
        'slot', $SrcVersion, '--clean-incomplete'
    )
    if ($result.Output) { $result.Output -split "`r?`n" | ForEach-Object { Write-Host "  ...    $_" } }
    if ($result.ExitCode -ne 0) {
        Write-ServiceErr "Failed to clean incomplete runtime slot (versions/$SrcVersion)"
        return $false
    }
    return $true
}

function Invoke-VersionedMarkComplete {
    <# Write the slot's completion marker AFTER its isolated health gate passed,
       so "marker present" == "healthy, complete build". A crashed / watchdog-
       killed install never reaches here, leaving its slot markerless and thus
       tossable + retryable (#935). No-op in legacy mode. #>
    if (-not $VersionedRuntime) { return $true }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
        $VenvPython
    } else {
        Get-BootstrapPython
    }
    if (-not $py) {
        Write-ServiceErr 'Cannot mark runtime complete: no bootstrap Python is available'
        return $false
    }
    $mcArgs = @($vr, '--root', $InstallDir, '--link-name', (Split-Path -Leaf $LinkDir), 'mark-complete', $SrcVersion)
    $ph = Get-PayloadHash
    if ($ph) { $mcArgs += @('--payload-hash', $ph) }
    & $py @mcArgs 2>&1 | ForEach-Object { Write-Host "  ...    $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-ServiceErr "Failed to mark runtime slot complete (versions/$SrcVersion)"
        return $false
    }
    return $true
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

function Get-InstalledPackageDir {
    param([string]$Python, [string]$Module)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $dir = & $Python -c "import $Module, os; print(os.path.dirname($Module.__file__))" 2>$null
    $ErrorActionPreference = $prevEAP
    if ($dir) { return ($dir | Out-String).Trim() }
    return $null
}

function Stamp-BuildInfo {
    <# Stamp _build_info.py into the INSTALLED site-packages copy (post-install).
       agent-codespaces ships no _build_info.py in source, so this provides the
       version/commit that `agent-codespaces version` reports. #>
    param([string]$Python)
    $pkgDir = Get-InstalledPackageDir -Python $Python -Module 'agent_codespaces'
    if (-not $pkgDir) {
        Write-ServiceWarn "Could not locate installed agent_codespaces -- build info not stamped"
        return
    }
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $git = Get-GitInfo -Path $RepoRoot
    $srcNorm = ($PluginDir -replace '\\', '/')
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    $biContent = @"
`"`"`"Build provenance -- auto-generated at deploy time. Do not edit.`"`"`"

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$ver",
    "commit": "$($git.commit)",
    "branch": "$($git.branch)",
    "build_timestamp": "$ts",
    "source": "$srcNorm",
}
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $pkgDir '_build_info.py'), $biContent, $utf8NoBom)
}

function Assert-Uv {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required but not found on PATH. Install uv and retry."
    }
}

function Install-PackageInto {
    <# uv pip install the vendored libs (ssh-manager, credential-relay) then
       agent-codespaces into the given venv python. Non-editable; deps resolved
       from pyproject.toml. The vendored libs are force-reinstalled so a local
       code change propagates even without a version bump (uv otherwise skips a
       same-version path dep, leaving the venv stale). #>
    param([string]$Python)
    if (-not (Test-Path (Join-Path $SshMgrDir 'pyproject.toml'))) {
        Write-ServiceErr "ssh-manager source not found at $SshMgrDir"
        return $false
    }
    if (-not (Test-Path (Join-Path $CredRelayDir 'pyproject.toml'))) {
        Write-ServiceErr "credential-relay source not found at $CredRelayDir"
        return $false
    }
    if (-not (Test-Path (Join-Path $CfgMigrateDir 'pyproject.toml'))) {
        Write-ServiceErr "config-migrate source not found at $CfgMigrateDir"
        return $false
    }
    # Pre-strip: rename any locked console-script trampoline aside so uv can write
    # a fresh one (Windows denies overwriting an in-use .exe -- os error 5; the
    # stale binstub or a live `agent-codespaces ssh` session may hold it open).
    Remove-ConsoleTrampolines -VenvDir (Split-Path -Parent (Split-Path -Parent $Python))
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv pip install --python $Python --reinstall-package agent-ssh-manager "$SshMgrDir" --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = $prevEAP
        Write-ServiceErr "ssh-manager install failed"
        return $false
    }
    & uv pip install --python $Python --reinstall-package agent-credential-relay "$CredRelayDir" --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = $prevEAP
        Write-ServiceErr "credential-relay install failed"
        return $false
    }
    & uv pip install --python $Python --reinstall-package agent-config-migrate "$CfgMigrateDir" --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $ErrorActionPreference = $prevEAP
        Write-ServiceErr "config-migrate install failed"
        return $false
    }
    & uv pip install --python $Python --reinstall-package agent-codespaces "$PluginDir" --quiet 2>&1 | Out-Null
    $rc = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($rc -ne 0) {
        Write-ServiceErr "agent-codespaces install failed (exit $rc)"
        return $false
    }
    # Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused).
    Remove-ConsoleTrampolines -VenvDir (Split-Path -Parent (Split-Path -Parent $Python))
    return $true
}

function Deploy-Package {
    <# Install agent-codespaces into its own venv and stamp build info. #>
    if (-not (Install-PackageInto -Python $VenvPython)) { return $false }
    Stamp-BuildInfo -Python $VenvPython
    Write-ServiceOk "Package installed into venv"

    # #1643: agent-codespaces is a PURE providers.d marker -- the bridge daemon
    # drives our binstub over a process boundary and NEVER imports agent_codespaces.
    # So we install ONLY into our own venv and drop the providers.d marker (via
    # register-bridge-provider.ps1); we deliberately do NOT vendor a copy into the
    # agent-bridge venv (the retired issue-#14 sync). agent-bridge's own installer
    # prunes any stale copy and guards against one lingering.
    return $true
}

function Deploy-Venv {
    <# Create the Python venv via uv. Deps come from pyproject at package
       install time -- no ad-hoc pyyaml here. #>
    Assert-Uv
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Prefer a SAC-trusted signed base Python via `--copies` so the venv
    # python.exe is itself signed (Smart App Control blocks the unsigned
    # uv-managed python + console-script trampoline). Fall back to uv.
    $signedBase = $null
    if ($env:OS -eq 'Windows_NT' -and (Get-Command py -ErrorAction SilentlyContinue)) {
        foreach ($v in '3.13', '3.12', '3.11') {
            $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $cand -and (Test-Path $cand)) {
                try { if ((Get-AuthenticodeSignature $cand).Status -eq 'Valid') { $signedBase = $cand; break } } catch {}
            }
        }
    }
    if (-not (Invoke-VersionedSlotClean)) { return $false }
    if ($signedBase -and (Test-Path $VenvPython)) {
        try { if ((Get-AuthenticodeSignature $VenvPython).Status -ne 'Valid') { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop } } catch {}
    }
    if ($signedBase -and -not (Test-Path $VenvPython)) {
        & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        $signedRc = $LASTEXITCODE
        if ($signedRc -ne 0) {
            if (Test-PythonVenv -Dir $VenvDir -Python $VenvPython) {
                Write-ServiceWarn "Signed Python venv creation exited $signedRc after producing a usable venv"
            } else {
                Write-ServiceWarn "Signed Python venv creation failed (exit $signedRc) -- falling back to uv"
                try {
                    Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction Stop
                } catch {
                    $ErrorActionPreference = $prevEAP
                    Write-ServiceErr "Could not discard failed signed-Python venv: $($_.Exception.Message)"
                    return $false
                }
            }
        }
    }
    if (-not (Test-Path $VenvPython)) {
        & uv venv $VenvDir --python 3.11 --allow-existing 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $rc = $LASTEXITCODE
                $ErrorActionPreference = $prevEAP
                Write-ServiceErr "Venv creation failed (exit $rc)"
                return $false
            }
        }
    }
    $ErrorActionPreference = $prevEAP

    if (-not (Test-PythonVenv -Dir $VenvDir -Python $VenvPython)) {
        Write-ServiceErr "Venv validation failed at $VenvDir"
        return $false
    }
    Write-ServiceOk "Venv ready at $VenvDir"
    return $true
}

function Deploy-SelfProvisioningBinstub {
    <# Deploy the agent-codespaces CLI binstubs into ~/.local/bin, SELF-PROVISIONING
       (#1393): fast-path the built versioned slot's python; if no slot is built
       yet (a `stamp` deferred the venv), provision on first use by running the
       slot-local snapshot's `scripts/install.ps1 provision`, then dispatch. Opt
       out with AGENT_CODESPACES_NO_SELFPROVISION=1.

       Primary agent-codespaces.ps1 + agent-codespaces.cmd fallback. PowerShell
       resolves a .ps1 (ExternalScript) ahead of a .cmd (Application) in the
       same dir and forwards argv verbatim via @args, so quoting, &&, |, ;, and
       ! in `ssh --remote-cmd` payloads survive intact. A .cmd forwarding %*
       re-tokenizes the command line and mangles (and can inject) those; the
       .cmd is kept only as a fallback for non-PowerShell callers (cmd.exe or a
       bare CreateProcess/PATHEXT spawn). Both launch the signed venv python via
       -m, never the SAC-blocked console-script trampoline .exe. #>
    if (-not (Test-Path $LocalBin)) {
        New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    $ps1Path = Join-Path $LocalBin 'agent-codespaces.ps1'
    $ps1Content = @'
$env:PYTHONUTF8 = '1'
$_root = Join-Path $env:USERPROFILE '.agent-codespaces'
function _resolve_cs_py {
    function _version_key([string]$ver) {
        if ($ver -match '^(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?$') {
            $phase = if ($Matches[4]) { '0' } else { '1' }
            $dev = if ($Matches[4]) { $Matches[4] } else { '0' }
            return '0:{0}.{1}.{2}.{3}.{4}' -f $Matches[1].PadLeft(20, '0'), $Matches[2].PadLeft(20, '0'), $Matches[3].PadLeft(20, '0'), $phase, $dev.PadLeft(20, '0')
        }
        return '1:' + [regex]::Replace($ver.ToLowerInvariant(), '\d+', { param($m) $m.Value.PadLeft(20, '0') })
    }
    function _try_slot([string]$ver) {
        if (-not $ver) { return $null }
        $slot = Join-Path $_root ('versions\' + $ver)
        try {
            $raw = [IO.File]::ReadAllText((Join-Path $slot '.install-complete.json'))
            if ($raw -cnotmatch '^\{"version": "[^"\\]+", "completed_at": "[^"\\]+", "pid": (0|[1-9][0-9]*)(, "payload_hash": "[^"\\]+")?\}$') { return $null }
            $marker = $raw | ConvertFrom-Json -ErrorAction Stop
            if (-not (($marker -is [pscustomobject]) -and ([string]$marker.version -ceq $ver))) { return $null }
        } catch { return $null }
        foreach ($sub in @('Scripts\python.exe', 'bin\python')) {
            $candidate = Join-Path $slot $sub
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
        return $null
    }
    foreach ($marker in @('current-version', 'last-known-good')) {
        $ver = ''
        try { $ver = ([IO.File]::ReadAllText((Join-Path $_root $marker))).Trim() } catch {}
        $p = _try_slot $ver
        if ($p) { return $p }
    }
    Get-ChildItem (Join-Path $_root 'versions') -Directory -ErrorAction SilentlyContinue |
        Sort-Object { _version_key $_.Name } | ForEach-Object { _try_slot $_.Name } |
        Where-Object { $_ } | Select-Object -Last 1
}
$_py = _resolve_cs_py
if ($_py) { & $_py -m agent_codespaces @args; exit $LASTEXITCODE }
if ($env:AGENT_CODESPACES_NO_SELFPROVISION) { [Console]::Error.WriteLine('[agent-codespaces] runtime not provisioned (AGENT_CODESPACES_NO_SELFPROVISION set).'); exit 1 }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) { Join-Path $_snap 'scripts\install.ps1' } else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-codespaces] cannot self-provision: snapshot installer not found. Re-enable the plugin, then retry.'); exit 127 }
[Console]::Error.WriteLine('[agent-codespaces] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-codespaces eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
$mutex = [System.Threading.Mutex]::new($false, 'Local\Copilot.AgentCodespaces.Provision')
$held = $false
$_provisionRc = 0
try {
    try { $held = $mutex.WaitOne() } catch [System.Threading.AbandonedMutexException] { $held = $true }
    $_py = _resolve_cs_py
    if (-not $_py) {
        & $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
        $_provisionRc = $LASTEXITCODE
    }
} finally {
    if ($held) { try { $mutex.ReleaseMutex() } catch {} }
    $mutex.Dispose()
}
if ($_provisionRc -ne 0) {
    [Console]::Error.WriteLine("[agent-codespaces] provisioning failed (exit $_provisionRc).")
    exit $_provisionRc
}
$_py = _resolve_cs_py
if ($_py) { & $_py -m agent_codespaces @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-codespaces] provisioning did not yield a runtime. See the log above; retry, or run the snapshot installer manually.')
exit 1
'@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

    $stubPath = Join-Path $LocalBin 'agent-codespaces.cmd'
    $stubContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-codespaces.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent-codespaces.ps1" %*
)
exit /b %ERRORLEVEL%
'@
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    Write-ServiceOk "Binstub: $ps1Path (+ .cmd fallback, self-provisioning)"

    # Ensure ~/.local/bin is on User PATH
    $currentUserPath = Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'User'
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        Set-CopilotPersistentEnvironmentVariable -Name 'PATH' -Value "$LocalBin;$currentUserPath" -Target 'User'
        $env:PATH = "$LocalBin;$env:PATH"
        Write-ServiceChanged "Added $LocalBin to User PATH"
    }
}

function Write-DeployManifest {
    <# Unified schema_version 3 manifest. Records the source footprint
       (local vs marketplace) and is written atomically (temp+move). #>
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $git = Get-GitInfo -Path $RepoRoot
        $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-codespaces'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-codespaces'
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
    Write-ServiceOk "Deploy manifest written (source: $kind)"
}

# -- Actions ---------------------------------------------------------------

# -- Connection Owner service (config-gated; default off) ------------------
# The persistent per-machine Connection Owner relay daemon (dotfiles#1320/#1333)
# is provisioned as a per-user scheduled task, but ONLY when connection_owner is
# enabled in config. Default off -> the task is ensured ABSENT, so a machine with
# the feature disabled is unchanged (truly inert). Enabling it is "flip the
# config, run update" (the install/update convergence contract, ce#488). The task
# launches through the stable self-provisioning binstub (agent-codespaces.ps1),
# which resolves the active versioned slot at runtime, so it survives updates.
$OwnerTaskName = 'agent-codespaces-owner'

function Get-ConnectionOwnerConfig {
    <# Ask the freshly-built runtime whether the Connection Owner is enabled.
       Returns @{ Enabled = <bool>; Interval = <double> }; disabled on any
       failure (never throws). #>
    $result = @{ Enabled = $false; Interval = 15.0 }
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $env:PYTHONUTF8 = '1'
        $json = & $LinkPython -m agent_codespaces owner --status 2>$null
        if ($LASTEXITCODE -eq 0 -and $json) {
            $obj = (($json | Out-String).Trim() | ConvertFrom-Json)
            $result.Enabled = [bool]$obj.enabled
            if ($obj.reconcile_interval) { $result.Interval = [double]$obj.reconcile_interval }
        }
    } catch { }
    $ErrorActionPreference = $prevEAP
    return $result
}

function Unregister-ConnectionOwnerService {
    <# Stop + remove the Owner scheduled task (used when the feature is disabled
       or on uninstall). Best-effort; never throws. #>
    $existing = Get-ScheduledTask -TaskName $OwnerTaskName -ErrorAction SilentlyContinue
    if ($existing) {
        try { Stop-ScheduledTask -TaskName $OwnerTaskName -ErrorAction SilentlyContinue } catch { }
        try {
            Unregister-ScheduledTask -TaskName $OwnerTaskName -Confirm:$false -ErrorAction Stop
        } catch {
            try { & schtasks.exe /Delete /TN $OwnerTaskName /F *> $null } catch { }
        }
        Write-ServiceChanged "Removed Connection Owner scheduled task ($OwnerTaskName)"
    }
}

function Sync-ConnectionOwnerService {
    <# Config-gated provisioning of the Connection Owner daemon. Enabled ->
       register + start the per-user scheduled task; disabled (default) -> ensure
       it is absent. Idempotent + additive; failures are non-fatal to install. #>
    $co = Get-ConnectionOwnerConfig
    if (-not $co.Enabled) {
        Unregister-ConnectionOwnerService
        return
    }
    $stub = Join-Path $LocalBin 'agent-codespaces.ps1'
    if (-not (Test-Path $stub)) {
        Write-ServiceWarn "Connection Owner enabled but binstub missing ($stub) -- skipping service provisioning"
        return
    }
    try {
        $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
        $exe = if ($pwshCmd) { $pwshCmd.Source } else { (Get-Command powershell.exe).Source }
        # conhost --headless so Windows Terminal / the DefTerm handoff can't surface
        # this at-logon task's pwsh as a visible console window -- -WindowStyle Hidden
        # alone is ignored by DefTerm (windows-launch-hardening #786; matches the
        # agent-bridge / agent-dispatch scheduled-task pattern).
        $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
            -Argument "--headless `"$exe`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$stub`" owner" `
            -WorkingDirectory $InstallDir
        # Interactive at-logon: the daemon needs the user's gh/ssh session context.
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
            -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $OwnerTaskName -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
        Write-ServiceChanged "Registered Connection Owner scheduled task ($OwnerTaskName; interval=$($co.Interval)s)"
        try {
            Start-ScheduledTask -TaskName $OwnerTaskName -ErrorAction Stop
            Write-ServiceOk 'Connection Owner daemon started'
        } catch {
            Write-ServiceWarn 'Connection Owner task registered but did not start now (no interactive session?) -- it starts at next logon'
        }
    } catch {
        Write-ServiceWarn "Connection Owner service provisioning failed: $_"
    }
}

function Invoke-Install {
    Write-ServiceHeader $ServiceName
    # Create directories
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }

    # Deploy venv
    if (-not (Deploy-Venv)) { throw 'Venv deployment failed' }

    # Deploy package
    if (-not (Deploy-Package)) { throw 'Package deployment failed' }

    # Versioned layout (#581): health-gate the slot + swap the `.venv` link.
    if (-not (Invoke-VersionedActivate)) { throw 'Runtime activation failed' }

    # Deploy binstub
    Deploy-SelfProvisioningBinstub

    # Machine-local config schema migration (idempotent + atomic; never touches
    # repo-committed .agent-codespaces/config.yaml -- that is an adopt concern). Non-fatal.
    try {
        $env:PYTHONUTF8 = '1'
        & $VenvPython -m agent_codespaces config-migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
    } catch {
        Write-ServiceWarn "Config migration skipped: $_"
    }

    # Write manifest
    Write-DeployManifest

    # Verify the package imports from the venv (no PYTHONPATH). Retry briefly
    # for transient AV file locks.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $importOk = $false
    for ($i = 0; $i -lt 3; $i++) {
        & $LinkPython -c 'import agent_codespaces' 2>$null
        if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
        Start-Sleep -Seconds 1
    }
    $ErrorActionPreference = $prevEAP
    if ($importOk) {
        Write-ServiceOk 'Verification: module imports successfully'
    } else {
        Write-ServiceErr 'Verification: module import failed'
        throw 'Runtime verification failed'
    }

    # Connection Owner daemon (config-gated; default off -> ensured absent).
    Sync-ConnectionOwnerService

    Write-Host ''
    Write-ServiceOk "$ServiceName installed"
}

function Stop-ManagedSshConnections {
    <# Stop SSH ControlMaster processes this plugin started. They multiplex
       connections to CodeSpaces via sockets under ~/.agent-codespaces/sockets.
       A separate uninstall process can't reach ssh-manager's in-memory state,
       so close each master via `ssh -O exit` (best-effort) and then kill any
       lingering ssh.exe bound to the socket dir. #>
    $socketDir = Join-Path $InstallDir 'sockets'
    if (Test-Path $socketDir) {
        Get-ChildItem $socketDir -File -ErrorAction SilentlyContinue | ForEach-Object {
            $sock = $_.FullName
            # ssh -O exit needs a host arg; the socket already pins the target,
            # so any placeholder works to address the existing master.
            & ssh -o "ControlPath=$sock" -O exit placeholder *> $null 2>&1
        }
    }
    # Kill any ssh process still referencing our socket dir (orphaned masters).
    $needle = (Join-Path $InstallDir 'sockets') -replace '\\', '\\'
    Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'agent-codespaces' } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-ServiceChanged "Stopped SSH ControlMaster (pid=$($_.ProcessId))"
        }
}

function Invoke-Uninstall {
    Write-ServiceHeader "$ServiceName Uninstall"

    # Remove the Connection Owner scheduled task (if provisioned).
    Unregister-ConnectionOwnerService

    # Stop managed SSH ControlMaster connections before removing files.
    Stop-ManagedSshConnections

    # A marketplace invocation self-stages below the runtime tree. Leave that
    # working directory before removing the tree so uninstall never deletes its
    # own active ancestor.
    $cwd = [IO.Directory]::GetCurrentDirectory()
    $installPrefix = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
    if ($cwd.StartsWith($installPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        Set-Location -LiteralPath $env:USERPROFILE
        [IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
    }

    # Remove binstub
    $removedStub = $false
    foreach ($stub in @('agent-codespaces.ps1', 'agent-codespaces.cmd')) {
        $stubPath = Join-Path $LocalBin $stub
        if (Test-Path $stubPath) {
            Remove-Item $stubPath -Force
            Write-ServiceChanged "Removed binstub: $stubPath"
            $removedStub = $true
        }
    }
    if (-not $removedStub) {
        Write-ServiceSkipped "Binstub not found"
    }

    # Remove install directory
    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
        Write-ServiceChanged "Removed: $InstallDir"
    } else {
        Write-ServiceSkipped "Install directory not found"
    }

    Write-ServiceOk "$ServiceName uninstalled"
}

function Invoke-Status {
    Write-ServiceHeader "$ServiceName Status"

    # Install dir
    if (Test-Path $InstallDir) {
        Write-ServiceOk "Install dir: $InstallDir"
    } else {
        Write-ServiceErr "Not installed ($InstallDir not found)"
        return
    }

    # Venv
    if (Test-Path $LinkPython) {
        Write-ServiceOk "Venv: $LinkDir"
    } else {
        Write-ServiceErr "Venv missing"
    }

    # Package (installed into the venv)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $LinkPython -c 'import agent_codespaces' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-ServiceOk "Package: agent_codespaces importable in venv"
    } else {
        Write-ServiceErr "Package not importable in venv"
    }
    & $VenvPython -c 'import ssh_manager' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-ServiceOk "ssh-manager: importable in venv"
    } else {
        Write-ServiceErr "ssh-manager not importable in venv"
    }
    & $VenvPython -c 'import credential_relay' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-ServiceOk "credential-relay: importable in venv"
    } else {
        Write-ServiceErr "credential-relay not importable in venv"
    }
    $ErrorActionPreference = $prevEAP

    # Launch path (SAC-safe model): the Windows console-script exe is
    # deliberately stripped post-install (unsigned PE, blocked by Smart App
    # Control); the binstub, services, and probes all launch via
    # "python.exe -m agent_codespaces". So verify *that* path works rather than
    # the (intentionally absent) Scripts\agent-codespaces.exe -- checking the
    # stripped exe here reported a bogus "Console script missing" error and made
    # a healthy deploy look broken (#49).
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $VenvPython -m agent_codespaces --help 2>&1 | Out-Null
    $launchRc = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($launchRc -eq 0) {
        Write-ServiceOk "Launch path: python -m agent_codespaces"
    } else {
        Write-ServiceErr "Launch path failed: python -m agent_codespaces (exit $launchRc)"
    }

    # Binstub
    $ps1Path = Join-Path $LocalBin 'agent-codespaces.ps1'
    $cmdPath = Join-Path $LocalBin 'agent-codespaces.cmd'
    if (Test-Path $ps1Path) {
        $suffix = if (Test-Path $cmdPath) { ' (+ .cmd fallback)' } else { '' }
        Write-ServiceOk "Binstub: $ps1Path$suffix"
    } elseif (Test-Path $cmdPath) {
        Write-ServiceWarn "Only .cmd fallback present: $cmdPath (no .ps1 -- args may mangle in PowerShell)"
    } else {
        Write-ServiceWarn "Binstub not found at $ps1Path"
    }

    # Version (from the installed package, via the SAC-safe launch path)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $verInfo = & $VenvPython -m agent_codespaces version 2>$null
    $ErrorActionPreference = $prevEAP
    if ($verInfo) {
        Write-ServiceOk "Version: $(($verInfo | Out-String).Trim())"
    }

    # Deploy manifest + source footprint (local checkout vs marketplace)
    $manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifest) {
        try {
            $m = Get-Content $manifest -Raw | ConvertFrom-Json
            if ($m.source) {
                $extra = ''
                if ($m.source.kind -eq 'local' -and $m.source.commit) {
                    $extra = " @ $($m.source.commit)$(if ($m.source.dirty) { '+dirty' })"
                }
                Write-ServiceOk "Source: $($m.source.kind) ($($m.source.version))$extra"
            }
            Write-ServiceOk "Deployed: $($m.deployed_at)"
        } catch { }
    }

    # gh CLI
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        Write-ServiceOk "gh CLI: $($gh.Source)"
    } else {
        Write-ServiceWarn "gh CLI not found"
    }

    # ssh
    $ssh = Get-Command ssh -ErrorAction SilentlyContinue
    if ($ssh) {
        Write-ServiceOk "ssh: $($ssh.Source)"
    } else {
        Write-ServiceWarn "ssh not found"
    }
}

function Invoke-Update {
    Write-ServiceHeader "$ServiceName Update"

    if (-not (Test-Path $InstallDir)) {
        Write-ServiceWarn "Not installed -- running full install"
        Invoke-Install
        return
    }

    # Re-deploy venv (update deps)
    if (-not (Deploy-Venv)) { throw 'Venv deployment failed' }

    # Re-deploy package
    if (-not (Deploy-Package)) { throw 'Package deployment failed' }

    # Versioned layout (#581): health-gate the slot + swap the `.venv` link.
    if (-not (Invoke-VersionedActivate)) { throw 'Runtime activation failed' }

    # Re-deploy binstub
    Deploy-SelfProvisioningBinstub

    # Machine-local config schema migration (idempotent + atomic; never touches
    # repo-committed .agent-codespaces/config.yaml -- that is an adopt concern). Non-fatal.
    try {
        $env:PYTHONUTF8 = '1'
        & $VenvPython -m agent_codespaces config-migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
    } catch {
        Write-ServiceWarn "Config migration skipped: $_"
    }

    # Update manifest
    Write-DeployManifest

    # Connection Owner daemon (config-gated; default off -> ensured absent).
    Sync-ConnectionOwnerService

    Write-ServiceOk "$ServiceName updated"
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE
    # into a per-version snapshot under ~/.agent-codespaces/snapshots/<ver>/,
    # record markers, and deploy the self-provisioning binstub -- deferring the
    # heavy venv build to the binstub's first use. No venv, no uv; fits a
    # sessionStart grace window and NEVER holds the marketplace payload open (it
    # copies from the already self-staged $PluginDir, freeing the singleton
    # immediately).
    Write-ServiceHeader "$ServiceName stamp (defer runtime to first use)"
    if (-not $SrcVersion) { Write-ServiceErr 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    $snapTmp = "$snapDir.tmp-$PID"
    if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
    # Copy everything needed to `uv pip install .` from the slot (src, libs,
    # scripts, pyproject, plugin.json, hooks, README); skip VCS/build/test junk.
    $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
    Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
    }
    if (Test-Path $snapDir) { Remove-Item $snapDir -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $snapDir, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    Write-ServiceOk "Snapshot: $snapDir"
    Deploy-SelfProvisioningBinstub
    Write-ServiceOk 'Stamped: agent-codespaces binstub on PATH; runtime provisions on first use.'
}

# -- Dispatch --------------------------------------------------------------

$lifecycleMutex = $null
$lifecycleHeld = $false
try {
    if ($Action -ne 'status') {
        # Marketplace self-staging runs below $InstallDir. Move every contender
        # off that tree before it waits, not only uninstall itself: otherwise a
        # queued install/update can hold a CWD handle that makes the lock owner’s
        # concurrent uninstall fail while removing the runtime.
        $cwd = [IO.Directory]::GetCurrentDirectory()
        $installPrefix = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
        if ($cwd.StartsWith($installPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Set-Location -LiteralPath $env:USERPROFILE
            [IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
        }
        $lifecycleMutex = [System.Threading.Mutex]::new($false, 'Local\Copilot.AgentCodespaces.InstallLifecycle')
        try {
            $lifecycleHeld = $lifecycleMutex.WaitOne([TimeSpan]::FromMinutes(10))
        } catch [System.Threading.AbandonedMutexException] {
            $lifecycleHeld = $true
        }
        if (-not $lifecycleHeld) {
            throw 'Timed out waiting for another agent-codespaces lifecycle operation'
        }
    }
    switch ($Action) {
        'install'   { Invoke-Install }
        'uninstall' { Invoke-Uninstall }
        'status'    { Invoke-Status }
        'update'    { Invoke-Update }
        'stamp'     { Invoke-Stamp }
        'provision' { Invoke-Install }
    }
} catch {
    Write-ServiceErr $_.Exception.Message
    exit 1
} finally {
    if ($lifecycleHeld) {
        try { $lifecycleMutex.ReleaseMutex() } catch { }
    }
    if ($lifecycleMutex) { $lifecycleMutex.Dispose() }
}
exit 0
