#!/usr/bin/env bash
# Mark a Copilot session as ended on the current worktree.
# Called from hooks.json on sessionEnd.

set -euo pipefail

_LOG="${WORKTREE_SETUP_LOG:-${APERTURE_SETUP_LOG:-/dev/null}}"
_log() { printf '[%s] [%s] deregister-session: %s\n' "$(date '+%H:%M:%S')" "$1" "$2" >> "$_LOG" 2>/dev/null || true; }

# Worktree id is resolved from CWD by the Python command (this hook runs in the
# worktree). WORKTREE_ID is forwarded only if present, for robustness.
wt_id="${WORKTREE_ID:-${APERTURE_WORKTREE_ID:-}}"
session_id="${COPILOT_AGENT_SESSION_ID:-}"

if [[ -z "$session_id" ]]; then
    _log SKIP "COPILOT_AGENT_SESSION_ID not set"
    exit 0
fi

PYTHON="$HOME/.agent-worktrees/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    _log SKIP "venv python not found"
    exit 0
fi

args=(-m agent_worktrees deregister-session --session-id "$session_id")
[[ -n "$wt_id" ]] && args+=(--worktree-id "$wt_id")

export PYTHONPATH=""  # package is installed in the venv (no lib/ shadow)
if PYTHONPATH="" "$PYTHON" "${args[@]}" 2>/dev/null; then
    _log OK "deregistered session=$session_id on wt=${wt_id:-<from-cwd>}"
else
    _log WARN "deregister-session failed (exit $?) for session=$session_id wt=${wt_id:-<from-cwd>}"
fi

exit 0
