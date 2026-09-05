<#
.SYNOPSIS
    Worktree Session Manager - standardized installer interface.

.DESCRIPTION
    Manages the worktree session infrastructure lifecycle: install, uninstall,
    start, stop, status, update-config, update.

    Shared runtime (venv, package, wrappers) lives at ~/.agent-worktrees/.
    Per-project config and state lives at ~/.{project}/.
    Binstubs go to ~/.local/bin/.

    Run from the repo root:
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 install
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 install -ProjectName my-repo
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 status

.PARAMETER Action
    Lifecycle action to perform.

.PARAMETER ProjectName
    Project name (e.g. 'my-project'). Defaults to an existing config matching
    the basename of the current directory.

.PARAMETER InstallDir
    Exact runtime installation root. Structured context callers must supply the
    root selected by the installation-context resolver.

.PARAMETER RemoveConfig
    On uninstall: also delete project config and worktree session metadata.

.PARAMETER Force
    Overwrite config without drift confirmation.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'status', 'update-config', 'update', 'refresh-profiles', 'stamp', 'provision')]
    [string]$Action = 'status',

    [string]$ProjectName,

    [string]$InstallDir,
    [switch]$RemoveConfig,
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

$InstallDirSpecified = [bool]$InstallDir
if ($InstallDir) {
    $InstallDir = [IO.Path]::GetFullPath($InstallDir)
    $PSBoundParameters['InstallDir'] = $InstallDir
} else {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-worktrees'
}
$ContextualInstall = [bool]$env:COPILOT_EXTENSIONS_CONTEXT
if ($ContextualInstall) {
    if ($Action -notin @('install', 'update', 'status')) {
        Write-Error "Structured installation context does not support action '$Action'."
        exit 1
    }
    if (-not $InstallDirSpecified) {
        Write-Error 'Structured installation context requires -InstallDir.'
        exit 1
    }
    $contextPath = [IO.Path]::GetFullPath($env:COPILOT_EXTENSIONS_CONTEXT)
    if (-not (Test-Path -LiteralPath $contextPath -PathType Leaf)) {
        Write-Error 'Structured installation context is unavailable.'
        exit 1
    }
    $contextPayload = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
        [IO.Path]::GetFullPath($env:COPILOT_PLUGIN_STAGED_FROM)
    } else {
        (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    }
    $contextHelper = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
    if (-not (Test-Path -LiteralPath $contextHelper -PathType Leaf)) {
        Write-Error 'Installation-context validator is unavailable.'
        exit 1
    }
    $contextDurableHome = $contextPath
    1..5 | ForEach-Object {
        $contextDurableHome = Split-Path -Parent $contextDurableHome
    }
    $hostExe = (Get-Process -Id $PID).Path
    $validatedJson = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $contextHelper validate `
        -Context $contextPath `
        -DurableHome $contextDurableHome `
        -ExpectedPluginId 'agent-worktrees' `
        -ExpectedPayloadRoot $contextPayload
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    try {
        $validatedContext = $validatedJson | ConvertFrom-Json
        $validatedRoot = (Resolve-Path -LiteralPath (
            [IO.Path]::GetFullPath([string]$validatedContext.pluginRoot)
        )).Path
        $InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
    } catch {
        Write-Error 'Installation-context validator returned invalid output.'
        exit 1
    }
    if ($validatedRoot -ne $InstallDir) {
        Write-Error '-InstallDir does not match validated installation context.'
        exit 1
    }

    # The standard self-stage block is byte-identical across plugins and uses
    # the legacy root. Stage context installs inside their selected root first,
    # then mark the child staged so the standard block remains inert.
    if (-not $env:COPILOT_PLUGIN_INSTALL_STAGED) {
        try {
            Set-Location -LiteralPath $env:USERPROFILE
            [System.IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
        } catch {}
        $contextStageRoot = Join-Path $InstallDir '.install-stage'
        $contextStage = Join-Path $contextStageRoot (
            (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff') + "-$PID"
        )
        New-Item -ItemType Directory -Force -Path $contextStage | Out-Null
        Copy-Item -LiteralPath $contextPayload -Destination $contextStage -Recurse -Force
        $contextStagedPayload = Join-Path $contextStage (Split-Path -Leaf $contextPayload)
        $contextStagedEntry = Join-Path (
            Join-Path $contextStagedPayload 'scripts'
        ) (Split-Path -Leaf $PSCommandPath)
        Get-ChildItem $contextStageRoot -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $contextStage } |
            ForEach-Object {
                $ownerPid = 0
                if ($_.Name -match '-(\d+)$') {
                    [void][int]::TryParse($Matches[1], [ref]$ownerPid)
                }
                $ownerAlive = $false
                if ($ownerPid -gt 0) {
                    $ownerAlive = [bool](
                        Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
                    )
                }
                if (-not $ownerAlive) {
                    try {
                        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop
                    } catch {}
                }
            }
        $contextForward = @()
        foreach ($key in $PSBoundParameters.Keys) {
            $value = $PSBoundParameters[$key]
            if ($value -is [System.Management.Automation.SwitchParameter]) {
                if ($value.IsPresent) { $contextForward += "-$key" }
            } else {
                $contextForward += "-$key"
                $contextForward += [string]$value
            }
        }
        $env:COPILOT_PLUGIN_INSTALL_STAGED = 'context-install'
        $env:COPILOT_PLUGIN_STAGED_FROM = $contextPayload
        $contextDeadline = 480
        $contextDeadlineRaw = $env:AGENT_WORKTREES_INSTALL_DEADLINE_SEC
        if (-not $contextDeadlineRaw) {
            $contextDeadlineRaw = $env:COPILOT_PLUGIN_INSTALL_DEADLINE_SEC
        }
        if ($contextDeadlineRaw) {
            [void][int]::TryParse(
                [string]$contextDeadlineRaw,
                [ref]$contextDeadline
            )
        }
        $contextChild = Start-Process -FilePath $hostExe -PassThru -NoNewWindow `
            -WorkingDirectory $contextStagedPayload `
            -ArgumentList (
                @(
                    '-NoProfile',
                    '-ExecutionPolicy',
                    'Bypass',
                    '-File',
                    $contextStagedEntry
                ) + $contextForward
            )
        if (
            $contextDeadline -gt 0 -and
            -not $contextChild.WaitForExit($contextDeadline * 1000)
        ) {
            try {
                & taskkill.exe /PID $contextChild.Id /T /F 2>&1 | Out-Null
            } catch {}
            try {
                Stop-Process -Id $contextChild.Id -Force -ErrorAction SilentlyContinue
            } catch {}
            try {
                Add-Content -LiteralPath (
                    Join-Path $InstallDir 'reconcile.err.log'
                ) -Value (
                    (
                        "[{0}] WATCHDOG-KILL agent-worktrees context install " +
                        "exceeded {1}s deadline (child pid {2}); killed tree. " +
                        "Stage: {3}"
                    ) -f
                    ((Get-Date).ToUniversalTime().ToString('s') + 'Z'),
                    $contextDeadline,
                    $contextChild.Id,
                    $contextStage
                )
            } catch {}
            $contextExitCode = 124
        } else {
            if ($contextDeadline -le 0) {
                $contextChild.WaitForExit()
            }
            $contextExitCode = $contextChild.ExitCode
        }
        Remove-Item -LiteralPath $contextStage -Recurse -Force -ErrorAction SilentlyContinue
        exit $contextExitCode
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


# -- Load shared utilities ------------------------------------------------

. (Join-Path $PSScriptRoot 'service-utils.ps1')

# -- Metadata -------------------------------------------------------------

$ServiceName     = 'Worktree Manager'
$BinDir          = Join-Path $InstallDir 'bin'
$LocalBin        = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir       = (Resolve-Path (Join-Path $ScriptDir '..'))
$ServiceYamlPath = Join-Path $ScriptDir 'service.yaml'

# Legacy alias binstubs that earlier versions deployed into BinDir and/or
# LocalBin. Removed from source (commit 688d74e) because they collide with
# worktree-manager and duplicate `agent-worktrees <subcommand>`, but already
# deployed copies linger and cause confusion (e.g. invoking the flag-only
# `mark-complete` alias instead of `push-changes`/`finalize`). Pruned on every
# install/update; bare, .cmd and .ps1 variants are removed from both dirs.
$LegacyBinstubs = @(
    'mark-worktree-complete',
    'cleanup-worktrees',
    'mark-session-complete'
)

# RepoDir: detect from existing config, then CWD.
$RepoDir = $null

# Infer project name: explicit parameter > existing config matching CWD
if (-not $ProjectName) {
    # Try to infer from existing config directories (find any .{name}/config.yaml)
    if ((Get-Location).Path -match '[\\/]([^\\/]+)$') {
        $cwdName = $Matches[1]
        $candidateConf = Join-Path $env:USERPROFILE ".$cwdName\config.yaml"
        if (Test-Path $candidateConf) { $ProjectName = $cwdName }
    }
}
# Don't auto-adopt the CWD repo -- project association is explicit.
# Runtime installs fine without a project name.
# Reserved-name guard: `agent-worktrees` is the runtime's own global command
# (the project-agnostic shim from bin/agent-worktrees.{ps1,cmd}, deployed by
# Deploy-GlobalBinstub), never a per-project launcher. If inference or an
# explicit -ProjectName resolves to it (e.g. the installer run from a dir
# literally named `agent-worktrees`), a project deploy would overwrite the
# global shims with self-`--project` binstubs. Never treat it as a project.
if ($ProjectName -eq 'agent-worktrees') {
    Write-ServiceWarn "Ignoring reserved runtime name 'agent-worktrees' as a project (global command is owned by the tool binstub)"
    $ProjectName = $null
}
$HasProject = [bool]$ProjectName

if ($HasProject) {
    $ProjectDir      = Join-Path $env:USERPROFILE ".$ProjectName"
    $WorktreesDir    = Join-Path $ProjectDir 'worktrees'

    # Detect repo dir from existing project config, then CWD
    $configPath_ = Join-Path $ProjectDir 'config.yaml'
    if (Test-Path $configPath_) {
        try {
            $cfgLines = Get-Content $configPath_ -Raw
            if ($cfgLines -match 'anchor:\s*(.+)') {
                $candidate = $Matches[1].Trim()
                if (Test-Path $candidate) { $RepoDir = $candidate }
            }
        } catch { }
    }
    if (-not $RepoDir -and (Test-Path (Join-Path (Get-Location) '.git'))) {
        $RepoDir = (Get-Location).Path
    }
} else {
    $ProjectDir   = $null
    $WorktreesDir = $null
}

$DeploySourcePaths = @('plugins/agent-worktrees/')
$InstallerRelPath  = 'plugins/agent-worktrees/scripts/install.ps1'


# Python runtime paths (shared across projects)
$LibDir   = Join-Path $InstallDir 'lib'
$VenvDir  = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

# === install-contract:v3 versioned-venv (agent-worktrees: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction into it, so the binstubs,
# wrappers, and deploy-manifest -- all of which reference `.venv` -- resolve
# through the link unchanged. agent-worktrees is a CLI (no daemon), so there is no
# running process to drain: a version bump builds a fresh slot and swaps the link.
# LinkDir/LinkPython is the stable `.venv` path; VenvDir/VenvPython is the
# versions/<v> slot (build + health-gate). ALWAYS versioned -- the env opt-out
# (COPILOT_EXT_NO_VERSIONED / AGENT_WORKTREES_VERSIONED) and the legacy in-place
# fork are retired; the code below reads neither var. scripts/versioned_runtime.py
# owns the swap + migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
if ($true) {  # always versioned (junction-free marker model; COPILOT_EXT_NO_VERSIONED retired)
    $pyprojForVer = if ($PluginDir) { Join-Path $PluginDir 'pyproject.toml' } else { $null }
    if ($pyprojForVer -and (Test-Path $pyprojForVer)) {
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
       gc old slots keeping current + the previous-good. Returns $false on failure
       so the caller can abort. No-op ($true) in legacy mode. #>
    if (-not $VersionedRuntime) { return $true }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    if (-not (Test-Path $py)) { return $true }
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $VenvPython -c 'import agent_worktrees' 2>$null
    $slotOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if (-not $slotOk) {
        Write-ServiceErr "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
        return $false
    }
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $VenvPython -m agent_worktrees.picker_tui.prewarm 2>$null
    $pickerWarm = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if (-not $pickerWarm) {
        Write-ServiceErr "Fresh runtime slot failed its Picker prewarm gate (versions/$SrcVersion) -- not activating"
        return $false
    }
    Write-ServiceOk "Picker import path prewarmed in runtime version $SrcVersion"
    Invoke-VersionedMarkComplete
    $prev = (& $py $vr --root $InstallDir --link-name '.venv' current 2>$null); $prev = ("$prev").Trim()
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
        ForEach-Object { Write-ServiceChanged $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-ServiceErr "Failed to activate runtime version (marker -> versions/$SrcVersion)"
        return $false
    }
    Write-ServiceOk "Runtime version $SrcVersion active (marker -> versions/$SrcVersion)"
    # Consolidated-status-daemon Phase 1 (#1696): the cutover just superseded any
    # running status-monitor, which self-retires but only RESPAWNS on the next
    # session start -- leaving live sessions' status bars frozen until then. Reap
    # the superseded monitor + spawn the current one now (from the NEW slot's
    # python), so every live session's bar is re-served with no session restart.
    # Best-effort, never fatal.
    if (-not $ContextualInstall) {
        try {
            & $LinkPython -m agent_worktrees status-monitor-restart 2>&1 |
                ForEach-Object { Write-ServiceChanged "monitor: $_" }
        } catch {}
    }
    # #742: record the just-activated version as `last-known-good` so a future
    # marker-absent resolution (resolve-runtime.ps1 tier 2) prefers it over a
    # newest-slot guess. Atomic (temp + rename); best-effort, never fatal.
    try {
        $lkgTmp = Join-Path $InstallDir ("last-known-good.tmp." + $PID)
        [IO.File]::WriteAllText($lkgTmp, "$SrcVersion`n")
        Move-Item -LiteralPath $lkgTmp -Destination (Join-Path $InstallDir 'last-known-good') -Force
    } catch {
        try { Remove-Item -LiteralPath $lkgTmp -Force -ErrorAction SilentlyContinue } catch {}
    }
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($prev) { $gcArgs += @('--keep', $prev) }
    & $LinkPython @gcArgs 2>&1 | ForEach-Object { Write-ServiceChanged "gc: $_" }
    $ErrorActionPreference = $prevEAP
    return $true
}
# === end install-contract:v3 versioned-venv ===

# Layered project config deliberately omits machine paths. During marketplace
# update the cwd is the installed payload, so resolve the adopted repo's anchor
# from the canonical global repo registry through the previous-good runtime.
if ($HasProject -and -not $RepoDir -and (Test-Path $LinkPython)) {
    try {
        $candidate = & $LinkPython -c "import sys; from agent_worktrees.repos import find_repo; e=find_repo(sys.argv[1]); print((e.local_path('windows') if e else '') or '')" $ProjectName 2>$null
        $candidate = ("$candidate").Trim()
        if ($candidate -and (Test-Path $candidate)) {
            $RepoDir = $candidate
        }
    } catch { }
}

# -- Projects registry ----------------------------------------------------

$ProjectsYamlPath = Join-Path $InstallDir 'projects.yaml'

function Read-ProjectsRegistry {
    <# Read projects.yaml and return hashtable. Returns empty projects hash if file missing. #>
    if (-not (Test-Path $ProjectsYamlPath)) {
        return @{ projects = @{} }
    }
    if (-not (Test-Path $VenvPython)) {
        # Can't parse YAML without Python -- return empty
        return @{ projects = @{} }
    }
    try {
        $raw = & $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $ProjectsYamlPath 2>$null
        $parsed = $raw | ConvertFrom-Json
        if (-not $parsed.projects) { $parsed | Add-Member -NotePropertyName 'projects' -NotePropertyValue @{} -Force }
        return $parsed
    } catch {
        return @{ projects = @{} }
    }
}

function Format-YamlValue {
    <# Format a scalar value for YAML output. #>
    param([object]$Val)
    if ($null -eq $Val) { return 'null' }
    if ($Val -is [bool]) { if ($Val) { return 'true' } else { return 'false' } }
    if ($Val -is [string]) { return "`"$($Val -replace '\\', '\\')`"" }
    return "$Val"
}

function Write-YamlFields {
    <# Write fields of a dict/PSCustomObject at a given indent depth. #>
    param([object]$Entry, [int]$Indent = 4)
    $pad = ' ' * $Indent
    $fields = if ($Entry -is [hashtable]) {
        $Entry.GetEnumerator() | Sort-Object Name | ForEach-Object { [PSCustomObject]@{ Name = $_.Key; Value = $_.Value } }
    } elseif ($Entry -is [PSCustomObject]) {
        $Entry.PSObject.Properties
    } else { @() }

    $result = @()
    foreach ($field in $fields) {
        $val = $field.Value
        # Check scalars first: Join-Path and other cmdlets wrap strings in
        # PSObject, making them pass -is [PSCustomObject].  Checking string/
        # ValueType/null before the PSCustomObject test prevents that.
        if ($null -eq $val -or $val -is [string] -or $val -is [ValueType]) {
            $result += "${pad}$($field.Name): $(Format-YamlValue $val)"
        } elseif ($val -is [hashtable] -or $val -is [PSCustomObject]) {
            $result += "${pad}$($field.Name):"
            $result += Write-YamlFields -Entry $val -Indent ($Indent + 2)
        } else {
            $result += "${pad}$($field.Name): $(Format-YamlValue $val)"
        }
    }
    return $result
}

function Register-ProjectEntry {
    <# Thin wrapper: the projects.yaml write lives in ONE place -- the Python
       `register-project-entry` subcommand (installer.register_project). Both
       platform installers call it rather than reimplementing the registry
       logic, so the lean-entry rules, field preservation, and schema stamping
       have a single owner (the drift that caused the anchor bug is removed).
       `expose_agent` is resolved from repos.yaml inside the subcommand. #>
    param([string[]]$ExtraArgs = @())
    if (-not (Test-Path $VenvPython)) { return }
    $awArgs = @('-m', 'agent_worktrees', 'register-project-entry',
                $ProjectName) + $ExtraArgs
    if ($RepoDir -and -not ($ExtraArgs -contains '--repo-dir')) {
        $awArgs += @('--repo-dir', $RepoDir)
    }
    $prevPythonPath = $env:PYTHONPATH
    $savedPayloadRoot = $env:AGENT_WORKTREES_PAYLOAD_ROOT
    try {
        $env:PYTHONPATH = $null
        $env:AGENT_WORKTREES_PAYLOAD_ROOT = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
            $env:COPILOT_PLUGIN_STAGED_FROM
        } else {
            $PluginDir
        }
        & $VenvPython @awArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Project registration failed (exit $LASTEXITCODE)"
        }
    } finally {
        $env:PYTHONPATH = $prevPythonPath
        if ($null -eq $savedPayloadRoot) {
            Remove-Item Env:AGENT_WORKTREES_PAYLOAD_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:AGENT_WORKTREES_PAYLOAD_ROOT = $savedPayloadRoot
        }
    }
}

# -- WSL availability (cached, with timeout) ------------------------------

$script:WslTimeoutSeconds = 5
$script:WslChecked = $false
$script:WslAvailable = $false

function Invoke-WslWithTimeout {
    <# Run wsl.exe with a timeout. Returns @{ Success; ExitCode; Output; TimedOut }. #>
    param(
        [string[]]$Arguments,
        [int]$TimeoutSeconds = $script:WslTimeoutSeconds
    )
    $result = @{ Success = $false; ExitCode = -1; Output = ''; TimedOut = $false }
    try {
        $job = Start-Job -ScriptBlock {
            param($args_)
            $out = & wsl.exe @args_ 2>&1
            [PSCustomObject]@{ ExitCode = $LASTEXITCODE; Output = ($out | Out-String) }
        } -ArgumentList (,@($Arguments))
        $done = Wait-Job $job -Timeout $TimeoutSeconds
        if ($done) {
            $data = Receive-Job $job
            $result.ExitCode = $data.ExitCode
            $result.Output = $data.Output
            $result.Success = ($data.ExitCode -eq 0)
        } else {
            $result.TimedOut = $true
            Stop-Job $job -ErrorAction SilentlyContinue
        }
        Remove-Job $job -Force -ErrorAction SilentlyContinue
    } catch {
        # wsl.exe not found or other fatal error
    }
    return $result
}

function Test-WslAvailable {
    <# One-time check whether WSL is functional. Caches result for this script run. #>
    if ($script:WslChecked) { return $script:WslAvailable }
    $script:WslChecked = $true
    $r = Invoke-WslWithTimeout -Arguments @('-l', '-q')
    if ($r.TimedOut) {
        Write-ServiceWarn "WSL timed out after ${script:WslTimeoutSeconds}s - skipping all WSL operations"
        $script:WslAvailable = $false
        return $false
    }
    if (-not $r.Success) {
        Write-ServiceWarn "WSL not available (exit code $($r.ExitCode)) - skipping all WSL operations"
        $script:WslAvailable = $false
        return $false
    }
    # Verify at least one distro is listed
    $distros = ($r.Output -replace "`0", '') -split "`n" | Where-Object { $_ -match '\S' }
    if ($distros.Count -eq 0) {
        Write-ServiceWarn "No WSL distros found - skipping all WSL operations"
        $script:WslAvailable = $false
        return $false
    }
    $script:WslAvailable = $true
    return $true
}

# -- Machine detection ----------------------------------------------------

$HostnameMap = @{
    # Add entries here if COMPUTERNAME differs from desired machine name.
    # If empty, the lowercase hostname is used as-is.
}

function Resolve-Machine {
    $hostname = $env:COMPUTERNAME
    if ($HostnameMap.ContainsKey($hostname)) {
        return $HostnameMap[$hostname]
    }
    # Parity with the Python `detect_machine` (dotfiles#572): consult
    # machines.yaml (keys, the explicit `hostname` field, and aliases) so a box
    # whose COMPUTERNAME differs from its roster key still resolves to the
    # canonical machine name instead of the raw hostname. Best-effort -- needs
    # the venv and a repo dir with a machines.yaml; otherwise falls through to
    # the lowercase-hostname default below.
    if ($RepoDir -and $VenvPython -and (Test-Path $VenvPython)) {
        try {
            $detected = & $VenvPython -c "import sys; from agent_worktrees.config import detect_machine; print(detect_machine(sys.argv[1]))" $RepoDir 2>$null
            if ($LASTEXITCODE -eq 0 -and $detected) {
                $detected = "$detected".Trim()
                if ($detected) { return $detected }
            }
        } catch { }
    }
    # Unknown machine -- use lowercase hostname as machine name
    return $hostname.ToLower()
}

# -- Helpers --------------------------------------------------------------

function Test-ScriptSyntax {
    <# Validate PowerShell script syntax. Returns $true if valid. #>
    param([string]$Path)
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        Write-ServiceErr "Syntax errors in $(Split-Path $Path -Leaf):"
        $errors | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        return $false
    }
    return $true
}

function Invoke-NativeCapture {
    <# Windows PowerShell 5.1 promotes native stderr to NativeCommandError.
       Scope ErrorActionPreference to Continue so callers can inspect the real
       exit code and output, then restore the installer's fail-fast behavior. #>
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

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
# === install-contract:v4 marker/toss helpers (#935) ===
function Get-ApplicationPath {
    <# Resolve exactly one application path. Get-Command is array-valued when
       PATH exposes several matches; never let .Source array expansion become a
       newline-joined native command. #>
    param([Parameter(Mandatory)][string[]]$Name)
    foreach ($candidate in $Name) {
        $commands = @(Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            $source = [string]$command.Source
            if (-not $source -or $source -match 'WindowsApps') { continue }
            $wingetTarget = Resolve-WinGetPackageExecutable -Source $source
            if ($wingetTarget) { return $wingetTarget }
            if (Test-Path -LiteralPath $source -PathType Leaf) { return $source }
        }
    }
    return $null
}

function Resolve-WinGetPackageExecutable {
    <# WinGet's Links directory can contain a reparse shim that PowerShell
       cannot invoke while native output is redirected. Resolve that link to
       the ordinary package binary before capture. #>
    param([Parameter(Mandatory)][string]$Source)
    if (-not $env:LOCALAPPDATA) { return $null }
    $linksRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    $sourceParent = Split-Path -Parent $Source
    if (-not $sourceParent -or -not [string]::Equals(
        [IO.Path]::GetFullPath($sourceParent).TrimEnd('\'),
        [IO.Path]::GetFullPath($linksRoot).TrimEnd('\'),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        return $null
    }
    $link = Get-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue
    if (-not $link -or
        -not ($link.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        return $null
    }
    $packagesRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (-not (Test-Path -LiteralPath $packagesRoot -PathType Container)) {
        return $null
    }
    $leaf = Split-Path -Leaf $Source
    $enumeration = @{
        LiteralPath = $packagesRoot
        Recurse = $true
        File = $true
        ErrorAction = 'SilentlyContinue'
    }
    if (-not [Management.Automation.WildcardPattern]::ContainsWildcardCharacters(
        $leaf
    )) {
        $enumeration.Filter = $leaf
    }
    $matches = @(Get-ChildItem @enumeration |
        Where-Object {
            [string]::Equals(
                $_.Name,
                $leaf,
                [StringComparison]::OrdinalIgnoreCase
            ) -and $_.Length -gt 0 -and
            -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
        } |
        Select-Object -First 2)
    if ($matches.Count -eq 1) { return [string]$matches[0].FullName }
    return $null
}

function Get-CurrentPowerShellPath {
    <# Return the on-disk host needed to invoke an ExternalScript from the
       Python activation-preservation subprocess. #>
    $processPath = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    if ($processPath -and (Test-Path -LiteralPath $processPath -PathType Leaf)) {
        return $processPath
    }
    $fallback = Get-ApplicationPath -Name @('pwsh', 'powershell')
    if ($fallback) { return $fallback }
    throw "Cannot resolve the current PowerShell host to an executable path"
}

function Resolve-CopilotCommand {
    <# Resolve `copilot` to an argv prefix that Python can execute. Applications
       bypass shadowing aliases/functions; aliases to a differently named
       Application or ExternalScript are followed. Functions and cmdlets are
       intentionally unsupported because they cannot cross the process boundary. #>
    $pending = New-Object System.Collections.Queue
    $pending.Enqueue('copilot')
    $seen = @{}
    $unsupported = New-Object System.Collections.Generic.HashSet[string]

    while ($pending.Count -gt 0) {
        $candidate = [string]$pending.Dequeue()
        if ($seen.ContainsKey($candidate)) { continue }
        $seen[$candidate] = $true

        $application = Get-ApplicationPath -Name @($candidate)
        if ($application) {
            return @($application)
        }

        $commands = @(Get-Command $candidate -All -ErrorAction SilentlyContinue)
        foreach ($command in $commands) {
            $commandType = [string]$command.CommandType
            if ($commandType -eq 'Alias') {
                $unsupported.Add($commandType) | Out-Null
                $target = [string]$command.Definition
                if ($target) { $pending.Enqueue($target) }
                continue
            }
            if ($commandType -eq 'ExternalScript') {
                $scriptPath = [string]$command.Source
                if (-not $scriptPath) { $scriptPath = [string]$command.Path }
                if (
                    $scriptPath -and
                    [IO.Path]::GetExtension($scriptPath) -ieq '.ps1' -and
                    (Test-Path -LiteralPath $scriptPath -PathType Leaf)
                ) {
                    return @(
                        (Get-CurrentPowerShellPath),
                        '-NoProfile',
                        '-File',
                        $scriptPath
                    )
                }
            }
            if ($commandType) { $unsupported.Add($commandType) | Out-Null }
        }
    }

    if ($unsupported.Count -gt 0) {
        $types = (@($unsupported) | Sort-Object) -join ', '
        throw "Copilot CLI resolves only to unsupported PowerShell command type(s): $types"
    }
    return @()
}

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
        $result = Invoke-NativeCapture { & py -3 -c 'import sys; print(sys.executable)' }
        if ($result.ExitCode -eq 0 -and $result.Output -and (Test-Path $result.Output)) {
            return $result.Output
        }
    }
    return Get-ApplicationPath -Name @('python3', 'python')
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

function Write-V3Manifest {
    <# Unified schema_version 3 manifest -- self-contained per plugin. Records
       the source footprint (local vs marketplace); written atomically. #>
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $pluginPath = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
        $env:COPILOT_PLUGIN_STAGED_FROM
    } else {
        $PluginDir.ToString()
    }
    $kind = Get-SourceKind -PluginPath $pluginPath
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $ver = ($vl.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $g = Get-GitInfo -Path (Split-Path -Parent (Split-Path -Parent $pluginPath))
        $commit = $g.commit; $branch = $g.branch; $dirty = $g.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-worktrees'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($pluginPath -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-worktrees'
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($LinkDir -replace '\\', '/')
        runtime        = 'python'
    }
    $tmp = "$manifestPath.tmp"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        $tmp,
        ($manifest | ConvertTo-Json -Depth 4),
        $utf8NoBom
    )
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-ServiceOk "Deploy manifest written (source: $kind)"
}

function Test-UvConfiguredIndex {
    if ($env:UV_CONFIG_FILE) {
        $configPaths = @($env:UV_CONFIG_FILE)
    } else {
        $configPaths = @()
        if ($env:APPDATA) {
            $configPaths += Join-Path $env:APPDATA 'uv\uv.toml'
        }
        if ($env:PROGRAMDATA) {
            $configPaths += Join-Path $env:PROGRAMDATA 'uv\uv.toml'
        }
    }
    foreach ($configPath in $configPaths) {
        if (-not $configPath -or -not (Test-Path -LiteralPath $configPath)) { continue }
        $inIndex = $false
        foreach ($line in Get-Content -LiteralPath $configPath) {
            $value = ($line -replace '\s+#.*$', '').Trim()
            if ($value -match '^index-url\s*=') { return $true }
            if ($value -match '^\[\[index\]\]$') {
                $inIndex = $true
                continue
            }
            if ($value -match '^\[') { $inIndex = $false }
            if ($inIndex -and $value -match '^default\s*=\s*true$') {
                return $true
            }
        }
    }
    return $false
}

function Ensure-UvIndex {
    <# Bridge the governed pip index-url to uv. uv does not read pip.conf, so a
       governed feed (where public PyPI is blocked) must be exported as
       UV_DEFAULT_INDEX or uv resolves against public PyPI and fails. No-op when
       uv already has an environment or file-configured index, or when pip has no
       configured index. Mirrors install.sh's `_ensure_uv_index`. #>
    if ($env:UV_DEFAULT_INDEX -or $env:UV_INDEX_URL -or (Test-UvConfiguredIndex)) { return }
    $idx = ''
    if (Get-Command pip -CommandType Application -ErrorAction SilentlyContinue) {
        $result = Invoke-NativeCapture { & pip config get global.index-url }
        if ($result.ExitCode -eq 0) { $idx = $result.Output }
    }
    if (-not $idx) {
        $pythonPath = Get-ApplicationPath -Name @('python')
        if (-not $pythonPath) { $pythonPath = Get-BootstrapPython }
        if ($pythonPath) {
            $result = Invoke-NativeCapture { & $pythonPath -m pip config get global.index-url }
            if ($result.ExitCode -eq 0) { $idx = $result.Output }
        }
    }
    if (-not $idx) {
        $configPaths = @($env:PIP_CONFIG_FILE)
        if ($env:APPDATA) {
            $configPaths += Join-Path $env:APPDATA 'pip\pip.ini'
        }
        if ($env:PROGRAMDATA) {
            $configPaths += Join-Path $env:PROGRAMDATA 'pip\pip.ini'
        }
        foreach ($configPath in $configPaths) {
            if (-not $configPath -or -not (Test-Path -LiteralPath $configPath)) { continue }
            $match = Select-String -LiteralPath $configPath `
                -Pattern '^\s*index-url\s*=\s*(\S+)\s*$' |
                Select-Object -First 1
            if ($match) {
                $idx = $match.Matches[0].Groups[1].Value
                break
            }
        }
    }
    if ($idx) {
        $env:UV_DEFAULT_INDEX = $idx
        Write-ServiceChanged "uv index derived from pip config (governed-feed bridge)"
    }
}

function Ensure-Uv {
    $existing = Get-ApplicationPath -Name @('uv')
    if ($existing) {
        $result = Invoke-NativeCapture { & $existing --version }
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

    $pythonPath = Get-ApplicationPath -Name @('python', 'python3')
    if (-not $pythonPath) { $pythonPath = Get-BootstrapPython }
    if (-not $pythonPath) {
        Write-ServiceErr 'uv is absent and no Python is available to bootstrap it'
        return $false
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
        Write-ServiceErr "uv bootstrap does not support Windows architecture: $arch"
        return $false
    }

    Write-ServiceChanged "uv not found -- vendoring the official Windows release into $toolDir"
    Ensure-InstallDir $toolDir
    $urlTemplate = $env:AGENT_WORKTREES_UV_BOOTSTRAP_URL
    if (-not $urlTemplate) {
        $urlTemplate = 'https://github.com/astral-sh/uv/releases/latest/download/{asset}'
    }
    $url = $urlTemplate.Replace('{asset}', $asset)
    $bootstrap = @'
import os
import pathlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile

url, target = sys.argv[1:3]
target = pathlib.Path(target)
request = urllib.request.Request(url, headers={'User-Agent': 'agent-worktrees-bootstrap'})
fd, archive = tempfile.mkstemp(suffix='.zip')
os.close(fd)
staging = pathlib.Path(tempfile.mkdtemp(prefix='uv-stage-', dir=target.parent))
try:
    with urllib.request.urlopen(request, timeout=120) as response, open(archive, 'wb') as out:
        shutil.copyfileobj(response, out)
    found = set()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            name = pathlib.PurePosixPath(member).name
            if name not in {'uv.exe', 'uvx.exe'}:
                continue
            with bundle.open(member) as source, open(staging / name, 'wb') as out:
                shutil.copyfileobj(source, out)
            found.add(name)
    if 'uv.exe' not in found:
        raise RuntimeError('uv.exe was absent from the release archive')
    target.mkdir(parents=True, exist_ok=True)
    if (staging / 'uvx.exe').exists():
        os.replace(staging / 'uvx.exe', target / 'uvx.exe')
    os.replace(staging / 'uv.exe', target / 'uv.exe')
finally:
    try:
        pathlib.Path(archive).unlink()
    except FileNotFoundError:
        pass
    shutil.rmtree(staging, ignore_errors=True)
'@
    $result = Invoke-NativeCapture { & $pythonPath -c $bootstrap $url $toolDir }
    if ($result.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $uvPath)) {
        Write-ServiceErr "Failed to vendor uv: $($result.Output)"
        return $false
    }
    $result = Invoke-NativeCapture { & $uvPath --version }
    if ($result.ExitCode -ne 0) {
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Write-ServiceErr "Vendored uv is not executable: $($result.Output)"
        return $false
    }
    $env:PATH = "$toolDir;$env:PATH"
    Write-ServiceOk "Vendored uv into $toolDir"
    return $true
}

function Invoke-VenvPackageInstall {
    <# Install a local package dir into the venv, forcing the local code to
       refresh even when its (dev) version string is unchanged.

       PREFERS `uv` (harness convention: always uv, never bare pip). uv is
       immune to a failure class pip is not: pip's post-install "scripts on
       PATH?" check `realpath`s EVERY %PATH% entry, so an untrusted junction on
       PATH -- e.g. Agency's `%APPDATA%\agency\CurrentVersion` -- makes pip die
       with `WinError 448` under Windows RedirectionGuard (dotfiles book2). uv
       never resolves PATH entries, so it sidesteps it. Falls back to
       `<venv python> -m pip` ONLY when uv is unavailable (uv strictly preferred
       whenever present). Every command runs from a trusted CWD (SystemDrive
       root), never the profile mount, so the WinGet uv.exe reparse shim is safe
       on SAC / profile-mount Cloud PCs.

       Returns [pscustomobject]@{ ExitCode; Output }. #>
    param(
        [Parameter(Mandatory)][string]$VenvPython,
        [Parameter(Mandatory)][string]$PkgName,
        [Parameter(Mandatory)][string]$PkgDir
    )

    $uvPath = Get-ApplicationPath -Name @('uv')
    $uvAvailable = [bool]$uvPath
    $out = ''
    $rc = 0

    $prevLoc = Get-Location
    Set-Location "$env:SystemDrive\"
    try {
        if ($uvAvailable) {
            Ensure-UvIndex
            # Install-contract: resolved-path equivalent of `uv pip install`.
            $result = Invoke-NativeCapture {
                & $uvPath pip install --python $VenvPython `
                    --reinstall-package $PkgName "$PkgDir" --quiet
            }
            $out = $result.Output
            $rc = $result.ExitCode
        }
        if (-not $uvAvailable -or $rc -ne 0) {
            # Fallback only when uv is absent (or its install failed). pip may
            # itself fail here on a machine whose PATH carries an untrusted
            # junction (see the header) -- which is why uv is preferred.
            $pipVersion = Invoke-NativeCapture { & $VenvPython -m pip --version }
            $hasPip = $pipVersion.ExitCode -eq 0
            if ($hasPip) {
                # 1) Resolve + install dependencies (idempotent once present).
                $result = Invoke-NativeCapture {
                    & $VenvPython -m pip install "$PkgDir" --quiet
                }
                $pipOut = $result.Output
                $rc = $result.ExitCode
                if ($rc -eq 0) {
                    # 2) Force just the local package's code to refresh (deps are
                    #    already satisfied) so unchanged dev versions still update.
                    $result = Invoke-NativeCapture {
                        & $VenvPython -m pip install --force-reinstall `
                            --no-deps "$PkgDir" --quiet
                    }
                    $pipOut = "$pipOut`n$($result.Output)".Trim()
                    $rc = $result.ExitCode
                }
                $out = if ($out) {
                    @($out, $pipOut) -join [Environment]::NewLine
                } else {
                    $pipOut
                }
            }
        }
    } finally {
        Set-Location $prevLoc
    }
    return [pscustomobject]@{ ExitCode = $rc; Output = $out }
}

function Deploy-Package {
    <# Install the agent_worktrees package into the venv via uv (non-editable),
       then stamp build info into the INSTALLED site-packages copy. Replaces the
       old file-copy-to-lib + PYTHONPATH model. Requires the venv to exist. #>
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (-not (Test-Path $pyproj)) {
        Write-ServiceErr "Plugin source not found: $PluginDir"
        return $false
    }

    # Purge stale in-tree build artifacts before building. A setuptools build
    # writes build\lib\... (and *.egg-info) into the source tree; on the NEXT
    # deploy an isolated build copies the whole source dir -- including that
    # stale build\lib -- and repackages the OLD code from it instead of the
    # fresh src\ tree (symptom: venv reports the new version yet imports old
    # code). Removing these forces a clean rebuild from src\ on every deploy.
    # Covers the plugin and its vendored libs.
    foreach ($pat in @(
            (Join-Path $PluginDir 'build'),
            (Join-Path $PluginDir 'src\*.egg-info'),
            (Join-Path $PluginDir 'libs\*\build'),
            (Join-Path $PluginDir 'libs\*\src\*.egg-info'))) {
        Get-Item $pat -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    # On Windows, the console-script exe (Scripts\agent-worktrees.exe) may be
    # held open by a running invocation -- most commonly the bare launcher/
    # picker process that hosts the current session. uv does remove-then-write,
    # and Windows denies *deleting* an in-use exe (os error 5) even though it
    # *allows renaming* it. So pre-emptively move any locked console script
    # aside; uv then writes a fresh one and the old process keeps its renamed
    # handle until it exits. Best-effort cleanup of prior stashes too.
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    $consoleExe = Join-Path $scriptsDir 'agent-worktrees.exe'
    if (Test-Path $consoleExe) {
        try {
            Remove-Item $consoleExe -Force -ErrorAction Stop
        } catch {
            $stash = "$consoleExe.old-$(Get-Date -Format yyyyMMddHHmmss)"
            try {
                Rename-Item $consoleExe $stash -ErrorAction Stop
            } catch {
                Write-ServiceErr "Console script is locked and could not be moved aside: $consoleExe"
                $ErrorActionPreference = $prevEAP
                return $false
            }
        }
    }
    Get-ChildItem (Join-Path $scriptsDir 'agent-worktrees.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }

    # Vendored config-schema-migration lib (agent-config-migrate / module
    # config_migrate). Install it first so the package dependency resolves from
    # the local path on every deploy. It lives inside the plugin folder, so the
    # path is identical in the git-checkout and marketplace layouts.
    $cfgMigrateDir = Join-Path $PluginDir 'libs\config-migrate'
    if (Test-Path (Join-Path $cfgMigrateDir 'pyproject.toml')) {
        $libRes = Invoke-VenvPackageInstall -VenvPython $VenvPython -PkgName 'agent-config-migrate' -PkgDir $cfgMigrateDir
        if ($libRes.ExitCode -ne 0) {
            Write-ServiceErr "config-migrate library install failed (exit $($libRes.ExitCode))"
            if ($libRes.Output.Trim()) { Write-ServiceErr ("install: " + $libRes.Output.Trim()) }
            $ErrorActionPreference = $prevEAP
            return $false
        }
    }

    # Vendored plugin-resolution lib (agent-plugin-resolve / module
    # plugin_resolve). Like config-migrate, install it first so the package's
    # dependency is satisfied from the local path: the venv build prefers
    # `python -m pip`, which does NOT honor pyproject's [tool.uv.sources] path,
    # so the dep must already be present when the main package installs.
    $pluginResolveDir = Join-Path $PluginDir 'libs\plugin-resolve'
    if (Test-Path (Join-Path $pluginResolveDir 'pyproject.toml')) {
        $libRes = Invoke-VenvPackageInstall -VenvPython $VenvPython -PkgName 'agent-plugin-resolve' -PkgDir $pluginResolveDir
        if ($libRes.ExitCode -ne 0) {
            Write-ServiceErr "plugin-resolve library install failed (exit $($libRes.ExitCode))"
            if ($libRes.Output.Trim()) { Write-ServiceErr ("install: " + $libRes.Output.Trim()) }
            $ErrorActionPreference = $prevEAP
            return $false
        }
    }

    $installRes = Invoke-VenvPackageInstall -VenvPython $VenvPython -PkgName 'agent-worktrees' -PkgDir $PluginDir
    $rc = $installRes.ExitCode
    $ErrorActionPreference = $prevEAP
    if ($rc -ne 0) {
        Write-ServiceErr "Package install failed (exit $rc)"
        if ($installRes.Output.Trim()) { Write-ServiceErr ("install: " + $installRes.Output.Trim()) }
        return $false
    }

    # Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused).
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    # Retire the legacy file-copy package dir FIRST, so a stale ambient
    # PYTHONPATH=...\lib cannot make the import below resolve to the old copy
    # (and so it can't shadow the venv copy at runtime).
    if (Test-Path $LibDir) {
        Remove-Item $LibDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Stamp build info into the installed copy so --version reflects this deploy.
    # Clear PYTHONPATH for the resolution so the import resolves to site-packages.
    $prevPP = $env:PYTHONPATH
    $env:PYTHONPATH = ''
    $pkgDir = (& $VenvPython -c "import agent_worktrees, os; print(os.path.dirname(agent_worktrees.__file__))" 2>$null | Out-String).Trim()
    $env:PYTHONPATH = $prevPP
    if ($pkgDir) {
        $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PluginDir)
        $commit = ''; $branch = ''
        try {
            $commit = (git -C $repoRoot rev-parse HEAD 2>$null)
            $branch = (git -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null)
        } catch { }
        if (-not $commit) { $commit = 'unknown' }
        if (-not $branch) { $branch = 'unknown' }
        $srcNorm = ($PluginDir -replace '\\', '/')
        $ver = '0.0.0'
        if (Test-Path $pyproj) {
            $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
            if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
        }
        $buildContent = @"
`"`"`"Build provenance -- auto-generated at deploy time. Do not edit.`"`"`"

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$ver",
    "commit": "$commit",
    "branch": "$branch",
    "build_timestamp": "$ts",
    "source": "$srcNorm",
}
"@
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText((Join-Path $pkgDir '_build_info.py'), $buildContent, $utf8NoBom)
    } else {
        Write-ServiceWarn "Could not locate installed agent_worktrees -- build info not stamped"
    }

    Write-ServiceOk "Package installed into venv"
    return $true
}

function Get-SignedBasePython {
    <# Return the path to a SAC-trusted (Authenticode-signed) base Python
       (>=3.11), or $null. Smart App Control blocks the unsigned uv-managed
       Python and the console-script trampoline .exe; building the venv from a
       signed base with `--copies` embeds a signed python.exe in the venv
       (Authenticode survives the copy), which SAC allows.

       Candidates are gathered from several sources because none is reliable on
       its own: the `py` launcher is absent on some Cloud PCs, so
       we also scan the well-known all-users / per-user install roots (where a
       signed `C:\Program Files\Python312\python.exe` lives) and any
       `python`/`python3` on PATH -- skipping the WindowsApps App Execution
       Alias, which is a 0-byte reparse stub, not a real interpreter. Each
       candidate is verified to be a real interpreter >=3.11 before its
       signature is checked. #>
    $cands = @()

    # 1. py launcher (when present) -- newest first.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11') {
            $result = Invoke-NativeCapture {
                & py "-$v" -c 'import sys;print(sys.executable)'
            }
            if ($result.ExitCode -eq 0 -and $result.Output) {
                $cands += $result.Output
            }
        }
    }

    # 2. Well-known install roots (all-users + per-user); newest version first.
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs\Python'))
    foreach ($root in $roots) {
        if (-not $root) { continue }
        Get-ChildItem -Path $root -Filter 'Python3*' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path $exe) { $cands += $exe }
            }
    }

    # 3. python / python3 on PATH, minus the WindowsApps alias reparse stub.
    foreach ($name in 'python', 'python3') {
        Get-Command $name -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path -notmatch '\\WindowsApps\\' } |
            ForEach-Object { $cands += $_.Path }
    }

    foreach ($c in ($cands | Select-Object -Unique)) {
        if (-not (Test-Path $c)) { continue }
        # Must be a real interpreter >= 3.11 ...
        $result = Invoke-NativeCapture {
            & $c -c 'import sys;print("%d.%d" % sys.version_info[:2])'
        }
        $ver = if ($result.ExitCode -eq 0) { $result.Output } else { '' }
        if (-not ($ver -match '^(\d+)\.(\d+)$')) { continue }
        if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 11)) { continue }
        # ... and SAC-trusted (Authenticode Valid).
        try {
            if ((Get-AuthenticodeSignature $c).Status -eq 'Valid') { return $c }
        } catch {}
    }
    return $null
}

