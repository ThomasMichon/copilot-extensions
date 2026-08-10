#!/usr/bin/env bash
# session-conduct -- sessionStart hook (hooks.json). See session-conduct.ps1 for
# the full rationale.
#
# Emits the static "conduct" guidance fragments deployed under
# ~/.agent-worktrees/bin/conduct/*.md as {"additionalContext": "..."} -- but
# ONLY when the session cwd is inside an agent-worktrees-managed project
# (cwd self-gating); emits {} otherwise so a globally-loaded plugin never leaks
# guidance into unrelated repos. Declarative, launch-path-independent
# replacement for the per-project *.instructions.md previously loaded via
# COPILOT_CUSTOM_INSTRUCTIONS_DIRS (dotfiles#1053 / effort instructions-to-hooks).

set -uo pipefail

emit_empty() { printf '{}'; exit 0; }

# --- cwd gate: only inside an agent-worktrees-managed project ---
_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PY="${AW_PY:-}"
[[ -x "$PY" ]] || emit_empty
project="$(PYTHONPATH="" "$PY" -m agent_worktrees get project 2>/dev/null || true)"
[[ -n "$project" ]] || emit_empty

# --- collect + JSON-encode deployed conduct fragments ---
dir="$HOME/.agent-worktrees/bin/conduct"
[[ -d "$dir" ]] || emit_empty

PYTHONPATH="" "$PY" - "$dir" <<'PYEOF'
import json, os, sys

d = sys.argv[1]
parts = []
for name in sorted(os.listdir(d)):
    if not name.endswith(".md"):
        continue
    with open(os.path.join(d, name), encoding="utf-8") as fh:
        text = fh.read().rstrip()
    if text:
        parts.append(text)

print(json.dumps({"additionalContext": "\n\n".join(parts)}) if parts else "{}")
PYEOF
exit 0
