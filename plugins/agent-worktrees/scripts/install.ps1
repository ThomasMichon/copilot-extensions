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
    Project name (e.g. 'my-project'). Defaults to: WORKTREE_PROJECT env var,
    then inferred from existing config, then basename of CWD repo.

.PARAMETER RemoveConfig
    On uninstall: also delete project config and worktree session metadata.

.PARAMETER Force
    Overwrite config without drift confirmation.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'status', 'update-config', 'update', 'refresh-profiles')]
    [string]$Action = 'status',

    [string]$ProjectName,

    [switch]$RemoveConfig,
    [switch]$Force
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
$InstallDir      = Join-Path $env:USERPROFILE '.agent-worktrees'
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

# Infer project name: explicit parameter > env var > existing config > basename of CWD repo
if (-not $ProjectName) { $ProjectName = $env:WORKTREE_PROJECT }
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
# versions/<v> slot (build + health-gate). Legacy mode: Link == Venv. Gated behind
# AGENT_WORKTREES_VERSIONED=0 (default ON); COPILOT_EXT_NO_VERSIONED=1
# force-disables. scripts/versioned_runtime.py owns the swap + migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
if (($env:COPILOT_EXT_NO_VERSIONED -ne '1') -and
    ($env:AGENT_WORKTREES_VERSIONED -notin @('0', 'false', 'no', 'off'))) {
    $pyprojForVer = if ($PluginDir) { Join-Path $PluginDir 'pyproject.toml' } else { $null }
    if ($pyprojForVer -and (Test-Path $pyprojForVer)) {
        $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    if ($SrcVersion) {
        $VersionedRuntime = $true
        $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
        $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
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
    Invoke-VersionedMarkComplete
    $prev = (& $py $vr --root $InstallDir --link-name '.venv' current 2>$null); $prev = ("$prev").Trim()
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --replace-nonlink 2>&1 |
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
    $prevPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $null
        & $VenvPython @awArgs
    } finally {
        $env:PYTHONPATH = $prevPythonPath
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

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
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
    $pluginPath = $PluginDir.ToString()
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
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-ServiceOk "Deploy manifest written (source: $kind)"
}

function Invoke-VenvPackageInstall {
    <# Install a local package dir into the venv, forcing the local code to
       refresh even when its (dev) version string is unchanged.

       Prefers `<venv python> -m pip` whenever the venv has pip -- which it does
       when the venv was built from a signed system Python via `python -m venv`
       (the SAC path in Deploy-Venv). This avoids `uv` entirely on Smart App
       Control / profile-mount Cloud PCs, where launching the
       WinGet `uv.exe` reparse shim fails ("untrusted mount point"). Falls back
       to `uv` only on venvs that lack pip (uv-created). Every command runs from
       a trusted CWD (SystemDrive root), never the profile mount, so even the uv
       fallback is safe there.

       Returns [pscustomobject]@{ ExitCode; Output }. #>
    param(
        [Parameter(Mandatory)][string]$VenvPython,
        [Parameter(Mandatory)][string]$PkgName,
        [Parameter(Mandatory)][string]$PkgDir
    )

    $hasPip = $false
    try { & $VenvPython -m pip --version *> $null; $hasPip = ($LASTEXITCODE -eq 0) } catch { $hasPip = $false }

    $prevLoc = Get-Location
    Set-Location "$env:SystemDrive\"
    try {
        if ($hasPip) {
            # 1) Resolve + install dependencies (idempotent once present).
            $out = & $VenvPython -m pip install "$PkgDir" --quiet 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) {
                # 2) Force just the local package's code to refresh (deps are
                #    already satisfied) so unchanged dev versions still update.
                $out += & $VenvPython -m pip install --force-reinstall --no-deps "$PkgDir" --quiet 2>&1 | Out-String
            }
        } else {
            $out = & uv pip install --python $VenvPython --reinstall-package $PkgName "$PkgDir" --quiet 2>&1 | Out-String
        }
        $rc = $LASTEXITCODE
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
            $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p }
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
        $ver = (& $c -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null | Out-String).Trim()
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
            & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0 -and (Test-Path $VenvPython)) {
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
                $result = & uv @args_ 2>&1
                if ($LASTEXITCODE -ne 0) {
                    # Fallback: try without version constraint
                    $args_ = @('venv', $VenvDir, '--allow-existing')
                    $result = & uv @args_ 2>&1
                }
            } finally {
                Set-Location $prevLoc
            }
            if ($LASTEXITCODE -ne 0) {
                Write-ServiceErr "Failed to create venv: $result"
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
        $basePrefix = & $VenvPython -c "import sys; print(sys.base_prefix)" 2>$null
        if ($basePrefix) {
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

    # Deploy hook scripts: sessionStart (bootstrap-check + project-hooks + register/deregister-session + anchor-hygiene-check + provision-check) + preToolUse (statelessness_guard.py)
    foreach ($script in @('bootstrap-check.ps1', 'bootstrap-check.sh', 'project-hooks.ps1', 'project-hooks.sh', 'register-session.ps1', 'register-session.sh', 'deregister-session.ps1', 'deregister-session.sh', 'anchor-hygiene-check.ps1', 'anchor-hygiene-check.sh', 'provision-check.ps1', 'provision-check.sh', 'statelessness_guard.py')) {
        $src = Join-Path $ScriptDir $script
        $dst = Join-Path $BinDir $script
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
            Write-ServiceOk "Hook: $script"
        }
    }

    # Deploy default setup scripts to ~/.agent-worktrees/scripts/ (used when a
    # repo lacks its own tools/setup/setup.ps1). The agent-bridge launch plan
    # invokes ~/.agent-worktrees/scripts/default-setup.ps1, so these MUST be
    # deployed here to keep the install flow and that launch instruction in
    # sync -- otherwise spawning a worktree agent fails at LAUNCH_ACP because
    # the setup script is missing. Mirrors installer.py deploy_wrappers().
    $ScriptsDir = Join-Path $InstallDir 'scripts'
    Ensure-InstallDir $ScriptsDir
    foreach ($setup in @('default-setup.ps1', 'default-setup.sh')) {
        $src = Join-Path $ScriptDir $setup
        $dst = Join-Path $ScriptsDir $setup
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
            Write-ServiceOk "Default setup: $setup"
        }
    }

    return $true
}