function Deploy-Venv {
    <# Create venv and install pyyaml via uv. #>

    # Rebuild an existing venv whose python.exe is unsigned (Smart App Control
    # blocks it) when a signed base Python is available to rebuild from.
    if (Test-Path $VenvPython) {
        $sigStatus = try { (Get-AuthenticodeSignature $VenvPython).Status } catch { 'Unknown' }
        if ($sigStatus -ne 'Valid' -and (Get-SignedBasePython)) {
            Write-ServiceChanged "Existing venv python is unsigned (Smart App Control-incompatible) -- rebuilding from signed Python"
            try {
                Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop
            } catch {
                Write-ServiceWarn "Could not remove existing venv (in use?): $_ -- keeping it"
            }
        }
    }

    # Create the venv. Prefer a SAC-trusted signed base Python via `--copies`
    # (the signed python.exe is embedded in the venv); fall back to uv when no
    # signed Python is present (fine on machines without Smart App Control).
    if (-not (Test-Path $VenvPython)) {
        Invoke-VersionedSlotClean
        $signedBase = Get-SignedBasePython
        $created = $false
        if ($signedBase) {
            $result = Invoke-NativeCapture {
                & $signedBase -m venv --copies $VenvDir
            }
            if ($result.ExitCode -eq 0 -and (Test-Path $VenvPython)) {
                $created = $true
                Write-ServiceOk "Venv created from signed Python ($signedBase)"
            } else {
                Write-ServiceWarn "Signed-Python venv creation failed -- falling back to uv"
            }
        }
        if (-not $created) {
            if (-not $signedBase) {
                Write-ServiceWarn "No signed system Python found -- using uv (unsigned). On Smart App Control machines, install python.org Python 3.11+ and re-run update."
            }
            # Run uv from a trusted CWD (SystemDrive root), never the profile
            # mount -- launching the WinGet uv.exe reparse shim with the profile
            # as CWD is blocked on SAC/profile-mount Cloud PCs ("untrusted mount
            # point").
            $prevLoc = Get-Location
            Set-Location "$env:SystemDrive\"
            try {
                $args_ = @('venv', $VenvDir, '--python', '3.11', '--allow-existing')
                $uvResult = Invoke-NativeCapture { & uv @args_ }
                if ($uvResult.ExitCode -ne 0) {
                    # Fallback: try without version constraint
                    $args_ = @('venv', $VenvDir, '--allow-existing')
                    $uvResult = Invoke-NativeCapture { & uv @args_ }
                }
            } finally {
                Set-Location $prevLoc
            }
            if ($uvResult.ExitCode -ne 0) {
                Write-ServiceErr "Failed to create venv: $($uvResult.Output)"
                return $false
            }
            Write-ServiceOk "Venv created at $VenvDir"
        }
    } else {
        Write-ServiceSkipped "Venv already exists at $VenvDir"
    }

    # Ensure pyvenv.cfg exists (uv can sometimes omit it)
    $pyvenvCfg = Join-Path $VenvDir 'pyvenv.cfg'
    if (-not (Test-Path $pyvenvCfg)) {
        $result = Invoke-NativeCapture {
            & $VenvPython -c 'import sys; print(sys.base_prefix)'
        }
        if ($result.ExitCode -eq 0 -and $result.Output) {
            $basePrefix = $result.Output
            @"
home = $basePrefix\Scripts
implementation = CPython
include-system-site-packages = false
prompt = .venv
"@ | Set-Content -Path $pyvenvCfg
            Write-ServiceChanged "Created missing pyvenv.cfg"
        }
    }

    Write-ServiceOk "Venv ready"
    return $true
}

