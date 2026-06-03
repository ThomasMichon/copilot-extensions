#!/usr/bin/env bash
# Mark a Copilot session as ended on the current worktree.
# Called from hooks.json on sessionEnd.

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
"$PYTHON" -m agent_worktrees deregister-session \
    --worktree-id "$wt_id" \
    --session-id "$session_id" \
    2>/dev/null || true

exit 0
