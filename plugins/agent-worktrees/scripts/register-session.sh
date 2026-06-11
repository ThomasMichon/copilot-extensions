#!/usr/bin/env bash
# Register a Copilot session against the current worktree.
# Called from hooks.json on sessionStart.

set -euo pipefail

_LOG="${WORKTREE_SETUP_LOG:-${APERTURE_SETUP_LOG:-/dev/null}}"
_log() { printf '[%s] [%s] register-session: %s\n' "$(date '+%H:%M:%S')" "$1" "$2" >> "$_LOG" 2>/dev/null || true; }

wt_id="${WORKTREE_ID:-${APERTURE_WORKTREE_ID:-}}"
session_id="${COPILOT_AGENT_SESSION_ID:-}"

if [[ -z "$wt_id" ]]; then
    _log SKIP "WORKTREE_ID not set"
    exit 0
fi
if [[ -z "$session_id" ]]; then
    _log SKIP "COPILOT_AGENT_SESSION_ID not set (wt=$wt_id)"
    exit 0
fi

PYTHON="$HOME/.agent-worktrees/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    _log SKIP "venv python not found"
    exit 0
fi

export PYTHONPATH=""  # package is installed in the venv (no lib/ shadow)
if "$PYTHON" -m agent_worktrees register-session \
    --worktree-id "$wt_id" \
    --session-id "$session_id" \
    2>/dev/null; then
    _log OK "registered session=$session_id on wt=$wt_id"
else
    _log WARN "register-session failed (exit $?) for session=$session_id wt=$wt_id"
fi

exit 0