function Deploy-Wrappers {
    <# Copy the static launch wrappers and bootstrap scripts to ~/.agent-worktrees/bin/. #>
    Ensure-InstallDir $BinDir

    foreach ($wrapper in @('launch-session.cmd', 'launch-session.ps1')) {
        $src = Join-Path $PluginDir "bin\$wrapper"
        $dst = Join-Path $BinDir $wrapper
        if (-not (Test-Path $src)) {
            Write-ServiceErr "Wrapper source not found: $src"
            return $false
        }
        Copy-Item $src $dst -Force
        Write-ServiceOk "Wrapper: $wrapper"
    }

    # Deploy the pane wrapper (records the pane_exited exit code inside psmux
    # panes + shows a crash diagnostic). Optional -- mirrors install.sh's
    # pane-wrapper.sh handling; absence just falls back to the verbatim command.
    $paneSrc = Join-Path $PluginDir "bin\pane-wrapper.ps1"
    if (Test-Path $paneSrc) {
        Copy-Item $paneSrc (Join-Path $BinDir 'pane-wrapper.ps1') -Force
        Write-ServiceOk "Wrapper: pane-wrapper.ps1"
    }

    if (-not (Deploy-RuntimeResolvers)) { return $false }

    # Deploy hook scripts, including the consolidated pre/post client and its fallback modules.
    foreach ($script in @('session-conduct.ps1', 'session-conduct.sh', 'session-machine.ps1', 'session-machine.sh', 'bootstrap-check.ps1', 'bootstrap-check.sh', 'project-hooks.ps1', 'project-hooks.sh', 'register-nudge.ps1', 'register-nudge.sh', 'register-session.ps1', 'register-session.sh', 'deregister-session.ps1', 'deregister-session.sh', 'anchor-hygiene-check.ps1', 'anchor-hygiene-check.sh', 'marketplace-overrides.ps1', 'marketplace-overrides.sh', 'provision-check.ps1', 'provision-check.sh', 'statelessness_guard.py', 'cross_repo_guard.py', 'anchor_write_guard.py', 'nudge_status.py', 'bind_nudge.py', 'hook_client.py', 'bind-nudge.sh', 'bind-nudge.ps1')) {
        $src = Join-Path $ScriptDir $script
        $dst = Join-Path $BinDir $script
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
            Write-ServiceOk "Hook: $script"
        }
    }

    # Deploy the session-conduct data fragments (scripts/conduct/*.md) that the
    # session-conduct sessionStart hook emits as additionalContext, cwd-gated.
    # This replaces the per-project *.instructions.md deploy for these generic
    # fragments (dotfiles#1053 / effort instructions-to-hooks).
    $ConductSrc = Join-Path $ScriptDir 'conduct'
    if (Test-Path $ConductSrc) {
        $ConductDst = Join-Path $BinDir 'conduct'
        Ensure-InstallDir $ConductDst
        foreach ($frag in (Get-ChildItem -Path $ConductSrc -Filter '*.md' -File)) {
            Copy-Item $frag.FullName (Join-Path $ConductDst $frag.Name) -Force
            Write-ServiceOk "Conduct: $($frag.Name)"
        }
    }

    # Deploy normalized setup and optional machine-settings reconciliation.
    # Mirrors installer.py deploy_wrappers().
    $ScriptsDir = Join-Path $InstallDir 'scripts'
    Ensure-InstallDir $ScriptsDir
    foreach ($setup in @(
        'default-setup.ps1',
        'default-setup.sh',
        'launch-command.ps1',
        'launch-command.sh',
        'reconcile-machine-settings.ps1',
        'reconcile-machine-settings.sh'
    )) {
        $src = Join-Path $ScriptDir $setup
        $dst = Join-Path $ScriptsDir $setup
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
            Write-ServiceOk "Session script: $setup"
        }
    }

    return $true
}

