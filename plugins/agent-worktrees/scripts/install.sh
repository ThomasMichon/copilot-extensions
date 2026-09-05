#!/usr/bin/env bash
# =============================================================================
# install.sh -- Worktree Session Manager -- standardized installer interface
# =============================================================================
# Manages the worktree session infrastructure lifecycle: install, uninstall,
# start, stop, status, update-config, update.
#
# Deploys launcher and finalizer scripts to ~/.agent-worktrees/bin/ and creates
# the project binstub in ~/.local/bin/.
# Shared runtime at ~/.agent-worktrees/; project config at ~/.{project}/.
#
# Usage:
#   bash plugins/agent-worktrees/scripts/install.sh install
#   bash plugins/agent-worktrees/scripts/install.sh install --project-name my-repo
#   bash plugins/agent-worktrees/scripts/install.sh status
#   bash plugins/agent-worktrees/scripts/install.sh update
#
# Options:
#   --project-name N Project name (auto-detected if omitted)
#   --force          Overwrite config without drift confirmation
#   --remove-config  On uninstall: also delete config and session metadata
#   --machine NAME   Machine name (auto-detected if omitted)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# A structured caller supplies both the validated context and its exact root.
# Validate them again before any installer staging or runtime mutation.
CONTEXTUAL_INSTALL=false
__aw_action="${1:-status}"
__aw_install_dir_arg=""
__aw_args=("$@")
for ((__aw_i = 1; __aw_i < ${#__aw_args[@]}; __aw_i++)); do
    if [[ "${__aw_args[$__aw_i]}" == "--install-dir" ]]; then
        ((__aw_i + 1 < ${#__aw_args[@]})) || {
            printf '%s\n' "ERROR: --install-dir requires a value" >&2
            exit 1
        }
        __aw_install_dir_arg="${__aw_args[$((__aw_i + 1))]}"
    fi
done
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    CONTEXTUAL_INSTALL=true
    case "$__aw_action" in
        install|update|status) ;;
        *)
            printf '%s\n' \
                "ERROR: structured installation context does not support action '$__aw_action'" >&2
            exit 1
            ;;
    esac
    [[ -n "$__aw_install_dir_arg" && "$__aw_install_dir_arg" == /* ]] || {
        printf '%s\n' \
            "ERROR: structured installation context requires an absolute --install-dir" >&2
        exit 1
    }
    [[ -f "$COPILOT_EXTENSIONS_CONTEXT" ]] || {
        printf '%s\n' "ERROR: structured installation context is unavailable" >&2
        exit 1
    }
    __aw_context_helper="$SCRIPT_DIR/installation-context/installation-context.sh"
    __aw_json_query="$SCRIPT_DIR/installation-context/json-query.awk"
    [[ -f "$__aw_context_helper" && -f "$__aw_json_query" ]] || {
        printf '%s\n' "ERROR: installation-context validator is unavailable" >&2
        exit 1
    }
    __aw_durable_home="$COPILOT_EXTENSIONS_CONTEXT"
    for _ in 1 2 3 4 5; do
        __aw_durable_home="$(dirname -- "$__aw_durable_home")"
    done
    __aw_validated_context="$(
        bash "$__aw_context_helper" validate \
            --context "$COPILOT_EXTENSIONS_CONTEXT" \
            --durable-home "$__aw_durable_home" \
            --expected-plugin-id agent-worktrees \
            --expected-payload-root "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
    )" || exit $?
    __aw_context_root="$(
        LC_ALL=C awk -f "$__aw_json_query" \
            -v mode=get -v query_path=pluginRoot \
            - <<<"$__aw_validated_context"
    )" || {
        printf '%s\n' "ERROR: validated context omitted pluginRoot" >&2
        exit 1
    }
    __aw_context_root="$(cd -P -- "$__aw_context_root" && pwd)"
    __aw_install_root="$(cd -P -- "$__aw_install_dir_arg" && pwd)"
    [[ "$__aw_install_root" == "$__aw_context_root" ]] || {
        printf '%s\n' \
            "ERROR: --install-dir does not match validated installation context" >&2
        exit 1
    }

    # The standard self-stage block is intentionally byte-identical across
    # plugins and stages beneath the legacy root. Context installs stage here
    # instead, inside the selected installation, then mark the child staged so
    # the standard block remains inert.
    if [[ -z "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then
        cd "$HOME"
        __aw_stage_root="$__aw_install_root/.install-stage"
        __aw_stage="$__aw_stage_root/$(date -u +%Y%m%dT%H%M%S)-$$"
        mkdir -p "$__aw_stage"
        cp -a "$PLUGIN_DIR" "$__aw_stage/"
        __aw_staged_payload="$__aw_stage/$(basename "$PLUGIN_DIR")"
        for __aw_sibling in "$__aw_stage_root"/*; do
            [[ -d "$__aw_sibling" && "$__aw_sibling" != "$__aw_stage" ]] ||
                continue
            __aw_owner="${__aw_sibling##*-}"
            if [[ "$__aw_owner" =~ ^[0-9]+$ ]] &&
                    kill -0 "$__aw_owner" 2>/dev/null; then
                continue
            fi
            rm -rf "$__aw_sibling" 2>/dev/null || true
        done
        __aw_deadline=480
        __aw_deadline_raw="${AGENT_WORKTREES_INSTALL_DEADLINE_SEC:-${COPILOT_PLUGIN_INSTALL_DEADLINE_SEC:-}}"
        if [[ "$__aw_deadline_raw" =~ ^-?[0-9]+$ ]]; then
            __aw_deadline="$__aw_deadline_raw"
        fi
        export COPILOT_PLUGIN_INSTALL_STAGED=context-install
        export COPILOT_PLUGIN_STAGED_FROM="$PLUGIN_DIR"
        set -m
        (
            cd "$__aw_staged_payload"
            exec bash \
                "$__aw_staged_payload/scripts/$(basename "${BASH_SOURCE[0]}")" \
                "$@"
        ) &
        __aw_child=$!
        set +m
        if [[ "$__aw_deadline" -gt 0 ]]; then
            (
                __aw_waited=0
                while kill -0 "$__aw_child" 2>/dev/null; do
                    sleep 1
                    __aw_waited=$((__aw_waited + 1))
                    if [[ "$__aw_waited" -ge "$__aw_deadline" ]]; then
                        : >"$__aw_stage/.watchdog-fired"
                        kill -- -"$__aw_child" 2>/dev/null ||
                            kill "$__aw_child" 2>/dev/null || true
                        printf '[%sZ] WATCHDOG-KILL agent-worktrees context install exceeded %ss deadline (child pid %s); killed tree. Stage: %s\n' \
                            "$(date -u +%Y-%m-%dT%H:%M:%S)" \
                            "$__aw_deadline" "$__aw_child" "$__aw_stage" \
                            >>"$__aw_install_root/reconcile.err.log" 2>/dev/null ||
                            true
                        break
                    fi
                done
            ) &
            __aw_watcher=$!
        else
            __aw_watcher=""
        fi
        if wait "$__aw_child"; then __aw_rc=0; else __aw_rc=$?; fi
        if [[ -n "$__aw_watcher" ]]; then
            kill "$__aw_watcher" 2>/dev/null || true
            wait "$__aw_watcher" 2>/dev/null || true
        fi
        if [[ -e "$__aw_stage/.watchdog-fired" ]]; then
            __aw_rc=124
        fi
        rm -rf "$__aw_stage"
        exit "$__aw_rc"
    fi
fi

# === install-contract:v4 self-stage -- keep byte-identical across plugins ===
# dotfiles #935: a plugin installer reads its own payload (src/, libs/,
# pyproject.toml) to build the venv, so while it runs -- especially if it wedges
# or times out -- it holds the SINGLETON installed-plugins/<mkt>/<plugin> payload
# dir busy (cwd/open handles). A concurrent `copilot plugin update <plugin>` then
# fights it (os error 32 on Windows; POSIX is more forgiving, but the design must
# be uniform): the payload freezes at the old version and reconcile keeps
# reverting the runtime toward it (the version-drift saga). Fix: when running
# from the marketplace payload, copy the WHOLE payload into a UNIQUE
# per-invocation staging dir OUTSIDE the payload and re-exec from there, so the
# singleton is touched only for the fast copy. A stalled run then holds only its
# own throwaway stage dir, never blocking the next invocation or a `copilot
# plugin update`. COPILOT_PLUGIN_STAGED_FROM tells _source_kind the payload was
# really the marketplace (see below). Env-guarded against re-exec loops; the
# stage-dir path (not under installed-plugins) is a second guard. The staging
# parent doubles as a WATCHDOG: it launches the staged child in its OWN session/
# process group and, on a deadline, kills the WHOLE group (POSIX process-group
# kill -- the twin of Windows `taskkill /T`), so a stalled install (the
# session-start-hook failure class) self-terminates instead of leaking forever.
# Best-effort, pid-guarded reap of dead-owner stage dirs (a concurrent or wedged
# installer's dir is never touched -- it uses its own unique dir).
if [[ -z "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then
    __ss_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    __ss_payload="$(cd "$__ss_self_dir/.." && pwd)"
    case "$(printf '%s' "$__ss_payload" | tr '\\' '/')" in
        */.copilot/installed-plugins/*)
            __ss_name="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$__ss_payload/plugin.json" 2>/dev/null | head -1)"
            if [[ -n "$__ss_name" ]]; then
                __ss_root="$HOME/.$__ss_name/.install-stage"
                __ss_stage="$__ss_root/$(date -u +%Y%m%dT%H%M%S)-$$"
                if mkdir -p "$__ss_stage" && cp -a "$__ss_payload" "$__ss_stage/"; then
                    __ss_staged_payload="$__ss_stage/$(basename "$__ss_payload")"
                    __ss_entry="$__ss_staged_payload/scripts/$(basename "${BASH_SOURCE[0]}")"
                    # Reap prior stage dirs; NEVER touch a live one. Remove only a
                    # sibling whose owner pid (the -<pid> suffix) is DEAD, so a
                    # concurrent or wedged installer's dir is left alone.
                    if [[ -d "$__ss_root" ]]; then
                        for __ss_sib in "$__ss_root"/*; do
                            [[ -d "$__ss_sib" ]] || continue
                            if [[ "$__ss_sib" == "$__ss_stage" ]]; then continue; fi
                            __ss_owner="${__ss_sib##*-}"
                            if [[ "$__ss_owner" =~ ^[0-9]+$ ]] && kill -0 "$__ss_owner" 2>/dev/null; then continue; fi
                            rm -rf "$__ss_sib" 2>/dev/null || true
                        done
                    fi
                    # WATCHDOG deadline: <NAME>_INSTALL_DEADLINE_SEC, else
                    # COPILOT_PLUGIN_INSTALL_DEADLINE_SEC, else 480s; <=0 disables.
                    __ss_deadline=480
                    __ss_dl_var="$(printf '%s' "$__ss_name" | sed 's/[^A-Za-z0-9][^A-Za-z0-9]*/_/g' | tr '[:lower:]' '[:upper:]')_INSTALL_DEADLINE_SEC"
                    __ss_dl_raw="${!__ss_dl_var:-}"
                    if [[ -z "$__ss_dl_raw" ]]; then __ss_dl_raw="${COPILOT_PLUGIN_INSTALL_DEADLINE_SEC:-}"; fi
                    if [[ "$__ss_dl_raw" =~ ^-?[0-9]+$ ]]; then __ss_deadline="$__ss_dl_raw"; fi
                    export COPILOT_PLUGIN_INSTALL_STAGED=1
                    export COPILOT_PLUGIN_STAGED_FROM="$__ss_payload"
                    # Launch the staged child in its OWN process group (bash job
                    # control) so `wait` propagates its REAL exit code AND the
                    # watchdog can kill the WHOLE tree via a process-group signal
                    # (the POSIX twin of Windows `taskkill /T`). setsid -w is
                    # avoided: on some util-linux builds it swallows the child's
                    # exit code (returns 0), which would mask a failed install.
                    set -m
                    bash "$__ss_entry" "$@" &
                    __ss_child=$!
                    set +m
                    if [[ "$__ss_deadline" -gt 0 ]]; then
                        (
                            __ss_waited=0
                            while kill -0 "$__ss_child" 2>/dev/null; do
                                sleep 1
                                __ss_waited=$((__ss_waited + 1))
                                if [[ "$__ss_waited" -ge "$__ss_deadline" ]]; then
                                    : > "$__ss_stage/.watchdog-fired"
                                    kill -- -"$__ss_child" 2>/dev/null || kill "$__ss_child" 2>/dev/null || true
                                    printf '[%sZ] WATCHDOG-KILL %s: install exceeded %ss deadline (child pid %s); killed tree. Slot lacks a completion marker -> will be tossed + retried. Stage: %s\n' \
                                        "$(date -u +%Y-%m-%dT%H:%M:%S)" "$__ss_name" "$__ss_deadline" "$__ss_child" "$__ss_stage" \
                                        >> "$HOME/.$__ss_name/reconcile.err.log" 2>/dev/null || true
                                    break
                                fi
                            done
                        ) &
                        __ss_watcher=$!
                        if wait "$__ss_child"; then __ss_rc=0; else __ss_rc=$?; fi
                        kill "$__ss_watcher" 2>/dev/null || true
                        wait "$__ss_watcher" 2>/dev/null || true
                        if [[ -e "$__ss_stage/.watchdog-fired" ]]; then exit 124; fi
                        exit "$__ss_rc"
                    fi
                    if wait "$__ss_child"; then exit 0; else exit $?; fi
                else
                    printf '  [WARN] self-stage failed, running in place\n' >&2
                fi
            fi
            ;;
    esac
fi
# === end install-contract:v4 self-stage ===

# === install-contract:v4 smoke seam (test-only) -- keep byte-identical ===
# #935 install-flow test hook. When COPILOT_PLUGIN_INSTALL_SMOKE is set, prove
# the self-stage/lock/watchdog behavior WITHOUT a heavy venv build: this
# (post-stage) process records where it runs from + the recorded marketplace
# origin, optionally spawns a grandchild sleeper in the SAME process group (so a
# watchdog test can prove the WHOLE tree is killed), then sleeps to simulate a
# slow/wedged install. Never set in production.
if [[ -n "${COPILOT_PLUGIN_INSTALL_SMOKE:-}" ]]; then
    __sm_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    __sm_payload="$(cd "$__sm_self_dir/.." && pwd)"
    __sm_name="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$__sm_payload/plugin.json" 2>/dev/null | head -1)"
    __sm_home="$HOME/.$__sm_name"
    mkdir -p "$__sm_home"
    __sm_sleep=6
    if [[ "${COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP:-}" =~ ^[0-9]+$ ]]; then __sm_sleep="$COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP"; fi
    __sm_grand_pid=0
    if [[ -n "${COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD:-}" ]]; then
        __sm_grand_sleep="$__sm_sleep"
        if [[ "$__sm_grand_sleep" -lt 3600 ]]; then __sm_grand_sleep=3600; fi
        sleep "$__sm_grand_sleep" &
        __sm_grand_pid=$!
    fi
    __sm_staged=false
    if [[ -n "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then __sm_staged=true; fi
    printf '{"ran_from":"%s","staged_from":"%s","staged":%s,"child_pid":%s,"grandchild_pid":%s}\n' \
        "$__sm_self_dir" "${COPILOT_PLUGIN_STAGED_FROM:-}" "$__sm_staged" "$$" "$__sm_grand_pid" \
        > "$__sm_home/smoke.json"
    sleep "$__sm_sleep"
    exit 0
fi
# === end install-contract:v4 smoke seam ===

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if [[ -z "${UV_HTTP_TIMEOUT:-}" ]]; then export UV_HTTP_TIMEOUT=60; fi


# Ensure ~/.local/bin is on PATH (uv, pip-installed tools live here;
# non-interactive SSH sessions often miss it)
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Parse arguments ──────────────────────────────────────────────────────

ACTION="${1:-status}"
shift || true

FORCE=false
REMOVE_CONFIG=false
MACHINE=""
PROJECT_NAME_ARG=""
INSTALL_DIR_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)         FORCE=true; shift ;;
        --remove-config) REMOVE_CONFIG=true; shift ;;
        --machine)       MACHINE="$2"; shift 2 ;;
        --project-name)  PROJECT_NAME_ARG="$2"; shift 2 ;;
        --install-dir)   INSTALL_DIR_ARG="$2"; shift 2 ;;
        *)               echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Infer project name ──────────────────────────────────────────────────
