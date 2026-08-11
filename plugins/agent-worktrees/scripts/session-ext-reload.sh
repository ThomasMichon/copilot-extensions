#!/usr/bin/env bash
# session-ext-reload -- sessionStart hook (hooks.json). See session-ext-reload.ps1
# for the full rationale.
#
# TEMPORARY: emits the extension-reload "Loading…/Resuming…" hang warning
# (github/copilot-agent-runtime#13492; fix: #13494) from the deployed
# ~/.agent-worktrees/bin/ext-reload-hang.md as {"additionalContext": "..."}.
# NOT strictly cwd-gated: also fires when cwd == HOME so it reaches a **Bare
# resume** session (cwd=~/), the exact scenario this warning covers; stays quiet
# in unrelated repos. Retired outright when #13494 ships (dotfiles#1055).

set -uo pipefail

emit_empty() { printf '{}'; exit 0; }

warn="$HOME/.agent-worktrees/bin/ext-reload-hang.md"
[[ -f "$warn" ]] || emit_empty

_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PY="${AW_PY:-}"
[[ -x "$PY" ]] || PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || emit_empty

# --- gate: managed project OR cwd == home (Bare resume) ---
cwd="$(pwd -P 2>/dev/null || pwd)"
home="$(cd "$HOME" 2>/dev/null && pwd -P || printf '%s' "$HOME")"
if [[ "$cwd" != "$home" ]]; then
    project="$(PYTHONPATH="" "$PY" -m agent_worktrees get project 2>/dev/null || true)"
    [[ -n "$project" ]] || emit_empty
fi

PYTHONPATH="" "$PY" - "$warn" <<'PYEOF'
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    text = fh.read().rstrip()

print(json.dumps({"additionalContext": text}) if text else "{}")
PYEOF
exit 0
