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
# Project identity is resolved from the worktree path.

set -euo pipefail

MACHINE="${HOSTNAME:-$(hostname)}"
RECOVERY=false
SETUP_HOOK=""
SESSION_PATH=""
ENV_SCRIPT=""
COPILOT_PATH_OVERRIDE=""
CONFIG_ROOT=""
RUNTIME_PYTHON=""
COPILOT_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --machine)      MACHINE="$2"; shift 2 ;;
        --recovery)     RECOVERY=true; shift ;;
        --setup-hook)   SETUP_HOOK="$2"; shift 2 ;;
        --session-path) SESSION_PATH="$2"; shift 2 ;;
        --env-script)   ENV_SCRIPT="$2"; shift 2 ;;
        --copilot-path) COPILOT_PATH_OVERRIDE="$2"; shift 2 ;;
        --config-root)  CONFIG_ROOT="$2"; shift 2 ;;
        --runtime-python) RUNTIME_PYTHON="$2"; shift 2 ;;
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

# -- Runtime --------------------------------------------------------------
_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
_AW_PY="${RUNTIME_PYTHON:-${AW_PY:-}}"

# -- Guarded setup configuration root ------------------------------------
# setup_hook is the supported cooperative writer boundary. Resolve its
# machine-local root, or validate an explicit caller-supplied root, before the
# hook gets a chance to execute.
if [[ -n "$SETUP_HOOK" && "$RECOVERY" != true ]]; then
    if [[ -n "$_AW_PY" && "$_AW_PY" != */* ]]; then
        _resolved_aw_py="$(command -v -- "$_AW_PY" 2>/dev/null || true)"
        [[ -n "$_resolved_aw_py" ]] && _AW_PY="$_resolved_aw_py"
    fi
    if [[ -z "$_AW_PY" || ! -x "$_AW_PY" ]]; then
        echo "ERROR: agent-worktrees runtime is unavailable; cannot validate the setup config root." >&2
        exit 3
    fi
    _config_root_args=(-m agent_worktrees config-root)
    if [[ -n "$CONFIG_ROOT" ]]; then
        _config_root_args+=(--destination "$CONFIG_ROOT")
    fi
    set +e
    _guarded_config_root="$(PYTHONPATH="" "$_AW_PY" -I "${_config_root_args[@]}")"
    _config_root_rc=$?
    set -e
    if [[ $_config_root_rc -ne 0 ]]; then
        exit "$_config_root_rc"
    fi
    if [[ -z "$_guarded_config_root" ]]; then
        echo "ERROR: agent-worktrees returned an empty setup config root." >&2
        exit 3
    fi
fi

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
PROJECT=""
if [[ -x "$_AW_PY" ]]; then
    PROJECT="$(PYTHONPATH="" "$_AW_PY" -m agent_worktrees get project 2>/dev/null || true)"
fi
[[ -z "$PROJECT" ]] && PROJECT="${PWD##*/}"
export WORKTREE_MACHINE="$MACHINE"

# -- Repo setup hook (vault / MCP; repo-specific) -------------------------
# Runs before launch, context passed by argument. Skipped in recovery so a
# broken hook can never lock the operator out of a recovery session. A
# non-zero exit warns but does not abort the launch.
if [[ -n "$SETUP_HOOK" && "$RECOVERY" != true ]]; then
    export AGENT_WORKTREES_CONFIG_ROOT="$_guarded_config_root"
    if [[ -f "$SETUP_HOOK" ]]; then
        say "  Setup:    $SETUP_HOOK"
        if $STDIO; then
            # Keep the hook's stdout off the ACP channel.
            if ! "$BASH" "$SETUP_HOOK" --machine "$MACHINE" >&2; then
                echo "  WARN: setup hook exited non-zero; continuing to launch." >&2
            fi
        elif ! "$BASH" "$SETUP_HOOK" --machine "$MACHINE"; then
            echo "  WARN: setup hook exited non-zero; continuing to launch." >&2
        fi
    else
        echo "  WARN: setup hook not found: $SETUP_HOOK" >&2
    fi
fi

# -- Welcome banner -------------------------------------------------------
BRANCH="(detached)"
DIRTY=""
if command -v git &>/dev/null; then
    BRANCH=$(git branch --show-current 2>/dev/null || echo "(detached)")
    # Guard against set -e: outside a git repo (e.g. Bare resume launches
    # Copilot in ~/), `git status` exits 128 and would otherwise abort launch.
    DIRTY=$(git status --porcelain 2>/dev/null || true)
fi
STATUS="clean"
[[ -n "$DIRTY" ]] && STATUS="dirty"

say ""
say "  Project:  $PROJECT"
say "  Branch:   $BRANCH ($STATUS)"
say "  Machine:  $MACHINE"
say "  Path:     $PWD"
say ""

# -- Launch Copilot -------------------------------------------------------
if [[ -n "$COPILOT_PATH_OVERRIDE" ]]; then
    if command -v "$COPILOT_PATH_OVERRIDE" &>/dev/null; then
        exec "$COPILOT_PATH_OVERRIDE" "${COPILOT_ARGS[@]}"
    else
        echo "ERROR: Configured Copilot executable not found: $COPILOT_PATH_OVERRIDE" >&2
        exit 1
    fi
elif command -v copilot &>/dev/null; then
    exec copilot "${COPILOT_ARGS[@]}"
elif command -v gh &>/dev/null; then
    exec gh copilot "${COPILOT_ARGS[@]}"
else
    echo "ERROR: Neither copilot nor gh found on PATH." >&2
    exit 1
fi