function Deploy-Binstub {
    <# Generate the project-specific binstub in ~/.local/bin/.
       Routes through the Python CLI for subcommand dispatch.
       Falls back to launch-session.cmd if the venv is missing. #>
    Ensure-InstallDir $LocalBin

    $content = @"
@echo off
set "PYTHONUTF8=1"
rem Context resolves from CWD / --project (git-like); the binstub names its
rem project via --project, not an ambient env var.
rem Resolve the .venv reparse target and launch the slot python directly, never
rem traversing the junction (blocked under RedirectionGuard) -- dotfiles #637.
set "_PY=%USERPROFILE%\.agent-worktrees\.venv\Scripts\python.exe"
for /f "tokens=2 delims=[]" %%i in ('dir /a:l "%USERPROFILE%\.agent-worktrees" 2^>nul ^| findstr /i /c:".venv"') do set "_PY=%%i\Scripts\python.exe"
if not exist "%_PY%" goto :_aw_fallback
"%_PY%" -m agent_worktrees --project $ProjectName %*
exit /b %ERRORLEVEL%
:_aw_fallback
rem Recovery (venv missing): launch-session reads WORKTREE_PROJECT
set "WORKTREE_PROJECT=$ProjectName"
"%USERPROFILE%\.agent-worktrees\bin\launch-session.cmd" %*
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
$env:PYTHONUTF8 = '1'
# Context resolves from CWD / --project (git-like). This .ps1 runs in-process in
# the caller's session, so it names its project via --project (not an ambient
# env var), leaving the live session env untouched. Recovery (venv missing)
# passes the project to launch-session via a scoped, restored WORKTREE_PROJECT.
$_venv = "$env:USERPROFILE\.agent-worktrees\.venv"
# Resolve the .venv reparse target and launch the slot python directly, never
# traversing the junction (blocked under RedirectionGuard) -- dotfiles #637.
$_py = "$_venv\Scripts\python.exe"
try { $_t = (Get-Item -LiteralPath $_venv -Force -ErrorAction Stop).Target; if ($_t) { $_py = Join-Path (@($_t)[0]) 'Scripts\python.exe' } } catch {}
if (Test-Path $_py) {
    & $_py -m agent_worktrees --project '%%PROJECT%%' @args
    exit $LASTEXITCODE
}
$_savedProj = $env:WORKTREE_PROJECT
$env:WORKTREE_PROJECT = '%%PROJECT%%'
try {
    & "$env:USERPROFILE\.agent-worktrees\bin\launch-session.cmd" @args
    $_rc = $LASTEXITCODE
} finally {
    if ($null -eq $_savedProj) { Remove-Item Env:WORKTREE_PROJECT -ErrorAction SilentlyContinue } else { $env:WORKTREE_PROJECT = $_savedProj }
}
exit $_rc
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

       Fix: put the REAL psmux binary's directory -- under WinGet\Packages, a
       stable per-package path that survives version bumps -- on the User PATH
       AHEAD of WinGet\Links, so `psmux` resolves to a plain exe in every session
       kind (interactive, redirected, and NoProfile dispatch). Idempotent. #>
    $realExe = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter psmux.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $realExe) { return }
    $realDir  = $realExe.DirectoryName
    $linksDir = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    $realN    = $realDir.TrimEnd('\').ToLowerInvariant()
    $linksN   = $linksDir.TrimEnd('\').ToLowerInvariant()

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }
    $parts = @($userPath -split ';' | Where-Object { $_ })

    $realIdx = -1; $linksIdx = -1
    for ($i = 0; $i -lt $parts.Count; $i++) {
        $pn = $parts[$i].TrimEnd('\').ToLowerInvariant()
        if ($realIdx  -lt 0 -and $pn -eq $realN)  { $realIdx  = $i }
        if ($linksIdx -lt 0 -and $pn -eq $linksN) { $linksIdx = $i }
    }

    # No-op when the real dir is already present AND ahead of the Links shim.
    if (-not ($realIdx -ge 0 -and ($linksIdx -lt 0 -or $realIdx -lt $linksIdx))) {
        $rebuilt = @($realDir) + @($parts | Where-Object { $_.TrimEnd('\').ToLowerInvariant() -ne $realN })
        [Environment]::SetEnvironmentVariable('Path', ($rebuilt -join ';'), 'User')
        Write-ServiceChanged "psmux: put real binary dir ahead of WinGet\Links on User PATH (SSH-safe): $realDir"
    }
    # Reflect in the current process too so a same-session check resolves it.
    if ($env:Path -notlike "*$realDir*") { $env:Path = "$realDir;$env:Path" }
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

function Ensure-PsmuxPin {
    <# Ensure a winget 'Gating' pin to psmux 3.3.5 exists so winget cannot
       auto-upgrade into the 3.3.6 `attach-session -t` regression (psmux#408).
       Idempotent: a no-op when the 3.3.5 pin is already present. Safe at any
       installed version -- a gating pin blocks upgrades outside 3.3.5 even when
       a below-target version (e.g. 3.3.3) is installed, which is why a
       still-on-3.3.3 box can self-pin instead of waiting for a hand
       `winget pin add`. #>
    $pins = ''
    try { $pins = & winget pin list 2>&1 | Out-String } catch {}
    $hasPin = @($pins -split "`n" | Where-Object { $_ -match 'marlocarlo\.psmux' -and $_ -match '3\.3\.5' }).Count -gt 0
    if ($hasPin) { return }
    & winget pin add --id marlocarlo.psmux --version 3.3.5 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-ServiceChanged "psmux: added winget gating pin to 3.3.5 (blocks auto-upgrade into the 3.3.6 regression)"
    }
}

function Ensure-Psmux {
    <# Install psmux 3.3.5 (pinned) when absent; if 3.3.6 is present -- the
       version with the `attach-session -t` regression (psmux#408, which makes
       worktree launches land in the wrong session) -- auto-downgrade+pin back
       to 3.3.5. The downgrade is skipped when live sessions exist, because a
       winget uninstall/reinstall tears down the running psmux server and every
       attached session; in that case we warn and defer to the next clean run.
       Called from both 'install' and 'update' so existing 3.3.6 boxes
       self-heal. launch-session.ps1 carries a last_session workaround as
       defense-in-depth while still on 3.3.6. #>
    if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) {
        Write-Host "  Installing psmux 3.3.5 (terminal multiplexer)..."
        & winget install --id marlocarlo.psmux --version 3.3.5 --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            # Block winget from auto-upgrading back into the 3.3.6 regression.
            & winget pin add --id marlocarlo.psmux --version 3.3.5 2>&1 | Out-Null
            Write-ServiceOk "psmux 3.3.5 installed (pinned)"
        } else {
            Write-ServiceWarn "psmux install failed - sessions will launch without multiplexing"
        }
        Ensure-PsmuxSshSafe
        return
    }
    $muxBin = Resolve-AwPsmuxBin (Get-Command psmux -ErrorAction SilentlyContinue)
    $psmuxVer = (& $muxBin --help 2>&1 | Select-Object -First 1) -replace '.*psmux v([0-9.]+).*', '$1'
    if ($psmuxVer -eq '3.3.6') {
        $liveSessions = @()
        try { $liveSessions = @(& $muxBin ls 2>$null | Where-Object { $_ -match '\S' }) } catch {}
        if ($liveSessions.Count -gt 0) {
            # Can't downgrade with live sessions, but still ensure the pin so the
            # box can't drift further before the operator does a clean downgrade.
            Ensure-PsmuxPin
            Write-ServiceWarn "psmux 3.3.6 has the attach -t regression (psmux#408); the launcher works around it. $($liveSessions.Count) live session(s) present -- not downgrading now (it would kill them). Close all worktree sessions and re-run 'update' to auto-pin 3.3.5."
        } else {
            Write-ServiceChanged "psmux 3.3.6 has the attach -t regression (psmux#408) -- downgrading to pinned 3.3.5"
            & winget install --id marlocarlo.psmux --version 3.3.5 --uninstall-previous --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            # Block winget from auto-upgrading back into the 3.3.6 regression.
            & winget pin add --id marlocarlo.psmux --version 3.3.5 2>&1 | Out-Null
            $newVer = (& $muxBin --help 2>&1 | Select-Object -First 1) -replace '.*psmux v([0-9.]+).*', '$1'
            if ($newVer -eq '3.3.5') {
                Write-ServiceOk "psmux pinned to 3.3.5 (regression-free)"
            } else {
                Write-ServiceWarn "psmux downgrade attempted but version reads '$newVer' -- verify manually (winget install --id marlocarlo.psmux --version 3.3.5 --uninstall-previous; winget pin add --id marlocarlo.psmux --version 3.3.5)"
            }
        }
    } else {
        # Present at a non-3.3.6 version (e.g. 3.3.3 below target, or an
        # unpinned 3.3.5). Ensure the 3.3.5 gating pin so the box self-pins and
        # winget can't drift it up into the 3.3.6 regression.
        Ensure-PsmuxPin
        Write-ServiceOk "psmux available ($psmuxVer)"
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

function Build-TerminalFragment {
    <# Generate a Windows Terminal fragment JSON with local + remote SSH profiles
       for ALL registered projects in projects.yaml. #>
    param([string]$Machine)

    $profiles = @()

    # Helper: generate stable GUID from a seed string
    function New-StableGuid {
        param([string]$Seed)
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Seed)
        $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
        return [guid]::new(
            [BitConverter]::ToInt32($hash, 0),
            [BitConverter]::ToInt16($hash, 4),
            [BitConverter]::ToInt16($hash, 6),
            $hash[8], $hash[9], $hash[10], $hash[11],
            $hash[12], $hash[13], $hash[14], $hash[15]
        )
    }

    # Helper: title-case a slug ("my-project" -> "My Project")
    function Get-DisplayName {
        param([string]$Slug)
        return ($Slug -replace '-', ' ') -replace '(^| )(.)', { $_.Value.ToUpper() }
    }

    # Collect projects: start with current project, then add from registry
    $projectList = @()
    $registry = Read-ProjectsRegistry

    # Resolve every registered repo's anchor from repos.yaml -- the single
    # owning store of paths. projects.yaml is lean and name-keyed to these
    # entries (derive-don't-duplicate), so per-project anchors are derived here
    # rather than read from projects.yaml. Loaded once; falls back per-project
    # to any residual projects.yaml 'anchor' for a not-yet-migrated file.
    function Get-RepoAnchorMap {
        $map = @{}
        if (-not (Test-Path $VenvPython)) { return $map }
        try {
            $json = & $VenvPython -c "import json; from agent_worktrees import repos; reg = repos.read_registry(); print(json.dumps({n: (e.local_path('windows') or '') for n, e in reg.repos.items()}))" 2>$null
            if ($json) {
                $obj = $json | ConvertFrom-Json
                foreach ($p in $obj.PSObject.Properties) { $map[$p.Name] = $p.Value }
            }
        } catch { }
        return $map
    }
    $RepoAnchorMap = Get-RepoAnchorMap

    # Name -> agent-exposure map, resolved from repos.yaml through the SAME
    # source of truth the Python side uses (``repos.RepoEntry.agent``: ON for
    # worktree/singleton, OFF for reference, explicit ``agent:`` wins). Used to
    # lock the local self.agent launcher profile for agent-exposed projects (see
    # the diagonal lock in the per-project loop below). Projects absent from the
    # registry default to exposed -- an adopted project normally launches itself.
    function Get-RepoAgentMap {
        $map = @{}
        if (-not (Test-Path $VenvPython)) { return $map }
        try {
            $json = & $VenvPython -c "import json; from agent_worktrees import repos; reg = repos.read_registry(); print(json.dumps({n: bool(e.agent) for n, e in reg.repos.items()}))" 2>$null
            if ($json) {
                $obj = $json | ConvertFrom-Json
                foreach ($p in $obj.PSObject.Properties) { $map[$p.Name] = [bool]$p.Value }
            }
        } catch { }
        return $map
    }
    $RepoAgentMap = Get-RepoAgentMap

    # Anchor for a project name: repos.yaml wins; fall back to a residual
    # projects.yaml 'anchor' (pre-migration file) or an explicit override.
    function Resolve-ProjectAnchor {
        param([string]$Name, [object]$Entry, [string]$Override)
        if ($Override) { return $Override }
        $fromRegistry = $RepoAnchorMap[$Name]
        if ($fromRegistry -and (Test-Path $fromRegistry)) { return $fromRegistry }
        if ($Entry -and $Entry.PSObject.Properties['anchor'] -and $Entry.anchor -is [string] -and $Entry.anchor) {
            return [string]$Entry.anchor
        }
        if ($fromRegistry) { return $fromRegistry }
        return $null
    }

    # machines.yaml for a resolved anchor (derived), else a residual override.
    function Resolve-ProjectMachinesYaml {
        param([string]$Anchor, [object]$Entry)
        if ($Anchor -and (Test-Path (Join-Path $Anchor 'machines.yaml'))) {
            return [string](Join-Path $Anchor 'machines.yaml')
        }
        if ($Entry -and $Entry.PSObject.Properties['machines_yaml'] -and $Entry.machines_yaml -is [string] -and $Entry.machines_yaml) {
            return [string]$Entry.machines_yaml
        }
        return $null
    }

    # Display casing: an explicit projects.yaml 'display_name' wins; else the
    # title-cased slug.
    function Resolve-ProjectDisplay {
        param([string]$Name, [object]$Entry)
        if ($Entry -and $Entry.PSObject.Properties['display_name'] -and $Entry.display_name) {
            return [string]$Entry.display_name
        }
        return (Get-DisplayName $Name)
    }

    # Helper: load a project's terminal-profile SELECTION (own-column model).
    # Reads top-level ``terminal_profiles`` from ~/.<project>/config.yaml and
    # returns a hashtable keyed "machine|env|kind". Returns $null when the file
    # or key is absent (UNMANAGED) -- the caller then substitutes the computed
    # DEFAULT column (minimal per-agent + bare cross-machine; see
    # Get-DefaultSelection). The retired legacy behavior emitted every candidate
    # profile for an unmanaged project; that is no longer done.
    function Get-TerminalSelection {
        param([string]$ProjName)
        $cfg = Join-Path $env:USERPROFILE ".$ProjName\config.yaml"
        if (-not (Test-Path $cfg)) { return $null }
        try {
            # The one-liner prints '' when the key is ABSENT (v is None) and a
            # JSON array (e.g. '[]') when it is PRESENT -- so $raw distinguishes
            # "unmanaged" ($null -> default column) from "explicit empty selection".
            $raw = & $VenvPython -c "import yaml,json,sys; d=yaml.safe_load(open(sys.argv[1],encoding='utf-8')) or {}; v=d.get('terminal_profiles'); print(json.dumps(v) if v is not None else '')" $cfg 2>$null
            if ($null -eq $raw) { return $null }
            $trimmed = $raw.Trim()
            if ($trimmed -eq '') { return $null }        # key absent -> unmanaged (use default column)
            # Explicit empty list = "no terminal profiles for this project". Note
            # '[]' | ConvertFrom-Json yields $null (not @()), so this MUST be
            # special-cased before the parse -- otherwise it would be mistaken
            # for the absent/legacy case and wrongly emit every profile.
            if ($trimmed -eq '[]') { return @{} }
            $sel = $trimmed | ConvertFrom-Json
            # $raw was non-empty JSON, so treat a null/empty parse as an explicit
            # (empty) selection -> no profiles, NOT legacy.
            if ($null -eq $sel) { return @{} }
            $set = @{}
            foreach ($t in @($sel)) {
                if ($null -eq $t) { continue }
                $k = if ($t.kind) { $t.kind } else { 'agent' }
                $set["$($t.machine)|$($t.env)|$k"] = $true
            }
            return $set
        } catch { return $null }
    }

    # Helper: is a (machine, env, kind) target in this project's selection?
    # By the time this is called the caller has already substituted the DEFAULT
    # column for an unmanaged project, so $Selection is normally a concrete set.
    # A $null selection is treated defensively as "everything on" (only reachable
    # if a caller skips the default substitution).
    function Test-ProfileSelected {
        param($Selection, [string]$Machine, [string]$Env, [string]$Kind)
        if ($null -eq $Selection) { return $true }
        return [bool]$Selection.ContainsKey("$Machine|$Env|$Kind")
    }

    # machines.yaml ssh env name -> the selection's short env label.
    function Get-SelEnvLabel {
        param([string]$Name)
        switch ($Name) {
            'windows' { 'Win' }
            'wsl'     { 'WSL' }
            'linux'   { 'Linux' }
            default   { $Name }
        }
    }

    # Helper: compute the DEFAULT terminal-profile column for a project that has
    # no explicit ``terminal_profiles`` selection (unmanaged). The default is
    # **minimal per-agent + bare cross-machine**, matching the canonical rule in
    # ``agent_worktrees.profiles.is_default_on``:
    #   - self.agent diagonal  -> the local host's own launcher (native env)
    #   - a ``shell`` target    -> for every OTHER (remote) machine x env
    # It deliberately omits remote agent-launch combos and local non-self shells.
    # This replaces the retired "unmanaged emits every candidate profile" behavior.
    function Get-DefaultSelection {
        param($RosterData, [string]$Machine, [string]$LocalDisplay)
        $set = @{}
        # Minimal per-agent: this host's local launcher (Windows-native here --
        # install.ps1 only runs on Windows). No local WSL launcher by default.
        $set["$LocalDisplay|Win|agent"] = $true
        if ($RosterData -and $RosterData.machines) {
            foreach ($mProp in $RosterData.machines.PSObject.Properties) {
                $key = $mProp.Name
                $mEntry = $mProp.Value
                # Resilience: a null/empty-bodied machines.yaml entry ($mEntry is
                # $null) would throw under Set-StrictMode on the .PSObject access
                # below. Note it, flag, and move on so the rest of the roster
                # still produces profiles.
                if (-not ($mEntry -is [PSCustomObject])) {
                    Write-ServiceWarn "Skipped malformed machines.yaml roster entry '$key' (empty/invalid body)"
                    continue
                }
                # Robust self-skip (mirrors the SSH loop below): match the local
                # identity against key / display_name / hostname / ssh aliases.
                $entryIds = @($key)
                if ($mEntry.PSObject.Properties['display_name'] -and $mEntry.display_name) { $entryIds += $mEntry.display_name }
                if ($mEntry.PSObject.Properties['hostname'] -and $mEntry.hostname) { $entryIds += $mEntry.hostname }
                if ($mEntry.PSObject.Properties['ssh'] -and $mEntry.ssh -and $mEntry.ssh.PSObject.Properties['environments'] -and $mEntry.ssh.environments) {
                    foreach ($e in $mEntry.ssh.environments) {
                        if ($e.PSObject.Properties['alias'] -and $e.alias) { $entryIds += $e.alias }
                    }
                }
                $isSelf = $false
                foreach ($lid in @($Machine, $env:COMPUTERNAME.ToLower())) {
                    foreach ($eid in $entryIds) {
                        if ($lid -and $eid -and ("$lid".ToLower() -eq "$eid".ToLower())) { $isSelf = $true; break }
                    }
                    if ($isSelf) { break }
                }
                if ($isSelf) { continue }
                if (-not $mEntry.ssh -or -not $mEntry.ssh.ready) { continue }
                # Bare cross-machine: a plain-shell target per remote env.
                foreach ($sshEnv in $mEntry.ssh.environments) {
                    $selEnv = Get-SelEnvLabel $sshEnv.name
                    $set["$($mEntry.display_name)|$selEnv|shell"] = $true
                }
            }
        }
        return $set
    }

    # Helper: extract WSL info from a registry entry
    function Get-WslInfo {
        param([object]$Entry)
        $wsl = $null
        if ($Entry -is [PSCustomObject] -and $Entry.PSObject.Properties['wsl']) {
            $wsl = $Entry.wsl
        } elseif ($Entry -is [hashtable] -and $Entry.ContainsKey('wsl')) {
            $wsl = $Entry['wsl']
        }
        if (-not $wsl) { return $null }
        $state = $null; $distro = $null
        if ($wsl -is [PSCustomObject]) {
            if ($wsl.PSObject.Properties['state']) { $state = $wsl.state }
            if ($wsl.PSObject.Properties['distro']) { $distro = $wsl.distro }
        } elseif ($wsl -is [hashtable]) {
            $state = $wsl['state']; $distro = $wsl['distro']
        }
        return @{ state = $state; distro = $distro }
    }

    # Ensure current project is always included (even if not yet in registry)
    $currentRegEntry = $null
    if ($registry.projects -is [PSCustomObject] -and $registry.projects.PSObject.Properties[$ProjectName]) {
        $currentRegEntry = $registry.projects.$ProjectName
    } elseif ($registry.projects -is [hashtable] -and $registry.projects.ContainsKey($ProjectName)) {
        $currentRegEntry = $registry.projects[$ProjectName]
    }
    $currentAnchor = Resolve-ProjectAnchor $ProjectName $currentRegEntry $RepoDir
    $currentMachinesYaml = Resolve-ProjectMachinesYaml $currentAnchor $currentRegEntry
    $currentEntry = @{
        name          = $ProjectName
        anchor        = $currentAnchor
        machines_yaml = $currentMachinesYaml
        display       = Resolve-ProjectDisplay $ProjectName $currentRegEntry
        wsl_info      = if ($currentRegEntry) { Get-WslInfo $currentRegEntry } else { $null }
    }
    $projectList += [PSCustomObject]$currentEntry

    # Add other registered projects
    $registeredNames = @($ProjectName)
    if ($registry.projects) {
        $projObj = $registry.projects
        $propList = if ($projObj -is [PSCustomObject]) { $projObj.PSObject.Properties } else { @() }
        foreach ($prop in $propList) {
            if ($prop.Name -in $registeredNames) { continue }
            $registeredNames += $prop.Name
            $e = $prop.Value
            # Anchor + machines.yaml derive from repos.yaml by name (single
            # owning store); a residual projects.yaml value is only a
            # pre-migration fallback.
            $anchor = Resolve-ProjectAnchor $prop.Name $e $null
            $my = Resolve-ProjectMachinesYaml $anchor $e
            $projectList += [PSCustomObject]@{
                name          = $prop.Name
                anchor        = $anchor
                machines_yaml = $my
                display       = Resolve-ProjectDisplay $prop.Name $e
                wsl_info      = Get-WslInfo $e
            }
        }
    }

    # Generate profiles for each project
    # Track plain SSH GUIDs already emitted to avoid duplicates when multiple
    # projects reference the same machines.yaml.
    $emittedSshGuids = @{}

    foreach ($proj in $projectList) {
        $pName = $proj.name
        $pDisplay = $proj.display
        $pAnchor = $proj.anchor
        $pMachinesYaml = $proj.machines_yaml
        $pWslInfo = $proj.wsl_info

        # This project's terminal-profile selection (own-column). $null = the
        # project is UNMANAGED (no selection persisted yet); the default column
        # is substituted below, once the roster + local display are known.
        $pSel = Get-TerminalSelection $pName

        # Parse this project's machines.yaml once (local display name + SSH loop).
        $rosterData = $null
        if ($pMachinesYaml -and (Test-Path $pMachinesYaml)) {
            try {
                $rosterData = (& $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $pMachinesYaml 2>$null) | ConvertFrom-Json
            } catch {
                Write-ServiceWarn "Could not parse machines.yaml for '$pName' terminal profiles: $_"
            }
        }
        # Local machine's display name (selection vocabulary). Falls back to the
        # machine key when the roster lacks an entry.
        $localDisplay = $Machine
        if ($rosterData -and $rosterData.machines -and $rosterData.machines.PSObject.Properties[$Machine]) {
            $dn = $rosterData.machines.$Machine.display_name
            if ($dn) { $localDisplay = $dn }
        }

        # Unmanaged project ($pSel is $null): substitute the DEFAULT column
        # (minimal per-agent + bare cross-machine) instead of the retired
        # emit-everything behavior. A managed selection (including an explicit
        # empty '[]') is honored as-is.
        if ($null -eq $pSel) {
            $pSel = Get-DefaultSelection $rosterData $Machine $localDisplay
        }

        # Lock the self.agent diagonal for an agent-exposed project, mirroring
        # profiles.normalize_selection ("a host always launches itself"): the
        # local agent launcher is ALWAYS emitted -- even when the persisted
        # selection is an explicit empty '[]' that predates or contradicts the
        # project's agent exposure (a stale ``--no-agent`` seed left on a project
        # that is in fact agent-exposed). Without this, the generator honored the
        # empty list literally and dropped even the mandatory local launcher, so
        # the whole project vanished from the Terminal dropdown despite
        # ``expose_agent: true``; ``update`` could never put it back because the
        # empty selection was faithfully reproduced. A genuine ``--no-agent``
        # project (``agent: false``) keeps its empty selection and emits no
        # launcher. Idempotent: the default column already carries this diagonal.
        $pExposeAgent = if ($RepoAgentMap.ContainsKey($pName)) { [bool]$RepoAgentMap[$pName] } else { $true }
        if ($pExposeAgent) {
            $pSel["$localDisplay|Win|agent"] = $true
        }

        # Local Windows profile (self·agent on a Windows host). Always selected
        # when present (the diagonal is locked), but still gated for symmetry.
        if (Test-ProfileSelected $pSel $localDisplay 'Win' 'agent') {
            $guid = New-StableGuid "${pName}-local-windows"
            $profiles += @{
                guid              = "{$guid}"
                name              = $pDisplay
                commandline       = "cmd /c `"%USERPROFILE%\.local\bin\${pName}.cmd`""
                startingDirectory = "%USERPROFILE%"
                colorScheme       = 'Aperture Science'
                hidden            = $false
            }
        }

        # Local WSL profile -- only when WSL support is recorded in the registry
        $wslDistro = if ($pWslInfo) { $pWslInfo['distro'] } else { $null }
        $wslState = if ($pWslInfo) { $pWslInfo['state'] } else { $null }
        if ($wslState -and $wslDistro -and (Test-ProfileSelected $pSel $localDisplay 'WSL' 'agent')) {
            # Distro is always known (required for WSL profile generation)
            $wslCmd = "wsl.exe -d $wslDistro -- bash -lc $pName"
            $wslLabel = "$pDisplay (WSL)"

            $guid = New-StableGuid "${pName}-local-wsl"
            $profiles += @{
                guid              = "{$guid}"
                name              = $wslLabel
                commandline       = $wslCmd
                startingDirectory = "%USERPROFILE%"
                colorScheme       = 'Aperture Science'
                hidden            = $false
            }
        }

        # Local Windows *shell* profile -- a plain login shell on this host, as
        # opposed to the self·agent launch above. Gated by a 'shell' selection
        # and deduplicated across projects: the local shell is project-
        # independent, so multiple projects selecting it emit a single profile
        # (dotfiles#564 -- local-host 'shell' rows used to be silently dropped).
        if (Test-ProfileSelected $pSel $localDisplay 'Win' 'shell') {
            $localWinShellGuid = New-StableGuid "shell-local-${Machine}-windows"
            if (-not $emittedSshGuids.ContainsKey("{$localWinShellGuid}")) {
                $profiles += @{
                    guid              = "{$localWinShellGuid}"
                    name              = $localDisplay
                    commandline       = "pwsh.exe"
                    startingDirectory = "%USERPROFILE%"
                    colorScheme       = 'Aperture Science'
                    hidden            = $false
                }
                $emittedSshGuids["{$localWinShellGuid}"] = $true
            }
        }

        # Local WSL *shell* profile -- a plain WSL login shell. Unlike the WSL
        # *agent* profile it does not require a registry-recorded distro (bare
        # `wsl.exe` opens the default distro), so a WSL/shell selection is
        # honored even without wsl_info; the recorded distro is used when known.
        # Deduplicated across projects (dotfiles#564).
        if (Test-ProfileSelected $pSel $localDisplay 'WSL' 'shell') {
            $localWslShellGuid = New-StableGuid "shell-local-${Machine}-wsl"
            if (-not $emittedSshGuids.ContainsKey("{$localWslShellGuid}")) {
                $wslShellCmd = if ($wslDistro) { "wsl.exe -d $wslDistro" } else { "wsl.exe" }
                $profiles += @{
                    guid              = "{$localWslShellGuid}"
                    name              = "$localDisplay (WSL)"
                    commandline       = $wslShellCmd
                    startingDirectory = "%USERPROFILE%"
                    colorScheme       = 'Aperture Science'
                    hidden            = $false
                }
                $emittedSshGuids["{$localWslShellGuid}"] = $true
            }
        }

        # SSH profiles from this project's machines.yaml
        if ($rosterData) {
            try {
                $machinesData = $rosterData
                if ($machinesData.machines) {
                    foreach ($mProp in $machinesData.machines.PSObject.Properties) {
                        $key = $mProp.Name
                        $mEntry = $mProp.Value
                        # Resilience: skip a null/empty-bodied roster entry (it
                        # would throw under Set-StrictMode on the .PSObject access
                        # below) -- flag it and keep emitting the other machines'
                        # SSH profiles.
                        if (-not ($mEntry -is [PSCustomObject])) {
                            Write-ServiceWarn "Skipped malformed machines.yaml roster entry '$key' (empty/invalid body)"
                            continue
                        }
                        # Skip self robustly (dotfiles#572): a drifted/stale
                        # checkout may key the local machine under an old name,
                        # so match the local identity against the entry's key,
                        # display_name, hostname AND its SSH aliases -- otherwise
                        # the box emits SSH-to-itself profiles. Property access is
                        # guarded: under Set-StrictMode -Latest, reading an absent
                        # property (e.g. the optional `hostname`) throws.
                        $entryIds = @($key)
                        if ($mEntry.PSObject.Properties['display_name'] -and $mEntry.display_name) { $entryIds += $mEntry.display_name }
                        if ($mEntry.PSObject.Properties['hostname'] -and $mEntry.hostname) { $entryIds += $mEntry.hostname }
                        if ($mEntry.PSObject.Properties['ssh'] -and $mEntry.ssh -and $mEntry.ssh.PSObject.Properties['environments'] -and $mEntry.ssh.environments) {
                            foreach ($e in $mEntry.ssh.environments) {
                                if ($e.PSObject.Properties['alias'] -and $e.alias) { $entryIds += $e.alias }
                            }
                        }
                        $isSelf = $false
                        foreach ($lid in @($Machine, $env:COMPUTERNAME.ToLower())) {
                            foreach ($eid in $entryIds) {
                                if ($lid -and $eid -and ("$lid".ToLower() -eq "$eid".ToLower())) { $isSelf = $true; break }
                            }
                            if ($isSelf) { break }
                        }
                        if ($isSelf) { continue }  # skip self
                        if (-not $mEntry.ssh -or -not $mEntry.ssh.ready) { continue }

                        foreach ($sshEnv in $mEntry.ssh.environments) {
                            $alias = $sshEnv.alias
                            $envLabel = switch ($sshEnv.name) {
                                'windows' { 'Windows' }
                                'wsl'     { 'WSL' }
                                'linux'   { 'Linux' }
                                default   { $sshEnv.name }
                            }
                            # Selection vocabulary: display name + short env.
                            $remoteDisplay = $mEntry.display_name
                            $selEnv = Get-SelEnvLabel $sshEnv.name

                            # WSL/Linux SSH targets get the WSL icon (#479);
                            # remote Windows keeps the standard icon.

                            # Plain SSH (shell) profile -- gated by a 'shell'
                            # selection; deduplicated across projects since
                            # multiple projects may reference the same machines.yaml.
                            $sshGuid = New-StableGuid "ssh-${key}-$($sshEnv.name)"
                            if ((Test-ProfileSelected $pSel $remoteDisplay $selEnv 'shell') -and (-not $emittedSshGuids.ContainsKey("{$sshGuid}"))) {
                                $profileName = if ($envLabel -eq 'WSL') { "$($mEntry.display_name) (WSL)" } else { $mEntry.display_name }
                                $profiles += @{
                                    guid              = "{$sshGuid}"
                                    name              = $profileName
                                    commandline       = "ssh $alias"
                                    startingDirectory = "%USERPROFILE%"
                                    colorScheme       = 'Aperture Science'
                                    hidden            = $false
                                }
                                $emittedSshGuids["{$sshGuid}"] = $true
                            }

                            # Launch-via-SSH (agent) profile -- gated by an
                            # 'agent' selection for this remote target.
                            if (Test-ProfileSelected $pSel $remoteDisplay $selEnv 'agent') {
                                $binstubCmd = if ($sshEnv.shell -eq 'pwsh') { "${pName}.cmd" } else { $pName }
                                $launchCmdline = "ssh -t $alias $binstubCmd"
                                $launchLabel = if ($envLabel -eq 'WSL') { "$($mEntry.display_name) WSL" } else { $mEntry.display_name }
                                $launchProfileName = "$pDisplay ($launchLabel)"

                                $launchGuid = New-StableGuid "${pName}-launch-${key}-$($sshEnv.name)"
                                $profiles += @{
                                    guid              = "{$launchGuid}"
                                    name              = $launchProfileName
                                    commandline       = $launchCmdline
                                    startingDirectory = "%USERPROFILE%"
                                    colorScheme       = 'Aperture Science'
                                    hidden            = $false
                                }
                            }
                        }
                    }
                }
            } catch {
                Write-ServiceWarn "Could not parse machines.yaml for '$pName' terminal profiles: $_"
            }
        }
    }

    $colorScheme = @{
        name            = 'Aperture Science'
        background      = '#0C0C0C'
        foreground      = '#E8DFD0'
        cursorColor     = '#F6A821'
        selectionBackground = '#3A3A5C'
        black           = '#0C0C0C'
        red             = '#E24C3E'
        green           = '#6EA667'
        yellow          = '#F6A821'
        blue            = '#3B8EEA'
        purple          = '#9B6BC4'
        cyan            = '#4EC9B0'
        white           = '#D4D4D4'
        brightBlack     = '#3A3A3A'
        brightRed       = '#F44747'
        brightGreen     = '#B5CEA8'
        brightYellow    = '#FFD700'
        brightBlue      = '#6CB6FF'
        brightPurple    = '#D4BFFF'
        brightCyan      = '#7EECD8'
        brightWhite     = '#F0F0F0'
    }

    $fragment = @{
        profiles = $profiles
        schemes  = @($colorScheme)
    }

    return ($fragment | ConvertTo-Json -Depth 5)
}

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
# Thin binstub for $ProjectName - deployed by agent-worktrees (Windows)
# Requires agent-worktrees to be installed in WSL via the copilot-extensions plugin.
# This thin launcher only starts a session (no CLI dispatch), so it passes the
# project to launch-session via WORKTREE_PROJECT.
export WORKTREE_PROJECT="$ProjectName"
_launcher="`$HOME/.agent-worktrees/bin/launch-session.sh"
if [[ -x "`$_launcher" ]]; then
    exec "`$_launcher" "`$@"
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

    # Generate the fragment dynamically from projects.yaml + machines.yaml
    $fragment = Build-TerminalFragment -Machine $Machine
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

    if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
        Write-ServiceWarn "Copilot CLI not found - skipping plugin install"
        return
    }

    # Detect if we are running from the installed plugin directory.
    # When cmd_update invokes us, it sets cwd to the plugin dir and
    # has already done the plugin update -- re-running it would EBUSY
    # on Windows because copilot CLI tries to rmdir our own cwd.
    $installedPluginsDir = Join-Path $env:USERPROFILE '.copilot\installed-plugins'
    $runningFromInstalled = $PluginDir.Path -like "$installedPluginsDir*"

    # 1. Register marketplace if not present
    $marketplaces = (copilot plugin marketplace list 2>$null) -join "`n"
    if ($marketplaces -notmatch 'copilot-extensions') {
        $addOut = copilot plugin marketplace add ThomasMichon/copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Failed to register marketplace: $addOut"
            return
        }
        Write-ServiceChanged "Registered copilot-extensions marketplace"
    }

    # 2. Parse current plugin state
    $pluginList = copilot plugin list 2>$null
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
        $out = copilot plugin update agent-worktrees@copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Plugin update failed: $out"
        } else {
            Write-ServiceOk "Copilot plugin updated (marketplace)"
        }
    } else {
        $out = copilot plugin install agent-worktrees@copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Plugin install failed: $out"
            return
        }
        Write-ServiceChanged "Copilot plugin installed (agent-worktrees@copilot-extensions)"
    }

    # 4. Remove stale _direct install if marketplace is now present
    if ($hasDirect) {
        $verify = (copilot plugin list 2>$null) -join "`n"
        if ($verify -match 'agent-worktrees@copilot-extensions') {
            copilot plugin uninstall agent-worktrees 2>$null | Out-Null
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
        $settings = $raw | ConvertFrom-Json -AsHashtable
    } catch {
        Write-ServiceWarn "Could not parse $settingsFile -- skipping"
        return
    }

    if ($settings.ContainsKey('experimental') -and $settings['experimental'] -eq $true) {
        Write-ServiceOk "Copilot experimental mode enabled"
        return
    }

    $settings['experimental'] = $true
    $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsFile -Encoding utf8NoBOM
    Write-ServiceChanged "Copilot experimental mode enabled (required for extensions)"
}

function Deploy-GitHooksPath {
    <# Point core.hooksPath at tools/hooks ONLY for repos that actually ship a
       tools/hooks dir (the legacy scheme). Repos using the PR-workflow hook
       facility keep the DEFAULT .git/hooks (where install_hooks writes the
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
    foreach ($stub in @("$ProjectName.cmd", "$ProjectName.ps1", 'mark-session-complete.cmd', 'mark-session-complete.ps1', 'agent-worktrees.cmd', 'agent-worktrees.ps1')) {
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
    try {
        $env:PYTHONUTF8 = '1'
        & $VenvPython -m agent_worktrees reconcile-binstubs 2>&1 |
            ForEach-Object { Write-Host "  $_" }
    } catch {
        Write-ServiceWarn "Binstub reconciliation skipped: $_"
    }
}

# -- Actions --------------------------------------------------------------

switch ($Action) {
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
        try { uv --version 2>&1 | Out-Null } catch { $missingPrereqs += 'uv' }
        if ($missingPrereqs.Count -gt 0) {
            Write-ServiceErr "Missing prerequisites: $($missingPrereqs -join ', ')"
            exit 1
        }

        # Optional: psmux terminal multiplexer for session persistence. Pinned
        # to 3.3.5; 3.3.6 has the `attach-session -t` regression (psmux#408).
        # See Ensure-Psmux (shared with 'update' so existing boxes self-heal).
        Ensure-Psmux

        # Create directory structure (runtime dirs always; project dirs only if adopting)
        $runtimeDirs = @($InstallDir, $BinDir, $LocalBin)
        if ($HasProject) { $runtimeDirs += @($ProjectDir, $WorktreesDir) }
        foreach ($dir in $runtimeDirs) {
            Ensure-InstallDir $dir
        }

        # -- Shared runtime (venv first: package install targets the venv) --
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
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
            Deploy-Binstub
            Register-ProjectEntry
            Deploy-Shortcuts -Machine $machine
            if ($RepoDir) { Deploy-GitHooksPath }

            # Deploy machine.instructions.md + AGENTS.md from machines.yaml
            if ($RepoDir) {
                try {
                    $env:PYTHONUTF8 = '1'
                    $env:WORKTREE_PROJECT = $ProjectName
                    & $VenvPython -m agent_worktrees deploy-instructions --machine $machine 2>&1 | ForEach-Object { Write-Host "  $_" }
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
        # Also remove distro-specific WSL shortcuts (e.g. "Aperture Labs (WSL: Ubuntu).lnk")
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
        foreach ($wrapper in @('launch-session.cmd', 'launch-session.ps1')) {
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

        if (-not (Test-Path $BinDir)) {
            Write-ServiceErr "Not installed - run 'install' first"
            exit 1
        }

        # -- Shared runtime (venv first: package install targets the venv) --
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
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
            Deploy-Binstub
            Register-ProjectEntry
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

            # Deploy machine.instructions.md + AGENTS.md from machines.yaml
            if ($RepoDir) {
                try {
                    $env:PYTHONUTF8 = '1'
                    $env:WORKTREE_PROJECT = $ProjectName
                    & $VenvPython -m agent_worktrees deploy-instructions --machine $updateMachine 2>&1 | ForEach-Object { Write-Host "  $_" }
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