function Deploy-RuntimeResolvers {
    <# Atomically install the runtime-owned helpers used by payload-local shims. #>
    Ensure-InstallDir $BinDir
    foreach ($resolver in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $src = Join-Path $ScriptDir $resolver
        if (-not (Test-Path $src)) {
            Write-ServiceErr "Runtime resolver source not found: $src"
            return $false
        }
        $dst = Join-Path $BinDir $resolver
        $tmp = Join-Path $BinDir ".$resolver.$PID.tmp"
        try {
            Copy-Item $src $tmp -Force
            Move-Item $tmp $dst -Force
        } finally {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        }
        Write-ServiceOk "Runtime resolver: $resolver"
    }
    return $true
}

function Deploy-Binstub {
    <# Generate the project-specific binstub in ~/.local/bin/.
       Routes through the Python CLI for subcommand dispatch.
       Falls back to launch-session.cmd if the venv is missing. #>
    Ensure-InstallDir $LocalBin

    # Reserved-name guard (belt-and-suspenders with the ProjectName resolution
    # above): a project stub for `agent-worktrees` writes agent-worktrees.{cmd,
    # ps1} -- the SAME paths as the global shims -- clobbering them with a
    # self-`--project` binstub. Never overwrite the global command.
    if ($ProjectName -eq 'agent-worktrees') {
        Write-ServiceWarn "Refusing to deploy project binstub for reserved runtime name 'agent-worktrees' (global command owned by Deploy-GlobalBinstub)"
        return
    }

    $content = @"
@echo off
rem agent-worktrees project binstub
set "PYTHONUTF8=1"
set "AGENT_WORKTREES_LAUNCH_ID=$ProjectName-%RANDOM%-%RANDOM%"
set "AGENT_WORKTREES_BINSTUB_STARTED=%DATE% %TIME%"
set "AGENT_WORKTREES_LAUNCH_TRACE=%USERPROFILE%\.agent-worktrees\logs\picker-launches.jsonl"
if not exist "%USERPROFILE%\.agent-worktrees\logs" mkdir "%USERPROFILE%\.agent-worktrees\logs" >nul 2>&1
(>>"%AGENT_WORKTREES_LAUNCH_TRACE%" echo {"event":"binstub_start","timestamp":"%AGENT_WORKTREES_BINSTUB_STARTED%","launch_id":"%AGENT_WORKTREES_LAUNCH_ID%","project":"$ProjectName"}) 2>nul
set "AGENT_WORKTREES_BINSTUB_TRACED=1"
if "%~1"=="" (
  call "%USERPROFILE%\.agent-worktrees\bin\launch-session.cmd" --project $ProjectName
  exit /b %ERRORLEVEL%
)
rem Context resolves from CWD / --project (git-like); the binstub names its
rem project via --project, not an ambient env var.
set "_PSHOST="
for /f "delims=" %%I in ('"%SystemRoot%\System32\where.exe" pwsh 2^>nul') do if not defined _PSHOST set "_PSHOST=%%I"
if not defined _PSHOST set "_PSHOST=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%_PSHOST%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0$ProjectName.ps1" %*
exit /b %ERRORLEVEL%
"@
    $dst = Join-Path $LocalBin "$ProjectName.cmd"
    Set-Content -Path $dst -Value $content -NoNewline

    # Primary .ps1 (PowerShell prefers it over the .cmd in the same dir; @args
    # forwards argv verbatim so quoting/&&/|/;/! survive). The .cmd above stays
    # as a fallback for cmd.exe, `cmd /c` Windows Terminal profiles, and ssh
    # launchers. Both route through the signed venv python via -m, falling back
    # to launch-session when the venv is missing (recovery).
    $ps1Content = (@'
# agent-worktrees project binstub
$env:PYTHONUTF8 = '1'
if (-not $env:AGENT_WORKTREES_BINSTUB_TRACED) {
    $env:AGENT_WORKTREES_LAUNCH_ID = '%%PROJECT%%-' + [guid]::NewGuid().ToString('N')
    $env:AGENT_WORKTREES_BINSTUB_STARTED = [DateTime]::UtcNow.ToString('o')
    $_awTraceDir = Join-Path $env:USERPROFILE '.agent-worktrees\logs'
    $env:AGENT_WORKTREES_LAUNCH_TRACE = Join-Path $_awTraceDir 'picker-launches.jsonl'
    try {
        [IO.Directory]::CreateDirectory($_awTraceDir) | Out-Null
        $_awEvent = [ordered]@{ event = 'binstub_start'; timestamp = $env:AGENT_WORKTREES_BINSTUB_STARTED; launch_id = $env:AGENT_WORKTREES_LAUNCH_ID; project = '%%PROJECT%%' }
        [IO.File]::AppendAllText($env:AGENT_WORKTREES_LAUNCH_TRACE, ($_awEvent | ConvertTo-Json -Compress) + [Environment]::NewLine)
    } catch {}
}
if ($args.Count -eq 0) {
    & "$env:USERPROFILE\.agent-worktrees\bin\launch-session.ps1" --project '%%PROJECT%%'
    exit $LASTEXITCODE
}
# Context resolves from CWD / --project (git-like). This .ps1 runs in-process in
# the caller's session, so it names its project via --project (not an ambient
# env var), leaving the live session env untouched. Recovery (venv missing)
# uses the same explicit launcher argument.
# Resolve the runtime slot python via the junction-free current-version marker
# (the .venv junction is retired -- #637/#1085/#1106).
$_root = Join-Path $env:USERPROFILE '.agent-worktrees'
$AwPy = $null
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
if (Test-Path -LiteralPath $_resolver -PathType Leaf) { . $_resolver }
$_py = $AwPy
if (Test-Path $_py) {
    & $_py -m agent_worktrees --project '%%PROJECT%%' @args
    exit $LASTEXITCODE
}
& "$env:USERPROFILE\.agent-worktrees\bin\launch-session.cmd" --project '%%PROJECT%%' @args
exit $LASTEXITCODE
'@).Replace('%%PROJECT%%', $ProjectName)
    $ps1Dst = Join-Path $LocalBin "$ProjectName.ps1"
    [System.IO.File]::WriteAllText($ps1Dst, $ps1Content, (New-Object System.Text.UTF8Encoding($false)))
    Write-ServiceOk "Binstub: $ps1Dst (+ .cmd fallback)"
}


function Deploy-GlobalBinstub {
    <# Deploy the project-agnostic ~/.local/bin/agent-worktrees.{ps1,cmd} from
       the plugin's static bin/agent-worktrees.{ps1,cmd}. Runs as its own early
       step (not buried in WT shortcut handling) so the SAC-safe launcher is
       always refreshed on install/update.

       The .ps1 is the primary entry point: PowerShell resolves a .ps1
       (ExternalScript) ahead of a .cmd (Application) in the same dir, and
       @args forwards argv verbatim. The .cmd is kept as a fallback for callers
       that cannot resolve a .ps1 (cmd.exe, `cmd /c` Windows Terminal profiles,
       ssh launchers).

       Skip the copy when on-disk content already matches (newline-normalized):
       running the global stub while overwriting it with a different-length
       file corrupts cmd.exe's byte-offset read (issue #13). #>
    Ensure-InstallDir $LocalBin
    foreach ($name in @('agent-worktrees.ps1', 'agent-worktrees.cmd', 'agent-worktrees')) {
        $src = Join-Path $PluginDir "bin\$name"
        $dst = Join-Path $LocalBin $name
        if (Test-Path $src) {
            $srcNorm = ([System.IO.File]::ReadAllText($src)) -replace "`r`n", "`n" -replace "`r", "`n"
            $dstNorm = if (Test-Path $dst) { ([System.IO.File]::ReadAllText($dst)) -replace "`r`n", "`n" -replace "`r", "`n" } else { $null }
            if ($srcNorm -cne $dstNorm) {
                Copy-Item $src $dst -Force
                Write-ServiceOk "Global binstub: $dst"
            } else {
                Write-ServiceSkipped "Global binstub up to date: $dst"
            }
        }
    }
}


function Deploy-Config {
    <# Write config.yaml to the project dir if missing (or Force). Returns $true if written. #>
    param([string]$Machine)

    # Global machine-wide config first (the user-owned base tier).
    $globalPath = Join-Path $InstallDir 'config.yaml'
    if (-not (Test-Path $globalPath)) {
        $srcRootG = if ($RepoDir) { Split-Path -Parent $RepoDir } else { '' }
        @"
# ~/.agent-worktrees/config.yaml
# GLOBAL machine-wide agent-worktrees config (lowest precedence tier).
#
# Machine-wide defaults shared across every project on this machine. Per-repo
# settings layer on top: <anchor>/.agent-worktrees/config.yaml (the repo's own
# config) then ~/.<project>/config.yaml (machine-local override).

srcroot: $srcRootG
machine: $Machine
platform: windows

# Copilot backend profiles -- machine-wide (Tab to cycle in the picker).
# User-authored; uncomment and edit. Example:
# copilot_profiles:
#   - name: cloud
#     label: "Cloud (GitHub)"
"@ | Set-Content -Path $globalPath
        Write-ServiceChanged "Written global config: $globalPath"
    } else {
        # User-owned: scaffolded once, then never overwritten (not even -Force).
        Write-ServiceSkipped "Global config exists at $globalPath (user-owned, left as-is)"
    }

    $configPath = Join-Path $ProjectDir 'config.yaml'
    if ((Test-Path $configPath) -and -not $Force) {
        Write-ServiceSkipped "Config exists at $configPath (use -Force to overwrite)"
        return $false
    }

    if (-not $RepoDir) {
        Write-ServiceSkipped "Config generation skipped (no repo detected -- set CWD to the repo or create config.yaml manually)"
        return $false
    }

    $worktreeRoot = "$RepoDir.worktrees"

    # Resolve this machine's display name (selection vocabulary) so the seeded
    # self·agent diagonal matches the Picker's roster axes. Falls back to the
    # machine key when machines.yaml has no entry.
    $seedDisplay = $Machine
    $myYaml = if ($RepoDir) { Join-Path $RepoDir 'machines.yaml' } else { $null }
    if ($myYaml -and (Test-Path $myYaml)) {
        try {
            $md = (& $VenvPython -c "import yaml, json, sys; print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))" $myYaml 2>$null) | ConvertFrom-Json
            if ($md.machines -and $md.machines.PSObject.Properties[$Machine] -and $md.machines.$Machine.display_name) {
                $seedDisplay = $md.machines.$Machine.display_name
            }
        } catch { }
    }

    @"
# ~/.$ProjectName/config.yaml
# Machine-local config for $ProjectName (overrides + machine paths only).
# Machine-wide defaults -> ~/.agent-worktrees/config.yaml.
# Repo settings may live in-repo -> <anchor>/.agent-worktrees/config.yaml.

repo_name: $ProjectName

repos:
  ${ProjectName}:
    anchor: $RepoDir
    # worktree_root defaults to $worktreeRoot -- a sibling
    # <anchor>.worktrees dir, matching Copilot CLI's /worktree layout.
    # Uncomment and set an absolute path to override.
    default_branch: master
    remote: origin

# terminal_profiles -- this machine's terminal-profile column (the Picker's
# Profiles grid). Seeded with the locked self-agent diagonal only; add other
# targets via the Picker's Profiles view or `$ProjectName profiles apply`.
terminal_profiles:
  - {machine: $seedDisplay, env: Win, kind: agent}
"@ | Set-Content -Path $configPath
    Write-ServiceChanged "Written config: $configPath"
    return $true
}

function Deploy-TerminalScripts {
    <# Deploy the per-session psmux options + opt-in keybind scripts to BIN_DIR.
       agent-worktrees no longer owns ~/.psmux.conf: the launcher stamps the
       status bar + behaviors per-session from session-options.ps1, and
       apply-mux-keybinds.ps1 is an opt-in server-global tuning script the user
       (or a restore flow) may run. Mirrors install.sh deploy_terminal_scripts. #>
    Ensure-InstallDir $BinDir
    $srcDir = Join-Path $PluginDir 'terminal'
    foreach ($script in @('session-options.ps1', 'apply-mux-keybinds.ps1', 'psmux-passthrough.conf')) {
        $src = Join-Path $srcDir $script
        if (-not (Test-Path $src)) {
            Write-ServiceWarn "terminal script not found at $src"
            continue
        }
        Copy-Item $src (Join-Path $BinDir $script) -Force
        Write-ServiceOk "Terminal script: $script"
    }
    $psmuxPathHelper = Join-Path $PluginDir 'scripts\psmux-path.ps1'
    if (Test-Path -LiteralPath $psmuxPathHelper) {
        Copy-Item $psmuxPathHelper (Join-Path $BinDir 'psmux-path.ps1') -Force
        Write-ServiceOk "Terminal script: psmux-path.ps1"
    }

    # Relinquish the legacy installer-owned ~/.psmux.conf. Earlier versions
    # deployed a fully-managed global config (status bar + keybinds) and
    # drift-overwrote it; the per-session model makes that file obsolete and its
    # global status bar would double up with the per-session one. Only remove it
    # when it is unmistakably OUR old managed file (header match) -- never a
    # user's personal config, nor an opt-in apply-mux-keybinds.ps1 block.
    $psmuxConf = Join-Path $env:USERPROFILE '.psmux.conf'
    if (Test-Path $psmuxConf) {
        $head = (Get-Content -LiteralPath $psmuxConf -TotalCount 5 -ErrorAction SilentlyContinue) -join "`n"
        if ($head -match 'Deployed by agent-worktrees installer') {
            Remove-Item $psmuxConf -Force -ErrorAction SilentlyContinue
            Write-ServiceChanged "Relinquished legacy psmux config ($psmuxConf) - now configured per-session"
        }
    }
}

function Ensure-PsmuxSshSafe {
    <# `winget install marlocarlo.psmux` registers psmux as an App-Execution-Alias
       reparse shim at %LOCALAPPDATA%\Microsoft\WinGet\Links\psmux.exe. Windows
       refuses to run that shim over a NON-interactive network logon ("the path
       cannot be traversed because it contains an untrusted mount point"), so
       `psmux` is unusable when a worktree session is driven over SSH -- e.g. the
       dtssh interactive reach or an agent-bridge dispatch landing as a
       piped/NoProfile pwsh. (Same reparse-shim wall the WinGet uv.exe install
       hits.)

       Fix: select the newest compatible REAL binary, remove
       every stale marlocarlo.psmux package directory from User and process
       PATH, and put that directory first. This does not mutate the package or
       its server, so it remains safe while existing sessions are live. #>
    $packageRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $helper = Join-Path $PSScriptRoot 'psmux-path.ps1'
    if (-not (Test-Path -LiteralPath $helper)) {
        Write-ServiceWarn "psmux: PATH repair helper missing at $helper"
        return
    }
    . $helper
    $selected = Find-AwCompatiblePsmuxPackageBinary `
        -PackageRoot $packageRoot
    if (-not $selected) {
        Write-ServiceWarn "psmux: no compatible WinGet package binary was found; User PATH was not changed"
        return
    }

    $userPath = Get-CopilotPersistentEnvironmentVariable -Name 'Path' -Target 'User'
    if (-not $userPath) { $userPath = '' }
    $repair = Repair-AwPsmuxPath -SelectedDirectory $selected.Directory `
        -UserPath $userPath -ProcessPath $env:Path -PackageRoot $packageRoot
    if ($repair.UserChanged) {
        Set-CopilotPersistentEnvironmentVariable -Name 'Path' -Value $repair.UserPath -Target 'User'
        Write-ServiceChanged "psmux: selected compatible $($selected.Version) package binary and removed stale package dirs from User PATH (SSH-safe): $($selected.Directory)"
    }
    $env:Path = $repair.ProcessPath

    # PATH can expose the selected package binary plus a WinGet link/older
    # package. Get-Command then returns an array; select the actual winner before
    # passing Source to scalar -Path parameters.
    $resolved = Get-Command psmux -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $resolvedVersion = if ($resolved) {
        Get-AwPsmuxBinaryVersion -Path $resolved.Source
    } else {
        $null
    }
    if (-not $resolved -or
        (ConvertTo-AwNormalizedPath $resolved.Source) -ne
            (ConvertTo-AwNormalizedPath $selected.Path) -or
        $resolvedVersion -ne $selected.Version) {
        throw "psmux PATH repair failed current-process verification: resolved '$($resolved.Source)' version '$resolvedVersion', expected '$($selected.Path)' version '$($selected.Version)'"
    }

    $env:AW_PSMUX_EXPECTED_VERSION = $selected.Version
    try {
        $verify = '$cmd = Get-Command psmux -CommandType Application -ErrorAction Stop | Select-Object -First 1; ' +
            '$out = (& $cmd.Source --help 2>&1 | Select-Object -First 1) | Out-String; ' +
            '$pattern = "(?<![0-9.])" + [regex]::Escape($env:AW_PSMUX_EXPECTED_VERSION) + "(?![0-9.])"; ' +
            'if ($out -notmatch $pattern) { exit 1 }'
        & pwsh.exe -NoLogo -NoProfile -NonInteractive -Command $verify
        if ($LASTEXITCODE -ne 0) {
            throw "psmux PATH repair failed NoProfile/SSH-style verification for version $($selected.Version)"
        }
    } finally {
        Remove-Item Env:AW_PSMUX_EXPECTED_VERSION -ErrorAction SilentlyContinue
    }
    Write-ServiceOk "psmux resolves and runs compatible version $($selected.Version) in current and NoProfile/SSH-style shells"
}

function Resolve-AwPsmuxBin {
    <# WinGet installs psmux as a 0-byte App Execution Alias reparse stub under
       %LOCALAPPDATA%\Microsoft\WinGet\Links\psmux.exe that PowerShell 7.4.x
       cannot launch -- a native `& psmux ...` throws a terminating error
       ("StandardOutputEncoding is only supported when standard output is
       redirected"). Resolve the stub to the real
       ...\WinGet\Packages\...\psmux.exe so native launches work on any pwsh
       version (pwsh >=7.5 launches the stub fine too). #>
    param($Cmd)
    if (-not $Cmd) { return 'psmux' }
       $helper = Join-Path $PSScriptRoot 'psmux-path.ps1'
       if (Test-Path -LiteralPath $helper) {
           . $helper
           $desired = Find-AwCompatiblePsmuxPackageBinary `
               -PackageRoot (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages')
           if ($desired) { return $desired.Path }
       }
       $src = $Cmd.Source
    try {
        $item = Get-Item -LiteralPath $src -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -eq 0) {
            $real = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') `
                -Recurse -Filter 'psmux.exe' -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($real) { return $real }
        }
    } catch {}
    return $src
}

function Ensure-Psmux {
    <# Ensure a compatible psmux version. Versions 3.3.5 and 3.3.7+ are
       accepted; 3.3.6 is blocked. Missing or incompatible versions are
       installed/upgraded to the preferred version. The
       installer deliberately does not own the machine's winget pin -- an
       adopting machine-state package may pin an exact validated version.
       Replacement happens only when no live sessions exist, because refreshing
       the portable package
       tears down the running psmux server and every attached session. #>
    $installVersion = '3.3.8'
    if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) {
        Write-Host "  Installing psmux $installVersion (terminal multiplexer)..."
        & winget install --id marlocarlo.psmux --version $installVersion --exact `
            --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-ServiceOk "psmux $installVersion installed"
        } else {
            Write-ServiceWarn "psmux install failed - sessions will launch without multiplexing"
        }
        Ensure-PsmuxSshSafe
        return
    }
    $muxBin = Resolve-AwPsmuxBin (Get-Command psmux -ErrorAction SilentlyContinue)
    $helper = Join-Path $PSScriptRoot 'psmux-path.ps1'
    if (-not (Test-Path -LiteralPath $helper)) {
        Write-ServiceWarn "psmux compatibility cannot be validated because the helper is missing -- not replacing it"
        return
    }
    . $helper
    if (-not (Get-Command Get-AwPsmuxBinaryVersion -ErrorAction SilentlyContinue) -or
        -not (Get-Command Test-AwPsmuxVersionCompatible -ErrorAction SilentlyContinue)) {
        Write-ServiceWarn "psmux compatibility cannot be validated because the helper is unavailable -- not replacing it"
        return
    }
    $psmuxVer = Get-AwPsmuxBinaryVersion -Path $muxBin
    $psmuxDisplay = if ($psmuxVer) { $psmuxVer } else { '<unknown>' }
    $compatible = Test-AwPsmuxVersionCompatible -Version $psmuxVer
    if (-not $compatible) {
        $sessionState = Get-AwPsmuxSessionState -Path $muxBin
        if (-not $sessionState.Known) {
            Write-ServiceWarn "psmux $psmuxDisplay is incompatible, but live-session state could not be determined -- not replacing it. Re-run 'update' after confirming all psmux sessions are closed."
        } elseif ($sessionState.Sessions.Count -gt 0) {
            Write-ServiceWarn "psmux $psmuxDisplay is incompatible. $($sessionState.Sessions.Count) live session(s) present -- not replacing it now (that would kill them). Close all worktree sessions and re-run 'update' to upgrade."
        } else {
            Write-ServiceChanged "psmux $psmuxDisplay is incompatible -- refreshing the portable package"
            & winget install --id marlocarlo.psmux --version $installVersion --exact `
                --uninstall-previous --force --accept-source-agreements `
                --accept-package-agreements 2>&1 | Out-Null
            $selected = Find-AwCompatiblePsmuxPackageBinary `
                -PackageRoot (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages')
            if ($selected) {
                Write-ServiceOk "psmux upgraded to compatible version $($selected.Version)"
            } else {
                Write-ServiceWarn "psmux refresh attempted but no compatible installed binary was found"
            }
        }
    } else {
        Write-ServiceOk "psmux available ($psmuxDisplay; compatible)"
    }
    Ensure-PsmuxSshSafe
}

# Helper: check if a WSL binstub actually exists on disk
function Test-WslBinstubExists {
    param(
        [string]$Name,
        [string]$Distro
    )
    if (-not (Test-WslAvailable)) { return $false }
    $args_ = if ($Distro) {
        @('-d', $Distro, '--', 'bash', '-c', "test -x `"`$HOME/.local/bin/$Name`"")
    } else {
        @('--', 'bash', '-c', "test -x `"`$HOME/.local/bin/$Name`"")
    }
    $r = Invoke-WslWithTimeout -Arguments $args_
    return $r.Success
}

# Helper: detect the default WSL distro name
function Get-WslDefaultDistro {
    if (-not (Test-WslAvailable)) { return $null }
    $r = Invoke-WslWithTimeout -Arguments @('-l', '-q')
    if (-not $r.Success) { return $null }
    $name = ($r.Output -replace "`0", '') -split "`n" | Where-Object { $_ -match '\S' } | Select-Object -First 1
    $name = $name.Trim()
    if ($name) { return $name }
    return $null
}

# Build-TerminalFragment (PowerShell) RETIRED: the Windows Terminal fragment
# is now generated by the single Python source of truth
# (agent_worktrees.terminal_fragment). Deploy-Shortcuts captures its JSON via
# `agent_worktrees terminal-fragment --machine <key>`. This kills the PS/Python
# generator drift that silently dropped Terminal profiles.

function Sync-TerminalState {
    <# Synchronize WT settings.json and state.json after a fragment regeneration.

       When the fragment changes, two WT state files need cleanup:

       1. settings.json -- cached fragment-sourced profiles with stale GUIDs
          must be removed so they don't persist as ghost entries.

       2. state.json -- the generatedProfiles array tracks every profile GUID
          WT has ever seen from fragments.  If a GUID is in generatedProfiles
          but absent from both the fragment and settings.json, WT interprets
          this as "user intentionally deleted this profile" and hides it.
          We must remove stale GUIDs and newly-added GUIDs from this list so
          WT rediscovers them fresh on next launch. #>
    param(
        [string[]]$OldFragmentGuids = @(),
        [string[]]$NewFragmentGuids = @(),
        [string[]]$ChangedGuids = @()
    )

    # Warn if WT is running -- state.json changes may be overwritten on WT exit
    $wtProc = Get-Process -Name WindowsTerminal -ErrorAction SilentlyContinue
    if ($wtProc) {
        Write-ServiceWarn "Windows Terminal is running -- close it fully and re-run update for new profiles to appear"
    }

    # --- state.json: generatedProfiles ---
    $statePath = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\state.json'

    # Stale = OUR previous fragment GUIDs no longer emitted -> WT should forget
    # them. Hoisted out of the state block so it is always defined (the
    # Clean-TerminalSettingsJson call below references it even when state.json
    # is absent).
    $staleGuids = @($OldFragmentGuids | Where-Object { $_ -notin $NewFragmentGuids })

    # GUIDs WT has actually materialized into settings.json right now. The
    # convergent invariant (dotfiles#601): any CURRENT fragment GUID that WT has
    # NOT materialized must be pruned from generatedProfiles so WT re-discovers
    # it. This is idempotent -- it heals every run regardless of update history,
    # unlike the retired "newly added since the previous fragment" delta, which
    # only fired on a one-time transition and so left a profile hidden forever
    # once that single cleanup was lost to the WT-running race warned about
    # above ("every update makes the fragments worse").
    $settingsGuids = Get-SettingsProfileGuids
    $notMaterialized = @($NewFragmentGuids | Where-Object { $_ -notin $settingsGuids })

    # GUIDs from EVERY installed fragment (ours + any other extension's), so
    # orphan reclamation never touches a GUID some live fragment still emits.
    $allFragGuids = Get-AllFragmentGuids

    if (Test-Path $statePath) {
        try {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json
            if ($state.generatedProfiles) {
                $genProfiles = @($state.generatedProfiles)
                $before = $genProfiles.Count

                # Reclaim OUR accumulated orphans (dotfiles#601): a
                # generatedProfiles GUID that WT has NOT materialized
                # (not in settings), that NO installed fragment emits, and that
                # is NOT an RFC v4/v5 GUID. Our New-StableGuid builds a GUID from
                # raw SHA-256 bytes without setting the version bits, so it is
                # v4/v5 only ~1/8 of the time; WT's own built-in generators
                # (WSL/Azure/default shells) mint v5 and hand-added profiles are
                # v4. Excluding v4/v5 therefore drains our leftover cruft while
                # NEVER resurrecting a WSL/Azure profile the user deliberately
                # deleted (see Test-IsV4OrV5Guid). Foreign-safe by construction.
                $reclaimOrphans = @($genProfiles | Where-Object {
                    $g = $_.ToLower()
                    ($g -notin $settingsGuids) -and
                    ($g -notin $allFragGuids) -and
                    (-not (Test-IsV4OrV5Guid $g))
                })

                # Remove: stale (dropped from the fragment) + not-materialized
                # (WT is hiding a live fragment profile) + changed (same GUID,
                # new content -> force rediscovery) + our reclaimable orphans.
                # Unchanged, materialized GUIDs stay, preserving user
                # customizations; foreign GUIDs are never touched.
                $removeSet  = @(@($staleGuids) + @($notMaterialized) + @($ChangedGuids) + @($reclaimOrphans) | ForEach-Object { $_.ToLower() } | Sort-Object -Unique)

                if ($removeSet.Count -gt 0) {
                    $state.generatedProfiles = @($genProfiles | Where-Object {
                        $_.ToLower() -notin $removeSet
                    })
                    $after = @($state.generatedProfiles).Count
                    if ($after -ne $before) {
                        $state | ConvertTo-Json -Depth 10 | Set-Content $statePath -Encoding UTF8
                        $reclN = @($reclaimOrphans).Count
                        $suffix = if ($reclN -gt 0) { " ($reclN orphan(s) reclaimed)" } else { "" }
                        Write-ServiceChanged "Cleaned $($before - $after) GUID(s) from WT state.json generatedProfiles$suffix"
                    }
                }
            }
        } catch {
            Write-ServiceWarn "Could not update WT state.json: $_"
        }
    }

    # --- settings.json: stale cached profiles ---
    Clean-TerminalSettingsJson -StaleGuids @(@($staleGuids) + @($ChangedGuids) | Sort-Object -Unique) -NewFragmentGuids $NewFragmentGuids
}

function Test-IsV4OrV5Guid {
    <# Whether a GUID's RFC-4122 version nibble is 4 (random) or 5 (SHA-1) --
       the shapes WT's built-in dynamic generators and hand-added profiles use.
       Our New-StableGuid emits raw-hash GUIDs whose version nibble is ~uniform,
       so this cheaply distinguishes "almost certainly foreign" (v4/v5) from
       "probably ours" for foreign-safe orphan reclamation. The version nibble
       is the first hex digit of the third dash-group: xxxxxxxx-xxxx-Nxxx-... #>
    param([string]$Guid)
    $s = ($Guid -replace '[{}]', '')
    $parts = $s.Split('-')
    if ($parts.Count -lt 3 -or [string]::IsNullOrEmpty($parts[2])) { return $false }
    return ($parts[2][0] -eq '4' -or $parts[2][0] -eq '5')
}

function Get-AllFragmentGuids {
    <# Lower-cased union of profile GUIDs across EVERY installed Windows Terminal
       fragment (ours + any other extension's), so orphan reclamation never
       prunes a GUID a live fragment still emits. Returns @() when no fragments
       are present. #>
    $fragRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments'
    if (-not (Test-Path $fragRoot)) { return @() }
    $out = @()
    Get-ChildItem $fragRoot -Recurse -File -Filter *.json -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $frag = Get-Content $_.FullName -Raw | ConvertFrom-Json
            if ($frag.profiles) {
                $out += @($frag.profiles | Where-Object { $_.PSObject.Properties['guid'] } | ForEach-Object { $_.guid.ToLower() })
            }
        } catch { }
    }
    return @($out | Sort-Object -Unique)
}

