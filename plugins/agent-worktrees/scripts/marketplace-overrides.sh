#!/usr/bin/env bash
# Reconcile registered local marketplace sources on session start.

set -uo pipefail

_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PY="${AW_PY:-}"
[[ -x "$PY" ]] || { printf '{}'; exit 0; }

out="$(PYTHONPATH="" "$PY" -m agent_worktrees reconcile-marketplaces \
    --stdin --session-start 2>/dev/null || true)"
if [[ -n "$out" ]]; then printf '%s' "$out"; else printf '{}'; fi
exit 0
