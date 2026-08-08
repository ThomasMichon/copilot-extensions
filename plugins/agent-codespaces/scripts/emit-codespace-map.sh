#!/usr/bin/env bash
# emit-codespace-map -- agent-codespaces sessionStart hook (bash).
#
# Emits {"additionalContext": "<map of CodeSpace-delegated repos>"} so a session
# knows which repos have no local checkout and must be worked via a CodeSpace.
# Derived from `agent-worktrees related list --json` (delegate=agent-codespaces),
# cwd-gated to a managed project; emits {} otherwise. See emit_codespace_map.py.
set -uo pipefail

emit_empty() { printf '{}'; exit 0; }

PY="$HOME/.agent-worktrees/.venv/bin/python"
[[ -x "$PY" ]] || emit_empty
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/emit_codespace_map.py"
[[ -f "$SCRIPT" ]] || emit_empty

PYTHONPATH="" "$PY" "$SCRIPT" 2>/dev/null || emit_empty