function Get-SettingsProfileGuids {
    <# Lower-cased GUIDs WT currently has in settings.json profiles.list --
       i.e. the profiles WT has actually materialized. Used by the convergent
       generatedProfiles invariant in Sync-TerminalState. Returns @() when
       settings.json is absent or unparseable. #>
    $settingsPath = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
    if (-not (Test-Path $settingsPath)) { return @() }
    try {
        $j = Get-Content $settingsPath -Raw | ConvertFrom-Json
        if ($j.profiles -and $j.profiles.list) {
            return @($j.profiles.list |
                Where-Object { $_.PSObject.Properties['guid'] } |
                ForEach-Object { $_.guid.ToLower() })
        }
    } catch { }
    return @()
}

function Clean-TerminalSettingsJson {
    <# Remove stale profiles and schemes from WT settings.json.

       Removes AgentWorktrees-sourced profiles whose GUID is stale (no longer
       in the current fragment) or changed (same GUID, updated content --
       must be rediscovered from the new fragment). #>
    param(
        [string[]]$StaleGuids = @(),
        [string[]]$NewFragmentGuids = @()
    )

    $settingsPath = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
    if (-not (Test-Path $settingsPath)) { return }

    try {
        $raw = Get-Content $settingsPath -Raw -ErrorAction Stop
        $json = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-ServiceWarn "Could not parse WT settings.json for cleanup: $_"
        return
    }

    # If no GUIDs were passed, read them from the fragment on disk
    if ($NewFragmentGuids.Count -eq 0) {
        $fragmentPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees\agent-worktrees.json'
        if (Test-Path $fragmentPath) {
            try {
                $frag = Get-Content $fragmentPath -Raw | ConvertFrom-Json
                $NewFragmentGuids = @($frag.profiles | ForEach-Object { $_.guid.ToLower() })
            } catch { }
        }
    }

    $changed = $false

    if ($json.profiles -and $json.profiles.list) {
        $before = $json.profiles.list.Count
        $json.profiles.list = @($json.profiles.list | Where-Object {
            if (-not $_.PSObject.Properties['source']) {
                # Manually-added (no source) -- keep unless GUID is explicitly stale
                if ($_.PSObject.Properties['guid'] -and $_.guid.ToLower() -in $StaleGuids) {
                    return $false
                }
                return $true
            }

            # AgentWorktrees-sourced: remove if stale or not in current fragment
            if ($_.source -eq 'AgentWorktrees') {
                if ($_.PSObject.Properties['guid']) {
                    $g = $_.guid.ToLower()
                    # Remove if stale/changed, or if not in current fragment at all
                    if ($g -in $StaleGuids) { return $false }
                    return ($g -in $NewFragmentGuids)
                }
                return $false  # no GUID = orphan, remove
            }

            return $true
        })
        $removed = $before - $json.profiles.list.Count
        if ($removed -gt 0) {
            $changed = $true
            Write-ServiceChanged "Removed $removed stale profile(s) from WT settings.json"
        }
    }

    if ($changed) {
        $backup = "$settingsPath.wt-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item $settingsPath $backup -Force
        $json | ConvertTo-Json -Depth 20 | Set-Content $settingsPath -Encoding UTF8
        Write-ServiceOk "WT settings.json cleaned (backup: $backup)"
    }
}

