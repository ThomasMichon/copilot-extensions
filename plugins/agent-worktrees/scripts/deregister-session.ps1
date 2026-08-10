# Mark a Copilot session as ended on the current worktree.
# Called from hooks.json on sessionEnd.

# Worktree id is resolved from CWD by the Python command (this hook runs in the
# worktree). WORKTREE_ID is forwarded only if present, for robustness.
$wt_id = $env:WORKTREE_ID
$session_id = $env:COPILOT_AGENT_SESSION_ID

if (-not $session_id) { exit 0 }

$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { exit 0 }

$env:PYTHONPATH = ''  # package is installed in the venv (no lib/ shadow)
$deregArgs = @('-m', 'agent_worktrees', 'deregister-session', '--session-id', $session_id)
if ($wt_id) { $deregArgs += @('--worktree-id', $wt_id) }
try {
    & $python @deregArgs 2>$null
} catch {}

exit 0
