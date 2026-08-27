# Mark a Copilot session as ended on the current worktree.
# Called from hooks.json on sessionEnd.

# The stdin payload is authoritative for session id/cwd. WORKTREE_ID and the
# environment session id are compatibility hints when the hook exports them.
$wt_id = $env:WORKTREE_ID
$session_id = $env:COPILOT_AGENT_SESSION_ID
$payload = ''
if ([Console]::IsInputRedirected) {
    try { $payload = [Console]::In.ReadToEnd() } catch { }
}

$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { exit 0 }

$env:PYTHONPATH = ''  # package is installed in the venv (no lib/ shadow)
$deregArgs = @('-m', 'agent_worktrees', 'deregister-session', '--stdin')
if ($session_id) { $deregArgs += @('--session-id', $session_id) }
if ($wt_id) { $deregArgs += @('--worktree-id', $wt_id) }
try {
    $payload | & $python @deregArgs 2>$null
} catch {}

exit 0
