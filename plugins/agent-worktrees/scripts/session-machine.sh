#!/usr/bin/env bash
# session-machine -- sessionStart hook (hooks.json). See session-machine.ps1 for
# the full rationale.
#
# Emits the machine-identity block as {"additionalContext": "..."}, computed live
# from machines.yaml by `agent_worktrees machine-context` (cwd-gated: emits {}
# outside an agent-worktrees-managed project). Declarative, launch-path-independent
# replacement for the per-project machine.instructions.md + nested AGENTS.md that
# were loaded via COPILOT_CUSTOM_INSTRUCTIONS_DIRS (dotfiles#1056).

set -uo pipefail

PY="$HOME/.agent-worktrees/.venv/bin/python"
[[ -x "$PY" ]] || { printf '{}'; exit 0; }
out="$(PYTHONPATH="" "$PY" -m agent_worktrees machine-context 2>/dev/null || true)"
if [[ -n "$out" ]]; then printf '%s' "$out"; else printf '{}'; fi
exit 0
