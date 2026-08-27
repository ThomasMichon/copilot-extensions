$env:PYTHONUTF8 = '1'
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
function _resolve_aw_py {
    function _version_key([string]$ver) {
        [regex]::Replace($ver.ToLowerInvariant(), '\d+', { param($m) $m.Value.PadLeft(20, '0') })
    }
    function _try_slot([string]$ver) {
        if (-not $ver) { return $null }
        $slot = Join-Path $_root ('versions\' + $ver)
        if (-not (Test-Path -LiteralPath (Join-Path $slot '.install-complete.json') -PathType Leaf)) { return $null }
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
$_py = _resolve_aw_py
if ($_py) { & $_py -m agent_worktrees @args; exit $LASTEXITCODE }
if ($env:AGENT_WORKTREES_NO_SELFPROVISION) { & python -m agent_worktrees @args; exit $LASTEXITCODE }
$_snap = ''
try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
$_inst = if ($_snap) { Join-Path $_snap 'scripts\install.ps1' } else { '' }
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) {
    $_inst = Get-ChildItem (Join-Path $env:USERPROFILE '.copilot\installed-plugins') -Recurse -Filter 'install.ps1' -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '[\\/]agent-worktrees[\\/]scripts[\\/]install\.ps1$' } |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) { [Console]::Error.WriteLine('[agent-worktrees] cannot self-provision: installer not found. Re-enable the plugin, then retry.'); exit 127 }
[Console]::Error.WriteLine('[agent-worktrees] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
[Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-worktrees eta_seconds=120 reason=first-use')
$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
$mutex = [System.Threading.Mutex]::new($false, 'Local\Copilot.AgentWorktrees.Provision')
$held = $false
try {
    try { $held = $mutex.WaitOne() } catch [System.Threading.AbandonedMutexException] { $held = $true }
    $_py = _resolve_aw_py
    if (-not $_py) {
        & $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
    }
} finally {
    if ($held) { try { $mutex.ReleaseMutex() } catch {} }
    $mutex.Dispose()
}
$_py = _resolve_aw_py
if ($_py) { & $_py -m agent_worktrees @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[agent-worktrees] provisioning did not yield a runtime. See the log above; retry, or run the installer manually.')
exit 1
