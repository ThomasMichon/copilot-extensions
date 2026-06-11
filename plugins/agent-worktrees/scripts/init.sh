#!/usr/bin/env bash
# Bootstrap the agent-worktrees shared runtime (delegates to install.sh).
# Backwards-compatible shim -- the canonical, self-contained install flow lives
# in install.sh (the uv pip install model). See docs/install-contract.md.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Shared-runtime bootstrap only: never auto-adopt a project.
unset WORKTREE_PROJECT
exec bash "$SCRIPT_DIR/install.sh" install
