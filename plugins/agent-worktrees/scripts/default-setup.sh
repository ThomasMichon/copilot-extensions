#!/usr/bin/env bash
# Default session setup script for repos without their own.
#
# Used by agent-worktrees when the anchor repo does not provide a
# tools/setup/setup.sh.  Sets basic environment variables, displays
# a brief welcome banner, and launches the Copilot CLI.
#
# The launcher (launch-session.sh) sets the working directory before calling
# this script. Context (project) resolves from CWD, git-like -- no ambient
# WORKTREE_PROJECT is required.

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
# Resolve the project from CWD (git-like); fall back to the directory name if
# the CLI is unavailable (e.g. recovery mode).
_AW_PY="$HOME/.agent-worktrees/.venv/bin/python"
PROJECT=""
if [[ -x "$_AW_PY" ]]; then
    PROJECT="$(PYTHONPATH="" "$_AW_PY" -m agent_worktrees get project 2>/dev/null || true)"
fi
[[ -z "$PROJECT" ]] && PROJECT="$(basename "$PWD")"
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
