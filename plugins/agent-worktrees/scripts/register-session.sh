#!/usr/bin/env bash
# Register a Copilot session against the current worktree.
# Called from hooks.json on sessionStart.
#
# The Copilot CLI pipes {sessionId, cwd, ...} as a JSON payload on stdin.
# COPILOT_AGENT_SESSION_ID is NOT reliably set in the sessionStart hook
# environment, so the stdin payload is the authoritative source for the
# session id. We forward it to the Python command (--stdin), which parses
# it and resolves the worktree from cwd when WORKTREE_ID is absent.

set -euo pipefail

_LOG="${WORKTREE_SETUP_LOG:-${APERTURE_SETUP_LOG:-/dev/null}}"
_log() { printf '[%s] [%s] register-session: %s\n' "$(date '+%H:%M:%S')" "$1" "$2" >> "$_LOG" 2>/dev/null || true; }

wt_id="${WORKTREE_ID:-${APERTURE_WORKTREE_ID:-}}"

PYTHON="$HOME/.agent-worktrees/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    _log SKIP "venv python not found"
    exit 0
fi

args=(-m agent_worktrees register-session --stdin)
[[ -n "$wt_id" ]] && args+=(--worktree-id "$wt_id")

# Forward the CLI's stdin payload to the Python command. PYTHONPATH is
# cleared because the package is installed in the venv (no lib/ shadow).
if PYTHONPATH="" "$PYTHON" "${args[@]}" 2>/dev/null; then
    _log OK "registered session (wt=${wt_id:-<from-cwd>})"
else
    _log WARN "register-session failed (exit $?) wt=${wt_id:-<from-cwd>}"
fi

exit 0