# Priority: --project-name arg > existing config
# CWD is NOT auto-adopted -- pass --project-name to adopt explicitly.

PROJECT_NAME=""

if [[ -n "$PROJECT_NAME_ARG" ]]; then
    PROJECT_NAME="$PROJECT_NAME_ARG"
else
    # Try to infer from CWD basename matching an existing config dir
    _cwd_name="$(basename "$PWD")"
    if [[ -f "$HOME/.$_cwd_name/config.yaml" ]]; then
        PROJECT_NAME="$_cwd_name"
    fi
fi
# Reserved-name guard: `agent-worktrees` is the runtime's own global command
# (the project-agnostic shim from bin/agent-worktrees, deployed by
# deploy_tool_binstub), never a per-project launcher. If inference or an
# explicit flag resolves the name to it (e.g. the installer run from a dir
# literally named `agent-worktrees`, whose ~/.agent-worktrees/config.yaml always
# exists), a project deploy would overwrite the global shim with a
# self-`--project` binstub -- historically the seed of a fork-storm. Never treat
# the reserved runtime name as a project. (echo, not warn(): the output helpers
# are not defined until later in the script.)
if [[ "$PROJECT_NAME" == "agent-worktrees" ]]; then
    echo "  ! Ignoring reserved runtime name 'agent-worktrees' as a project (global command is owned by the tool binstub)" >&2
    PROJECT_NAME=""
fi
# Don't auto-adopt CWD repo -- runtime installs fine without a project.
HAS_PROJECT=false
if [[ -n "$PROJECT_NAME" ]]; then
    HAS_PROJECT=true
    # Validate project name (safe for dotdirs, binstubs, YAML keys)
    if [[ ! "$PROJECT_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "ERROR: Invalid project name '$PROJECT_NAME' -- must match [A-Za-z0-9._-]+" >&2
        exit 1
    fi
fi

# ── Detect REPO_DIR from project config, then CWD ───────────────────────

REPO_DIR=""
if $HAS_PROJECT; then
    _config_file="$HOME/.$PROJECT_NAME/config.yaml"
    if [[ -f "$_config_file" ]]; then
        # `|| true`: under `set -euo pipefail` a no-match grep exits 1 and
        # aborts the whole installer; a missing key must just yield empty.
        _anchor=$(grep 'anchor:' "$_config_file" 2>/dev/null | head -1 | sed 's/.*anchor:\s*//' || true)
        if [[ -n "$_anchor" ]] && git -C "$_anchor" rev-parse --show-toplevel >/dev/null 2>&1; then
            REPO_DIR="$_anchor"
        fi
    fi
fi
if [[ -z "$REPO_DIR" ]]; then
    _git_root="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$_git_root" ]]; then
        REPO_DIR="$_git_root"
    fi
fi

# ── Metadata ─────────────────────────────────────────────────────────────

