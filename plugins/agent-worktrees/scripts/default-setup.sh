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
ENV_SCRIPT=""
COPILOT_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)      MACHINE="$2"; shift 2 ;;
        --recovery)     RECOVERY=true; shift ;;
        --setup-hook)   SETUP_HOOK="$2"; shift 2 ;;
        --session-path) SESSION_PATH="$2"; shift 2 ;;
        --env-script)   ENV_SCRIPT="$2"; shift 2 ;;
        *)              COPILOT_ARGS+=("$1"); shift ;;
    esac
done

# In --stdio (ACP) mode, stdout is the JSON-RPC channel; keep all human-facing
# output (banner, hook output) off it. `say` and the hook redirect to stderr.
STDIO=false
for _a in "${COPILOT_ARGS[@]:-}"; do
    [[ "$_a" == "--stdio" ]] && STDIO=true
done
say() { if $STDIO; then echo "$@" >&2; else echo "$@"; fi; }

# -- Session PATH prepend (generic; repo-provided dirs) -------------------
if [[ -n "$SESSION_PATH" ]]; then
    export PATH="${SESSION_PATH}:${PATH}"
fi

# -- Enlistment env priming (repo env_script) -----------------------------
# Source the repo's env-priming script so the vars it exports reach the Copilot
# exec below (UNLIKE the setup hook, which runs as a child and loses its env).
# `set -a` auto-exports; the script's own stdout is redirected to stderr to keep
# the ACP channel clean. Runs even in recovery -- the build env is always needed.
if [[ -n "$ENV_SCRIPT" ]]; then
    if [[ -f "$ENV_SCRIPT" ]]; then
        say "  Env:      $ENV_SCRIPT"
        set -a
        # shellcheck disable=SC1090
        . "$ENV_SCRIPT" >/dev/null 2>&1 || echo "  WARN: env_script exited non-zero; continuing." >&2
        set +a
    else
        echo "  WARN: env_script not found: $ENV_SCRIPT" >&2
    fi
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
        say "  Setup:    $SETUP_HOOK"
        if $STDIO; then
            # Keep the hook's stdout off the ACP channel.
            if ! bash "$SETUP_HOOK" --machine "$MACHINE" >&2; then
                echo "  WARN: setup hook exited non-zero; continuing to launch." >&2
            fi
        elif ! bash "$SETUP_HOOK" --machine "$MACHINE"; then
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

say ""
say "  Project:  $PROJECT"
say "  Branch:   $BRANCH ($STATUS)"
say "  Machine:  $MACHINE"
say "  Path:     $PWD"
say ""

# -- Launch Copilot -------------------------------------------------------
if command -v copilot &>/dev/null; then
    exec copilot "${COPILOT_ARGS[@]}"
elif command -v gh &>/dev/null; then
    exec gh copilot "${COPILOT_ARGS[@]}"
else
    echo "ERROR: Neither copilot nor gh found on PATH." >&2
    exit 1
fi
