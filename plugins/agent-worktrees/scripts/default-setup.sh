#!/usr/bin/env bash
# Default session setup script for repos without their own.
#
# Used by agent-worktrees when the anchor repo does not provide a
# tools/setup/setup.sh.  Sets basic environment variables, displays
# a brief welcome banner, and launches the Copilot CLI.
#
# The launcher (launch-session.sh) sets WORKTREE_ID, WORKTREE_PROJECT,
# and the working directory before calling this script.

set -euo pipefail

MACHINE="${HOSTNAME:-$(hostname)}"
RECOVERY=false
COPILOT_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)  MACHINE="$2"; shift 2 ;;
        --recovery) RECOVERY=true; shift ;;
        *)          COPILOT_ARGS+=("$1"); shift ;;
    esac
done

# ── Environment ──────────────────────────────────────────────────────────
PROJECT="${WORKTREE_PROJECT:-$(basename "$PWD")}"
export WORKTREE_MACHINE="$MACHINE"

# ── Welcome banner ───────────────────────────────────────────────────────
BRANCH=$(git branch --show-current 2>/dev/null || echo "(detached)")
DIRTY=$(git status --porcelain 2>/dev/null)
STATUS="clean"
[[ -n "$DIRTY" ]] && STATUS="dirty"

echo ""
echo "  Project:  $PROJECT"
echo "  Branch:   $BRANCH ($STATUS)"
echo "  Machine:  $MACHINE"
echo "  Path:     $PWD"
echo ""

# ── Launch Copilot ───────────────────────────────────────────────────────
if command -v copilot &>/dev/null; then
    exec copilot "${COPILOT_ARGS[@]}"
elif command -v gh &>/dev/null; then
    exec gh copilot "${COPILOT_ARGS[@]}"
else
    echo "ERROR: Neither copilot nor gh found on PATH." >&2
    exit 1
fi
