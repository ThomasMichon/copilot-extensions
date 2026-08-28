#!/usr/bin/env bash
# Mark a Copilot session as ended on the current worktree.
# Called from hooks.json on sessionEnd.

set -euo pipefail

_LOG="${WORKTREE_SETUP_LOG:-/dev/null}"
_log() { printf '[%s] [%s] deregister-session: %s\n' "$(date '+%H:%M:%S')" "$1" "$2" >> "$_LOG" 2>/dev/null || true; }

# The stdin payload is authoritative for session id/cwd. Environment values are
# compatibility hints when the hook exports them.
wt_id="${WORKTREE_ID:-}"

_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PYTHON="${AW_PY:-}"
if [[ ! -x "$PYTHON" ]]; then
    _log SKIP "venv python not found"
    exit 0
fi

args=(-m agent_worktrees deregister-session --stdin)
[[ -n "$wt_id" ]] && args+=(--worktree-id "$wt_id")

export PYTHONPATH=""  # package is installed in the venv (no lib/ shadow)
if PYTHONPATH="" "$PYTHON" "${args[@]}" 2>/dev/null; then
    _log OK "deregistered payload session on wt=${wt_id:-<resolved>}"
else
    _log WARN "deregister-session failed (exit $?) for payload session wt=${wt_id:-<resolved>}"
fi

exit 0
