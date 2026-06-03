#!/usr/bin/env bash
# Register a Copilot session against the current worktree.
# Called from hooks.json on sessionStart.

set -euo pipefail

wt_id="${WORKTREE_ID:-${APERTURE_WORKTREE_ID:-}}"
session_id="${COPILOT_AGENT_SESSION_ID:-}"

if [[ -z "$wt_id" || -z "$session_id" ]]; then
    exit 0
fi

PYTHON="$HOME/.agent-worktrees/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    exit 0
fi

export PYTHONPATH="$HOME/.agent-worktrees/lib"
"$PYTHON" -m agent_worktrees register-session \
    --worktree-id "$wt_id" \
    --session-id "$session_id" \
    2>/dev/null || true

exit 0
