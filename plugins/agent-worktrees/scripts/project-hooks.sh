#!/usr/bin/env bash
# Project hooks runner -- runs on session start via hooks.json
# Discovers and executes per-project session-start hooks from the
# project config directory (~/.{project}/hooks/session-start.sh).

set -euo pipefail

# Resolve the project from CWD (git-like); this hook runs in the worktree.
PYTHON="$HOME/.agent-worktrees/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then exit 0; fi
project="$(PYTHONPATH="" "$PYTHON" -m agent_worktrees get project 2>/dev/null || true)"
if [[ -z "$project" ]]; then exit 0; fi

hook="$HOME/.$project/hooks/session-start.sh"
if [[ ! -f "$hook" ]]; then exit 0; fi

bash "$hook" || true

exit 0
