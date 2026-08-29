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

# --- collect dynamic conduct; one Python assembler owns ordering + budget ---
# Dynamic: the "the user's state repo" definition (binds the term to the
# resolved checkout so downstream plugins can refer to it in plain prose).
defn="$(PYTHONPATH="" "$PY" -m agent_worktrees state-root --conduct 2>/dev/null || true)"
related=""
if [[ "${1:-}" != "--aggregate" ]]; then
    related="$(PYTHONPATH="" "$PY" -m agent_worktrees --project "$project" related --conduct 2>/dev/null || true)"
fi
dir="$HOME/.agent-worktrees/bin/conduct"

# Dynamic: the worktree's own recent-history recovery digest (record-first
# recovery -- what this worktree has been doing, so a fresh/successor session
# inherits it even if a live handoff never completed). Empty when no history.
digest="$(PYTHONPATH="" "$PY" -m agent_worktrees history-digest 2>/dev/null || true)"

AW_CONDUCT_DEFINITION="$defn" \
AW_CONDUCT_RELATED="$related" \
AW_CONDUCT_HISTORY="$digest" \
PYTHONPATH="" "$PY" -m agent_worktrees.conduct "$dir" "$@"
exit 0
