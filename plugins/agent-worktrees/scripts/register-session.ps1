# Register a Copilot session against the current worktree.
# Called from hooks.json on sessionStart.
#
# The Copilot CLI delivers {sessionId, cwd, ...} as a JSON payload on
# stdin. COPILOT_AGENT_SESSION_ID is NOT reliably set in the sessionStart
# hook environment, so the stdin payload is the authoritative source for
# the session id. We forward it to the Python command (--stdin), which
# parses it and resolves the worktree from cwd when WORKTREE_ID is absent.

$ErrorActionPreference = 'SilentlyContinue'

$python = "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { exit 0 }

$wt_id = if ($env:WORKTREE_ID) { $env:WORKTREE_ID } else { $env:APERTURE_WORKTREE_ID }

# Read the hook payload from stdin (only when redirected, so a manual run
# in an interactive console does not block on ReadToEnd).
$payload = ''
if ([Console]::IsInputRedirected) {
    try { $payload = [Console]::In.ReadToEnd() } catch { }
}

$env:PYTHONPATH = ''  # package is installed in the venv (no lib/ shadow)
$cmdArgs = @('-m', 'agent_worktrees', 'register-session', '--stdin')
if ($wt_id) { $cmdArgs += @('--worktree-id', $wt_id) }

try {
    $payload | & $python @cmdArgs 2>$null
} catch { }

exit 0