function Deploy-WslBinstub {
    <# Deploy a thin project binstub into WSL's ~/.local/bin/.
       The binstub launches via the agent-worktrees Python CLI if installed,
       or prints setup instructions if not.  Returns $true if deployed,
       $false if WSL is unavailable or deployment failed. #>

    if (-not (Test-WslAvailable)) { return $false }

    # Detect default distro (already validated by Test-WslAvailable)
    $distro = Get-WslDefaultDistro
    if (-not $distro) {
        Write-ServiceWarn "No WSL distro found - skipping binstub"
        return $false
    }

    # Generate thin launcher with helpful error when not yet installed
    $binstubScript = @"
#!/usr/bin/env bash
# agent-worktrees project binstub
# Thin binstub for $ProjectName - deployed by agent-worktrees (Windows)
# Requires agent-worktrees to be installed in WSL via the copilot-extensions plugin.
# This thin launcher only starts a session (no CLI dispatch), so it passes the
# project directly to the shared launcher.
_launcher="`$HOME/.agent-worktrees/bin/launch-session.sh"
if [[ -x "`$_launcher" ]]; then
    if grep -q -- 'elif \[\[ "`$arg" == "--project" \]\]' "`$_launcher"; then
        exec "`$_launcher" --project "$ProjectName" "`$@"
    fi
    echo "agent-worktrees in WSL is too old for explicit project routing." >&2
    echo "Update the copilot-extensions plugins in WSL, then retry." >&2
    exit 1
else
    echo "agent-worktrees is not installed in WSL." >&2
    echo "To set up:" >&2
    echo "  1. Install the copilot-extensions plugin in WSL" >&2
    echo "  2. Run: agent-worktrees install --project-name $ProjectName" >&2
    exit 1
fi
"@

    # Deploy to WSL via base64 to avoid quoting issues
    try {
        $r = Invoke-WslWithTimeout -Arguments @('-d', $distro, '--', 'bash', '-c', 'mkdir -p "$HOME/.local/bin"') -TimeoutSeconds 10
        if ($r.TimedOut) {
            Write-ServiceWarn "WSL mkdir timed out - skipping binstub"
            return $false
        }

        $cleanScript = $binstubScript -replace "`r", ""
        $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($cleanScript))
        $deployCmd = "echo `"$b64`" | base64 -d > `"`$HOME/.local/bin/$ProjectName`" && chmod +x `"`$HOME/.local/bin/$ProjectName`""
        $r = Invoke-WslWithTimeout -Arguments @('-d', $distro, '--', 'bash', '-c', $deployCmd) -TimeoutSeconds 10

        if ($r.TimedOut) {
            Write-ServiceWarn "WSL binstub deploy timed out"
            return $false
        }
        if ($r.Success) {
            Write-ServiceOk "WSL binstub deployed to ~/.local/bin/$ProjectName ($distro)"

            # Record distro in projects registry (metadata only, not used for
            # gating) through the single Python registry writer.
            Register-ProjectEntry -ExtraArgs @('--wsl-state', 'bootstrap', '--wsl-distro', $distro)

            return $true
        } else {
            Write-ServiceWarn "Failed to deploy WSL binstub"
            return $false
        }
    } catch {
        Write-ServiceWarn "Failed to deploy WSL binstub: $_"
        return $false
    }
}

function Deploy-Shortcuts {
    <# Deploy Windows Terminal fragment (with remote SSH profiles) and create .lnk shortcuts.
       Handles WT state cleanup so new/changed profiles appear correctly on next WT launch. #>
    param([string]$Machine)

    # Deploy WT fragment - use a shared fragment directory for all projects
    $fragmentDir = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees'
    if (-not (Test-Path $fragmentDir)) {
        New-Item -ItemType Directory -Path $fragmentDir -Force | Out-Null
    }

    # Collect GUIDs from existing fragment BEFORE any overwrites.
    # We need these to compute stale GUIDs for state cleanup later.
    $oldFragGuids = @()
    $fragmentDst = Join-Path $fragmentDir 'agent-worktrees.json'
    if (Test-Path $fragmentDst) {
        try {
            $oldFrag = Get-Content $fragmentDst -Raw | ConvertFrom-Json
            $oldFragGuids += @($oldFrag.profiles | ForEach-Object { $_.guid.ToLower() })
        } catch { }
    }
    $oldFragGuids = @($oldFragGuids | Sort-Object -Unique)

    # Generate the fragment from the Python single-source-of-truth generator.
    # First normalize any legacy display-name selections to canonical machine
    # keys (idempotent), then capture the fragment JSON (stdout is pure JSON).
    & $VenvPython -m agent_worktrees terminal-fragment --machine $Machine --migrate-selections 2>&1 |
        ForEach-Object { Write-ServiceChanged "profiles: $_" }
    $fragment = & $VenvPython -m agent_worktrees terminal-fragment --machine $Machine
    if ($LASTEXITCODE -ne 0 -or -not $fragment) {
        Write-ServiceErr "Fragment generation failed (agent_worktrees terminal-fragment exited $LASTEXITCODE)"
        return
    }
    $newFragObj = $fragment | ConvertFrom-Json
    $newFragGuids = @($newFragObj.profiles | ForEach-Object { $_.guid.ToLower() })

    # Detect changed profiles: same GUID but different content (e.g. renamed
    # machine, changed SSH alias).  These need WT rediscovery even though the
    # GUID didn't change.
    $changedGuids = @()
    if ($oldFragGuids.Count -gt 0) {
        $commonGuids = @($oldFragGuids | Where-Object { $_ -in $newFragGuids })
        foreach ($g in $commonGuids) {
            $oldP = $oldFrag.profiles | Where-Object { $_.guid.ToLower() -eq $g }
            $newP = $newFragObj.profiles | Where-Object { $_.guid.ToLower() -eq $g }
            if ($oldP -and $newP) {
                $oldCmd  = if ($oldP.PSObject.Properties['commandline']) { $oldP.commandline } else { '' }
                $newCmd  = if ($newP.PSObject.Properties['commandline']) { $newP.commandline } else { '' }
                $oldName = if ($oldP.PSObject.Properties['name']) { $oldP.name } else { '' }
                $newName = if ($newP.PSObject.Properties['name']) { $newP.name } else { '' }
                if ($oldCmd -ne $newCmd -or $oldName -ne $newName) {
                    $changedGuids += $g
                }
            }
        }
        if ($changedGuids.Count -gt 0) {
            Write-ServiceChanged "$($changedGuids.Count) profile(s) changed content -- will force WT rediscovery"
        }
    }

    # Clean WT state BEFORE writing the new fragment to avoid a race where
    # WT reads the new fragment while stale GUIDs are still in state.json.
    Sync-TerminalState -OldFragmentGuids $oldFragGuids -NewFragmentGuids $newFragGuids -ChangedGuids $changedGuids

    # Write the new fragment
    $fragment | Set-Content $fragmentDst -Encoding UTF8
    Write-ServiceOk "Windows Terminal profiles deployed (fragment with all registered projects)"

    # Create .lnk shortcuts for each registered project
    $shell = New-Object -ComObject WScript.Shell
    $wtExe = "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe"

    $registry = Read-ProjectsRegistry
    $allProjects = @($ProjectName)
    if ($registry.projects -is [PSCustomObject]) {
        foreach ($p in $registry.projects.PSObject.Properties) {
            if ($p.Name -notin $allProjects) { $allProjects += $p.Name }
        }
    } elseif ($registry.projects -is [hashtable]) {
        foreach ($p in $registry.projects.Keys) {
            if ($p -notin $allProjects) { $allProjects += $p }
        }
    }

    # One-time sweep: remove truncated, zero-length shortcut residue left by an
    # earlier unquoted-path bug -- e.g. "Dotfiles (WSL" (no closing paren, no
    # extension) sitting beside the real "Dotfiles (WSL).lnk". The stale-shortcut
    # cleanup below only matches *.lnk, so these orphans were never swept. Scope
    # strictly to EMPTY, extension-less files carrying the " (" label signature,
    # so real binstubs (.cmd/.ps1), shortcuts (.lnk) and foreign files are safe.
    Get-ChildItem -LiteralPath $LocalBin -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -eq 0 -and -not $_.Extension -and $_.Name -like '* (*' } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            Write-ServiceChanged "Removed shortcut residue: $($_.Name)"
        }

    foreach ($proj in $allProjects) {
        # Resilience: a single malformed / incomplete projects.yaml entry (e.g. a
        # null-bodied orphan left by a partial registration) must NOT abort the
        # whole shortcut deploy. Do as much as possible -- note the bad entry,
        # flag it, and move on so every other project's shortcut still lands.
        try {
            $displayName = ($proj -replace '-', ' ') -replace '(^| )(.)', { $_.Value.ToUpper() }

            $lnkPath = Join-Path $LocalBin "$displayName.lnk"
            $lnk = $shell.CreateShortcut($lnkPath)
            $lnk.TargetPath = $wtExe
            $lnk.Arguments = "-p `"$displayName`""
            $lnk.WorkingDirectory = "%USERPROFILE%"
            $lnk.Description = "$displayName - Worktree Session Manager"
            $lnk.Save()

            # WSL shortcut -- only when WSL support is recorded in registry. Guard
            # the entry TYPE before touching .PSObject: a null-valued project key
            # (``realproj:`` with no body) yields $null here, and $null.PSObject
            # .Properties throws "The property 'Properties' cannot be found" --
            # the exact crash this hardening prevents.
            $projWslInfo = $null
            if ($registry.projects -is [PSCustomObject] -and $registry.projects.PSObject.Properties[$proj]) {
                $projEntry = $registry.projects.$proj
                if ($projEntry -is [PSCustomObject] -and $projEntry.PSObject.Properties['wsl'] -and $projEntry.wsl) {
                    $projWslInfo = $projEntry.wsl
                }
            }
            $shortcutWslState = if ($projWslInfo -is [PSCustomObject] -and $projWslInfo.PSObject.Properties['state']) { $projWslInfo.state } else { $null }
            $shortcutWslDistro = if ($projWslInfo -is [PSCustomObject] -and $projWslInfo.PSObject.Properties['distro']) { $projWslInfo.distro } else { $null }
            if ($shortcutWslState -and $shortcutWslDistro) {
                $wslLabel = "$displayName (WSL)"
                $lnkPath = Join-Path $LocalBin "$wslLabel.lnk"
                $lnk = $shell.CreateShortcut($lnkPath)
                $lnk.TargetPath = $wtExe
                $lnk.Arguments = "-p `"$wslLabel`""
                $lnk.WorkingDirectory = "%USERPROFILE%"
                $lnk.Description = "$displayName - Worktree Session Manager (WSL)"
                $lnk.Save()
            } else {
                # Remove stale WSL shortcut if it exists from a previous install
                foreach ($pattern in @("$displayName (WSL).lnk", "$displayName (WSL: *).lnk")) {
                    Get-ChildItem -Path $LocalBin -Filter $pattern -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
                }
            }
        } catch {
            Write-ServiceWarn "Skipped shortcut for project '$proj' (malformed or incomplete registry entry): $_"
            continue
        }
    }

    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null

    # Global binstub deploy now runs as its own early step (Deploy-GlobalBinstub)
    # in the install/update paths, so it is no longer dependent on this WT-heavy
    # function completing. Kept here as an idempotent safety net.
    Deploy-GlobalBinstub
    Write-ServiceOk "Shortcuts deployed to $LocalBin (targeting wt.exe profiles)"
}

function Deploy-CopilotPlugin {
    <# Install agent-worktrees from the copilot-extensions marketplace.
       Ensures the marketplace is registered, installs or updates the plugin,
       then removes any stale _direct install.

       When the installer itself is running from the installed-plugins
       directory (i.e. invoked by cmd_update after it already ran
       'copilot plugin update'), skip the update call to avoid EBUSY
       errors from trying to replace files in our own working directory. #>

    try {
        $copilotCommand = @(Resolve-CopilotCommand)
    } catch {
        Write-ServiceErr $_.Exception.Message
        throw
    }
    if ($copilotCommand.Count -eq 0) {
        Write-ServiceWarn "Copilot CLI not found - skipping plugin install"
        return
    }
    $copilotExecutable = $copilotCommand[0]
    $copilotPrefix = @()
    if ($copilotCommand.Count -gt 1) {
        $copilotPrefix = @($copilotCommand[1..($copilotCommand.Count - 1)])
    }

    # Detect if we are running from the installed plugin directory.
    # When cmd_update invokes us, it sets cwd to the plugin dir and
    # has already done the plugin update -- re-running it would EBUSY
    # on Windows because copilot CLI tries to rmdir our own cwd.
    $installedPluginsDir = Join-Path $env:USERPROFILE '.copilot\installed-plugins'
    $runningFromInstalled = $PluginDir.Path -like "$installedPluginsDir*"

    # 1. Register marketplace if not present
    $marketplaces = (& $copilotExecutable @copilotPrefix plugin marketplace list 2>$null) -join "`n"
    if ($marketplaces -notmatch 'copilot-extensions') {
        $addOut = & $copilotExecutable @copilotPrefix plugin marketplace add ThomasMichon/copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Failed to register marketplace: $addOut"
            return
        }
        Write-ServiceChanged "Registered copilot-extensions marketplace"
    }

    # 2. Parse current plugin state
    $pluginList = & $copilotExecutable @copilotPrefix plugin list 2>$null
    $hasMarketplace = $false
    $hasDirect = $false
    foreach ($line in $pluginList) {
        if ($line -match 'agent-worktrees@copilot-extensions') {
            $hasMarketplace = $true
        } elseif ($line -match 'agent-worktrees' -and $line -notmatch '@') {
            $hasDirect = $true
        }
    }

    # 3. Install or update marketplace plugin
    if ($runningFromInstalled) {
        Write-ServiceOk "Copilot plugin updated (marketplace)"
    } elseif ($hasMarketplace) {
        $out = & $copilotExecutable @copilotPrefix plugin update agent-worktrees@copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Plugin update failed: $out"
        } else {
            Write-ServiceOk "Copilot plugin updated (marketplace)"
        }
    } else {
        $copilotCommandJson = ConvertTo-Json -Compress -InputObject @($copilotCommand)
        $out = & $VenvPython -m agent_worktrees.activation_preservation `
            agent-worktrees@copilot-extensions `
            --copilot-command-json $copilotCommandJson 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Plugin install failed: $out"
            return
        }
        Write-ServiceChanged "Copilot plugin installed (agent-worktrees@copilot-extensions)"
    }

    # 4. Remove stale _direct install if marketplace is now present
    if ($hasDirect) {
        $verify = (& $copilotExecutable @copilotPrefix plugin list 2>$null) -join "`n"
        if ($verify -match 'agent-worktrees@copilot-extensions') {
            & $copilotExecutable @copilotPrefix plugin uninstall agent-worktrees 2>$null | Out-Null
            Write-ServiceChanged "Removed stale _direct plugin install"
        }
    }
}

