#!/usr/bin/env bash
# Default / normalized session setup script for repos.
#
# Used by agent-worktrees as the normalized launcher. Prepends any
# repo-provided session PATH directories, runs an optional repo setup hook
# (vault / MCP; context passed by argument, not ambient env), displays a brief
# welcome banner, and launches the Copilot CLI.
#
# A repo opts into this normalized flow by declaring a setup_hook in its
# .agent-worktrees/config.yaml. When absent, this script is still used as the
# fallback launcher for repos without their own tools/setup/setup.sh.
#
# The launcher (launch-session.sh) sets the working directory before calling
# this script. Context (project) resolves from CWD, git-like -- no ambient
# WORKTREE_PROJECT is required.

set -euo pipefail

MACHINE="${HOSTNAME:-$(hostname)}"
RECOVERY=false
SETUP_HOOK=""
SESSION_PATH=""
COPILOT_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)      MACHINE="$2"; shift 2 ;;
        --recovery)     RECOVERY=true; shift ;;
        --setup-hook)   SETUP_HOOK="$2"; shift 2 ;;
        --session-path) SESSION_PATH="$2"; shift 2 ;;
        *)              COPILOT_ARGS+=("$1"); shift ;;
    esac
done

# -- Session PATH prepend (generic; repo-provided dirs) -------------------
if [[ -n "$SESSION_PATH" ]]; then
    export PATH="${SESSION_PATH}:${PATH}"
fi

# -- Environment ----------------------------------------------------------
# Resolve the project from CWD (git-like); fall back to the directory name if
# the CLI is unavailable (e.g. recovery mode).
_AW_PY="$HOME/.agent-worktrees/.venv/bin/python"
PROJECT=""
if [[ -x "$_AW_PY" ]]; then
    PROJECT="$(PYTHONPATH="" "$_AW_PY" -m agent_worktrees get project 2>/dev/null || true)"
fi
[[ -z "$PROJECT" ]] && PROJECT="$(basename "$PWD")"
export WORKTREE_MACHINE="$MACHINE"

# -- Repo setup hook (vault / MCP; repo-specific) -------------------------
# Runs before launch, context passed by argument. Skipped in recovery so a
# broken hook can never lock the operator out of a recovery session. A
# non-zero exit warns but does not abort the launch.
if [[ -n "$SETUP_HOOK" && "$RECOVERY" != true ]]; then
    if [[ -f "$SETUP_HOOK" ]]; then
        echo "  Setup:    $SETUP_HOOK"
        if ! bash "$SETUP_HOOK" --machine "$MACHINE"; then
            echo "  WARN: setup hook exited non-zero; continuing to launch." >&2
        fi
    else
        echo "  WARN: setup hook not found: $SETUP_HOOK" >&2
    fi
fi

# -- Welcome banner -------------------------------------------------------
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

# -- Launch Copilot -------------------------------------------------------
if command -v copilot &>/dev/null; then
    exec copilot "${COPILOT_ARGS[@]}"
elif command -v gh &>/dev/null; then
    exec gh copilot "${COPILOT_ARGS[@]}"
else
    echo "ERROR: Neither copilot nor gh found on PATH." >&2
    exit 1
fi
