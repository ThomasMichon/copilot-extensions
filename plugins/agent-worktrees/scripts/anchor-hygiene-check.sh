#!/usr/bin/env bash
# Anchor hygiene check -- runs on session start via hooks.json
# Warns if the anchor repo has uncommitted changes or stash entries.
# Always exits 0 (warning only, never blocks session start).

set -euo pipefail

PYTHON="$HOME/.agent-worktrees/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    exit 0
fi

export PYTHONPATH=""  # package is installed in the venv (no lib/ shadow)
"$PYTHON" -m agent_worktrees anchor-check --quiet 2>/dev/null || true

exit 0
