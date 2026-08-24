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
# Dynamic: the "the user's state repo" definition (binds the term to the
# resolved checkout so downstream plugins can refer to it in plain prose).
defn="$(PYTHONPATH="" "$PY" -m agent_worktrees state-root --conduct 2>/dev/null || true)"
related="$(PYTHONPATH="" "$PY" -m agent_worktrees --project "$project" related --conduct 2>/dev/null || true)"
dir="$HOME/.agent-worktrees/bin/conduct"

# Dynamic: the worktree's own recent-history recovery digest (record-first
# recovery -- what this worktree has been doing, so a fresh/successor session
# inherits it even if a live handoff never completed). Empty when no history.
digest="$(PYTHONPATH="" "$PY" -m agent_worktrees history-digest 2>/dev/null || true)"

PYTHONPATH="" "$PY" - "$dir" "$defn" "$related" "$digest" <<'PYEOF'
import json, os, sys

d = sys.argv[1]
defn = sys.argv[2] if len(sys.argv) > 2 else ""
related = sys.argv[3] if len(sys.argv) > 3 else ""
digest = sys.argv[4] if len(sys.argv) > 4 else ""
parts = []
if defn.strip():
    parts.append(defn.strip())
if related.strip():
    parts.append(related.strip())
if os.path.isdir(d):
    for name in sorted(os.listdir(d)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(d, name), encoding="utf-8") as fh:
            text = fh.read().rstrip()
        if text:
            parts.append(text)
if digest.strip():
    parts.append(digest.strip())

print(json.dumps({"additionalContext": "\n\n".join(parts)}) if parts else "{}")
PYEOF
exit 0