SERVICE_NAME="Worktree Session Manager"
INSTALL_DIR="${INSTALL_DIR_ARG:-$HOME/.agent-worktrees}"
if [[ "$INSTALL_DIR" != /* ]]; then
    echo "ERROR: --install-dir must be absolute" >&2
    exit 1
fi
BIN_DIR="$INSTALL_DIR/bin"
LOCAL_BIN="$HOME/.local/bin"
SERVICE_YAML="$SCRIPT_DIR/service.yaml"

if $HAS_PROJECT; then
    PROJECT_DIR="$HOME/.$PROJECT_NAME"
    WORKTREES_DIR="$PROJECT_DIR/worktrees"
else
    PROJECT_DIR=""
    WORKTREES_DIR=""
fi

DEPLOY_SOURCE_PATHS=("plugins/agent-worktrees/")
INSTALLER_REL_PATH="plugins/agent-worktrees/scripts/install.sh"

# Legacy scripts (pre-Python) -- for cleanup during migration
LEGACY_SCRIPTS=(
    launch-session.ps1
    finalize-session.ps1
    finalize-session.sh
    worktree-status.ps1
    worktree-cleanup.ps1
    status-writer.sh
)

# Legacy alias binstubs that earlier versions deployed into BIN_DIR and/or
# LOCAL_BIN. They were removed from source (commit 688d74e) because they
# collide with worktree-manager and duplicate `agent-worktrees <subcommand>`,
# but already-deployed copies linger and cause confusion (e.g. invoking the
# flag-only `mark-complete` alias instead of `push-changes`/`finalize`).
# Pruned on every install/update. Bare name + .cmd/.ps1 variants are removed.
LEGACY_BINSTUBS=(
    mark-worktree-complete
    cleanup-worktrees
    mark-session-complete
)

# Python runtime paths (shared across projects)
LIB_DIR="$INSTALL_DIR/lib"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_BIN="$VENV_DIR/bin/agent-worktrees"

# === install-contract:v3 versioned-venv (agent-worktrees: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and make the historical `.venv` path a symlink into it, so the binstub,
# wrappers, and deploy-manifest resolve through the link unchanged. agent-worktrees
# is a CLI (no daemon), so no process to drain. LINK_DIR is the stable `.venv` path;
# VENV_DIR is the versions/<v> slot (build + health-gate). ALWAYS versioned -- the
# env opt-out (COPILOT_EXT_NO_VERSIONED / AGENT_WORKTREES_VERSIONED) and the legacy
# in-place fork are retired; scripts/versioned_runtime.py owns the swap + migration + gc.
LINK_DIR="$VENV_DIR"
LINK_PYTHON="$VENV_PYTHON"
VERSIONED_RUNTIME=1
SRC_VERSION=""
if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
    SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" | head -n1)"
fi
if [[ -z "$SRC_VERSION" ]]; then
    echo "[FAIL] Cannot determine plugin version from pyproject.toml (required for the versioned runtime)." >&2
    exit 1
fi
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_BIN="$VENV_DIR/bin/agent-worktrees"

# Layered project config deliberately omits machine paths. During marketplace
# update the cwd is the installed payload, so resolve the adopted repo's anchor
# from the canonical global repo registry through the previous-good runtime.
if $HAS_PROJECT && [[ -z "$REPO_DIR" && -x "$LINK_PYTHON" ]]; then
    _registry_anchor="$(PYTHONPATH= "$LINK_PYTHON" -c '
import sys
from agent_worktrees.repos import find_repo
entry = find_repo(sys.argv[1])
print((entry.local_path() if entry else "") or "")
' "$PROJECT_NAME" 2>/dev/null || true)"
    if [[ -n "$_registry_anchor" ]] &&
            git -C "$_registry_anchor" rev-parse --show-toplevel >/dev/null 2>&1; then
        REPO_DIR="$_registry_anchor"
    fi
fi

_versioned_activate() {
    # CLI (no daemon): health-gate the freshly-built slot, publish the
    # `current-version` marker (junction-free, `--no-link`: no `.venv` symlink is
    # laid; the marker is the SINGLE source of truth and any stale legacy link is
    # removed -- #1106), then gc old slots keeping current + previous-good.
    # Returns non-zero on failure. No-op in legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_PYTHON"
    [[ -x "$py" ]] || return 0
    if ! PYTHONPATH= "$VENV_PYTHON" -c 'import agent_worktrees' 2>/dev/null; then
        err "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    if ! PYTHONPATH= "$VENV_PYTHON" -m agent_worktrees.picker_tui.prewarm 2>/dev/null; then
        err "Fresh runtime slot failed its Picker prewarm gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    ok "Picker import path prewarmed in runtime version $SRC_VERSION"
    _versioned_mark_complete
    local prev
    prev="$("$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo "")"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --no-link; then
        err "Failed to activate runtime version (marker -> versions/$SRC_VERSION)"
        return 1
    fi
    ok "Runtime version $SRC_VERSION active (marker -> versions/$SRC_VERSION)"
    # Consolidated-status-daemon Phase 1 (#1696): the cutover just superseded any
    # running status-monitor, which self-retires but only RESPAWNS on the next
    # session start -- leaving live sessions' status bars frozen until then. Reap
    # the superseded monitor + spawn the current one now (from the NEW slot's
    # python), so every live session's bar is re-served with no session restart.
    # Best-effort, never fatal.
    if ! $CONTEXTUAL_INSTALL; then
        "$VENV_PYTHON" -m agent_worktrees status-monitor-restart 2>&1 | sed 's/^/  → monitor: /' || true
    fi
    # #742: record the just-activated version as `last-known-good` so a future
    # marker-absent resolution (resolve-runtime.sh tier 2) prefers it over a
    # newest-slot guess. Atomic (temp + rename); best-effort, never fatal.
    if printf '%s\n' "$SRC_VERSION" > "$INSTALL_DIR/last-known-good.tmp.$$" 2>/dev/null; then
        mv -f "$INSTALL_DIR/last-known-good.tmp.$$" "$INSTALL_DIR/last-known-good" 2>/dev/null \
            || rm -f "$INSTALL_DIR/last-known-good.tmp.$$" 2>/dev/null
    fi
    if [[ -n "$prev" ]]; then
        "$VENV_PYTHON" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$prev" 2>&1 | sed 's/^/  → gc: /' || true
    else
        "$VENV_PYTHON" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  → gc: /' || true
    fi
    return 0
}
# === end install-contract:v3 versioned-venv ===

# ── Status output helpers ────────────────────────────────────────────────

ok()      { echo "  ✓ $*"; }
changed() { echo "  → $*"; }
skipped() { echo "  ○ $*"; }
warn()    { echo "  ! $*"; }
err()     { echo "  ✗ $*"; }
header()  { echo ""; echo "═══ $* $(printf '═%.0s' $(seq 1 $((56 - ${#1}))))"; }

_bootstrap_python() {
    # A python to run the stdlib-only versioned_runtime.py helper BEFORE the slot
    # venv exists (e.g. the pre-build toss). Prefers the current `venv` link's
    # python, then python3/python on PATH. Prints nothing + returns 1 if none
    # found (#935).
    if [[ -x "$LINK_DIR/bin/python" ]]; then echo "$LINK_DIR/bin/python"; return 0; fi
    local __c
    for __c in python3 python; do
        if command -v "$__c" >/dev/null 2>&1; then command -v "$__c"; return 0; fi
    done
    return 1
}

_payload_hash() {
    # Cheap payload fingerprint for the completion marker (#935): sha256 of
    # pyproject.toml + the vendored-lib version set. Detects a dev-checkout that
    # changed the payload WITHOUT bumping the version. Empty on any error.
    local __parts=""
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then __parts="$(cat "$PLUGIN_DIR/pyproject.toml")"; fi
    if [[ -d "$PLUGIN_DIR/libs" ]]; then
        local __f
        while IFS= read -r __f; do
            __parts="$__parts"$'\n'"$(cat "$__f")"
        done < <(find "$PLUGIN_DIR/libs" -name pyproject.toml 2>/dev/null | sort)
    fi
    printf '%s' "$__parts" | sha256sum 2>/dev/null | awk '{print $1}' || true
}

_versioned_slot_clean() {
    # #935: ensure the target slot exists, tossing it first if a prior build left
    # it INCOMPLETE (no completion marker) so we never `uv venv --allow-existing`
    # over a corpse. The current/active slot is never tossed (the link-name is
    # derived from LINK_DIR so the current-slot guard works per plugin). No-op in
    # legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || return 0
    [[ -n "$py" ]] || return 0
    "$py" "$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" slot "$SRC_VERSION" --clean-incomplete 2>&1 | sed 's/^/  ...    /' || true
}

_versioned_mark_complete() {
    # #935: write the slot's completion marker AFTER its isolated health gate
    # passed, so "marker present" == "healthy, complete build". A crashed /
    # watchdog-killed install never reaches here, leaving its slot markerless and
    # thus tossable + retryable. No-op in legacy mode. Runs the stdlib-only
    # versioned_runtime.py via any bootstrap python (the marker is slot-scoped, so
    # this helper is portable byte-identically across plugins).
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || return 0
    [[ -n "$py" ]] || return 0
    local ph
    ph="$(_payload_hash)"
    local args=("$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" mark-complete "$SRC_VERSION")
    if [[ -n "$ph" ]]; then args+=(--payload-hash "$ph"); fi
    "$py" "${args[@]}" 2>&1 | sed 's/^/  ...    /' || true
}

# === install-contract:v4 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. #935: when the installer self-staged
# out of the marketplace payload, its live path is a throwaway stage dir, so
# infer the kind from the ORIGINAL payload path the self-stage prologue recorded
# in COPILOT_PLUGIN_STAGED_FROM (else the current path).
_source_kind() {
    case "$(printf '%s' "${COPILOT_PLUGIN_STAGED_FROM:-$1}" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v4 source-kind ===

# ── Machine detection ────────────────────────────────────────────────────

resolve_machine() {
    if [[ -n "$MACHINE" ]]; then
        echo "$MACHINE"
        return
    fi
    local hn
    hn="$(hostname | tr '[:upper:]' '[:lower:]')"
    # Use lowercase hostname as-is. If hostname differs from the desired
    # machine key, set MACHINE explicitly before running the installer.
    echo "$hn"
}

detect_platform() {
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
    else
        echo "linux"
    fi
}

# ── Projects registry ────────────────────────────────────────────────────

PROJECTS_YAML="$INSTALL_DIR/projects.yaml"

register_project() {
    # Thin wrapper: the projects.yaml write lives in ONE place -- the Python
    # `register-project-entry` subcommand (installer.register_project). Both
    # platform installers call it rather than reimplementing the registry logic.
    # Must be called after deploy_venv (requires the installed package).
    if [[ ! -x "$VENV_PYTHON" ]]; then
        skipped "Projects registry: venv not ready"
        return
    fi

    local platform
    platform="$(detect_platform)"
    # Positional project name (NOT --project: main() pre-pops a global
    # --project flag before argparse reaches this subcommand).
    local -a args=("$PROJECT_NAME")
    [[ -n "$REPO_DIR" ]] && args+=(--repo-dir "$REPO_DIR")
    if [[ "$platform" == "wsl" || "$platform" == "linux" ]]; then
        args+=(--wsl-state adopted)
        [[ -n "${WSL_DISTRO_NAME:-}" ]] && args+=(--wsl-distro "$WSL_DISTRO_NAME")
        [[ -n "$REPO_DIR" ]] && args+=(--wsl-path "$REPO_DIR")
    fi
    AGENT_WORKTREES_PAYLOAD_ROOT="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" \
        PYTHONPATH= "$VENV_PYTHON" -m agent_worktrees \
        register-project-entry "${args[@]}"
}

# ── Helpers ──────────────────────────────────────────────────────────────

deploy_package() {
    if [[ ! -f "$PLUGIN_DIR/pyproject.toml" ]]; then
        err "Plugin source not found: $PLUGIN_DIR"
        return 1
    fi
    if [[ ! -x "$VENV_PYTHON" ]]; then
        err "Venv Python missing -- create the venv first"
        return 1
    fi

    # Purge stale in-tree build artifacts before building. A setuptools build
    # writes ``build/lib/...`` (and ``*.egg-info``) into the source tree; on the
    # NEXT deploy an isolated build still copies the *whole* source dir --
    # including that stale ``build/lib`` -- and setuptools repackages the OLD
    # code from it instead of the fresh ``src/`` tree. Symptom: the venv reports
    # the new version yet imports old code (a version/code mismatch). Removing
    # these forces a clean rebuild from ``src/`` on every deploy. Covers the
    # plugin and its vendored libs.
    local _art
    for _art in \
        "$PLUGIN_DIR/build" \
        "$PLUGIN_DIR"/src/*.egg-info \
        "$PLUGIN_DIR"/libs/*/build \
        "$PLUGIN_DIR"/libs/*/src/*.egg-info; do
        [[ -e "$_art" ]] && rm -rf "$_art"
    done

    # Vendored config-schema-migration lib (agent-config-migrate / module
    # config_migrate). Install it first so the package's dependency is satisfied
    # from the local path on every deploy (install and update). It lives inside
    # the plugin folder, so the path is identical in the git-checkout and
    # marketplace layouts.
    local cfg_migrate_dir="$PLUGIN_DIR/libs/config-migrate"
    if [[ -f "$cfg_migrate_dir/pyproject.toml" ]]; then
        if ! uv pip install --python "$VENV_PYTHON" --reinstall-package agent-config-migrate \
                "$cfg_migrate_dir" --quiet; then
            err "config-migrate library install failed"
            return 1
        fi
    fi

    # Vendored plugin-resolution lib (agent-plugin-resolve / module
    # plugin_resolve). Install it first so the package's dependency is satisfied
    # from the local path (mirrors config-migrate above).
    local plugin_resolve_dir="$PLUGIN_DIR/libs/plugin-resolve"
    if [[ -f "$plugin_resolve_dir/pyproject.toml" ]]; then
        if ! uv pip install --python "$VENV_PYTHON" --reinstall-package agent-plugin-resolve \
                "$plugin_resolve_dir" --quiet; then
            err "plugin-resolve library install failed"
            return 1
        fi
    fi

    if ! uv pip install --python "$VENV_PYTHON" --reinstall-package agent-worktrees "$PLUGIN_DIR" --quiet; then
        err "Package install failed"
        return 1
    fi

    # Retire the legacy file-copy dir FIRST so a stale PYTHONPATH=.../lib cannot
    # make the probe resolve to the old copy (or shadow it at runtime).
    rm -rf "$LIB_DIR"

    # Stamp build info into the installed copy (PYTHONPATH cleared for the probe).
    local pkg_dir
    pkg_dir="$(PYTHONPATH= "$VENV_PYTHON" -c 'import agent_worktrees, os; print(os.path.dirname(agent_worktrees.__file__))' 2>/dev/null || true)"
    if [[ -n "$pkg_dir" ]]; then
        local _repo_root _commit _branch _ts _src_norm _ver
        _repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
        _commit="$(git -C "$_repo_root" rev-parse HEAD 2>/dev/null || echo unknown)"
        _branch="$(git -C "$_repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
        _ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        _src_norm="$(echo "$PLUGIN_DIR" | tr '\\' '/')"
        _ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
        cat > "$pkg_dir/_build_info.py" <<PYEOF
"""Build provenance -- auto-generated at deploy time. Do not edit."""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$_ver",
    "commit": "$_commit",
    "branch": "$_branch",
    "build_timestamp": "$_ts",
    "source": "$_src_norm",
}
PYEOF
    else
        warn "Could not locate installed agent_worktrees -- build info not stamped"
    fi

    ok "Package installed into venv"
}

deploy_venv() {
    # Create venv via uv (--allow-existing handles re-install). Deps come from
    # pyproject at package install time -- no ad-hoc pyyaml here.
    _versioned_slot_clean
    if ! uv venv "$VENV_DIR" --python 3.11 --allow-existing 2>/dev/null; then
        if ! uv venv "$VENV_DIR" --allow-existing 2>/dev/null; then
            err "Failed to create venv at $VENV_DIR"
            return 1
        fi
    fi
    ok "Venv created at $VENV_DIR"
}

# --- self-provisioning helpers (runtime-self-provisioning pattern) -----------
# These let the LIGHTWEIGHT, agent-bootstrappable half of agent-worktrees (the
# in-worktree tools -- worktree/branch/change/session mgmt, esp. PR ops) come up
# standalone in a confined host (Copilot app / cloud agent) where the full
# launcher install never ran. The session-launcher half is out of scope here (it
#   relocates to the Worktree Manager -- see the installer-configurator effort Phase 6).

# Vendor a standalone uv into the runtime tool dir when uv is absent (pristine or
# governed box) instead of dead-ending; add it to PATH for this run.
_ensure_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    local tooldir="$INSTALL_DIR/tool"
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; return 0; fi
    changed "uv not found -- vendoring a standalone uv into $tooldir"
    mkdir -p "$tooldir"
    local url="https://astral.sh/uv/install.sh" script="$tooldir/uv-install.sh" got=""
    if command -v curl >/dev/null 2>&1; then curl -LsSf "$url" -o "$script" 2>/dev/null && got=1; fi
    if [[ -z "$got" ]] && command -v wget >/dev/null 2>&1; then wget -qO "$script" "$url" 2>/dev/null && got=1; fi
    if [[ -z "$got" ]] && command -v python3 >/dev/null 2>&1; then
        python3 - "$url" "$script" <<'PY' 2>/dev/null && got=1
import sys, urllib.request
urllib.request.urlretrieve(sys.argv[1], sys.argv[2])
PY
    fi
    if [[ -n "$got" && -s "$script" ]]; then
        env UV_INSTALL_DIR="$tooldir" UV_UNMANAGED_INSTALL="$tooldir" INSTALLER_NO_MODIFY_PATH=1 sh "$script" >/dev/null 2>&1 || true
    fi
    [[ -x "$tooldir/bin/uv" && ! -x "$tooldir/uv" ]] && ln -sf "$tooldir/bin/uv" "$tooldir/uv" 2>/dev/null || true
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; ok "Vendored uv into $tooldir"; return 0; fi
    err "uv is required but not found, and vendoring failed (no reachable uv installer). Install uv, then retry."
    return 1
}

# Mirror pip's configured index to uv on a governed box (public PyPI TLS-blocked):
# uv does not read pip.conf, so derive index-url from pip config / the pip.conf
# files and export it. Preserve uv's own configured index; the pip-derived value
# is only a fallback. No-op where pip has no index (e.g. pristine -- the index
# then arrives via env / the clean-room fixture).
_ensure_uv_index() {
    [[ -n "${UV_INDEX_URL:-}${UV_DEFAULT_INDEX:-}" ]] && return 0
    local uv_config configured
    local -a uv_configs
    if [[ -n "${UV_CONFIG_FILE:-}" ]]; then
        uv_configs=("$UV_CONFIG_FILE")
    else
        uv_configs=("${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml" /etc/uv/uv.toml /etc/xdg/uv/uv.toml)
    fi
    for uv_config in "${uv_configs[@]}"; do
        [[ -n "$uv_config" && -f "$uv_config" ]] || continue
        configured="$(awk '
            /^[[:space:]]*index-url[[:space:]]*=/ { print 1; exit }
            /^[[:space:]]*\[\[index\]\][[:space:]]*(#.*)?$/ { in_index=1; next }
            /^[[:space:]]*\[/ { in_index=0 }
            in_index && /^[[:space:]]*default[[:space:]]*=[[:space:]]*true[[:space:]]*(#.*)?$/ { print 1; exit }
        ' "$uv_config")"
        if [[ "$configured" == "1" ]]; then
            return 0
        fi
    done
    local idx=""
    if command -v pip >/dev/null 2>&1; then idx="$(pip config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [[ -z "$idx" ]] && command -v pip3 >/dev/null 2>&1; then idx="$(pip3 config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [[ -z "$idx" ]]; then
        local f
        for f in "${PIP_CONFIG_FILE:-}" "$HOME/.config/pip/pip.conf" "$HOME/.pip/pip.conf" /etc/pip.conf /etc/xdg/pip/pip.conf; do
            [[ -n "$f" && -f "$f" ]] || continue
            idx="$(sed -n 's/^[[:space:]]*index-url[[:space:]]*=[[:space:]]*//p' "$f" | head -n1 | tr -d '[:space:]')"
            [[ -n "$idx" ]] && break
        done
    fi
    if [[ -n "$idx" ]]; then export UV_DEFAULT_INDEX="$idx"; changed "uv index derived from pip config (governed-feed bridge)"; fi
}

# Deploy ONLY the primary `agent-worktrees` TOOL binstub (the self-provisioning
# POSIX-sh resolver shim in bin/agent-worktrees + its .cmd/.ps1 parity stubs) into
# ~/.local/bin -- NOT the per-project launcher binstubs (those are a full-install /
# adopt concern). This is what makes the tools callable standalone: the full
# install only deploys this under $HAS_PROJECT, so a runtime-only / confined host
# would otherwise have no `agent-worktrees` on PATH.
deploy_tool_binstub() {
    mkdir -p "$LOCAL_BIN"
    local stub tmp
    for stub in agent-worktrees agent-worktrees.cmd agent-worktrees.ps1; do
        local stub_src="$PLUGIN_DIR/bin/$stub"
        if [[ -f "$stub_src" ]]; then
            tmp="$(mktemp "$LOCAL_BIN/$stub.XXXXXX")"
            cp "$stub_src" "$tmp"
            chmod +x "$tmp"
            mv -f "$tmp" "$LOCAL_BIN/$stub"
            ok "Tool binstub: $LOCAL_BIN/$stub"
        fi
    done
}

deploy_runtime_resolvers() {
    # Payload-local shims resolve only through these runtime-owned helpers.
    # Install them in the lean provision path as well as the full wrapper path,
    # using create-as-caller + atomic rename so ownership and mode are correct
    # even when replacing a stale helper.
    mkdir -p "$BIN_DIR"
    local resolver src tmp
    for resolver in resolve-runtime.ps1 resolve-runtime.sh; do
        src="$SCRIPT_DIR/$resolver"
        if [[ ! -f "$src" ]]; then
            err "Runtime resolver source not found: $src"
            return 1
        fi
        tmp="$(mktemp "$BIN_DIR/$resolver.XXXXXX")"
        cp "$src" "$tmp"
        chmod +x "$tmp"
        mv -f "$tmp" "$BIN_DIR/$resolver"
        ok "Runtime resolver: $resolver"
    done
}

deploy_wrappers() {
    mkdir -p "$BIN_DIR"
    local src="$PLUGIN_DIR/bin/launch-session.sh"
    if [[ ! -f "$src" ]]; then
        err "Wrapper source not found: $src"
        return 1
    fi
    # Atomic replace -- write to temp then mv, so a concurrent session
    # reading launch-session.sh isn't corrupted mid-write.
    local tmp
    tmp="$(mktemp "$BIN_DIR/launch-session.sh.XXXXXX")"
    cp "$src" "$tmp"
    chmod +x "$tmp"
    mv -f "$tmp" "$BIN_DIR/launch-session.sh"
    ok "Wrapper: launch-session.sh"

    # Deploy pane wrapper (handles exit codes inside tmux/psmux panes)
    local pane_src="$PLUGIN_DIR/bin/pane-wrapper.sh"
    if [[ -f "$pane_src" ]]; then
        tmp="$(mktemp "$BIN_DIR/pane-wrapper.sh.XXXXXX")"
        cp "$pane_src" "$tmp"
        chmod +x "$tmp"
        mv -f "$tmp" "$BIN_DIR/pane-wrapper.sh"
        ok "Wrapper: pane-wrapper.sh"
    fi

    deploy_runtime_resolvers || return 1

    # Deploy hook scripts, including the consolidated pre/post client and its fallback modules.
    for script in session-conduct.ps1 session-conduct.sh session-machine.ps1 session-machine.sh bootstrap-check.ps1 bootstrap-check.sh project-hooks.ps1 project-hooks.sh register-nudge.ps1 register-nudge.sh register-session.ps1 register-session.sh deregister-session.ps1 deregister-session.sh anchor-hygiene-check.ps1 anchor-hygiene-check.sh marketplace-overrides.ps1 marketplace-overrides.sh provision-check.ps1 provision-check.sh statelessness_guard.py cross_repo_guard.py anchor_write_guard.py nudge_status.py bind_nudge.py hook_client.py bind-nudge.sh bind-nudge.ps1; do
        local script_src="$SCRIPT_DIR/$script"
        if [[ -f "$script_src" ]]; then
            tmp="$(mktemp "$BIN_DIR/$script.XXXXXX")"
            cp "$script_src" "$tmp"
            chmod +x "$tmp"
            mv -f "$tmp" "$BIN_DIR/$script"
            ok "Hook: $script"
        fi
    done

    # Deploy the session-conduct data fragments (scripts/conduct/*.md) that the
    # session-conduct sessionStart hook emits as additionalContext, cwd-gated.
    # Replaces the per-project *.instructions.md deploy for these generic
    # fragments (dotfiles#1053 / effort instructions-to-hooks).
    if [[ -d "$SCRIPT_DIR/conduct" ]]; then
        mkdir -p "$BIN_DIR/conduct"
        for frag in "$SCRIPT_DIR/conduct"/*.md; do
            [[ -f "$frag" ]] || continue
            cp -f "$frag" "$BIN_DIR/conduct/$(basename "$frag")"
            ok "Conduct: $(basename "$frag")"
        done
    fi

    # Deploy normalized setup and optional machine-settings reconciliation.
    # Mirrors installer.py deploy_wrappers().
    mkdir -p "$INSTALL_DIR/scripts"
    for setup in \
        default-setup.ps1 \
        default-setup.sh \
        launch-command.ps1 \
        launch-command.sh \
        reconcile-machine-settings.ps1 \
        reconcile-machine-settings.sh; do
        local setup_src="$SCRIPT_DIR/$setup"
        if [[ -f "$setup_src" ]]; then
            tmp="$(mktemp "$INSTALL_DIR/scripts/$setup.XXXXXX")"
            cp "$setup_src" "$tmp"
            chmod +x "$tmp"
            mv -f "$tmp" "$INSTALL_DIR/scripts/$setup"
            ok "Session script: $setup"
        fi
    done
}

remove_legacy_scripts() {
    local removed=0
    for script in "${LEGACY_SCRIPTS[@]}"; do
        if [[ -f "$BIN_DIR/$script" ]]; then
            rm -f "$BIN_DIR/$script"
            ((removed++)) || true
        fi
    done
    if [[ $removed -gt 0 ]]; then
        changed "Removed $removed legacy script(s) from $BIN_DIR"
    fi
}

remove_legacy_binstubs() {
    # Sweep legacy alias binstubs from both runtime BIN_DIR and user LOCAL_BIN,
    # covering bare (bash), .cmd (Windows) and .ps1 variants.
    local removed=0
    for name in "${LEGACY_BINSTUBS[@]}"; do
        for dir in "$BIN_DIR" "$LOCAL_BIN"; do
            for f in "$dir/$name" "$dir/$name.cmd" "$dir/$name.ps1"; do
                if [[ -f "$f" ]]; then
                    rm -f "$f"
                    ((removed++)) || true
                fi
            done
        done
    done
    if [[ $removed -gt 0 ]]; then
        changed "Removed $removed legacy binstub(s)"
    fi
}

reconcile_binstubs() {
    # Reconcile project binstubs in ~/.local/bin against projects.yaml: deploy
    # for every registered project and remove signature-matched stubs for
    # deregistered ones. Delegates to the Python implementation (single,
    # cross-platform source of truth) so it runs regardless of project context.
    if [[ ! -x "$VENV_PYTHON" ]]; then
        return 0
    fi
    AGENT_WORKTREES_PAYLOAD_ROOT="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" \
        PYTHONUTF8=1 \
        "$VENV_PYTHON" -m agent_worktrees reconcile-binstubs 2>&1 |
        while IFS= read -r line; do echo "  $line"; done || \
        warn "Binstub reconciliation skipped"
}

deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    # Reserved-name guard (belt-and-suspenders with the PROJECT_NAME resolution
    # above): the project-form is written to $LOCAL_BIN/$PROJECT_NAME, which for
    # the reserved runtime name IS the global shim path. Never overwrite the
    # global `agent-worktrees` command with a self-`--project` project binstub.
    if [[ "$PROJECT_NAME" == "agent-worktrees" ]]; then
        warn "Refusing to deploy project binstub for reserved runtime name 'agent-worktrees' (global command owned by deploy_tool_binstub)"
        return 0
    fi
    # Generate project-specific binstub that routes through the Python CLI.
    # The CLI dispatches: no args → launch session, known subcommand → handler.
    # Falls back to launch-session.sh if venv is missing (recovery path).
    local tmp
    tmp="$(mktemp "$LOCAL_BIN/$PROJECT_NAME.XXXXXX")"
    cat > "$tmp" <<'BINSTUB_HEAD'
#!/usr/bin/env bash
# agent-worktrees project binstub
BINSTUB_HEAD
    cat >> "$tmp" <<BINSTUB_BODY
export PYTHONUTF8=1
export AGENT_WORKTREES_LAUNCH_ID="$PROJECT_NAME-\$\$-\$RANDOM-\$(date +%s)"
export AGENT_WORKTREES_BINSTUB_STARTED="\$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export AGENT_WORKTREES_LAUNCH_TRACE="\$HOME/.agent-worktrees/logs/picker-launches.jsonl"
mkdir -p "\$(dirname "\$AGENT_WORKTREES_LAUNCH_TRACE")" 2>/dev/null || true
printf '%s\n' '{"event":"binstub_start","timestamp":"'"\$AGENT_WORKTREES_BINSTUB_STARTED"'","launch_id":"'"\$AGENT_WORKTREES_LAUNCH_ID"'","project":"$PROJECT_NAME"}' >>"\$AGENT_WORKTREES_LAUNCH_TRACE" 2>/dev/null || true
if [[ \$# -eq 0 ]]; then
    exec "\$HOME/.agent-worktrees/bin/launch-session.sh" --project "$PROJECT_NAME"
fi
# Context resolves from CWD / --project (git-like); the binstub names its
# project via --project, not an ambient env var.
# Resolve the active versioned runtime directly (the .venv junction is retired
# -- #637/#1085/#1106); NEVER exec this binstub itself, which would recurse
# into an unbounded process storm.
_root="\$HOME/.agent-worktrees"
AW_PY=""
[[ -f "\$_root/bin/resolve-runtime.sh" ]] && source "\$_root/bin/resolve-runtime.sh"
_py="\$AW_PY"
if [[ -n "\$_py" && -x "\$_py" ]]; then
    exec "\$_py" -m agent_worktrees --project "$PROJECT_NAME" "\$@"
fi
# Recovery (venv missing): preserve explicit project identity.
exec "\$HOME/.agent-worktrees/bin/launch-session.sh" --project "$PROJECT_NAME" "\$@"
BINSTUB_BODY
    chmod +x "$tmp"
    mv -f "$tmp" "$LOCAL_BIN/$PROJECT_NAME"
    ok "Binstub: $LOCAL_BIN/$PROJECT_NAME"

    # The `agent-worktrees` tool binstub is deployed unconditionally by
    # deploy_tool_binstub in the install/update actions (project or not), so it
    # is not re-deployed here.
}

deploy_global_config() {
    # Scaffold the global machine-wide config (~/.agent-worktrees/config.yaml),
    # the user-owned base tier. Created once when missing, then NEVER
    # overwritten -- not even with --force (which targets installer-owned
    # artifacts). Only a deliberate schema migration should rewrite it.
    local machine="$1"
    local platform="$2"
    local global_path="$INSTALL_DIR/config.yaml"

    if [[ -f "$global_path" ]]; then
        skipped "Global config exists at $global_path (user-owned, left as-is)"
        return 0
    fi
    local src_root=""
    [[ -n "$REPO_DIR" ]] && src_root="$(dirname "$REPO_DIR")"

    cat > "$global_path" <<EOF
# ~/.agent-worktrees/config.yaml
# GLOBAL machine-wide agent-worktrees config (lowest precedence tier).
#
# Machine-wide defaults shared across every project on this machine. Per-repo
# settings layer on top: <anchor>/.agent-worktrees/config.yaml (the repo's own
# config) then ~/.<project>/config.yaml (machine-local override).

srcroot: $src_root
machine: $machine
platform: $platform

# Copilot backend profiles -- machine-wide (Tab to cycle in the picker).
# User-authored; uncomment and edit. Example:
# copilot_profiles:
#   - name: cloud
#     label: "Cloud (GitHub)"
EOF
    changed "Written global config: $global_path"
    return 0
}

deploy_config() {
    local machine="$1"
    local platform="$2"
    local config_path="$PROJECT_DIR/config.yaml"

    # Global machine-wide config first (lowest tier).
    deploy_global_config "$machine" "$platform"

    if [[ -f "$config_path" ]] && ! $FORCE; then
        skipped "Config exists at $config_path (use --force to overwrite)"
        return 1
    fi

    if [[ -z "$REPO_DIR" ]]; then
        skipped "Config generation skipped (no repo detected -- create config.yaml manually)"
        return 1
    fi

    local worktree_root="$REPO_DIR.worktrees"

    cat > "$config_path" <<EOF
# ~/.$PROJECT_NAME/config.yaml
# Machine-local config for $PROJECT_NAME (overrides + machine paths only).
# Machine-wide defaults -> ~/.agent-worktrees/config.yaml.
# Repo settings may live in-repo -> <anchor>/.agent-worktrees/config.yaml.

repo_name: $PROJECT_NAME

repos:
  $PROJECT_NAME:
    anchor: $REPO_DIR
    # worktree_root defaults to $worktree_root -- a sibling
    # <anchor>.worktrees dir, matching Copilot CLI's /worktree layout.
    # Uncomment and set an absolute path to override.
    default_branch: master
    remote: origin
EOF
    changed "Written config: $config_path"
    return 0
}

write_deploy_manifest() {
    local manifest_path="$INSTALL_DIR/deploy-manifest.json"
    local machine platform kind ver commit branch dirty stable_plugin
    machine="$(resolve_machine)"
    platform="$(detect_platform)"
    stable_plugin="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
    kind="$(_source_kind "$stable_plugin")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"

    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root c b
        repo_root="$(cd "$stable_plugin/../.." && pwd)"
        c="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo unknown)"
        b="$(git -C "$repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
        commit="\"$c\""; branch="\"$b\""
        [[ -n "$(git -C "$repo_root" status --porcelain -- plugins/agent-worktrees/ 2>/dev/null)" ]] && dirty="true"
    fi

    local tmp="$manifest_path.tmp"
    cat > "$tmp" <<EOF
{
  "schema_version": 3,
  "service": "agent-worktrees",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deployed_by": "${machine}-${platform}",
  "source": {
    "kind": "$kind",
    "path": "$stable_plugin",
    "repo": "copilot-extensions",
    "plugin": "agent-worktrees",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$LINK_DIR",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest_path"
    ok "Deploy manifest written (source: $kind)"
}

show_deploy_status() {
    local manifest_path="$INSTALL_DIR/deploy-manifest.json"
    if [[ ! -f "$manifest_path" ]]; then
        skipped "No deploy manifest (deploy with updated installer to create one)"
        return
    fi

    local commit branch deployed_at is_dirty
    commit="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('commit','unknown')[:10] if m.get('commit') else 'unknown')")"
    branch="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('branch','unknown') or 'unknown')")"
    deployed_at="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('deployed_at','unknown'))")"
    is_dirty="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(str(m.get('dirty',False)).lower())")"

    if [[ "$is_dirty" == "true" ]]; then
        changed "Deployed from $branch @ $commit (DIRTY)"
    else
        ok "Deployed from $branch @ $commit"
    fi
    ok "Deployed at $deployed_at"

    # Staleness check
    local deployed_commit
    deployed_commit="$(python3 -c "import json; m=json.load(open('$manifest_path')); print(m.get('commit','') or '')")"
    if [[ -n "$deployed_commit" && -n "$REPO_DIR" ]]; then
        local stale_count
        stale_count="$(git -C "$REPO_DIR" log --oneline "$deployed_commit..HEAD" -- "${DEPLOY_SOURCE_PATHS[@]}" 2>/dev/null | wc -l)" || stale_count=0
        if [[ "$stale_count" -eq 0 ]]; then
            ok "Up to date (no source changes since deploy)"
        else
            changed "Stale -- $stale_count commit(s) behind HEAD"
        fi
    fi
}

deploy_tabby_profile() {
    local platform="$1"
    local machine="${2:-}"
    local tabby_template="$PLUGIN_DIR/terminal/tabby-template.yaml"
    local machines_yaml="${REPO_DIR:+$REPO_DIR/machines.yaml}"
    local tabby_config="$HOME/.config/tabby/config.yaml"

    # Skip on WSL -- Tabby is a native Linux desktop app
    if [[ "$platform" == "wsl" ]]; then
        skipped "Tabby profile: skipped on WSL"
        return
    fi

    # Skip if Tabby config dir doesn't exist (not installed / never launched)
    if [[ ! -d "$HOME/.config/tabby" ]]; then
        skipped "Tabby profile: ~/.config/tabby not found (Tabby not installed?)"
        return
    fi

    if [[ ! -f "$tabby_template" ]]; then
        err "Tabby template not found: $tabby_template"
        return 1
    fi

    # Warn if Tabby is running -- it overwrites config.yaml from memory on exit
    if pgrep -x tabby >/dev/null 2>&1; then
        echo "  ⚠ Tabby is running -- close it before updating, or changes will be overwritten"
    fi

    "$VENV_PYTHON" -c "
import sys, yaml, copy
from pathlib import Path

template_path = sys.argv[1]
config_path = sys.argv[2]
machines_path = sys.argv[3]
self_machine = sys.argv[4] if len(sys.argv) > 4 else ''
project_name = sys.argv[5] if len(sys.argv) > 5 else 'my-project'

template = yaml.safe_load(Path(template_path).read_text())
local_profile = template['profile']
scheme = template['colorScheme']

# Substitute project placeholders in the local profile
display_name = ' '.join(w.capitalize() for w in project_name.split('-'))
def _sub(obj):
    if isinstance(obj, str):
        return obj.replace('__PROJECT__', project_name).replace('__PROJECT_TITLE__', display_name)
    if isinstance(obj, dict):
        return {k: _sub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sub(v) for v in obj]
    return obj
local_profile = _sub(local_profile)

# Load existing config or create minimal structure
if Path(config_path).exists():
    try:
        config = yaml.safe_load(Path(config_path).read_text()) or {}
    except yaml.YAMLError:
        print('  ⚠ Tabby config is malformed -- skipping profile merge', file=sys.stderr)
        sys.exit(0)
else:
    config = {}

changed = False
profiles = config.setdefault('profiles', [])

def upsert_profile(prof):
    \"\"\"Insert or update a profile by id, returning True if changed.\"\"\"
    idx = next((i for i, p in enumerate(profiles) if p.get('id') == prof['id']), None)
    if idx is not None:
        if profiles[idx] != prof:
            profiles[idx] = prof
            return True
    else:
        profiles.append(prof)
        return True
    return False

# Local project profile (insert at front)
existing_idx = next((i for i, p in enumerate(profiles) if p.get('id') == local_profile['id']), None)
if existing_idx is not None:
    if profiles[existing_idx] != local_profile:
        profiles[existing_idx] = local_profile
        changed = True
else:
    profiles.insert(0, local_profile)
    changed = True

# Generate remote SSH profiles from machines.yaml
env_labels = {'windows': 'Windows', 'wsl': 'WSL', 'linux': 'Linux'}

if Path(machines_path).exists():
    try:
        machines_data = yaml.safe_load(Path(machines_path).read_text()) or {}
    except yaml.YAMLError:
        machines_data = {}

    for key, entry in (machines_data.get('machines') or {}).items():
        if key == self_machine:
            continue
        ssh = entry.get('ssh') or {}
        if not ssh.get('ready', False):
            continue
        display_name = entry.get('display_name', key)

        for env in ssh.get('environments', []):
            alias = env.get('alias', '')
            env_name = env.get('name', '')
            env_label = env_labels.get(env_name, env_name)
            shell = env.get('shell', 'bash')

            # Plain SSH profile
            ssh_id = f'ssh:{key}-{env_name}'
            ssh_profile = {
                'id': ssh_id,
                'type': 'local',
                'name': f'{display_name} ({env_label})',
                'icon': 'fa-terminal',
                'color': '#F6A821',
                'isBuiltin': False,
                'options': {
                    'command': 'ssh',
                    'args': [alias],
                    'cwd': '',
                    'env': {},
                },
            }
            if upsert_profile(ssh_profile):
                changed = True

            # Project launcher profile -- SSH + run binstub
            binstub = f'{project_name}.cmd' if shell == 'pwsh' else project_name
            launcher_id = f'{project_name}:{key}-{env_name}'
            launcher_label = display_name if env_label == 'Linux' else f'{display_name} {env_label}'
            # Title-case the project name for display
            display_project = project_name.replace('-', ' ').title()
            launcher_profile = {
                'id': launcher_id,
                'type': 'local',
                'name': f'{display_project} ({launcher_label})',
                'icon': 'fa-flask',
                'color': '#F6A821',
                'isBuiltin': False,
                'options': {
                    'command': 'ssh',
                    'args': ['-t', alias, binstub],
                    'cwd': '',
                    'env': {},
                },
            }
            if upsert_profile(launcher_profile):
                changed = True

# Set global color scheme to Aperture Science
terminal = config.setdefault('terminal', {})
current_scheme = terminal.get('colorScheme', {})
if current_scheme.get('name') != scheme['name'] or current_scheme.get('foreground') != scheme['foreground']:
    terminal['colorScheme'] = scheme
    changed = True

if changed:
    Path(config_path).write_text(yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True))
    print('changed')
else:
    print('ok')
" "$tabby_template" "$tabby_config" "$machines_yaml" "$machine" "$PROJECT_NAME"

    local result=$?
    if [[ $result -ne 0 ]]; then
        err "Tabby profile merge failed"
        return 1
    fi

    # Verify: check local profile + color scheme + SSH profiles
    local status
    status=$("$VENV_PYTHON" -c "
import sys, yaml
from pathlib import Path

project_name = sys.argv[2]
local_id = f'local:{project_name}'

config = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
profiles = config.get('profiles', [])
ids = {p.get('id', '') for p in profiles}
has_local = local_id in ids
has_ssh = any(pid.startswith('ssh:') for pid in ids)
scheme_name = config.get('terminal', {}).get('colorScheme', {}).get('name', '')
if has_local and scheme_name == 'Aperture Science':
    if has_ssh:
        print('ok_with_ssh')
    else:
        print('ok_local_only')
else:
    print('err')
" "$tabby_config" "$PROJECT_NAME")

    local display_project
    display_project="$(echo "$PROJECT_NAME" | tr '-' ' ' | sed 's/\b\(.\)/\u\1/g')"

    case "$status" in
        ok_with_ssh)
            ok "Tabby profile: $display_project + remote SSH profiles"
            ;;
        ok_local_only)
            ok "Tabby profile: $display_project (no remote SSH profiles generated)"
            ;;
        *)
            err "Tabby profile merge verification failed"
            ;;
    esac
}

remove_tabby_profile() {
    local tabby_config="$HOME/.config/tabby/config.yaml"

    if [[ ! -f "$tabby_config" ]]; then
        return
    fi

    "$VENV_PYTHON" -c "
import sys, yaml
from pathlib import Path

config_path = sys.argv[1]
project_name = sys.argv[2]
config = yaml.safe_load(Path(config_path).read_text()) or {}

profiles = config.get('profiles', [])
original_len = len(profiles)
local_id = f'local:{project_name}'
launcher_prefix = f'{project_name}:'
# Remove local profile, SSH profiles, and project launcher profiles
config['profiles'] = [
    p for p in profiles
    if p.get('id') != local_id
    and not (p.get('id', '').startswith('ssh:'))
    and not (p.get('id', '').startswith(launcher_prefix))
]

if len(config['profiles']) < original_len:
    Path(config_path).write_text(yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True))
    print('removed')
else:
    print('absent')
" "$tabby_config" "$PROJECT_NAME"

    local result
    result=$?
    if [[ $result -eq 0 ]]; then
        changed "Removed Tabby $PROJECT_NAME profiles (local + SSH)"
    fi
}

check_tabby_profile() {
    local tabby_config="$HOME/.config/tabby/config.yaml"

    if [[ ! -f "$tabby_config" ]]; then
        skipped "Tabby: not installed or never launched"
        return
    fi

    local status
    status=$("$VENV_PYTHON" -c "
import sys, yaml
from pathlib import Path

project_name = sys.argv[2]
local_id = f'local:{project_name}'
launcher_prefix = f'{project_name}:'

config = yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
profiles = config.get('profiles', [])
ids = {p.get('id', '') for p in profiles}
has_local = local_id in ids
has_ssh = any(pid.startswith('ssh:') for pid in ids)
has_launchers = any(pid.startswith(launcher_prefix) for pid in ids)
ssh_count = sum(1 for pid in ids if pid.startswith('ssh:'))
launcher_count = sum(1 for pid in ids if pid.startswith(launcher_prefix))
scheme_name = config.get('terminal', {}).get('colorScheme', {}).get('name', '')

if has_local and scheme_name == 'Aperture Science':
    if has_ssh and has_launchers:
        print(f'ok:{ssh_count}:{launcher_count}')
    elif has_ssh:
        print(f'ssh_only:{ssh_count}')
    else:
        print('local_only')
elif has_local:
    print('profile_only')
else:
    print('missing')
" "$tabby_config" "$PROJECT_NAME" 2>/dev/null)

    local display_project
    display_project="$(echo "$PROJECT_NAME" | tr '-' ' ' | sed 's/\b\(.\)/\u\1/g')"

    case "$status" in
        ok:*)
            local ssh_n launcher_n
            ssh_n="$(echo "$status" | cut -d: -f2)"
            launcher_n="$(echo "$status" | cut -d: -f3)"
            ok "Tabby: $display_project + ${ssh_n} SSH + ${launcher_n} launcher profiles"
            ;;
        ssh_only:*)
            local ssh_n
            ssh_n="$(echo "$status" | cut -d: -f2)"
            changed "Tabby: $display_project + ${ssh_n} SSH profiles (no launchers)"
            ;;
        local_only)
            changed "Tabby: $display_project local only (no remote SSH profiles)"
            ;;
        profile_only)
            changed "Tabby: $display_project present, but color scheme differs"
            ;;
        missing)
            err "Tabby profile: $display_project not found"
            ;;
        *)
            skipped "Tabby: could not check profile"
            ;;
    esac
}

deploy_git_hooks_path() {
    if [[ -z "$REPO_DIR" ]]; then return; fi
    local current
    current="$(git -C "$REPO_DIR" config --local core.hooksPath 2>/dev/null)" || true
    if [[ "$current" == "tools/hooks" ]]; then
        ok "Git hooksPath = tools/hooks"
        return
    fi
    if [[ -n "$current" ]]; then
        echo "  ⚠ Git core.hooksPath already set to '$current' -- not overwriting" >&2
        echo "    To update manually: git -C $REPO_DIR config --local core.hooksPath tools/hooks" >&2
        return
    fi
    git -C "$REPO_DIR" config --local core.hooksPath tools/hooks
    changed "Set git core.hooksPath = tools/hooks"
}

deploy_terminal_scripts() {
    # Deploy the terminal-integration scripts to BIN_DIR. agent-worktrees no
    # longer owns ~/.tmux.conf: the launcher applies the status bar + behaviors
    # per-session from session-options.sh, and apply-mux-keybinds.sh is an
    # opt-in server-global tuning script the user (or a restore flow) may run.
    local src_dir="$PLUGIN_DIR/terminal"
    local script src tmp
    for script in session-options.sh apply-mux-keybinds.sh; do
        src="$src_dir/$script"
        if [[ ! -f "$src" ]]; then
            echo "  ⚠ terminal script not found at $src" >&2
            continue
        fi
        tmp="$(mktemp "$BIN_DIR/$script.XXXXXX")"
        cp "$src" "$tmp"
        chmod +x "$tmp"
        mv -f "$tmp" "$BIN_DIR/$script"
        ok "Terminal script: $script"
    done
}

resolve_executable_command_path() {
    # Resolve only an executable file from PATH. Bash's `command -v` may return
    # an alias or function, neither of which a Python subprocess can execute.
    local command_name="$1"
    local resolved
    resolved="$(type -P -- "$command_name" 2>/dev/null || true)"
    if [[ -z "$resolved" || ! -f "$resolved" || ! -x "$resolved" ]]; then
        return 1
    fi
    if [[ "$resolved" != /* ]]; then
        resolved="$(cd "$(dirname "$resolved")" && pwd -P)/$(basename "$resolved")"
    fi
    printf '%s\n' "$resolved"
}

deploy_copilot_plugin() {
    # Install agent-worktrees from the copilot-extensions marketplace.
    # Ensures the marketplace is registered, installs or updates the plugin,
    # then removes any stale _direct install.
    #
    # When running from inside the installed-plugins directory (i.e.
    # invoked by cmd_update after it already ran 'copilot plugin update'),
    # skip the update call to avoid replacing files under our own feet.

    local copilot_path
    if ! copilot_path="$(resolve_executable_command_path copilot)"; then
        if command -v copilot >/dev/null 2>&1; then
            err "Copilot CLI resolves only to a non-executable shell command; an executable PATH command is required" >&2
            return 1
        fi
        warn "Copilot CLI not found - skipping plugin install"
        return
    fi

    # Detect if we are running from the installed plugin directory
    local installed_plugins_dir="$HOME/.copilot/installed-plugins"
    local running_from_installed=false
    case "$PLUGIN_DIR" in
        "$installed_plugins_dir"*) running_from_installed=true ;;
    esac

    # 1. Register marketplace if not present
    if ! "$copilot_path" plugin marketplace list 2>/dev/null | grep -q 'copilot-extensions'; then
        local add_out
        add_out=$("$copilot_path" plugin marketplace add ThomasMichon/copilot-extensions 2>&1) || {
            warn "Failed to register marketplace: $add_out"
            return
        }
        changed "Registered copilot-extensions marketplace"
    fi

    # 2. Parse current plugin state
    local plugin_list has_marketplace=false has_direct=false
    plugin_list=$("$copilot_path" plugin list 2>/dev/null)
    if echo "$plugin_list" | grep -q 'agent-worktrees@copilot-extensions'; then
        has_marketplace=true
    fi
    if echo "$plugin_list" | grep 'agent-worktrees' | grep -qv '@'; then
        has_direct=true
    fi

    # 3. Install or update marketplace plugin
    local out
    if $running_from_installed; then
        ok "Copilot plugin updated (marketplace)"
    elif $has_marketplace; then
        out=$("$copilot_path" plugin update agent-worktrees@copilot-extensions 2>&1) || {
            warn "Plugin update failed: $out"
        }
        ok "Copilot plugin updated (marketplace)"
    else
        out=$("$VENV_PYTHON" -m agent_worktrees.activation_preservation \
            agent-worktrees@copilot-extensions \
            --copilot "$copilot_path" 2>&1) || {
            warn "Plugin install failed: $out"
            return
        }
        changed "Copilot plugin installed (agent-worktrees@copilot-extensions)"
    fi

    # 4. Remove stale _direct install if marketplace is now present
    if $has_direct; then
        if "$copilot_path" plugin list 2>/dev/null | grep -q 'agent-worktrees@copilot-extensions'; then
            "$copilot_path" plugin uninstall agent-worktrees >/dev/null 2>&1 || true
            changed "Removed stale _direct plugin install"
        fi
    fi
}

assert_path() {
    case ":$PATH:" in
        *":$LOCAL_BIN:"*) ok "$LOCAL_BIN is on PATH" ;;
        *)
            err "$LOCAL_BIN is not on PATH"
            echo "    Add to ~/.bashrc: export PATH=\"\$HOME/.local/bin:\$PATH\""
            ;;
    esac
}

ensure_copilot_experimental() {
    # Ensure experimental: true in Copilot CLI settings.json.
    # The CLI gates extension loading on this flag -- COPILOT_FEATURE_FLAGS
    # alone is not sufficient. Both are required.
    local settings_file="$HOME/.copilot/settings.json"
    [[ -f "$settings_file" ]] || return 0

    if command -v python3 >/dev/null 2>&1; then
        local result
        result=$(python3 -c "
import json, sys
try:
    with open('$settings_file') as f:
        d = json.load(f)
    if d.get('experimental', False):
        print('already_on')
        sys.exit(0)
    d['experimental'] = True
    with open('$settings_file', 'w') as f:
        json.dump(d, f, indent=2)
        f.write('\n')
    print('updated')
except Exception as e:
    print(f'error: {e}', file=sys.stderr)
    print('error')
" 2>/dev/null) || result="error"
        case "$result" in
            already_on) ok "Copilot experimental mode enabled" ;;
            updated)    changed "Copilot experimental mode enabled (required for extensions)" ;;
            *)          warn "Could not update $settings_file" ;;
        esac
    fi
}

# ── Actions ──────────────────────────────────────────────────────────────

case "$ACTION" in
    stamp)
        # Cheap 'stamp' (runtime-self-provisioning pattern): splat the
        # self-provisioning tool binstub + record the payload dir, deferring the
        # venv build to the binstub's first use. No venv, no uv -- fits a
        # sessionStart hook's grace window. Only the TOOLS half; no launcher.
        header "Stamping $SERVICE_NAME (defer runtime to first use)"
        mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
        printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
        deploy_tool_binstub
        ok "Stamped: agent-worktrees tool binstub on PATH; runtime provisions on first use."
        ;;

    provision)
        # Lean runtime build (runtime-self-provisioning pattern): acquire uv,
        # build+activate the versioned venv slot (writes the `current-version`
        # marker the bin/agent-worktrees shim resolves), and ensure the tool
        # binstub is present. Deliberately does NOT deploy wrappers, hooks,
        # guards, terminal integration, the copilot plugin, or reconcile project
        # binstubs -- those belong to the full launcher `install`, which is out of
        # scope for the confined-host tools half. Invoked by the binstub on first
        # use (see bin/agent-worktrees).
        header "Provisioning $SERVICE_NAME runtime (lean; tools only, no launcher/hooks)"
        command -v git >/dev/null 2>&1 || { err "Missing prerequisite: git"; exit 1; }
        _ensure_uv || exit 1
        _ensure_uv_index
        mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$LOCAL_BIN"
        deploy_runtime_resolvers || exit 1
        deploy_venv || exit 1
        deploy_package || exit 1
        _versioned_activate || exit 1
        deploy_tool_binstub
        write_deploy_manifest
        ok "Runtime provisioned (marker -> versions/$SRC_VERSION); agent-worktrees tools ready."
        ;;

    install)
        header "Installing $SERVICE_NAME"

        machine="$(resolve_machine)"
        platform="$(detect_platform)"
        echo "  Machine:  $machine"
        echo "  Platform: $platform"
        if $HAS_PROJECT; then
            echo "  Project:  $PROJECT_NAME"
            if [[ -n "$REPO_DIR" ]]; then
                echo "  Repo:     $REPO_DIR"
            fi
        else
            echo "  Project:  (none - runtime only; pass --project-name to adopt a repo)"
        fi

        # Prereq checks
        missing_prereqs=()
        command -v git >/dev/null 2>&1 || missing_prereqs+=("git")
        command -v uv >/dev/null 2>&1 || missing_prereqs+=("uv")
        if [[ ${#missing_prereqs[@]} -gt 0 ]]; then
            err "Missing prerequisites: ${missing_prereqs[*]}"
            exit 1
        fi

        # Create only installation-local paths for a structured context.
        if $CONTEXTUAL_INSTALL; then
            mkdir -p "$INSTALL_DIR" "$BIN_DIR"
        else
            mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$LOCAL_BIN"
            if $HAS_PROJECT; then
                mkdir -p "$PROJECT_DIR" "$WORKTREES_DIR"
            fi
        fi

        # -- Shared runtime (venv first: package install targets the venv) --
        deploy_venv || exit 1
        deploy_package || exit 1
        deploy_wrappers || exit 1
        _versioned_activate || exit 1
        if $CONTEXTUAL_INSTALL; then
            remove_legacy_scripts
            write_deploy_manifest
            ok "Context runtime installed at $INSTALL_DIR"
            exit 0
        fi
        remove_legacy_scripts
        remove_legacy_binstubs
        reconcile_binstubs
        # Deploy the self-provisioning `agent-worktrees` TOOL binstub
        # unconditionally (project or not). A bare / runtime-only `install`
        # (no --project-name, e.g. the Worktree Manager core-install path) must
        # still leave `agent-worktrees` callable on PATH; the per-project
        # deploy_binstub below only runs under $HAS_PROJECT, so relying on it
        # left a project-less install with no tool binstub at all.
        deploy_tool_binstub
        deploy_copilot_plugin
        ensure_copilot_experimental
        assert_path
        # Machine-wide terminal integration: deploy the per-session options +
        # opt-in keybind scripts. We do NOT touch ~/.tmux.conf -- the launcher
        # applies the status bar per-session at runtime.
        deploy_terminal_scripts

        # -- Project-specific (only when adopting) --
        if $HAS_PROJECT; then
            deploy_config "$machine" "$platform" || true
            register_project
            reconcile_binstubs
            deploy_tabby_profile "$platform" "$machine"
            deploy_git_hooks_path

            if [[ -n "$REPO_DIR" ]]; then
                PYTHONUTF8=1 \
                    "$VENV_PYTHON" -m agent_worktrees --project "$PROJECT_NAME" deploy-instructions --machine "$machine" 2>&1 \
                    | sed 's/^/  /' || warn "Instruction file deployment skipped"
            fi
        fi

        # Machine-local config schema migration (idempotent + atomic; never
        # touches repo-committed config -- that is an adopt concern). Stamps or
        # upgrades ~/.agent-worktrees/{config,repos,projects}.yaml. Non-fatal.
        PYTHONUTF8=1 "$VENV_PYTHON" -m agent_worktrees config-migrate 2>&1 \
            | sed 's/^/  /' || warn "Config migration skipped"

        write_deploy_manifest

        echo ""
        ok "Installation complete"
        echo "  Runtime dir: $INSTALL_DIR"
        if $HAS_PROJECT; then
            echo "  Project dir: $PROJECT_DIR"
            echo "  Usage:       $PROJECT_NAME"
        fi
        echo "  Runtime:     Python ($VENV_PYTHON)"
        ;;

    uninstall)
        header "Uninstalling $SERVICE_NAME"

        # Remove Tabby profile (before venv removal -- needs Python)
        if $HAS_PROJECT; then
            remove_tabby_profile
        fi

        # Remove project binstub
        if $HAS_PROJECT && [[ -x "$VENV_PYTHON" ]]; then
            AGENT_WORKTREES_PAYLOAD_ROOT="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" \
                PYTHONUTF8=1 "$VENV_PYTHON" -m agent_worktrees \
                reconcile-binstubs --remove "$PROJECT_NAME" 2>&1 |
                while IFS= read -r line; do echo "  $line"; done || \
                warn "Project binstub preserved (ownership check failed)"
        fi

        # Remove tool binstubs
        for stub in mark-session-complete agent-worktrees; do
            local stub_path="$LOCAL_BIN/$stub"
            if [[ -f "$stub_path" ]]; then
                rm -f "$stub_path"
                changed "Removed binstub: $stub_path"
            fi
        done

        # Sweep any lingering legacy alias binstubs
        remove_legacy_binstubs

        # Remove Python runtime (venv + package). Versioned: the `.venv` link +
        # the versions/ tree; otherwise the single real venv dir.
        if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
            [[ -L "$LINK_DIR" ]] && rm -f "$LINK_DIR"
            [[ -d "$LINK_DIR" && ! -L "$LINK_DIR" ]] && rm -rf "$LINK_DIR"
            [[ -d "$INSTALL_DIR/versions" ]] && rm -rf "$INSTALL_DIR/versions"
            changed "Removed versioned venv (.venv link + versions/)"
        elif [[ -d "$VENV_DIR" ]]; then
            rm -rf "$VENV_DIR"
            changed "Removed venv: $VENV_DIR"
        fi
        if [[ -d "$LIB_DIR" ]]; then
            rm -rf "$LIB_DIR"
            changed "Removed package: $LIB_DIR"
        fi

        # Remove wrappers + terminal-integration scripts
        for wrapper in launch-session.cmd launch-session.sh pane-wrapper.sh \
                       session-options.sh apply-mux-keybinds.sh; do
            rm -f "$BIN_DIR/$wrapper"
        done
        remove_legacy_scripts
        changed "Removed wrappers from $BIN_DIR"

        # ~/.tmux.conf is intentionally left alone: agent-worktrees no longer
        # owns it (sessions are configured per-session at launch), so uninstall
        # must not delete a file that may now be the user's own.
        if [[ -f "$HOME/.tmux.conf" ]]; then
            skipped "Left ~/.tmux.conf in place (no longer managed by agent-worktrees)"
        fi

        if $REMOVE_CONFIG; then
            if $HAS_PROJECT; then
                rm -rf "$PROJECT_DIR"
                changed "Removed project dir $PROJECT_DIR (config + session metadata)"
            fi
            rm -rf "$INSTALL_DIR"
            changed "Removed runtime dir $INSTALL_DIR"
        else
            rm -f "$INSTALL_DIR/deploy-manifest.json"
            if $HAS_PROJECT; then
                skipped "Config and session metadata preserved at $PROJECT_DIR"
            fi
            echo "    Use --remove-config to delete everything"
        fi

        ok "Uninstall complete"
        ;;

    start)
        header "Starting $SERVICE_NAME"
        skipped "Not a daemon -- invoke with: agent-worktrees"
        ;;

    stop)
        header "Stopping $SERVICE_NAME"
        skipped "Not a daemon -- Ctrl+C or close the terminal to end a session"
        ;;

    status)
        header "$SERVICE_NAME Status"

        # Venv
        if [[ -x "$VENV_PYTHON" ]]; then
            ok "Venv Python: $VENV_PYTHON"
        else
            err "Venv Python missing: $VENV_PYTHON"
        fi

        # Package (installed in the venv)
        if PYTHONPATH= "$VENV_PYTHON" -c 'import agent_worktrees' 2>/dev/null; then
            ok "Package importable in venv"
        else
            err "Package not importable in venv"
        fi

        # Wrapper
        if [[ -f "$BIN_DIR/launch-session.sh" ]]; then
            ok "launch-session.sh deployed"
        else
            err "launch-session.sh missing"
        fi

        # Tool binstubs
        for stub in agent-worktrees; do
            if [[ -f "$LOCAL_BIN/$stub" ]]; then
                ok "Binstub installed at $LOCAL_BIN/$stub"
            else
                err "Binstub missing at $LOCAL_BIN/$stub"
            fi
        done

        if $HAS_PROJECT; then
            # Project binstub
            if [[ -f "$LOCAL_BIN/$PROJECT_NAME" ]]; then
                ok "Binstub installed at $LOCAL_BIN/$PROJECT_NAME"
            else
                err "Binstub missing at $LOCAL_BIN/$PROJECT_NAME"
            fi

            # Config (project dir)
            if [[ -f "$PROJECT_DIR/config.yaml" ]]; then
                ok "Config at $PROJECT_DIR/config.yaml"
            else
                err "Config missing at $PROJECT_DIR/config.yaml"
            fi

            # Tabby terminal profile
            check_tabby_profile

            # Active sessions
            if [[ -d "$WORKTREES_DIR" ]]; then
                total=$(find "$WORKTREES_DIR" -name '*.yaml' 2>/dev/null | wc -l)
                # `|| true`: a no-match `grep -l` exits 1 and, under pipefail,
                # would abort the script; treat "no active worktrees" as 0.
                active=$(grep -l 'status: active' "$WORKTREES_DIR"/*.yaml 2>/dev/null | wc -l || true)
                ok "$active active session(s), $total total"
            fi
        else
            skipped "Project status skipped (no project specified)"
        fi

        # Terminal-integration scripts (per-session options; opt-in keybinds)
        if [[ -x "$BIN_DIR/session-options.sh" ]]; then
            ok "terminal scripts at $BIN_DIR (session-options.sh)"
        else
            echo "  ! terminal scripts missing -- run 'update' to deploy" >&2
        fi

        assert_path

        # Git hooks
        if [[ -n "$REPO_DIR" ]] && $HAS_PROJECT; then
            hooks_path="$(git -C "$REPO_DIR" config --local core.hooksPath 2>/dev/null)" || true
            if [[ "$hooks_path" == "tools/hooks" ]]; then
                ok "Git hooksPath = tools/hooks"
            elif [[ -n "$hooks_path" ]]; then
                echo "  ! Git hooksPath = $hooks_path (expected tools/hooks)"
            else
                err "Git core.hooksPath not set -- run 'update' to configure"
            fi
        else
            skipped "Git hooks check skipped (no repo detected)"
        fi

        show_deploy_status
        ;;

    update-config)
        header "Updating $SERVICE_NAME Config"

        if ! $HAS_PROJECT; then
            err "No project specified -- pass --project-name"
            exit 1
        fi

        if [[ ! -f "$PROJECT_DIR/config.yaml" ]]; then
            err "Config not found -- run 'install' first"
            exit 1
        fi

        if $FORCE; then
            machine="$(resolve_machine)"
            platform="$(detect_platform)"
            deploy_config "$machine" "$platform"
        else
            skipped "Config is machine-generated -- use --force to regenerate"
            echo "    Current: $PROJECT_DIR/config.yaml"
        fi
        ;;

    update)
        header "Updating $SERVICE_NAME"

        if $CONTEXTUAL_INSTALL; then
            mkdir -p "$INSTALL_DIR" "$BIN_DIR"
            deploy_venv || exit 1
            deploy_package || exit 1
            deploy_wrappers || exit 1
            _versioned_activate || exit 1
            remove_legacy_scripts
            write_deploy_manifest
            ok "Context runtime updated at $INSTALL_DIR"
            exit 0
        fi

        if [[ ! -d "$BIN_DIR" ]]; then
            err "Not installed -- run 'install' first"
            exit 1
        fi

        # -- Shared runtime (venv first: package install targets the venv) --
        deploy_venv || exit 1
        deploy_package || exit 1
        deploy_wrappers || exit 1
        _versioned_activate || exit 1
        remove_legacy_scripts
        remove_legacy_binstubs
        reconcile_binstubs
        # Deploy the tool binstub unconditionally (see the install action) so a
        # project-less `update` also keeps `agent-worktrees` on PATH.
        deploy_tool_binstub
        deploy_copilot_plugin
        ensure_copilot_experimental
        # Machine-wide terminal integration: redeploy the per-session options +
        # opt-in keybind scripts regardless of project context. agent-worktrees
        # no longer owns ~/.tmux.conf (the launcher configures each session at
        # runtime), so a project-less update just refreshes these scripts.
        deploy_terminal_scripts

        # -- Project-specific (only when a project is known) --
        if $HAS_PROJECT; then
            register_project
            reconcile_binstubs
            deploy_tabby_profile "$(detect_platform)" "$(resolve_machine)"
            deploy_git_hooks_path

            if [[ -n "$REPO_DIR" ]]; then
                update_machine="$(resolve_machine)"
                PYTHONUTF8=1 \
                    "$VENV_PYTHON" -m agent_worktrees --project "$PROJECT_NAME" deploy-instructions --machine "$update_machine" 2>&1 \
                    | sed 's/^/  /' || warn "Instruction file deployment skipped"
            fi
        fi

        # Machine-local config schema migration (idempotent + atomic; never
        # touches repo-committed config -- that is an adopt concern). Stamps or
        # upgrades ~/.agent-worktrees/{config,repos,projects}.yaml. Non-fatal.
        PYTHONUTF8=1 "$VENV_PYTHON" -m agent_worktrees config-migrate 2>&1 \
            | sed 's/^/  /' || warn "Config migration skipped"

        write_deploy_manifest

        ok "Update complete"
        ;;

    *)
        echo "Usage: $0 {install|stamp|provision|uninstall|start|stop|status|update-config|update} [--project-name NAME] [--install-dir DIR] [--force] [--remove-config] [--machine NAME]" >&2
        exit 1
        ;;
esac