function Ensure-CopilotExperimental {
    <# Ensure experimental: true in Copilot CLI settings.json.
       The CLI gates extension loading on this flag -- COPILOT_FEATURE_FLAGS
       alone is not sufficient. Both are required. #>
    $settingsFile = Join-Path $env:USERPROFILE '.copilot\settings.json'
    if (-not (Test-Path $settingsFile)) { return }

    try {
        $raw = Get-Content $settingsFile -Raw
        $settings = $raw | ConvertFrom-Json
    } catch {
        Write-ServiceWarn "Could not parse $settingsFile -- skipping"
        return
    }

    $experimental = $settings.PSObject.Properties['experimental']
    if ($experimental -and $experimental.Value -eq $true) {
        Write-ServiceOk "Copilot experimental mode enabled"
        return
    }

    if ($experimental) {
        $settings.experimental = $true
    } else {
        $settings | Add-Member -NotePropertyName experimental -NotePropertyValue $true
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        $settingsFile,
        ($settings | ConvertTo-Json -Depth 10),
        $utf8NoBom
    )
    Write-ServiceChanged "Copilot experimental mode enabled (required for extensions)"
}

function Deploy-GitHooksPath {
    <# Point core.hooksPath at tools/hooks ONLY for repos that actually ship a
       tools/hooks dir (the legacy scheme). Repos using the PR-workflow hook
       multi-machine system keep the DEFAULT .git/hooks (where install_hooks writes the
       shims) -- pointing hooksPath at a missing tools/hooks silently disables
       them (dotfiles#234). Also self-heal a stale pointer an older installer
       left behind when the repo no longer ships tools/hooks. #>
    if (-not $RepoDir) { return }
    $current = git --no-pager -C $RepoDir config --local core.hooksPath 2>$null
    $hasToolsHooks = (Test-Path (Join-Path $RepoDir 'tools\hooks\pre-commit')) -or `
                     (Test-Path (Join-Path $RepoDir 'tools\hooks\pre-push'))
    if (-not $hasToolsHooks) {
        if ($current -eq 'tools/hooks') {
            git -C $RepoDir config --local --unset core.hooksPath 2>$null
            Write-ServiceChanged "Cleared stale core.hooksPath (repo ships no tools/hooks; PR-workflow shims use .git/hooks)"
        }
        return
    }
    if ($current -eq 'tools/hooks') {
        Write-ServiceOk "Git hooksPath = tools/hooks"
        return
    }
    if ($current -and $current -ne 'tools/hooks') {
        Write-ServiceWarn "Git core.hooksPath already set to '$current' - not overwriting"
        Write-Host "    To update manually: git -C $RepoDir config --local core.hooksPath tools/hooks"
        return
    }
    git -C $RepoDir config --local core.hooksPath tools/hooks
    Write-ServiceChanged "Set git core.hooksPath = tools/hooks"
}

function Test-PathIncludes {
    param([string]$Dir)
    $pathDirs = $env:PATH -split ';'
    return ($pathDirs -contains $Dir)
}

function Assert-PathIncludes {
    param([string]$Dir)
    if (-not (Test-PathIncludes $Dir)) {
        Write-ServiceErr "$Dir is not on PATH"
        Write-Host "    Add it: [Environment]::SetEnvironmentVariable('PATH', `$env:PATH + ';$Dir', 'User')"
    } else {
        Write-ServiceOk "$Dir is on PATH"
    }
}

function Remove-Binstub {
    if ($HasProject -and (Test-Path $VenvPython)) {
        $savedPayloadRoot = $env:AGENT_WORKTREES_PAYLOAD_ROOT
        try {
            $env:AGENT_WORKTREES_PAYLOAD_ROOT = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
                $env:COPILOT_PLUGIN_STAGED_FROM
            } else {
                $PluginDir
            }
            & $VenvPython -m agent_worktrees reconcile-binstubs --remove $ProjectName
            if ($LASTEXITCODE -ne 0) {
                Write-ServiceWarn 'Project binstub preserved (ownership check failed)'
            }
        } finally {
            if ($null -eq $savedPayloadRoot) {
                Remove-Item Env:AGENT_WORKTREES_PAYLOAD_ROOT -ErrorAction SilentlyContinue
            } else {
                $env:AGENT_WORKTREES_PAYLOAD_ROOT = $savedPayloadRoot
            }
        }
    }
    foreach ($stub in @('mark-session-complete.cmd', 'mark-session-complete.ps1', 'agent-worktrees.cmd', 'agent-worktrees.ps1')) {
        $path = Join-Path $LocalBin $stub
        if (Test-Path $path) {
            Remove-Item $path -Force
            Write-ServiceChanged "Removed binstub: $path"
        }
    }
}

function Remove-LegacyBinstubs {
    # Sweep legacy alias binstubs from both BinDir and LocalBin, covering
    # bare (bash), .cmd (Windows) and .ps1 variants.
    $removed = 0
    foreach ($name in $LegacyBinstubs) {
        foreach ($dir in @($BinDir, $LocalBin)) {
            foreach ($variant in @($name, "$name.cmd", "$name.ps1")) {
                $path = Join-Path $dir $variant
                if (Test-Path $path) {
                    Remove-Item $path -Force -ErrorAction SilentlyContinue
                    $removed++
                }
            }
        }
    }
    if ($removed -gt 0) {
        Write-ServiceChanged "Removed $removed legacy binstub(s)"
    }
}

function Reconcile-Binstubs {
    <# Reconcile project binstubs in ~/.local/bin against projects.yaml:
       deploy a complete set (.ps1 + .cmd) for every registered project and
       remove signature-matched stubs for deregistered ones. Delegates to the
       Python implementation (single, cross-platform source of truth) so it runs
       regardless of whether this install has a project context. #>
    if (-not (Test-Path $VenvPython)) { return }
    $savedPayloadRoot = $env:AGENT_WORKTREES_PAYLOAD_ROOT
    try {
        $env:PYTHONUTF8 = '1'
        $env:AGENT_WORKTREES_PAYLOAD_ROOT = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
            $env:COPILOT_PLUGIN_STAGED_FROM
        } else {
            $PluginDir
        }
        & $VenvPython -m agent_worktrees reconcile-binstubs 2>&1 |
            ForEach-Object { Write-Host "  $_" }
    } catch {
        Write-ServiceWarn "Binstub reconciliation skipped: $_"
    } finally {
        if ($null -eq $savedPayloadRoot) {
            Remove-Item Env:AGENT_WORKTREES_PAYLOAD_ROOT -ErrorAction SilentlyContinue
        } else {
            $env:AGENT_WORKTREES_PAYLOAD_ROOT = $savedPayloadRoot
        }
    }
}

# -- Actions --------------------------------------------------------------

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE into
    # ~/.agent-worktrees/snapshots/<ver>/, record markers, and deploy ONLY the
    # self-provisioning GLOBAL `agent-worktrees` tool binstub -- deferring the venv
    # build (and the full launcher install: wrappers, hooks, guards, terminal,
    # copilot plugin, per-project binstubs) to a LEAN `provision` on first use. No
    # venv, no uv; never holds the marketplace payload open (copies from the
    # already self-staged $PluginDir). Mirrors the POSIX install.sh `stamp`.
    Write-ServiceHeader "$ServiceName stamp (defer runtime to first use)"
    if (-not $SrcVersion) { Write-ServiceErr 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($dir in @($InstallDir, $LocalBin)) { Ensure-InstallDir $dir }
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    if (-not (Test-Path $snapDir)) {
        $snapTmp = "$snapDir.tmp-$PID"
        if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
        $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
        Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
        }
        Move-Item -LiteralPath $snapTmp -Destination $snapDir
        Write-ServiceOk "Snapshot: $snapDir"
    } else {
        Write-ServiceSkipped "Snapshot already stamped: $snapDir"
    }
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), "$snapDir", $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    Deploy-GlobalBinstub
    Write-ServiceOk 'Stamped: agent-worktrees tool binstub on PATH; runtime provisions on first use.'
}

