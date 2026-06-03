# Register a Copilot session against the current worktree.
# Called from hooks.json on sessionStart.

$wt_id = if ($env:WORKTREE_ID) { $env:WORKTREE_ID } else { $env:APERTURE_WORKTREE_ID }
$session_id = $env:COPILOT_AGENT_SESSION_ID

if (-not $wt_id -or -not $session_id) { exit 0 }

$python = "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { exit 0 }

$env:PYTHONPATH = "$env:USERPROFILE\.agent-worktrees\lib"
try {
    & $python -m agent_worktrees register-session `
        --worktree-id $wt_id `
        --session-id $session_id `
        2>$null
} catch {}

exit 0
