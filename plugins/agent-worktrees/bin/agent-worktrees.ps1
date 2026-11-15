$_awUpdateCaller = $PSCommandPath
$_awUpdatePayload = $false
. (Join-Path $PSScriptRoot 'agent-worktrees-update-entry.ps1') @args
if ($_awUpdateHandled) { exit $LASTEXITCODE }
$env:PYTHONUTF8 = '1'
$_awPayloadRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (Test-Path -LiteralPath (Join-Path $_awPayloadRoot 'plugin.json') -PathType Leaf) {
    $env:AGENT_WORKTREES_PAYLOAD_ROOT = $_awPayloadRoot
}
# Sanitize inherited Python runtime variables before dispatching to the
# resolved, architecture-correct runtime-slot interpreter (#2411). A stray
# mismatched-architecture PYTHONHOME/PYTHONPATH -- set at Windows User scope,
# inherited from a parent shell, or leaked from an unrelated venv activation
# -- otherwise survives into every dispatch below and can crash the resolved
# interpreter (e.g. an x64 PYTHONHOME under an ARM64 python.exe fails
# `import socket` with "DLL load failed"). This is the same sanitization
# class #2359 applied to Worktree Manager's own engine-launch subprocesses,
# applied here at the binstub entry point so it protects every direct
# invocation, not only Manager-spawned children. Clearing these here is safe
# regardless of downstream provisioning path: this script never itself
# imports Python modules, and the resolved interpreter's own site/venv
# machinery re-establishes whatever of these it legitimately needs.
#
# AGENT_RT_ROOT is included here too (#3220): unlike every other plugin's
# binstub, which sets this variable itself immediately before dot-sourcing
# the shared resolver, `resolve-runtime.ps1`'s local `_awr` fallback here
# honors an *inherited* AGENT_RT_ROOT as an override. A value leaked from
# another plugin's debugging session (Windows User/Machine scope, or baked
# into a long-lived parent process' environment) then silently hijacks this
# resolver into a different plugin's venv, failing with an opaque
# "No module named agent_worktrees". Clearing it here restores this
# binstub's own default (`~/.agent-worktrees`) every time, matching the
# canonical per-invocation-scoping contract every other plugin follows.
foreach ($_pyEnvVar in @('PYTHONHOME', 'PYTHONPATH', 'PYTHONEXECUTABLE', 'VIRTUAL_ENV', 'UV_INTERNAL__PYTHONHOME', '__PYVENV_LAUNCHER__', 'AGENT_RT_ROOT')) {
    Remove-Item "Env:\$_pyEnvVar" -ErrorAction SilentlyContinue
}
# Resolve the runtime slot python SOLELY via the junction-free `current-version`
# marker and launch it directly. The `.venv` junction is retired (marker model,
# #581/#1085/#1106): nothing traverses/parses a reparse point (blocked under
# RedirectionGuard, WinError 448/3, and prone to drift). Fallback: the newest
# versions/ slot only. dotfiles #637 / #1085 / #1106.
#
# SELF-PROVISIONING (#1393): if no runtime slot exists (a `stamp` deferred the
# venv, or a confined host where the full launcher install never ran), provision
# on first use via the LEAN `install.ps1 provision` (uv + venv + package + marker)
# from the snapshot the stamp recorded, then dispatch. Opt out with
# AGENT_WORKTREES_NO_SELFPROVISION=1 (then falls through to a PATH python).
$_root = Join-Path $env:USERPROFILE '.agent-worktrees'
function Invoke-AwTerminalPython {
    param([string]$Python, [string[]]$PythonArguments, [string[]]$QueryArguments)
    $payload = $_awPayloadRoot
    if (-not (Test-Path -LiteralPath (Join-Path $payload 'plugin.json') -PathType Leaf)) {
        $pointer = Join-Path $_root 'payload-dir'
        if (-not (Test-Path -LiteralPath $pointer -PathType Leaf)) {
            & $Python @PythonArguments
            return
        }
        $payload = ([IO.File]::ReadAllText($pointer)).Trim()
    }
    $hostScript = Join-Path $payload 'bin\terminal-launch.ps1'
    if (Test-Path -LiteralPath $hostScript -PathType Leaf) {
        . $hostScript
        Invoke-WorktreeTerminalLaunch -Executable $Python -Arguments $PythonArguments `
            -QueryArguments $QueryArguments -Package agent_worktrees
    } else {
        & $Python @PythonArguments
    }
}
function _resolve_aw_py {
    $resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
    if (-not (Test-Path -LiteralPath $resolver -PathType Leaf)) { return $null }
    . $resolver
    return $AwPy
}
function Get-AwPythonCache([string]$Python) {
    $selector = @'
import json, sys
try:
    from agent_procutil.python_cache import POLICY_VERSION, policy_arguments
    assert POLICY_VERSION == 2
    arguments = policy_arguments(sys.argv[1], runtime=sys.executable)
except Exception:
    arguments = ['-B']
print(json.dumps(arguments, ensure_ascii=True))
'@
    try {
        $selected = @(& $Python -I -B -X utf8 -c $selector $_root)
        if ($LASTEXITCODE -eq 0 -and $selected.Count -eq 1) {
            $cache = ConvertFrom-Json -InputObject ([string]$selected[0]) -ErrorAction Stop
            if ($cache.Count -eq 2 -and $cache[0] -ceq '-X' -and
                $cache[1] -is [string] -and $cache[1].StartsWith('pycache_prefix=') -and
                $cache[1].Length -gt 15 -and $cache[1] -notmatch "[`r`n]" -and
                $cache[1].IndexOf([char]0) -lt 0) { return $cache }
        }
    } catch {}
    return '-B'
}
$_py = _resolve_aw_py
if ($_py) { $_cache = @(Get-AwPythonCache $_py); Invoke-AwTerminalPython $_py ($_cache + @('-m', 'agent_worktrees') + $args) @('-I', '-B', '-m', 'agent_worktrees', '--version'); exit $LASTEXITCODE }
if ($env:AGENT_WORKTREES_INTERNAL_REQUIRE_RUNTIME) { exit 127 }
if ($_awUpdateDiagnostic) {
    [Console]::Error.WriteLine('[agent-worktrees] selected update operation requires a provisioned runtime; use ordinary update to repair it.')
    exit 127
}
if ($env:AGENT_WORKTREES_NO_SELFPROVISION) { Invoke-AwTerminalPython 'python' (@('-m', 'agent_worktrees') + $args) @('-I', '-B', '-m', 'agent_worktrees', '--version'); exit $LASTEXITCODE }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) {
    Join-Path $_snap 'scripts\install.ps1'
} elseif (Test-Path -LiteralPath (Join-Path $_awPayloadRoot 'plugin.json') -PathType Leaf) {
    Join-Path $_awPayloadRoot 'scripts\install.ps1'
} else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) {
    $_inst = Get-ChildItem (Join-Path $env:USERPROFILE '.copilot\installed-plugins') -Recurse -Filter 'install.ps1' -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '[\\/]agent-worktrees[\\/]scripts[\\/]install\.ps1$' } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) {
    # A `copilot plugin install <repo>:<path>` direct (non-marketplace)
    # install has no nested `agent-worktrees\` segment: it lands at
    # `installed-plugins\_direct\<owner>--<repo>--<subpath-with-dashes>\`,
    # which the marketplace-shaped match above never matches. Anchored on
    # the directory name *ending* in `-agent-worktrees` (the subpath's own
    # final segment, always `agent-worktrees` for this plugin) rather than
    # matching the substring anywhere, so an unrelated direct-installed
    # plugin whose name merely contains "agent-worktrees" elsewhere can
    # never be selected.
    $_inst = Get-ChildItem (Join-Path $env:USERPROFILE '.copilot\installed-plugins\_direct') -Recurse -Filter 'install.ps1' -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '[\\/]_direct[\\/][^\\/]*-agent-worktrees[\\/]scripts[\\/]install\.ps1$' } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-worktrees] cannot self-provision: installer not found. Re-enable the plugin, then retry.'); exit 127 }
[Console]::Error.WriteLine('[agent-worktrees] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-worktrees eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
$mutex = [System.Threading.Mutex]::new($false, 'Local\Copilot.AgentWorktrees.Provision')
$retryDeadline = [DateTime]::UtcNow.AddSeconds(120)
$reportedBusy = $false
try {
    while ($true) {
        $held = $false
        $retry = $false
        try {
            try { $held = $mutex.WaitOne() } catch [System.Threading.AbandonedMutexException] { $held = $true }
            $_py = _resolve_aw_py
            if (-not $_py) {
                $_latestSnapshot = ''
                try { $_latestSnapshot = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
                if ($_latestSnapshot) {
                    $_latestInstaller = Join-Path $_latestSnapshot 'scripts\install.ps1'
                    if (-not (Test-Path -LiteralPath $_latestInstaller -PathType Leaf)) {
                        [Console]::Error.WriteLine('[agent-worktrees] selected snapshot installer is unavailable; run an explicit install or repair.')
                        exit 126
                    }
                    $_inst = $_latestInstaller
                    $_snap = $_latestSnapshot
                } elseif ($_snap) {
                    [Console]::Error.WriteLine('[agent-worktrees] selected snapshot changed while waiting; retry.')
                    exit 126
                } elseif (Test-Path -LiteralPath (Join-Path $_root 'payload-dir')) {
                    [Console]::Error.WriteLine('[agent-worktrees] selected snapshot is unreadable; run an explicit install or repair.')
                    exit 126
                }
                if (-not ([IO.File]::ReadAllText($_inst).Contains('# agent-worktrees:first-use-driver-nonblocking-v1'))) {
                    [Console]::Error.WriteLine('[agent-worktrees] selected installer lacks safe first-use admission; run an explicit install or repair.')
                    exit 126
                }
                $previousNoWait = [Environment]::GetEnvironmentVariable('AGENT_WORKTREES_FIRST_USE_NO_WAIT', 'Process')
                $previousSnapshot = [Environment]::GetEnvironmentVariable('AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT', 'Process')
                $previousEap = $ErrorActionPreference
                $env:AGENT_WORKTREES_FIRST_USE_NO_WAIT = '1'
                if ($_latestSnapshot) {
                    $env:AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT = $_latestSnapshot
                } else {
                    $env:AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT = '::absent::'
                }
                $ErrorActionPreference = 'Continue'
                $script:worktreesDriverBusy = $false
                $script:worktreesDriverReselect = $false
                try {
                    & $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 |
                        ForEach-Object {
                            if ([string]$_ -ceq '::agent-worktrees-driver-busy::') {
                                $script:worktreesDriverBusy = $true
                            } elseif ([string]$_ -ceq '::agent-worktrees-driver-reselect::') {
                                $script:worktreesDriverReselect = $true
                            } elseif ($_ -is [System.Management.Automation.ErrorRecord]) {
                                [Console]::Error.WriteLine($_.Exception.Message)
                            } else {
                                [Console]::Error.WriteLine($_)
                            }
                        }
                    $retry = $LASTEXITCODE -eq 75 -and ($script:worktreesDriverBusy -or $script:worktreesDriverReselect)
                } finally {
                    $ErrorActionPreference = $previousEap
                    if ($null -eq $previousNoWait) {
                        Remove-Item Env:AGENT_WORKTREES_FIRST_USE_NO_WAIT -ErrorAction SilentlyContinue
                    } else {
                        $env:AGENT_WORKTREES_FIRST_USE_NO_WAIT = $previousNoWait
                    }
                    if ($null -eq $previousSnapshot) {
                        Remove-Item Env:AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT -ErrorAction SilentlyContinue
                    } else {
                        $env:AGENT_WORKTREES_FIRST_USE_EXPECTED_SNAPSHOT = $previousSnapshot
                    }
                }
            }
        } finally {
            if ($held) { try { $mutex.ReleaseMutex() } catch {} }
        }
        if (-not $retry) { break }
        if (-not $reportedBusy) {
            [Console]::Error.WriteLine('[agent-worktrees] update driver is busy or snapshot selection changed; releasing the provision lock before retry.')
            $reportedBusy = $true
        }
        if ([DateTime]::UtcNow -ge $retryDeadline) {
            [Console]::Error.WriteLine('[agent-worktrees] update driver remained busy; retry after the active update finishes.')
            exit 75
        }
        Start-Sleep -Milliseconds 200
    }
} finally {
    $mutex.Dispose()
}
$_py = _resolve_aw_py
if ($_py) { $_cache = @(Get-AwPythonCache $_py); Invoke-AwTerminalPython $_py ($_cache + @('-m', 'agent_worktrees') + $args) @('-I', '-B', '-m', 'agent_worktrees', '--version'); exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-worktrees] provisioning did not yield a runtime. See the log above; retry, or run the installer manually.')
exit 1