switch ($Action) {
    'stamp' {
        Invoke-Stamp
    }
    'provision' {
        # Lean runtime build (#1393): venv + package + activate + the global tool
        # binstub + manifest. Deliberately NOT the wrappers/hooks/guards/terminal/
        # copilot-plugin/per-project binstubs -- those belong to the full launcher
        # `install`. Invoked by the self-provisioning binstub on first use. Mirrors
        # the POSIX install.sh `provision`.
        Write-ServiceHeader "Provisioning $ServiceName runtime (lean; tools only, no launcher/hooks)"
        try { git --version 2>&1 | Out-Null } catch {
            Write-ServiceErr 'Missing prerequisite: git'
            exit 1
        }
        if (-not (Ensure-Uv)) { exit 1 }
        Ensure-UvIndex
        foreach ($dir in @($InstallDir, $BinDir, $LocalBin)) { Ensure-InstallDir $dir }
        if (-not (Deploy-RuntimeResolvers)) { exit 1 }
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
        Deploy-GlobalBinstub
        Write-V3Manifest
        Write-ServiceOk "Runtime provisioned (marker -> versions/$SrcVersion); agent-worktrees tools ready."
    }
    'install' {
        Write-ServiceHeader "Installing $ServiceName"

        $machine = Resolve-Machine
        Write-Host "  Machine: $machine"
        if ($HasProject) {
            Write-Host "  Project: $ProjectName"
            if ($RepoDir) { Write-Host "  Repo:    $RepoDir" }
        } else {
            Write-Host "  Project: (none - runtime only; pass -ProjectName to adopt a repo)"
        }

        # Prereq checks
        $missingPrereqs = @()
        try { git --version 2>&1 | Out-Null } catch { $missingPrereqs += 'git' }
        if (-not (Ensure-Uv)) { $missingPrereqs += 'uv' }
        if ($missingPrereqs.Count -gt 0) {
            Write-ServiceErr "Missing prerequisites: $($missingPrereqs -join ', ')"
            exit 1
        }

        # Optional: psmux terminal multiplexer for session persistence. New
        # installs use 3.3.8; existing compatible versions remain supported.
        # See Ensure-Psmux (shared with 'update' so existing boxes self-heal).
        if (-not $ContextualInstall) {
            Ensure-Psmux
        }

        # Structured contexts may mutate only their installation-local paths.
        $runtimeDirs = if ($ContextualInstall) {
            @($InstallDir, $BinDir)
        } else {
            @($InstallDir, $BinDir, $LocalBin)
        }
        if ($HasProject -and -not $ContextualInstall) {
            $runtimeDirs += @($ProjectDir, $WorktreesDir)
        }
        foreach ($dir in $runtimeDirs) {
            Ensure-InstallDir $dir
        }

        # -- Shared runtime (venv first: package install targets the venv) --
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
        if ($ContextualInstall) {
            Write-V3Manifest
            Write-ServiceOk "Context runtime installed at $InstallDir"
            exit 0
        }
        Deploy-CopilotPlugin
        Deploy-GlobalBinstub
        Ensure-CopilotExperimental
        Assert-PathIncludes $LocalBin
        Remove-LegacyBinstubs
        Reconcile-Binstubs
        # Machine-wide terminal integration: deploy the per-session psmux
        # options + opt-in keybind scripts. We do NOT own ~/.psmux.conf -- the
        # launcher stamps the status bar + behaviors per-session at runtime.
        # Deploy regardless of project context (a project-less update must still
        # refresh the terminal scripts).
        Deploy-TerminalScripts

        # -- Project-specific (only when adopting) --
        if ($HasProject) {
            Deploy-Config -Machine $machine | Out-Null
            Register-ProjectEntry
            Reconcile-Binstubs
            Deploy-Shortcuts -Machine $machine
            if ($RepoDir) { Deploy-GitHooksPath }

            # Retire migrated managed instruction files (machine identity now via the session-machine hook)
            if ($RepoDir) {
                try {
                    $env:PYTHONUTF8 = '1'
                    & $VenvPython -m agent_worktrees --project $ProjectName deploy-instructions --machine $machine 2>&1 | ForEach-Object { Write-Host "  $_" }
                } catch {
                    Write-ServiceWarn "Instruction file deployment skipped: $_"
                }
            }
        }

        # Machine-local config schema migration (idempotent + atomic; never
        # touches repo-committed config -- that is an adopt concern). Stamps or
        # upgrades ~/.agent-worktrees/{config,repos,projects}.yaml. Non-fatal.
        try {
            $env:PYTHONUTF8 = '1'
            & $VenvPython -m agent_worktrees config-migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
        } catch {
            Write-ServiceWarn "Config migration skipped: $_"
        }

        Write-V3Manifest

        Write-Host ""
        Write-ServiceOk "Installation complete"
        Write-Host "  Runtime dir: $InstallDir"
        if ($HasProject) {
            Write-Host "  Project dir: $ProjectDir"
            Write-Host "  Usage:       $ProjectName"
        }
        Write-Host "  Runtime:     Python ($VenvPython)"
    }

    'uninstall' {
        Write-ServiceHeader "Uninstalling $ServiceName"

        Remove-Binstub
        Remove-LegacyBinstubs

        # Remove Windows Terminal fragment
        $fragDir = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees'
        if (Test-Path $fragDir) {
            Remove-Item $fragDir -Recurse -Force
            Write-ServiceChanged "Removed Windows Terminal fragment: $fragDir"
        }

        # Remove the deployed terminal scripts. ~/.psmux.conf is intentionally
        # left alone: agent-worktrees no longer owns it (sessions are configured
        # per-session at launch), so uninstall must not delete a file that may
        # now be the user's own (or an opt-in apply-mux-keybinds.ps1 block).
        foreach ($script in @('session-options.ps1', 'apply-mux-keybinds.ps1', 'psmux-passthrough.conf')) {
            $sp = Join-Path $BinDir $script
            if (Test-Path $sp) { Remove-Item $sp -Force; Write-ServiceChanged "Removed terminal script: $script" }
        }
        $psmuxConf = Join-Path $env:USERPROFILE '.psmux.conf'
        if (Test-Path $psmuxConf) {
            Write-ServiceSkipped "Left ~/.psmux.conf in place (no longer managed by agent-worktrees)"
        }

        # Remove shortcuts
        $displayName = ($ProjectName -replace '-', ' ') -replace '(^| )(.)', { $_.Value.ToUpper() }
        foreach ($lnk in @("$displayName.lnk", "$displayName (WSL).lnk")) {
            $lnkPath = Join-Path $LocalBin $lnk
            if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force }
        }
        # Also remove distro-specific WSL shortcuts (e.g. "Test Chamber (WSL: Ubuntu).lnk")
        Get-ChildItem -Path $LocalBin -Filter "$displayName (WSL: *).lnk" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        Write-ServiceChanged "Removed shortcuts"

        # Remove Python runtime (venv + package). Versioned: the `.venv` link +
        # the whole versions/ tree; otherwise the single real venv dir.
        if ($VersionedRuntime) {
            if ((Test-Path $LinkDir) -and ((Get-Item $LinkDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                & cmd /c rmdir "$LinkDir" 2>$null
            } elseif (Test-Path $LinkDir) {
                Remove-Item $LinkDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            $verRoot = Join-Path $InstallDir 'versions'
            if (Test-Path $verRoot) { Remove-Item $verRoot -Recurse -Force -ErrorAction SilentlyContinue }
            Write-ServiceChanged "Removed versioned venv (.venv link + versions/)"
        } elseif (Test-Path $VenvDir) {
            Remove-Item $VenvDir -Recurse -Force
            Write-ServiceChanged "Removed venv: $VenvDir"
        }
        if (Test-Path $LibDir) {
            Remove-Item $LibDir -Recurse -Force
            Write-ServiceChanged "Removed package: $LibDir"
        }

        # Remove wrappers
        foreach ($wrapper in @('launch-session.cmd', 'launch-session.ps1', 'pane-wrapper.ps1')) {
            $path = Join-Path $BinDir $wrapper
            if (Test-Path $path) { Remove-Item $path -Force }
        }
        Write-ServiceChanged "Removed wrappers from $BinDir"

        if ($RemoveConfig) {
            if (Test-Path $ProjectDir) {
                Remove-Item $ProjectDir -Recurse -Force
                Write-ServiceChanged "Removed project dir $ProjectDir (config + session metadata)"
            }
            if (Test-Path $InstallDir) {
                Remove-Item $InstallDir -Recurse -Force
                Write-ServiceChanged "Removed runtime dir $InstallDir"
            }
        } else {
            $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
            if (Test-Path $manifestPath) {
                Remove-Item $manifestPath -Force
            }
            Write-ServiceSkipped "Config and session metadata preserved at $ProjectDir"
            Write-Host "    Use -RemoveConfig to delete everything"
        }

        Write-ServiceOk "Uninstall complete"
    }

    'start' {
        Write-ServiceHeader "Starting $ServiceName"
        Write-ServiceSkipped "Not a daemon - invoke with: $ProjectName"
    }

    'stop' {
        Write-ServiceHeader "Stopping $ServiceName"
        Write-ServiceSkipped "Not a daemon - Ctrl+C or close the terminal to end a session"
    }

    'status' {
        Write-ServiceHeader "$ServiceName Status"

        # Venv
        if (Test-Path $VenvPython) {
            Write-ServiceOk "Venv Python: $VenvPython"
        } else {
            Write-ServiceErr "Venv Python missing: $VenvPython"
        }

        # Package (installed in the venv)
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import agent_worktrees' 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-ServiceOk "Package importable in venv"
        } else {
            Write-ServiceErr "Package not importable in venv"
        }
        $ErrorActionPreference = $prevEAP

        # Wrapper
        foreach ($wrapper in @('launch-session.cmd', 'launch-session.ps1')) {
            $wrapperPath = Join-Path $BinDir $wrapper
            if (Test-Path $wrapperPath) {
                Write-ServiceOk "$wrapper deployed"
            } else {
                Write-ServiceErr "$wrapper missing"
            }
        }

        # Binstub (.ps1 primary, .cmd fallback)
        $binstubPs1 = Join-Path $LocalBin "$ProjectName.ps1"
        $binstubCmd = Join-Path $LocalBin "$ProjectName.cmd"
        if (Test-Path $binstubPs1) {
            $sfx = if (Test-Path $binstubCmd) { ' (+ .cmd fallback)' } else { '' }
            Write-ServiceOk "Binstub installed at $binstubPs1$sfx"
        } elseif (Test-Path $binstubCmd) {
            Write-ServiceWarn "Only .cmd fallback at $binstubCmd (no .ps1 -- args may mangle in PowerShell)"
        } else {
            Write-ServiceErr "Binstub missing at $binstubPs1"
        }

        # Config (project dir)
        $configPath = Join-Path $ProjectDir 'config.yaml'
        if (Test-Path $configPath) {
            Write-ServiceOk "Config at $configPath"
        } else {
            Write-ServiceErr "Config missing at $configPath"
        }

        Assert-PathIncludes $LocalBin

        # Git hooks
        if ($RepoDir) {
            $hooksPath = git --no-pager -C $RepoDir config --local core.hooksPath 2>$null
        if ($hooksPath -eq 'tools/hooks') {
            Write-ServiceOk "Git hooksPath = tools/hooks"
        } elseif ($hooksPath) {
            Write-ServiceWarn "Git hooksPath = $hooksPath (expected tools/hooks)"
        } else {
            Write-ServiceErr "Git core.hooksPath not set - run 'update' to configure"
        }
        } else {
            Write-ServiceSkipped "Git hooks check skipped (no repo detected)"
        }

        # Windows Terminal fragment
        $fragmentPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees\agent-worktrees.json'
        if (Test-Path $fragmentPath) {
            Write-ServiceOk "Windows Terminal fragment installed"
        } else {
            Write-ServiceErr "Windows Terminal fragment missing"
        }

        # Check for stale settings.json entries
        $wtSettingsPath = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
        if (Test-Path $wtSettingsPath) {
            try {
                $wtJson = Get-Content $wtSettingsPath -Raw | ConvertFrom-Json
                $fragPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees\agent-worktrees.json'
                $fragGuids = @()
                if (Test-Path $fragPath) {
                    $frag = Get-Content $fragPath -Raw | ConvertFrom-Json
                    $fragGuids = @($frag.profiles | ForEach-Object { $_.guid.ToLower() })
                }
                $stale = @($wtJson.profiles.list | Where-Object {
                    $_.PSObject.Properties['source'] -and $_.source -eq 'AgentWorktrees' -and
                    $_.PSObject.Properties['guid'] -and $_.guid.ToLower() -notin $fragGuids
                })
                if ($stale.Count -gt 0) {
                    Write-ServiceWarn "WT settings.json has $($stale.Count) stale profile(s) - run 'update' to clean"
                } else {
                    Write-ServiceOk "WT settings.json clean"
                }
            } catch { }
        }

        # Terminal scripts (per-session psmux options + opt-in keybinds)
        $sessOpts = Join-Path $BinDir 'session-options.ps1'
        if (Test-Path $sessOpts) {
            Write-ServiceOk "terminal scripts at $BinDir (session-options.ps1)"
        } else {
            Write-ServiceWarn "terminal scripts missing - run 'update' to deploy"
        }

        # Active worktree sessions
        if (Test-Path $WorktreesDir) {
            $sessions = @(Get-ChildItem $WorktreesDir -Filter '*.yaml' -ErrorAction SilentlyContinue)
            $active = @($sessions | ForEach-Object {
                $content = Get-Content $_.FullName -Raw
                if ($content -match 'status:\s*active') { $_ }
            })
            Write-ServiceOk "$($active.Count) active worktree(s), $($sessions.Count) total"
        }

        # Deploy provenance
        Show-DeployStatus -InstallDir $InstallDir
    }

    'update-config' {
        Write-ServiceHeader "Updating $ServiceName Config"

        $configPath = Join-Path $ProjectDir 'config.yaml'
        if (-not (Test-Path $configPath)) {
            Write-ServiceErr "Config not found - run 'install' first"
            exit 1
        }

        if ($Force) {
            $machine = Resolve-Machine
            Deploy-Config -Machine $machine
        } else {
            Write-ServiceSkipped "Config is machine-generated - use -Force to regenerate"
            Write-Host "    Current: $configPath"
        }
    }

    'refresh-profiles' {
        # Narrow, fast mirror path (dotfiles#563): regenerate ONLY the Windows
        # Terminal fragment from the current terminal-profile selection. The
        # profile mirror used to invoke the full 'update' (venv redeploy, pip
        # install, binstub reconcile, psmux, instruction deploy -- ~60s+) under
        # the caller's 30s subprocess timeout, so it was killed before the
        # fragment (Deploy-Shortcuts) ever ran and the failure was swallowed.
        # This action runs only the fragment regen; a non-zero exit lets the
        # caller report mirror success honestly.
        if (-not (Test-Path $BinDir)) {
            Write-ServiceErr "Not installed - run 'install' first"
            exit 1
        }
        $refreshMachine = Resolve-Machine
        if ($HasProject) {
            $configPath = Join-Path $ProjectDir 'config.yaml'
            if (Test-Path $configPath) {
                try {
                    $cfgRaw = & $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $configPath 2>$null
                    $cfgObj = $cfgRaw | ConvertFrom-Json
                    if ($cfgObj.machine) { $refreshMachine = $cfgObj.machine }
                } catch { }
            }
        }
        Deploy-Shortcuts -Machine $refreshMachine
    }

    'update' {
        Write-ServiceHeader "Updating $ServiceName"

        if ($ContextualInstall) {
            foreach ($dir in @($InstallDir, $BinDir)) {
                Ensure-InstallDir $dir
            }
            if (-not (Deploy-Venv)) { exit 1 }
            if (-not (Deploy-Package)) { exit 1 }
            if (-not (Deploy-Wrappers)) { exit 1 }
            if (-not (Invoke-VersionedActivate)) { exit 1 }
            Write-V3Manifest
            Write-ServiceOk "Context runtime updated at $InstallDir"
            exit 0
        }

        if (-not (Test-Path $BinDir)) {
            Write-ServiceErr "Not installed - run 'install' first"
            exit 1
        }

        # -- Shared runtime (venv first: package install targets the venv) --
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
        Deploy-CopilotPlugin
        Deploy-GlobalBinstub
        Ensure-CopilotExperimental
        Remove-LegacyBinstubs
        Reconcile-Binstubs
        # Machine-wide terminal integration (see install path): deploy the
        # per-session options + opt-in keybind scripts regardless of project
        # context.
        Ensure-Psmux
        Deploy-TerminalScripts

        # -- Project-specific (only when a project is known) --
        if ($HasProject) {
            Register-ProjectEntry
            Reconcile-Binstubs
            $updateMachine = Resolve-Machine
            $configPath = Join-Path $ProjectDir 'config.yaml'
            if (Test-Path $configPath) {
                try {
                    $cfgRaw = & $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $configPath 2>$null
                    $cfgObj = $cfgRaw | ConvertFrom-Json
                    if ($cfgObj.machine) { $updateMachine = $cfgObj.machine }
                } catch { }
            }
            Deploy-Shortcuts -Machine $updateMachine
            if ($RepoDir) { Deploy-GitHooksPath }

            # Retire migrated managed instruction files (machine identity now via the session-machine hook)
            if ($RepoDir) {
                try {
                    $env:PYTHONUTF8 = '1'
                    & $VenvPython -m agent_worktrees --project $ProjectName deploy-instructions --machine $updateMachine 2>&1 | ForEach-Object { Write-Host "  $_" }
                } catch {
                    Write-ServiceWarn "Instruction file deployment skipped: $_"
                }
            }
        }

        # Machine-local config schema migration (idempotent + atomic; never
        # touches repo-committed config -- that is an adopt concern). Stamps or
        # upgrades ~/.agent-worktrees/{config,repos,projects}.yaml. Non-fatal.
        try {
            $env:PYTHONUTF8 = '1'
            & $VenvPython -m agent_worktrees config-migrate 2>&1 | ForEach-Object { Write-Host "  $_" }
        } catch {
            Write-ServiceWarn "Config migration skipped: $_"
        }

        Write-V3Manifest

        Write-ServiceOk "Update complete"
    }
}
