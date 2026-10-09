#!/usr/bin/env bash
# =============================================================================
# install.sh -- agent-index -- plugin installer for Linux / WSL / macOS
# =============================================================================
# Manages the agent-index service shell lifecycle: install, update, status,
# start, stop, uninstall. Runtime lives at ~/.agent-index/ and the binstub goes
# to ~/.local/bin/agent-index.
# =============================================================================

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
unset __index_publication_root
. "$SCRIPT_DIR/installer-engine.sh"

# Refuse every mutating lifecycle action before self-staging touches the legacy
# runtime. Parse only the action and install-dir override here; the canonical
# parser below still owns full argument validation.
__legacy_action="${1:-status}"
__legacy_install_dir=""
__legacy_args=("$@")
for ((__legacy_i = 1; __legacy_i < ${#__legacy_args[@]}; __legacy_i++)); do
    if [[ "${__legacy_args[$__legacy_i]}" == --install-dir ]]; then
        ((__legacy_i + 1 < ${#__legacy_args[@]})) || {
            _fail '--install-dir requires a value'
            exit 1
        }
        __legacy_install_dir="${__legacy_args[$((__legacy_i + 1))]}"
    fi
done
if [[ "$__legacy_action" != status &&
      "$__legacy_action" != cell-provision &&
      "$__legacy_action" != cell-recover &&
      "$__legacy_action" != slot-provision &&
      "$__legacy_action" != slot-validate &&
      "$__legacy_action" != slot-complete &&
      "$__legacy_action" != slot-completion-validate &&
      "$__legacy_action" != slot-cutover ]]; then
    LEGACY_PROBE="$SCRIPT_DIR/installation-context/legacy-entrypoint-probe.sh"
    if [[ ! -f "$LEGACY_PROBE" ]]; then
        _fail 'Legacy mutation probe is unavailable'
        exit 1
    fi
    __legacy_root="${__legacy_install_dir:-$HOME/.agent-index}"
    if [[ "$__legacy_root" != /* ]]; then
        __legacy_root="$PWD/$__legacy_root"
    fi
    set +e
    bash "$LEGACY_PROBE" --payload-root "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" \
        --legacy-root "$__legacy_root"
    LEGACY_PROBE_STATUS=$?
    set -e
    if [[ "$LEGACY_PROBE_STATUS" -ne 0 ]]; then
        exit "$LEGACY_PROBE_STATUS"
    fi
fi

# Status and dependency-light cell-slot actions do not enter the self-stage
# block that creates and reaps legacy staging directories.
__skip_self_stage=0
if [[ "$__legacy_action" == cell-provision ||
      "$__legacy_action" == cell-recover ||
      "$__legacy_action" == slot-provision ||
      "$__legacy_action" == slot-validate ||
      "$__legacy_action" == slot-complete ||
      "$__legacy_action" == slot-completion-validate ||
      "$__legacy_action" == slot-cutover ]]; then
    cd "$HOME"
fi
if [[ ("$__legacy_action" == status ||
       "$__legacy_action" == cell-provision ||
       "$__legacy_action" == cell-recover ||
       "$__legacy_action" == slot-provision ||
       "$__legacy_action" == slot-validate ||
       "$__legacy_action" == slot-complete ||
       "$__legacy_action" == slot-completion-validate ||
       "$__legacy_action" == slot-cutover) &&
      -z "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then
    if [[ "$__legacy_action" == status ]]; then
        export COPILOT_PLUGIN_INSTALL_STAGED=read-only-status
    else
        export COPILOT_PLUGIN_INSTALL_STAGED=cell-slot-action
    fi
    __skip_self_stage=1
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
if [[ "$__skip_self_stage" -eq 1 ]]; then
    unset COPILOT_PLUGIN_INSTALL_STAGED
fi

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

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_index"

ACTION="${1:-status}"
shift || true

NO_SERVICE=0
PURGE=0
DRY_RUN=0
INSTALL_DIR=""
CONTEXT=""
EXPECTED_MARKETPLACE_ID=""
DURABLE_HOME=""
ORIGIN_PAYLOAD_ROOT=""
EXPECTED_NAMESPACE_GENERATION=""
EXPECTED_INSTALL_GENERATION=""
EXPECTED_CURRENT_VERSION=""
EXPECT_CURRENT_ABSENT=0
TARGET_PAYLOAD_ROOT=""
TARGET_PAYLOAD_VERSION=""
TARGET_SNAPSHOT_ID=""
TARGET_RUNTIME_VERSION=""
FORCE="${AGENT_INDEX_ALLOW_DOWNGRADE:-0}"
[[ "$FORCE" == "1" ]] && FORCE=1 || FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) NO_SERVICE=1; shift ;;
        --purge) PURGE=1; shift ;;
        --force) FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --context) CONTEXT="${2:-}"; shift 2 ;;
        --expected-marketplace-id) EXPECTED_MARKETPLACE_ID="${2:-}"; shift 2 ;;
        --durable-home) DURABLE_HOME="${2:-}"; shift 2 ;;
        --origin-payload-root) ORIGIN_PAYLOAD_ROOT="${2:-}"; shift 2 ;;
        --expected-namespace-generation) EXPECTED_NAMESPACE_GENERATION="${2:-}"; shift 2 ;;
        --expected-install-generation) EXPECTED_INSTALL_GENERATION="${2:-}"; shift 2 ;;
        --expected-current-version) EXPECTED_CURRENT_VERSION="${2:-}"; shift 2 ;;
        --expect-current-absent) EXPECT_CURRENT_ABSENT=1; shift ;;
        --target-payload-root) TARGET_PAYLOAD_ROOT="${2:-}"; shift 2 ;;
        --target-payload-version) TARGET_PAYLOAD_VERSION="${2:-}"; shift 2 ;;
        --target-snapshot-id) TARGET_SNAPSHOT_ID="${2:-}"; shift 2 ;;
        --target-runtime-version) TARGET_RUNTIME_VERSION="${2:-}"; shift 2 ;;
        *) shift ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-index}"
if [[ "$INSTALL_DIR" != /* ]]; then
    INSTALL_DIR="$PWD/$INSTALL_DIR"
fi
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-index"
SYSTEMD_UNIT="agent-index.service"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_FILE="$INSTALL_DIR/service.env"
SERVICE_LAUNCHER="$INSTALL_DIR/service.sh"

# === engine-daemon: durable, persistent embedding-engine runtime =============
# The heavy embedding stack (torch + transformers + sentence-transformers) lives
# in a DURABLE venv OUTSIDE the versioned service runtime, at
# AGENT_INDEX_ENGINE_HOME (default ~/.agent-index/engine). It is provisioned ONCE
# and preserved across service updates -- a routine `update` swaps only the
# versioned service runtime + symlink and never rebuilds torch or restarts the
# warm engine daemon (effort agent-index-engine-daemon; vision §warm-durable-engine).
ENGINE_HOME="${AGENT_INDEX_ENGINE_HOME:-$HOME/.agent-index/engine}"
ENGINE_HOME="${ENGINE_HOME/#\~/$HOME}"
ENGINE_VENV="$ENGINE_HOME/.venv"
ENGINE_VENV_PYTHON="$ENGINE_VENV/bin/python"
ENGINE_ENV_FILE="$ENGINE_HOME/engine.env"
ENGINE_SYSTEMD_UNIT="agent-index-engine.service"
ENGINE_LAUNCHER="$ENGINE_HOME/engine.sh"
# === end engine-daemon ======================================================

# === install-contract:v3 versioned-venv (agent-index: .venv-as-symlink) ===
# Build each version into versions/<version> and make the historical `.venv`
# path a symlink into the active slot. ALWAYS versioned -- the env opt-out
# (COPILOT_EXT_NO_VERSIONED / AGENT_INDEX_VERSIONED) and the legacy in-place fork
# are retired (a symlink is not a reparse point, so no opt-out is needed).
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
# Marker-only: retire the `.venv` symlink (uniform-runtime-resolution, #765).
# LINK_PYTHON now points at the versioned slot directly (the link is no longer
# created). LINK_DIR is kept ONLY to derive `--link-name` so activate/gc can still
# find and REMOVE any pre-existing `.venv` link.
LINK_PYTHON="$VENV_PYTHON"

if [[ "$ACTION" == "cell-provision" ||
      "$ACTION" == "cell-recover" ||
      "$ACTION" == "slot-cutover" ]]; then
    [[ -n "$CONTEXT" ]] || {
        _fail "$ACTION requires --context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization"
        exit 2
    }
    [[ -n "$EXPECTED_MARKETPLACE_ID" ]] || {
        _fail "$ACTION requires --expected-marketplace-id"
        exit 2
    }
    CELL_RUNTIME="$SCRIPT_DIR/cell-runtime.py"
    [[ -f "$CELL_RUNTIME" ]] || {
        _fail 'Installation-cell runtime coordinator is unavailable'
        exit 1
    }
    CELL_PYTHON=""
    for _candidate in python3 python; do
        if command -v "$_candidate" >/dev/null 2>&1; then
            CELL_PYTHON="$(command -v "$_candidate")"
            break
        fi
    done
    [[ -n "$CELL_PYTHON" ]] || {
        _fail 'Python 3.10+ is required for installation-cell lifecycle actions'
        exit 1
    }
    CELL_ARGS=(
        "$CELL_RUNTIME"
        "$ACTION"
        --context "$CONTEXT"
        --expected-marketplace-id "$EXPECTED_MARKETPLACE_ID"
    )
    if [[ -n "$DURABLE_HOME" ]]; then
        CELL_ARGS+=(--durable-home "$DURABLE_HOME")
    fi
    if [[ "$ACTION" == "cell-provision" ]]; then
        if [[ -n "$ORIGIN_PAYLOAD_ROOT" ]]; then
            CELL_ARGS+=(--origin-payload-root "$ORIGIN_PAYLOAD_ROOT")
        fi
    elif [[ "$ACTION" == "slot-cutover" ]]; then
        [[ -n "$EXPECTED_NAMESPACE_GENERATION" &&
           -n "$EXPECTED_INSTALL_GENERATION" ]] || {
            _fail 'slot-cutover requires expected namespace and install generations'
            exit 2
        }
        [[ -n "$TARGET_PAYLOAD_ROOT" &&
           -n "$TARGET_PAYLOAD_VERSION" &&
           -n "$TARGET_SNAPSHOT_ID" &&
           -n "$TARGET_RUNTIME_VERSION" ]] || {
            _fail 'slot-cutover requires explicit target payload, snapshot, and runtime identity'
            exit 2
        }
        if [[ "$EXPECT_CURRENT_ABSENT" -eq 1 && -n "$EXPECTED_CURRENT_VERSION" ]] ||
           [[ "$EXPECT_CURRENT_ABSENT" -eq 0 && -z "$EXPECTED_CURRENT_VERSION" ]]; then
            _fail 'slot-cutover requires exactly one current-version expectation'
            exit 2
        fi
        CELL_ARGS+=(
            --expected-namespace-generation "$EXPECTED_NAMESPACE_GENERATION"
            --expected-install-generation "$EXPECTED_INSTALL_GENERATION"
            --target-payload-root "$TARGET_PAYLOAD_ROOT"
            --target-payload-version "$TARGET_PAYLOAD_VERSION"
            --target-snapshot-id "$TARGET_SNAPSHOT_ID"
            --target-runtime-version "$TARGET_RUNTIME_VERSION"
        )
        if [[ "$EXPECT_CURRENT_ABSENT" -eq 1 ]]; then
            CELL_ARGS+=(--expect-current-absent)
        else
            CELL_ARGS+=(--expected-current-version "$EXPECTED_CURRENT_VERSION")
        fi
    fi
    unset PYTHONPATH PYTHONHOME
    cd "$PLUGIN_DIR" || exit 1
    exec "$CELL_PYTHON" -I -X utf8 "${CELL_ARGS[@]}"
fi

if [[ "$ACTION" == "slot-provision" ||
      "$ACTION" == "slot-validate" ||
      "$ACTION" == "slot-complete" ||
      "$ACTION" == "slot-completion-validate" ]]; then
    [[ -n "$CONTEXT" ]] || {
        _fail "$ACTION requires --context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization"
        exit 2
    }
    [[ -n "$EXPECTED_MARKETPLACE_ID" ]] || {
        _fail "$ACTION requires --expected-marketplace-id"
        exit 2
    }
    SLOT_RUNNER="$SCRIPT_DIR/installation-context/installation-context.sh"
    [[ -f "$SLOT_RUNNER" ]] || {
        _fail 'Installation-context runner is unavailable'
        exit 1
    }
    SLOT_ARGS=(
        "$ACTION"
        --context "$CONTEXT"
        --expected-marketplace-id "$EXPECTED_MARKETPLACE_ID"
        --expected-plugin-id agent-index
        --expected-payload-root "$PLUGIN_DIR"
        --expected-payload-version "$SRC_VERSION"
        --snapshot-id "$SRC_VERSION"
        --runtime-version "$SRC_VERSION"
    )
    if [[ -n "$DURABLE_HOME" ]]; then
        SLOT_ARGS+=(--durable-home "$DURABLE_HOME")
    fi
    exec bash "$SLOT_RUNNER" "${SLOT_ARGS[@]}"
fi

_versioned_activate() {
    _activate_index_runtime() {
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    _index_publication_fresh || { _skip 'Newer runtime or stamp superseded activation'; return 0; }
    # Monotonic activation (dotfiles #1508): never flip the active runtime BACKWARD.
    # An install/ensure run from a STALE payload (older than the active
    # current-version marker) must not downgrade the running runtime; the marker is
    # authoritative (#1504). Keep it and skip activating the older slot unless a
    # downgrade is explicitly forced -- guards every caller, not just _downgrade_guard.
    if [[ "${FORCE:-0}" != 1 && -n "${SRC_VERSION:-}" && -f "$INSTALL_DIR/current-version" ]]; then
        local cur_ver
        cur_ver="$(tr -d ' \t\r\n' < "$INSTALL_DIR/current-version")"
        if [[ -n "$cur_ver" ]] && _version_lt "$SRC_VERSION" "$cur_ver"; then
            _skip "Keeping active runtime $cur_ver -- not activating older $SRC_VERSION (monotonic; dotfiles #1508)"
            return 0
        fi
    fi
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || {
        _fail "Refusing to activate runtime slot without its target interpreter: $py"
        return 1
    }
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" is-complete "$SRC_VERSION" >/dev/null 2>&1; then
        _fail "Refusing to activate incomplete runtime slot versions/$SRC_VERSION"
        return 1
    fi
    if ! _runtime_origin_under "$py" "$VENV_DIR"; then
        _fail "Refusing to activate runtime slot versions/$SRC_VERSION because agent_index is not importable"
        return 1
    fi
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink --no-link; then
        _fail "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        return 1
    fi
    _ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
    }
    _with_index_publication_lock _activate_index_runtime
}

_versioned_current() {
    [[ "$VERSIONED_RUNTIME" == 1 ]] || { echo ""; return 0; }
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || { echo ""; return 0; }
    "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo ""
}

_versioned_gc() {
    local keep_prev="${1:-}"
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || return 0
    if [[ -n "$keep_prev" ]]; then
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$keep_prev" 2>&1 | sed 's/^/  ...    gc: /' || true
    else
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  ...    gc: /' || true
    fi
}
# === end install-contract:v3 versioned-venv ===

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
    (
        # All non-root entries, including root snapshot-provenance.json, count
        # toward these limits. The provenance file is omitted only from records.
        local __max_entries="${1:-100000}"
        local __max_path_bytes="${2:-4096}"
        local __max_content_bytes="${3:-4294967296}"
        local __sha_kind __kernel __work __before_index __before_state
        local __after_index __after_state __records __path __relative
        local __encoded __kind __metadata __size __count __total __fd
        local __opened __opened_after __current __file_digest
        local __digest __find_fd __find_pid
        local LC_ALL=C
        [[ -d "$PLUGIN_DIR" && ! -L "$PLUGIN_DIR" ]] || {
            _fail "Payload root must be an ordinary directory: $PLUGIN_DIR"
            exit 1
        }
        command -v sort >/dev/null 2>&1 || {
            _fail "Cannot sort payload contents because the sort utility is unavailable"
            exit 1
        }
        if command -v sha256sum >/dev/null 2>&1; then
            __sha_kind=sha256sum
        elif command -v shasum >/dev/null 2>&1; then
            __sha_kind=shasum
        elif command -v openssl >/dev/null 2>&1; then
            __sha_kind=openssl
        else
            _fail "No SHA-256 implementation is available (sha256sum, shasum, or openssl)"
            exit 1
        fi
        __kernel="$(uname -s)" || {
            _fail "Cannot identify the platform for payload validation"
            exit 1
        }
        __work="$(mktemp -d "${TMPDIR:-/tmp}/payload-hash.XXXXXX")" || {
            _fail "Cannot stage payload hash state"
            exit 1
        }
        trap 'rm -rf -- "$__work"' EXIT
        __before_index="$__work/before-index"
        __before_state="$__work/before-state"
        __after_index="$__work/after-index"
        __after_state="$__work/after-state"
        __records="$__work/records"

        __payload_stat() {
            local __target="$1" __follow="${2:-false}"
            if [[ "$__kernel" == Darwin ]]; then
                if [[ "$__follow" == true ]]; then
                    stat -L -f '%HT|%d|%i|%z|%m|%c' "$__target" 2>/dev/null
                else
                    stat -f '%HT|%d|%i|%z|%m|%c' "$__target" 2>/dev/null
                fi
            else
                local __args=(-c '%F|%d|%i|%s|%y|%z')
                [[ "$__follow" == true ]] && __args=(-L "${__args[@]}")
                stat "${__args[@]}" -- "$__target" 2>/dev/null
            fi
        }
        __payload_is_directory() {
            [[ "${1%%|*}" == directory || "${1%%|*}" == Directory ]]
        }
        __payload_is_regular() {
            [[ "${1%%|*}" == "regular file" ||
               "${1%%|*}" == "regular empty file" ||
               "${1%%|*}" == "Regular File" ]]
        }
        __payload_fstat() {
            # A real fstat(2) on an already-open file descriptor, bypassing
            # the path-based /dev/fd (or /proc/<pid>/fd) lookup entirely. On
            # Darwin, `stat -L` on the synthetic /dev/fd/<n> devfs node does
            # NOT behave like fstat(2): devfs can report its own device
            # number for that node rather than passing through the real
            # underlying file's device -- confirmed by a direct probe (type,
            # inode, size, mtime, and ctime all matched a pathname read;
            # only the device field differed). That harmless-looking
            # mismatch false-positived "payload content changed during
            # hashing" for an untouched file on every macOS install. Simply
            # dropping the device field from the comparison would silently
            # remove real replacement-race protection (inode numbers are
            # only unique within one device, so a cross-filesystem swap with
            # a matching inode could then pass undetected). Instead, redirect
            # the inherited descriptor to this helper's own stdin so Python's
            # os.fstat(0) inspects the SAME open file description and
            # returns its REAL device/inode -- identical to a pathname read
            # of the same untouched file on every platform, with no
            # devfs/procfs layer in between.
            local __fd="$1" __py
            __py="$(_bootstrap_python)" || {
                _fail "Cannot locate a Python interpreter for descriptor fstat verification"
                return 1
            }
            "$__py" -c '
import os, stat, sys
st = os.fstat(0)
if stat.S_ISDIR(st.st_mode):
    kind = "Directory"
elif stat.S_ISREG(st.st_mode):
    kind = "Regular File"
else:
    kind = "Other"
print(f"{kind}|{st.st_dev}|{st.st_ino}|{st.st_size}|{int(st.st_mtime)}|{int(st.st_ctime)}")
' <&"$__fd"
        }
        __payload_descriptor_stat() {
            # Stat an already-open descriptor in the SAME tuple format
            # __payload_stat uses for a pathname read, so the two remain
            # directly comparable with a plain string equality -- the real
            # identity/mutation proof this function relies on. Linux's
            # /proc/<pid>/fd/<n> entry already passes fstat(2)-equivalent
            # values through a path lookup correctly (the macOS false
            # positive this guards against never reproduced there), so keep
            # that cheaper path-stat on Linux and reserve the real-fstat
            # helper for Darwin, where it does not.
            local __fd="$1"
            if [[ "$__kernel" == Darwin ]]; then
                __payload_fstat "$__fd"
            else
                __payload_stat "/proc/$BASHPID/fd/$__fd" true
            fi
        }
        __payload_size() {
            local __rest="${1#*|}"
            __rest="${__rest#*|}"
            __rest="${__rest#*|}"
            printf '%s' "${__rest%%|*}"
        }
        __payload_utf8() {
            LC_ALL=C printf '%s' "$1" | od -An -v -tu1 | LC_ALL=C awk '
                BEGIN { remaining=0; minimum=128; maximum=191; valid=1 }
                {
                    for (field=1; field<=NF; field++) {
                        byte=$field+0
                        if (remaining>0) {
                            if (byte<minimum || byte>maximum) { valid=0; exit }
                            remaining--; minimum=128; maximum=191; continue
                        }
                        if (byte<=127) continue
                        if (byte>=194 && byte<=223) { remaining=1; continue }
                        if (byte==224) { remaining=2; minimum=160; continue }
                        if ((byte>=225 && byte<=236) || (byte>=238 && byte<=239)) {
                            remaining=2; continue
                        }
                        if (byte==237) { remaining=2; maximum=159; continue }
                        if (byte==240) { remaining=3; minimum=144; continue }
                        if (byte>=241 && byte<=243) { remaining=3; continue }
                        if (byte==244) { remaining=3; maximum=143; continue }
                        valid=0; exit
                    }
                }
                END { if (!valid || remaining!=0) exit 1 }
            '
        }
        __payload_hex() {
            LC_ALL=C printf '%s' "$1" | od -An -v -tx1 | tr -d ' \n'
        }
        __payload_decode() {
            local __target="$1" __hex="$2" __esc="" __byte
            while [[ -n "$__hex" ]]; do
                __byte="${__hex:0:2}"
                __esc+="\\x$__byte"
                __hex="${__hex:2}"
            done
            printf -v "$__target" '%b' "$__esc"
        }
        __payload_index() {
            local __index="$1" __state="$2" __unsorted_index="$__work/index-u"
            local __unsorted_state="$__work/state-u" __root_metadata
            : >"$__unsorted_index"
            : >"$__unsorted_state"
            __count=0
            __total=0
            __root_metadata="$(__payload_stat "$PLUGIN_DIR")" || {
                _fail "Cannot inspect payload root: $PLUGIN_DIR"
                return 1
            }
            __payload_is_directory "$__root_metadata" || {
                _fail "Payload root must be an ordinary directory: $PLUGIN_DIR"
                return 1
            }
            printf 'R\t\t%s\n' "$__root_metadata" >>"$__unsorted_state"
            exec {__find_fd}< <(find "$PLUGIN_DIR" -mindepth 1 -print0)
            __find_pid=$!
            while IFS= read -r -d '' __path <&"$__find_fd"; do
                __count=$((__count + 1))
                ((__count <= __max_entries)) || {
                    _fail "Payload content exceeds the $__max_entries-entry limit"
                    return 1
                }
                __relative="${__path#"$PLUGIN_DIR"/}"
                __payload_utf8 "$__relative" || {
                    _fail "Payload content path is not valid UTF-8"
                    return 1
                }
                ((${#__relative} <= __max_path_bytes)) || {
                    _fail "Payload content relative path exceeds the $__max_path_bytes-byte UTF-8 limit: $__relative"
                    return 1
                }
                [[ ! -L "$__path" ]] || {
                    _fail "Payload content may not contain symbolic links or reparse points: $__relative"
                    return 1
                }
                __metadata="$(__payload_stat "$__path")" || {
                    _fail "Cannot inspect payload content: $__relative"
                    return 1
                }
                __encoded="$(__payload_hex "$__relative")"
                if __payload_is_directory "$__metadata"; then
                    printf '%s\tD\n' "$__encoded" >>"$__unsorted_index"
                    printf 'D\t%s\t%s\n' "$__encoded" "$__metadata" >>"$__unsorted_state"
                    continue
                fi
                __payload_is_regular "$__metadata" || {
                    _fail "Payload content entries must be ordinary files or directories: $__relative"
                    return 1
                }
                __size="$(__payload_size "$__metadata")"
                [[ "$__size" =~ ^[0-9]+$ ]] || {
                    _fail "Cannot determine payload content size: $__relative"
                    return 1
                }
                ((__size <= __max_content_bytes - __total)) || {
                    _fail "Payload content exceeds the $__max_content_bytes-byte regular-file limit"
                    return 1
                }
                __total=$((__total + __size))
                printf '%s\tF\n' "$__encoded" >>"$__unsorted_index"
                printf 'F\t%s\n' "$__encoded" >>"$__unsorted_state"
            done
            exec {__find_fd}<&-
            wait "$__find_pid" || {
                _fail "Cannot enumerate all payload contents beneath $PLUGIN_DIR"
                return 1
            }
            printf 'T\t%s\t%s\n' "$__count" "$__total" >>"$__unsorted_state"
            LC_ALL=C sort -t $'\t' -k1,1 "$__unsorted_index" >"$__index"
            LC_ALL=C sort "$__unsorted_state" >"$__state"
        }

        __payload_index "$__before_index" "$__before_state" || exit 1
        : >"$__records"
        while IFS=$'\t' read -r __encoded __kind; do
            [[ "$__kind" == F ]] || continue
            __payload_decode __relative "$__encoded"
            [[ "$__relative" == snapshot-provenance.json ]] && continue
            __path="$PLUGIN_DIR/$__relative"
            __metadata="$(__payload_stat "$__path")" || {
                _fail "Cannot inspect payload content: $__relative"
                exit 1
            }
            __payload_is_regular "$__metadata" && [[ ! -L "$__path" ]] || {
                _fail "Payload content changed during hashing: $__relative"
                exit 1
            }
            exec {__fd}<"$__path" || {
                _fail "Cannot open payload content: $__relative"
                exit 1
            }
            __opened="$(__payload_descriptor_stat "$__fd")" || {
                exec {__fd}<&-
                _fail "Cannot inspect opened payload content: $__relative"
                exit 1
            }
            [[ "$__opened" == "$__metadata" ]] || {
                exec {__fd}<&-
                _fail "Payload content changed during hashing: $__relative"
                exit 1
            }
            case "$__sha_kind" in
                sha256sum) __file_digest="$(sha256sum <&"$__fd" | awk '{print $1}')" ;;
                shasum) __file_digest="$(shasum -a 256 <&"$__fd" | awk '{print $1}')" ;;
                openssl) __file_digest="$(openssl dgst -sha256 <&"$__fd" | awk '{print $NF}')" ;;
            esac
            __opened_after="$(__payload_descriptor_stat "$__fd")" || true
            exec {__fd}<&-
            __current="$(__payload_stat "$__path")" || true
            [[ "$__opened_after" == "$__opened" &&
               "$__current" == "$__metadata" &&
               ! -L "$__path" ]] || {
                _fail "Payload content changed during hashing: $__relative"
                exit 1
            }
            [[ "$__file_digest" =~ ^[0-9a-fA-F]{64}$ ]] || {
                _fail "SHA-256 output is invalid"
                exit 1
            }
            __file_digest="${__file_digest,,}"
            printf 'F\0%s\0%s\n' "$__relative" "$__file_digest" >>"$__records"
        done <"$__before_index"
        __payload_index "$__after_index" "$__after_state" || exit 1
        cmp -s -- "$__before_state" "$__after_state" || {
            _fail "Payload content tree changed during hashing"
            exit 1
        }
        case "$__sha_kind" in
            sha256sum) __digest="$(sha256sum "$__records" | awk '{print $1}')" ;;
            shasum) __digest="$(shasum -a 256 "$__records" | awk '{print $1}')" ;;
            openssl) __digest="$(openssl dgst -sha256 "$__records" | awk '{print $NF}')" ;;
        esac
        [[ "$__digest" =~ ^[0-9a-fA-F]{64}$ ]] || {
            _fail "SHA-256 output is invalid"
            exit 1
        }
        printf '%s' "${__digest,,}"
    )
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
    py="$(_bootstrap_python)" || {
        _fail "Cannot locate Python to write the runtime completion marker"
        return 1
    }
    [[ -n "$py" ]] || {
        _fail "Cannot locate Python to write the runtime completion marker"
        return 1
    }
    local ph
    ph="$(_payload_hash)"
    local args=("$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" mark-complete "$SRC_VERSION" --payload-hash "$ph")
    "$py" "${args[@]}" 2>&1 | sed 's/^/  ...    /'
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

# Resolve a vendored library path (libs/<name>) across multiple layouts.
# Prints the resolved directory path to stdout (nothing else).
# Returns 0 if found, 1 if not.
_resolve_vendored_lib() {
    local lib_name="$1"
    local candidate

    # 1. Vendored inside agent-index (marketplace install layout)
    candidate="$PLUGIN_DIR/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 2. Relative path (git checkout layout: plugins/agent-index/../../libs/<name>)
    candidate="$PLUGIN_DIR/../../libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 3. Git repo registry (~/.git-repos) -- use Python for safe YAML parsing
    if [[ -f "$HOME/.git-repos" ]]; then
        candidate="$(python3 -c "
import pathlib, os
try:
    import yaml
except ImportError:
    raise SystemExit(1)
reg = yaml.safe_load(pathlib.Path.home().joinpath('.git-repos').read_text())
repo = (reg or {}).get('repos', {}).get('copilot-extensions', {})
if repo:
    p = repo.get('path', os.path.join(reg.get('srcroot', ''), 'copilot-extensions'))
    p = os.path.expanduser(p)
    lib = os.path.join(p, 'libs', '$lib_name')
    if os.path.isfile(os.path.join(lib, 'pyproject.toml')):
        print(lib)
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null)" && {
            echo "$candidate"
            return 0
        }
    fi

    # 4. Common checkout path (repo exists but registry absent/stale)
    candidate="$HOME/src/copilot-extensions/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    return 1
}

# zero-downtime cutover primitives (module ``zdd``), extracted from agent-bridge.
_resolve_zdd() { _resolve_vendored_lib zdd; }

# Check if the zdd cutover lib is already importable in the venv.
_zdd_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from zdd.cutover import CutoverOrchestrator' 2>/dev/null
}

_installed_version() {
    # The version currently ACTIVE (via the current-version marker), for the
    # downgrade guard. Marker-only -- the `.venv` link is retired (#765).
    local ver="" py=""
    [[ -f "$INSTALL_DIR/current-version" ]] && ver="$(tr -d ' \t\r\n' < "$INSTALL_DIR/current-version" 2>/dev/null)"
    [[ -n "$ver" ]] && py="$INSTALL_DIR/versions/$ver/bin/python"
    [[ -x "$py" ]] || return 1
    local v
    v="$("$py" -c 'from importlib.metadata import version; print(version("agent-index"))' 2>/dev/null)" || return 1
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

_source_version() {
    local manifest="$PLUGIN_DIR/plugin.json"
    [[ -f "$manifest" ]] || return 1
    local v
    v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -n1)"
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

_version_lt() {
    local a="${1//-/.}" b="${2//-/.}"
    [[ "$a" == "$b" ]] && return 1
    local lower
    lower="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"
    [[ "$lower" == "$a" ]]
}

_runtime_origin_under() {
    local py="$1" slot="$2" origin="" origin_dir="" origin_abs="" slot_abs=""
    [[ -x "$py" && -d "$slot" ]] || return 1
    origin="$(
        cd "$slot" &&
        "$py" -I -X utf8 -c \
            'from pathlib import Path; import agent_index; print(Path(agent_index.__file__).resolve())'
    2>/dev/null)" || return 1
    [[ -n "$origin" && -f "$origin" ]] || return 1
    origin_dir="$(dirname "$origin")"
    origin_abs="$(cd "$origin_dir" && pwd -P)/$(basename "$origin")" || return 1
    slot_abs="$(cd "$slot" && pwd -P)" || return 1
    case "$origin_abs" in
        "$slot_abs"/*) return 0 ;;
        *) return 1 ;;
    esac
}

_downgrade_guard() {
    local installed source
    installed="$(_installed_version)" || return 0
    source="$(_source_version)" || {
        _warn "Could not read source version from plugin.json -- skipping downgrade guard"
        return 0
    }
    if _version_lt "$source" "$installed"; then
        if [[ "$FORCE" -eq 1 ]]; then
            _warn "Downgrade $installed -> $source forced (--force / AGENT_INDEX_ALLOW_DOWNGRADE)"
            return 0
        fi
        _fail "Refusing to downgrade agent-index: installed $installed > source $source"
        exit 1
    fi
}

_find_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" --version 2>&1 | grep -qi python; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# --- self-provisioning helpers (runtime-self-provisioning pattern) -----------
# Vendor a standalone uv into the runtime tool dir when uv is absent (pristine or
# governed box) instead of dead-ending; add it to PATH for this run.
_ensure_uv() {
    UV_COMMAND="$(_with_index_advisory_lock "$INSTALL_DIR/uv-acquisition" 7 180 ensure_uv "$INSTALL_DIR")" || return $?
    export PATH="$(dirname "$UV_COMMAND"):$PATH"
}

_uv_pip_install() {
    invoke_uv_pip_install_resilient "${UV_COMMAND:-uv}" "$@"
}

# Mirror pip's configured index to uv on a governed box (public PyPI TLS-blocked):
# uv does not read pip.conf, so derive index-url from pip config / the pip.conf
# files and export it. No-op where pip has no index (e.g. pristine -- the index
# then arrives via env / the clean-room fixture).
_ensure_uv_index() {
    [[ -n "${UV_INDEX_URL:-}${UV_DEFAULT_INDEX:-}" ]] && return 0
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
    if [[ -n "$idx" ]]; then export UV_DEFAULT_INDEX="$idx"; _step "uv index derived from pip config (governed-feed bridge)"; fi
}

# Deploy a stable machine-global redirector to the payload-owned lifecycle gate.
# The gate owns setup consent, runtime readiness, and provisioning.
deploy_binstub() {
    _deploy_index_binstub() {
    _index_publication_fresh || { _skip 'Newer runtime or stamp superseded launcher publication'; return 0; }
    mkdir -p "$LOCAL_BIN" "$INSTALL_DIR/bin"
    # Co-deploy the canonical marker-only resolver (uniform-runtime-resolution, #765).
    for r in resolve-runtime.sh resolve-runtime.ps1; do
        [ -f "$SCRIPT_DIR/$r" ] && cp -f "$SCRIPT_DIR/$r" "$INSTALL_DIR/bin/$r"
    done
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
    cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
_name="agent-index"
_root="$HOME/.$_name"
_payload="$(cat "$_root/payload-dir" 2>/dev/null || true)"
_gate="$_payload/scripts/runtime-gate.sh"
if [ ! -f "$_gate" ]; then
    printf '%s\n' "[$_name] payload lifecycle gate not found. Re-enable the plugin, then retry." >&2
    exit 127
fi
exec "$_gate" "$@"
STUBEOF
    chmod +x "$STUB"
    _ok "Binstub: $STUB (setup-gated)"
    }
    _with_index_publication_lock _deploy_index_binstub
}

# Cheap 'stamp': splat the binstub + payload marker, defer the venv build until
# explicit setup (fits a sessionStart hook's grace window). No venv, no uv.
do_stamp() {
    _stamp_index_payload() {
    _index_publication_fresh || { _skip 'Newer runtime or stamp superseded stamp'; return 0; }
    echo ''; echo '=== agent-index stamp (defer runtime to explicit setup) ==='; echo ''
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    deploy_binstub
    printf '%s\n' "$SRC_VERSION" > "$INSTALL_DIR/stamped-version"
    _ok "Stamped: binstub on PATH; runtime provisions after explicit setup."
    }
    _with_index_publication_lock _stamp_index_payload
}

_test_index_venv() {
    local dir="$1" python="$2" output prefix expected actual
    [[ -x "$python" && -f "$dir/pyvenv.cfg" ]] || return 1
    output="$("$python" -I -c 'import os, sys; print(os.path.abspath(sys.prefix)); print(int(sys.prefix != sys.base_prefix))' 2>/dev/null)" || return 1
    [[ "$output" == *$'\n'* && "${output#*$'\n'}" == 1 ]] || return 1
    prefix="${output%%$'\n'*}"
    [[ "$prefix" == /* ]] || return 1
    expected="$(cd "$dir" && pwd -P)" || return 1
    actual="$(cd "$prefix" && pwd -P)" || return 1
    [[ "$actual" == "$expected" ]]
}

_new_index_venv() {
    local dir="$1" python="$2" base_python="$3" have_uv="$4" output rc=0
    if _test_index_venv "$dir" "$python"; then return 0; fi
    if [[ -e "$dir" ]]; then
        _warn "Existing venv failed interpreter/prefix health validation -- rebuilding: $dir"
        rm -rf -- "$dir" || return 1
    fi
    if [[ "$have_uv" == 1 ]]; then
        output="$(invoke_uv_venv_resilient "${UV_COMMAND:-uv}" "$dir" --allow-existing 2>&1)" || rc=$?
        if [[ "$rc" == 0 ]] && _test_index_venv "$dir" "$python"; then return 0; fi
        _warn "uv venv failed health validation (exit $rc): $output"
    fi
    if ! output="$("$base_python" -m venv --clear "$dir" 2>&1)"; then
        _warn "Python venv fallback failed: $output"
        return 1
    fi
    if ! _test_index_venv "$dir" "$python"; then
        _warn "Created venv failed interpreter/prefix health validation: $dir"
        return 1
    fi
}

_install_server_venv() {
    # agent-index-server-venv-split: provision a sibling SERVER venv inside
    # the current runtime slot ($VENV_DIR/server), installing the full
    # agent-index[store,server] package -- so `spawn_passive` (and, once its
    # own dispatcher retarget lands, `cmd_start`/`cmd_cell_start`) can run the
    # FastAPI/uvicorn/pydantic service from a venv separate from the
    # client/orchestrator's own, keeping a pure client's install footprint
    # light. This is the exact sibling path `config.server_venv_python()`
    # already resolves (a `server` subdirectory of whichever directory
    # contains the current interpreter's own `Scripts`/`bin` folder) --
    # provisioning here just makes that existing, previously-inert resolver
    # find something.
    #
    # Host role only: a client never runs the service, so it never needs this
    # second venv. Provisioning failures are WARN, never FAIL --
    # `config.server_venv_python()` already falls back to `None` (in-process
    # `serve()`, or the shared venv for `spawn_passive`) when no sibling
    # exists, so a failure here must never block the primary client
    # install/update.
    local install_role="$1" py="$2"
    if [[ "$install_role" != "host" ]]; then
        _skip 'Server venv: skipped (client role never runs the service)'
        return 0
    fi

    local server_venv_dir="$VENV_DIR/server"
    local server_venv_python="$server_venv_dir/bin/python3"
    local have_uv=0
    command -v uv >/dev/null 2>&1 && have_uv=1

    if ! _new_index_venv "$server_venv_dir" "$server_venv_python" "$py" "$have_uv"; then
            _warn "Server venv creation failed -- $server_venv_python not found (spawn_passive falls back to the shared venv)"
            return 0
    fi

    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            _uv_pip_install --python "$server_venv_python" "$zdd_dir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet >/dev/null 2>&1
        else
            "$server_venv_python" -m pip install "$zdd_dir" >/dev/null 2>&1
        fi || {
            _warn "Server venv zdd install failed -- spawn_passive falls back to the shared venv"
            return 0
        }
    fi

    local srv_out srv_rc=0
    if [[ "$have_uv" -eq 1 ]]; then
        srv_out="$(_uv_pip_install --python "$server_venv_python" "${PLUGIN_DIR}[store,server]" 2>&1)" || srv_rc=$?
    else
        srv_out="$("$server_venv_python" -m pip install "${PLUGIN_DIR}[store,server]" 2>&1)" || srv_rc=$?
    fi
    if [[ "$srv_rc" -ne 0 ]]; then
        _warn 'Server venv package install failed -- spawn_passive falls back to the shared venv'
        printf '%s\n' "$srv_out" >&2
        return 0
    fi
    if ! _test_index_venv "$server_venv_dir" "$server_venv_python"; then
        _warn 'Server venv failed final interpreter/prefix health validation -- using the shared venv'
        return 0
    fi
    _ok "Server venv provisioned: $server_venv_dir"
}

_with_index_advisory_lock() (
    local target="$1" descriptor="$2" timeout="$3"
    shift 3
    if [[ ! "$timeout" =~ ^[0-9]{1,3}$ ]] || ((10#$timeout > 180)); then
        _warn 'Invalid runtime build admission timeout (expected 0-180 seconds)'
        return 1
    fi
    timeout=$((10#$timeout))
    local parent lock lock_python="" use_flock=0
    parent="$(dirname "$target")"
    mkdir -p "$parent" || return 1
    lock="$parent/.$(basename "$target").lock"
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
        use_flock=1
    else
        lock_python="$(_bootstrap_python)" || lock_python=""
        if [[ -n "$lock_python" ]]; then
            lock_python="$("$lock_python" -I -c 'import fcntl, sys; print(sys._base_executable)' 2>/dev/null)" || lock_python=""
        fi
        if [[ -z "$lock_python" ]] || [[ "$("$lock_python" -I -c 'import fcntl; print("index-advisory-lock")' 2>/dev/null)" != index-advisory-lock ]]; then
            lock_python="$(_find_python)" || {
                _warn 'Runtime build admission requires a bootstrap Python with stdlib fcntl'
                return 1
            }
            [[ "$("$lock_python" -I -c 'import fcntl; print("index-advisory-lock")' 2>/dev/null)" == index-advisory-lock ]] || {
                _warn 'Bootstrap Python cannot provide stdlib fcntl build admission'
                return 1
            }
        fi
    fi
    _python_index_build_lock() {
        "$lock_python" -I - "$1" "$timeout" "$target" "$descriptor" <<'PY'
import fcntl
import sys
import time

try:
    if sys.argv[1] == "unlock":
        fcntl.flock(int(sys.argv[4]), fcntl.LOCK_UN)
    else:
        deadline = time.monotonic() + int(sys.argv[2])
        while True:
            try:
                fcntl.flock(int(sys.argv[4]), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for runtime build admission: {sys.argv[3]}")
                time.sleep(0.05)
except OSError as error:
    print(f"Runtime build advisory lock failed: {error}", file=sys.stderr)
    sys.exit(1)
PY
    }
    if [[ "$descriptor" == 7 ]]; then
        exec 7>"$lock" || return 1
    elif [[ "$descriptor" == 8 ]]; then
        exec 8>"$lock" || return 1
    elif [[ "$descriptor" == 9 ]]; then
        exec 9>"$lock" || return 1
    else
        _warn 'Invalid installer lock descriptor'
        return 1
    fi
    _release_index_build_lock() {
        local rc=$?
        trap - EXIT
        if [[ "$use_flock" == 1 ]]; then
            if ! flock -u "$descriptor"; then _warn "Cannot unlock installer admission: $lock"; [[ "$rc" != 0 ]] || rc=1; fi
        else
            if ! _python_index_build_lock unlock; then _warn "Cannot unlock runtime build admission: $lock"; [[ "$rc" != 0 ]] || rc=1; fi
        fi
        if [[ "$descriptor" == 7 ]]; then exec 7>&-; elif [[ "$descriptor" == 8 ]]; then exec 8>&-; else exec 9>&-; fi
        exit "$rc"
    }
    if [[ "$use_flock" == 1 ]]; then
        if ! flock -w "$timeout" "$descriptor"; then
            _warn "Timed out waiting for runtime build admission: $target"
            if [[ "$descriptor" == 7 ]]; then exec 7>&-; elif [[ "$descriptor" == 8 ]]; then exec 8>&-; else exec 9>&-; fi
            return 1
        fi
    else
        if ! _python_index_build_lock lock; then
            if [[ "$descriptor" == 7 ]]; then exec 7>&-; elif [[ "$descriptor" == 8 ]]; then exec 8>&-; else exec 9>&-; fi
            return 1
        fi
    fi
    trap '_release_index_build_lock' EXIT
    "$@"
)

_with_index_build_lock() {
    local target="$1"
    shift
    _with_index_advisory_lock "$target.build" 8 "${INDEX_BUILD_LOCK_TIMEOUT_SECONDS:-180}" "$@"
}

_with_index_publication_lock() {
    if [[ "${__index_publication_root:-}" == "$INSTALL_DIR" ]]; then
        "$@"
    else
        _index_publication_callback() {
            local __index_publication_root="$INSTALL_DIR"
            "$@"
        }
        _with_index_advisory_lock "$INSTALL_DIR/publication" 9 20 _index_publication_callback "$@"
    fi
}

_index_publication_fresh() {
    [[ "${FORCE:-0}" == 1 ]] && return 0
    local marker version
    for marker in current-version stamped-version; do
        if [[ -f "$INSTALL_DIR/$marker" ]]; then
            version="$(tr -d ' \t\r\n' < "$INSTALL_DIR/$marker")" || return 1
            if [[ -n "$version" ]] && _version_lt "$SRC_VERSION" "$version"; then return 1; fi
        fi
    done
}

_ensure_runtime() {
    _ensure_runtime_build() {
    if [[ ! -d "$PKG_SRC_DIR" ]]; then
        _fail "Package source not found at $PKG_SRC_DIR"
        exit 1
    fi
    local py
    py="$(_find_python)" || { _fail 'Python not found on PATH (need 3.10+)'; exit 1; }
    _ok "Python: $py"
    # Self-acquire uv (vendored if absent) + mirror the governed pip index to uv
    # so a solo/standalone install works on a pristine or governed box.
    _ensure_uv_index
    local have_uv=0
    command -v uv >/dev/null 2>&1 && have_uv=1

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    _ok "Directories: $INSTALL_DIR"

    _prepare_index_runtime() {
    _index_publication_fresh || return 3
    # Detach an invalid active marker before any rebuild. If provisioning fails
    # later, no success-shaped current-version pointer remains.
    local active_version="" active_ready=0 active_python=""
    [[ -f "$INSTALL_DIR/current-version" ]] && active_version="$(tr -d ' \t\r\n' < "$INSTALL_DIR/current-version")"
    if [[ "$VERSIONED_RUNTIME" == 1 && -n "$active_version" ]]; then
        active_python="$INSTALL_DIR/versions/$active_version/bin/python"
        [[ -x "$active_python" ]] || active_python="$INSTALL_DIR/versions/$active_version/Scripts/python.exe"
        if _test_index_venv "$INSTALL_DIR/versions/$active_version" "$active_python" \
            && "$active_python" "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" --link-name ".venv" is-complete "$active_version" >/dev/null 2>&1 \
            && _runtime_origin_under "$active_python" "$INSTALL_DIR/versions/$active_version"; then
            active_ready=1
        fi
        if [[ "$active_ready" == 0 ]]; then
            _stop >/dev/null 2>&1 || true
            rm -f "$INSTALL_DIR/current-version"
            if [[ -f "$INSTALL_DIR/last-known-good" ]] \
                && [[ "$(tr -d ' \t\r\n' < "$INSTALL_DIR/last-known-good")" == "$active_version" ]]; then
                rm -f "$INSTALL_DIR/last-known-good"
            fi
            _warn "Detached invalid active runtime marker for versions/$active_version"
        fi
    fi

    # A failed prior build may have left an interpreter-shaped target slot or,
    # on older installers, even an active marker. Reuse requires both a valid
    # completion marker and an importable agent_index package.
    if [[ "$VERSIONED_RUNTIME" == 1 && -d "$VENV_DIR" ]]; then
        local slot_ready=0 vr="$SCRIPT_DIR/versioned_runtime.py"
        if _test_index_venv "$VENV_DIR" "$VENV_PYTHON" \
            && "$VENV_PYTHON" "$vr" --root "$INSTALL_DIR" --link-name ".venv" is-complete "$SRC_VERSION" >/dev/null 2>&1 \
            && _runtime_origin_under "$VENV_PYTHON" "$VENV_DIR"; then
            slot_ready=1
        fi
        if [[ "$slot_ready" == 0 || "${AGENT_INDEX_REBUILD_CURRENT:-0}" == 1 ]]; then
            local target_was_active=0
            local marker_name marker_version
            for marker_name in current-version last-known-good; do
                marker_version=""
                [[ -f "$INSTALL_DIR/$marker_name" ]] && marker_version="$(tr -d ' \t\r\n' < "$INSTALL_DIR/$marker_name")"
                if [[ "$marker_version" == "$SRC_VERSION" ]]; then
                    [[ "$marker_name" == current-version ]] && target_was_active=1
                    rm -f "$INSTALL_DIR/$marker_name"
                fi
            done
            [[ "$target_was_active" == 1 ]] && { _stop >/dev/null 2>&1 || true; }
            rm -rf "$VENV_DIR"
            _warn "Removed runtime slot versions/$SRC_VERSION before a clean role-aware rebuild"
        fi
    fi

    }
    local preparation_rc=0
    _with_index_publication_lock _prepare_index_runtime || preparation_rc=$?
    if [[ "$preparation_rc" == 3 ]]; then _skip 'Runtime preparation superseded'; return 0; fi
    [[ "$preparation_rc" == 0 ]] || return "$preparation_rc"
    if ! _test_index_venv "$VENV_DIR" "$VENV_PYTHON"; then _versioned_slot_clean; fi
    if ! _new_index_venv "$VENV_DIR" "$VENV_PYTHON" "$py" "$have_uv"; then
        _fail "Runtime venv failed interpreter/prefix health validation: $VENV_DIR"
        exit 1
    fi


    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            _uv_pip_install --python "$VENV_PYTHON" "$zdd_dir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet
        else
            "$VENV_PYTHON" -m pip install "$zdd_dir" >/dev/null
        fi || {
            _fail "zdd install failed"
            exit 1
        }
    elif _zdd_installed; then
        _skip "zdd already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate zdd library. Reinstall the agent-index plugin from the marketplace (copilot plugin install agent-index@copilot-extensions), then rerun this installer."
        exit 1
    fi

    # agent-procutil is a `uv`-editable canonical reference in a dev
    # checkout (vendor-pointer-generalization effort: no local copy at
    # all) and not on PyPI -- pre-install it the same way as zdd above, so
    # the non-uv (bare-pip) fallback below can still resolve it.
    local procutil_dir
    if procutil_dir="$(_resolve_vendored_lib agent-procutil)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            _uv_pip_install --python "$VENV_PYTHON" "$procutil_dir" --reinstall-package agent-procutil --refresh-package agent-procutil --quiet
        else
            "$VENV_PYTHON" -m pip install "$procutil_dir" >/dev/null
        fi || {
            _fail "agent-procutil install failed"
            exit 1
        }
    fi

    _pip_install() {
        # A host runs the local indexing/vector-store stack and the FastAPI/
        # uvicorn server, so it needs the [store,server] extras (numpy,
        # pyarrow, lancedb, tree-sitter*, mcp, fastapi, uvicorn, pydantic); a
        # client stays on the light base deps only (see
        # agent-index-server-venv-split, which will eventually move [server]
        # into its own dedicated per-version venv instead of this shared one).
        # This is distinct from -- and must never pull in -- the durable
        # agent-index-engine program (torch), which is provisioned exclusively
        # by `engine`/`engine-update` (see durable-vs-versioned-runtime.md).
        # Resolve role BEFORE the package install so a host's versioned venv
        # actually carries what its own service/search/index code imports.
        # _activation_role falls back to _machine_role, so this is correct
        # even before any per-repo role config exists.
        local install_role
        install_role="$(_activation_role)"
        [[ "$install_role" == "unconfigured" ]] && install_role="$(_machine_role)"
        local pkg="$PLUGIN_DIR"
        [[ "$install_role" == "host" ]] && pkg="${PLUGIN_DIR}[store,server]"
        if [[ "$have_uv" -eq 1 ]]; then
            _uv_pip_install --python "$VENV_PYTHON" "$pkg"
        else
            "$VENV_PYTHON" -m pip install "$pkg"
        fi
    }
    if ! pkg_out="$(_pip_install 2>&1)"; then
        _fail 'Failed to install agent-index package into venv'
        printf '%s\n' "$pkg_out" >&2
        exit 1
    fi
    # _pip_install ran in a command-substitution subshell, so its own
    # install_role assignment above didn't survive back to this scope --
    # recompute it directly (cheap, side-effect-free) rather than trying to
    # thread a subshell result back out.
    install_role="$(_activation_role)"
    [[ "$install_role" == "unconfigured" ]] && install_role="$(_machine_role)"
    _ok 'Package installed: agent-index'

    _install_server_venv "$install_role" "$py"

    local prev_version=""
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        prev_version="$(_versioned_current)"
        if ! _test_index_venv "$VENV_DIR" "$VENV_PYTHON" || ! _runtime_origin_under "$VENV_PYTHON" "$VENV_DIR"; then
            _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
            exit 1
        fi
        _versioned_mark_complete
        if ! "$VENV_PYTHON" "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" --link-name ".venv" is-complete "$SRC_VERSION" >/dev/null 2>&1; then
            _fail "Runtime completion marker was not published for versions/$SRC_VERSION -- not activating"
            exit 1
        fi
    fi

    _publish_index_runtime() {
        _index_publication_fresh || return 3
        _versioned_activate || return $?
        deploy_binstub || return $?
        _write_manifest
    }
    local publication_rc=0
    _with_index_publication_lock _publish_index_runtime || publication_rc=$?
    if [[ "$publication_rc" == 3 ]]; then _skip 'Runtime publication superseded'; return 0; fi
    [[ "$publication_rc" == 0 ]] || return "$publication_rc"

    if _test_index_venv "$(dirname "$(dirname "$LINK_PYTHON")")" "$LINK_PYTHON" \
        && _runtime_origin_under "$LINK_PYTHON" "$(dirname "$(dirname "$LINK_PYTHON")")"; then
        _ok 'Verification: module imports successfully'
    else
        _fail 'Verification: module import failed'
        exit 1
    fi

    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        _versioned_gc "$prev_version"
    fi

    case ":$PATH:" in
        *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
        *) _step "Add $LOCAL_BIN to your PATH: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
    }
    _ensure_uv || return 1
    _with_index_build_lock "$VENV_DIR" _ensure_runtime_build
}

_write_manifest() {
    _write_index_manifest() {
    _index_publication_fresh || return 0
    _git_info() {
        local path="$1" commit branch dirty
        commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
        branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        dirty="false"
        [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
        echo "$commit $branch $dirty"
    }
    write_deploy_manifest agent-index agent-index "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV_DIR" "" "${COPILOT_PLUGIN_STAGED_FROM:-}"
    }
    _with_index_publication_lock _write_index_manifest
}

_machine_role() {
    # Explicit machine-level role used by bare management calls and to preserve
    # an already-configured host runtime during updates from another repo.
    if [[ -n "${AGENT_INDEX_ROLE:-}" ]]; then
        local r
        r="$(printf '%s' "$AGENT_INDEX_ROLE" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
        if [[ "$r" == "host" || "$r" == "client" ]]; then printf '%s' "$r"; return 0; fi
    fi
    local cfg="${AGENT_INDEX_CONFIG:-$INSTALL_DIR/config.yaml}"
    cfg="${cfg/#\~/$HOME}"
    if [[ -f "$cfg" ]]; then
        local cr
        cr="$(sed -n 's/^[[:space:]]*\(role\|engine\)[[:space:]]*:[[:space:]]*"\?\([A-Za-z]*\)"\?.*/\2/p' "$cfg" | head -n1 | tr '[:upper:]' '[:lower:]')"
        case "$cr" in
            host|client) printf '%s' "$cr"; return 0 ;;
            engine|server|indexer) printf 'host'; return 0 ;;
            none|consumer) printf 'client'; return 0 ;;
        esac
    fi
    printf 'unconfigured'
}

_activation_role() {
    # A repository activates agent-index only by explicitly designating its
    # indexer(s). Bare management calls may use the machine-level role.
    local repo_root me
    repo_root="${AGENT_INDEX_REPO:-}"
    [[ -n "$repo_root" ]] || repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -z "$repo_root" ]]; then _machine_role; return 0; fi
    me="$(printf '%s' "${AGENT_INDEX_MACHINE:-$(hostname -s)}" |
        tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    local py role
    py="$(command -v python3 || command -v python || true)"
    [[ -n "$py" ]] || { printf 'unconfigured'; return 0; }
    if ! role="$("$py" -E -X utf8 "$SCRIPT_DIR/resolve-activation-role.py" --repo "$repo_root" --machine "$me" 2>/dev/null)"; then
        _warn 'Activation role resolver failed -- leaving this repository unconfigured'
        printf 'unconfigured'
        return 0
    fi
    case "$role" in host|client) printf '%s' "$role" ;; *) printf 'unconfigured' ;; esac
}

_install_engine() {
    if [[ "${AGENT_INDEX_NO_ENGINE_DEPS:-}" == 1 ]]; then
        _skip 'Engine runtime skipped (AGENT_INDEX_NO_ENGINE_DEPS=1)'
        return 1
    fi
    _install_engine_build() {
    # Provision the DURABLE engine venv (agent-index-engine, the torch stack) at
    # AGENT_INDEX_ENGINE_HOME. Built ONCE and skipped if present (idempotent);
    # never rebuilt by a service `update`. Non-fatal -- a failure here leaves the
    # light, torch-free service fully functional. With arg "upgrade", an existing
    # venv is upgraded in place (the explicit engine-runtime update path) instead
    # of skipped.
    local upgrade=0
    [[ "${1:-}" == "upgrade" ]] && upgrade=1
    if [[ "${AGENT_INDEX_NO_ENGINE_DEPS:-}" == "1" ]]; then
        _skip "Engine runtime skipped (AGENT_INDEX_NO_ENGINE_DEPS=1)"
        return 1
    fi
    if [[ "$upgrade" -eq 0 ]] && _test_index_venv "$ENGINE_VENV" "$ENGINE_VENV_PYTHON"; then
        _skip "Engine runtime already provisioned (durable venv preserved): $ENGINE_VENV"
        return 0
    fi
    local py
    py="$(_find_python)" || { _warn 'Python not found -- cannot provision engine runtime'; return 1; }
    if [[ "$upgrade" -eq 1 ]]; then
        _step 'Updating durable engine runtime (torch stack) -- may take a while'
    else
        _step 'Provisioning durable engine runtime (torch stack) -- one-time, may take a while'
    fi
    mkdir -p "$ENGINE_HOME"
    _ensure_uv_index
    _new_index_venv "$ENGINE_VENV" "$ENGINE_VENV_PYTHON" "$py" "$have_uv" || return 1

    # zdd is a declared dependency of agent-index but is not on PyPI -- install it
    # from the vendored lib first so pip can satisfy the requirement.
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            _uv_pip_install --python "$ENGINE_VENV_PYTHON" "$zdd_dir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet >/dev/null 2>&1 || { _warn 'Engine zdd install failed'; return 1; }
        else
            "$ENGINE_VENV_PYTHON" -m pip install "$zdd_dir" >/dev/null 2>&1 || { _warn 'Engine zdd install failed'; return 1; }
        fi
    fi

    # agent-procutil is likewise a `uv`-editable canonical reference in a
    # dev checkout (no local copy, not on PyPI) -- pre-install it the same
    # way as zdd above. Unlike zdd's own silent `|| true`, a failed refresh
    # here must fail the whole engine install: `agent-index` declares only
    # an UNVERSIONED `agent-procutil` requirement, so the main-package
    # install below could still "succeed" against a stale copy already
    # present in a preserved engine venv, silently shipping old shared
    # code (PR #4465 review).
    local rc=0
    local procutil_dir
    if procutil_dir="$(_resolve_vendored_lib agent-procutil)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            _uv_pip_install --python "$ENGINE_VENV_PYTHON" "$procutil_dir" --reinstall-package agent-procutil --refresh-package agent-procutil --quiet >/dev/null 2>&1 || rc=$?
        else
            "$ENGINE_VENV_PYTHON" -m pip install "$procutil_dir" >/dev/null 2>&1 || rc=$?
        fi
    fi

    # agent-index-engine (plugins/agent-index/server/) -- a SEPARATE, independently
    # installable program that owns the heavy embedding stack, into the DURABLE
    # venv only. It depends on the light `agent-index` base package (index_config,
    # engine.generation, engine.client) -- installed first, exactly like the zdd
    # pre-install above, since neither is on PyPI and a plain `pip install
    # agent-index-engine` cannot resolve "agent-index" on its own.
    #
    # Torch install is TWO STEPS so a GPU host works even behind a managed/CFS
    # package feed:
    #   1. Install agent-index-engine from the DEFAULT feed (governed mirror or
    #      public PyPI) -- CPU torch wheel + ALL of torch's pure-python deps
    #      (sympy, networkx, ...) + the rest of the engine stack.
    #   2. If AGENT_INDEX_TORCH_INDEX is set (a CUDA wheel index), SWAP the torch
    #      wheel ONLY from that index with --no-deps. Essential on a CFS box: the
    #      CUDA index links torch's pure-python deps to files.pythonhosted.org
    #      (often network-blocked), so we take deps from the reachable default feed
    #      (step 1) and only the reachable CUDA torch wheel here; --no-deps skips
    #      re-resolving the CUDA build's exact dep pins through the blocked host.
    local torch_idx="${AGENT_INDEX_TORCH_INDEX:-}"
    if [[ "$rc" -ne 0 ]]; then
        :
    elif [[ "$have_uv" -eq 1 ]]; then
        _uv_pip_install --python "$ENGINE_VENV_PYTHON" "$PLUGIN_DIR" || rc=$?
        if [[ "$rc" -eq 0 ]]; then
            local uv_args=(--python "$ENGINE_VENV_PYTHON" "$PLUGIN_DIR/server")
            [[ "$upgrade" -eq 1 ]] && uv_args+=(--upgrade)
            _uv_pip_install "${uv_args[@]}" || rc=$?
        fi
        if [[ "$rc" -eq 0 && -n "$torch_idx" ]]; then
            _step "Swapping in CUDA torch from the configured CUDA wheel index (wheel only, --no-deps)"
            _uv_pip_install --python "$ENGINE_VENV_PYTHON" --index-url "$torch_idx" --no-deps --reinstall-package torch torch || rc=$?
        fi
    else
        "$ENGINE_VENV_PYTHON" -m pip install "$PLUGIN_DIR" || rc=$?
        if [[ "$rc" -eq 0 ]]; then
            local pip_args=(-m pip install "$PLUGIN_DIR/server")
            [[ "$upgrade" -eq 1 ]] && pip_args+=(--upgrade)
            "$ENGINE_VENV_PYTHON" "${pip_args[@]}" || rc=$?
        fi
        if [[ "$rc" -eq 0 && -n "$torch_idx" ]]; then
            _step "Swapping in CUDA torch from the configured CUDA wheel index (wheel only, --no-deps)"
            "$ENGINE_VENV_PYTHON" -m pip install --index-url "$torch_idx" --no-deps --force-reinstall torch || rc=$?
        fi
    fi
    if [[ "$rc" -ne 0 ]]; then
        _warn 'Engine runtime install failed (torch stack) -- light service unaffected; provision later with the "engine" action'
        return 1
    fi
    if ! _test_index_venv "$ENGINE_VENV" "$ENGINE_VENV_PYTHON" \
        || ! "$ENGINE_VENV_PYTHON" -c 'import torch' 2>/dev/null; then
        _warn 'Engine venv failed final interpreter/prefix or torch import validation'
        return 1
    fi
    if [[ "$upgrade" -eq 1 ]]; then
        _ok "Engine runtime updated (durable venv): $ENGINE_VENV"
    else
        _ok "Engine runtime provisioned (durable venv): $ENGINE_VENV"
    fi
    return 0
    }
    _engine_cached() {
        if [[ "${1:-}" != upgrade ]] && _test_index_venv "$ENGINE_VENV" "$ENGINE_VENV_PYTHON"; then return 0; fi
        return 3
    }
    local cached_rc=0
    _with_index_build_lock "$ENGINE_VENV" _engine_cached "$@" || cached_rc=$?
    [[ "$cached_rc" != 0 ]] || return 0
    [[ "$cached_rc" == 3 ]] || return "$cached_rc"
    local have_uv=0
    if _ensure_uv; then have_uv=1; else _warn 'uv acquisition failed -- using the engine pip fallback'; fi
    _with_index_build_lock "$ENGINE_VENV" _install_engine_build "$@"
}

_restart_engine_daemon() {
    # Restart the engine daemon so a freshly-updated durable venv is loaded -- the
    # ONE place a restart is intended (the explicit engine-runtime update path),
    # decoupled from the service `update` (which must never bounce the engine).
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Engine daemon restart skipped (--no-service)"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" ]]; then
        systemctl --user restart "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        if systemctl --user is-active "$ENGINE_SYSTEMD_UNIT" >/dev/null 2>&1; then
            _ok "Engine daemon restarted (new engine runtime loaded) ($ENGINE_SYSTEMD_UNIT)"
        else
            _warn "Engine daemon restart failed -- check: systemctl --user status agent-index-engine"
        fi
    else
        _register_engine_daemon
    fi
}

_write_engine_files() {
    if [[ ! -f "$ENGINE_ENV_FILE" ]]; then
        cat > "$ENGINE_ENV_FILE" << 'ENVEOF'
# agent-index engine daemon environment
AGENT_INDEX_ENGINE_HOST=127.0.0.1
AGENT_INDEX_ENGINE_PORT=8421
ENVEOF
        _ok "Engine env: $ENGINE_ENV_FILE"
    else
        _skip "Engine env already exists: $ENGINE_ENV_FILE"
    fi
    cat > "$ENGINE_LAUNCHER" << EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1
export AGENT_INDEX_ENGINE_HOME="$ENGINE_HOME"
if [[ -f "$ENGINE_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENGINE_ENV_FILE"
    set +a
fi
cd "$ENGINE_HOME"
exec "$ENGINE_VENV_PYTHON" -I -X utf8 -m agent_index engine run
EOF
    chmod +x "$ENGINE_LAUNCHER"
}

_register_engine_daemon() {
    # Register the persistent systemd --user unit that runs the warm engine from
    # the durable venv through a stable launcher. A warm engine is left untouched
    # (never restarted) when it is already active.
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Engine daemon skipped (--no-service)"
        return 0
    fi
    if [[ ! -x "$ENGINE_VENV_PYTHON" ]]; then
        _skip "Engine runtime not provisioned -- daemon not registered"
        return 0
    fi
    _write_engine_files
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- run 'agent-index engine run' via your own supervisor on this host"
        return 0
    fi
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-index -- durable, persistent embedding-engine daemon (warm, torch)
After=network.target

[Service]
Type=simple
Environment=PYTHONUTF8=1
Environment=AGENT_INDEX_ENGINE_HOME=$ENGINE_HOME
EnvironmentFile=-$ENGINE_ENV_FILE
ExecStart=$ENGINE_LAUNCHER
Restart=on-failure
RestartSec=5
WorkingDirectory=$ENGINE_HOME

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
    # Keep a warm engine warm: only START if not already active (never restart on re-register).
    if systemctl --user is-active "$ENGINE_SYSTEMD_UNIT" >/dev/null 2>&1; then
        _skip "Engine daemon already running -- leaving the warm engine untouched ($ENGINE_SYSTEMD_UNIT)"
    else
        systemctl --user start "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        if systemctl --user is-active "$ENGINE_SYSTEMD_UNIT" >/dev/null 2>&1; then
            _ok "Engine daemon installed + started ($ENGINE_SYSTEMD_UNIT)"
        else
            _warn "Engine daemon installed but not active -- check: systemctl --user status agent-index-engine"
        fi
    fi
}

_write_service_files() {
    if [[ ! -f "$ENV_FILE" ]]; then
        cat > "$ENV_FILE" << 'ENVEOF'
# agent-index service environment
AGENT_INDEX_HOST=127.0.0.1
# AGENT_INDEX_PORT=0  # unset/0 = OS-assigned dynamic port advertised via rendezvous
ENVEOF
        _ok "Service env: $ENV_FILE"
    else
        _skip "Service env already exists: $ENV_FILE"
    fi
    cat > "$SERVICE_LAUNCHER" << EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi
_root="$INSTALL_DIR"
_resolver="\$_root/bin/resolve-runtime.sh"
AGENT_RT_PY=""
if [[ -f "\$_resolver" ]]; then
    AGENT_RT_ROOT="\$_root"
    # shellcheck disable=SC1090
    . "\$_resolver"
fi
if [[ -z "\${AGENT_RT_PY:-}" ]]; then
    printf '%s\n' '[agent-index] no complete, importable runtime slot is available.' >&2
    exit 1
fi
cd "$INSTALL_DIR"
exec "\$AGENT_RT_PY" -I -X utf8 -m agent_index start
EOF
    chmod +x "$SERVICE_LAUNCHER"
}

_install_service() {
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Service skipped (--no-service)"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- run 'agent-index start' manually if this host runs the service"
        return 0
    fi
    mkdir -p "$UNIT_DIR"
    _write_service_files
    cat > "$UNIT_DIR/$SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-index -- portable indexing/search service shell
After=network.target

[Service]
Type=simple
EnvironmentFile=-$ENV_FILE
Environment=PYTHONUTF8=1
ExecStart=$SERVICE_LAUNCHER
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    # --no-restart: refresh the unit (for next boot) but do NOT restart now --
    # used after a graceful zdd cutover already stood the new service up beside
    # the old (Thread B). A `systemctl restart` here would spawn a SECOND daemon.
    if [[ "${1:-}" == "--no-restart" ]]; then
        _ok "Service unit refreshed ($SYSTEMD_UNIT); not restarted -- the graceful cutover already brought the new service up"
        return 0
    fi
    systemctl --user restart "$SYSTEMD_UNIT" 2>/dev/null || true
    if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
        _ok "Service installed + started ($SYSTEMD_UNIT)"
    else
        _warn "Service installed but not active -- check: systemctl --user status agent-index"
    fi
}

_install_logon_autostart() {
    # Default durable tier on POSIX: user-scoped systemd units around the same
    # stable launchers. If systemd-user is unavailable we keep the tier-1
    # ensure path only and report the degraded persistence contract.
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Durable auto-start skipped (--no-service)"
        return 0
    fi
    local role
    role="$(_activation_role)"
    if [[ "$role" != "host" ]]; then
        _skip "Durable auto-start skipped (role: $role)"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- keeping tier-1 ensure only"
        return 0
    fi
    if [[ -x "$ENGINE_VENV_PYTHON" ]]; then
        _register_engine_daemon
    else
        _skip "Engine durable auto-start skipped -- engine runtime not provisioned"
    fi
    if _service_healthy; then
        _install_service --no-restart
    else
        _install_service
    fi
}

_status() {
    bash "$SCRIPT_DIR/runtime-gate.sh" status
}

_start() {
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user start "$SYSTEMD_UNIT"
        _ok "Service started ($SYSTEMD_UNIT)"
    elif [[ -x "$LINK_PYTHON" ]]; then
        if _service_healthy; then _skip 'Service already running -- not starting a second daemon'; return 0; fi
        nohup "$LINK_PYTHON" -I -X utf8 -m agent_index start >> "$INSTALL_DIR/service.log" 2>&1 &
        _ok "Service process started"
    else
        _fail 'Runtime not installed'
        exit 1
    fi
}

_stop() {
    if [[ -x "$LINK_PYTHON" ]]; then
        "$LINK_PYTHON" -I -X utf8 -m agent_index stop || true
        # The durable engine daemon is a SEPARATE detached process from the
        # light service stopped above (daemon.py, launched by 'engine start' /
        # Ensure-Running, outliving any systemd unit that happens to exist for
        # it). Stopping only the service leaves it running indefinitely --
        # this is the "kill the detached child, not just the task" gotcha
        # (service-lifecycle-supervision.md) applied to a plain process
        # rather than a scheduled task/unit. `engine stop` is idempotent and a
        # no-op when the engine was never started.
        "$LINK_PYTHON" -I -X utf8 -m agent_index engine stop || true
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok "Service stopped ($SYSTEMD_UNIT)"
    fi
}

_uninstall() {
    [[ "$DRY_RUN" -eq 1 ]] && echo '(dry run -- nothing will be changed)'
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[dry-run] would stop agent-index"
        [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]] && echo "[dry-run] would disable + remove: $SYSTEMD_UNIT"
        [[ -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" ]] && echo "[dry-run] would stop + disable + remove: $ENGINE_SYSTEMD_UNIT"
        [[ -e "$STUB" ]] && echo "[dry-run] would remove binstub: $STUB"
        if [[ "$PURGE" -eq 1 ]]; then
            [[ -d "$ENGINE_HOME" ]] && echo "[dry-run] would PURGE engine home: $ENGINE_HOME"
            [[ -d "$INSTALL_DIR" ]] && echo "[dry-run] would PURGE: $INSTALL_DIR"
        else
            echo "[dry-run] engine home + install dir would be kept (--purge to delete)"
        fi
        echo "agent-index uninstall dry run complete -- nothing was changed"
        return 0
    fi
    _stop
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$UNIT_DIR/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" ]]; then
        systemctl --user stop "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        systemctl --user disable "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    rm -f "$STUB"
    if [[ "$PURGE" -eq 1 ]]; then
        rm -rf "$ENGINE_HOME"
        rm -rf "$INSTALL_DIR"
    fi
    _ok 'agent-index uninstalled'
}

_service_healthy() {
    # Health-gate on the LIVE routing endpoint (active.json ephemeral port); a
    # stale active.json pointing at a dead pid correctly reads as unhealthy.
    # Prefer the runtime's OWN venv python (always present when installed) over a
    # global python3; fall back to curl so a host without either still works.
    local aj="$INSTALL_DIR/active.json"
    [ -f "$aj" ] || return 1
    local py=""
    if [ -x "$LINK_PYTHON" ]; then py="$LINK_PYTHON"
    elif command -v python3 >/dev/null 2>&1; then py="python3"; fi
    if [ -n "$py" ]; then
        "$py" - "$aj" <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    p = json.load(open(sys.argv[1]))["active"]["port"]
    urllib.request.urlopen("http://127.0.0.1:%d/health" % int(p), timeout=2).read()
except Exception:
    sys.exit(1)
PY
        return $?
    fi
    if command -v curl >/dev/null 2>&1; then
        local port
        port="$(sed -n 's/.*"port"[: ]*\([0-9]\{1,\}\).*/\1/p' "$aj" | head -n1)"
        [ -n "$port" ] || return 1
        curl -fsS --max-time 2 "http://127.0.0.1:$port/health" >/dev/null 2>&1
        return $?
    fi
    return 1
}

_ensure_service_running() {
    # Idempotent, user-mode. If a healthy daemon already serves, do nothing; else
    # start it (systemd --user is user-mode; nohup fallback). Never elevated.
    if [[ "$NO_SERVICE" -eq 1 ]]; then _skip 'Service ensure skipped (--no-service)'; return 0; fi
    # A client runs NO local indexer daemon: its MCP/CLI reach the designated
    # host's service over the trusted SSH transport (config.client_url). Only a
    # host runs a local service -- mirrors the engine gate and the single-host-
    # service architecture (a fleet fans in SSH forwards to ONE host service).
    local role
    role="$(_activation_role)"
    if [[ "$role" != "host" ]]; then
        _skip "Local indexer service skipped (role: $role) -- only an explicitly configured host runs a local daemon"
        return 0
    fi
    [[ -x "$LINK_PYTHON" ]] || { _skip 'Runtime not installed -- service not ensured'; return 0; }
    if _service_healthy; then _skip 'Service already healthy (user-mode daemon serving)'; return 0; fi
    # Unhealthy: if a systemd unit exists, RESTART it -- a hung-but-"active" unit
    # will not recover from `start` (a no-op). Else fall through to _start.
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user restart "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok 'Service ensured (systemd --user restart)'
        return 0
    fi
    _start
}

_ensure_engine_running() {
    # Host-side durable embedding engine, user-mode. Left warm if already active.
    if [[ "$NO_SERVICE" -eq 1 ]]; then _skip 'Engine ensure skipped (--no-service)'; return 0; fi
    [[ -x "$ENGINE_VENV_PYTHON" ]] || { _skip 'Engine runtime not provisioned -- engine not ensured'; return 0; }
    if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active "$ENGINE_SYSTEMD_UNIT" >/dev/null 2>&1; then
        _skip 'Engine already serving -- leaving the warm engine untouched'; return 0
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT" ]]; then
        systemctl --user start "$ENGINE_SYSTEMD_UNIT" 2>/dev/null || true
    else
        "$ENGINE_VENV_PYTHON" -I -X utf8 -m agent_index engine start 2>/dev/null || true
    fi
    _ok 'Engine ensured (user-mode durable daemon)'
}

_ensure_running() {
    # The DEFAULT user-mode lifecycle. No elevation. A host runs the engine then
    # the local service; a client runs neither (_ensure_service_running self-gates
    # on role), so this is a no-op on a client.
    if [[ "$(_activation_role)" == "host" ]]; then _ensure_engine_running; fi
    _ensure_service_running
}

# Thread B graceful cutover (parity with install.ps1 Invoke-ServiceCutover):
# if a LIVE routed service (published active.json + healthy /health) is serving,
# stand the new slot up beside it and flip routing via the OS-agnostic zdd
# `agent_index deploy` seam -- so no in-flight request is dropped -- instead of a
# systemctl restart. Returns 0 when the cutover brought a healthy service up (so
# the caller refreshes the unit with --no-restart); 1 to fall back to the
# SIGTERM-graceful `systemctl restart` (uvicorn drains, so the invariant holds).
# The warm embedding engine is a SEPARATE unit on a fixed port -- left untouched.
_service_cutover() {
    [[ "$NO_SERVICE" -eq 1 ]] && return 1
    command -v systemctl >/dev/null 2>&1 || return 1
    # A configured host may start/cut over. An unrelated repository may only
    # cut over an already-running service owned by an explicit machine host.
    local role
    role="$(_activation_role)"
    if [[ "$role" != "host" ]]; then
        [[ "$(_machine_role)" == "host" ]] && _service_healthy || return 1
    fi
    [[ -x "$LINK_PYTHON" ]] || return 1
    # Cut over only a LIVE routed service; no live endpoint -> fall back.
    _service_healthy || return 1
    _step 'Graceful cutover: moving the live service to the new build (zdd active/passive flip)...'
    if "$LINK_PYTHON" -I -X utf8 -m agent_index deploy >/dev/null 2>&1 && _service_healthy; then
        _ok 'Service cut over to the new build (routing flipped; old drained + retired; warm engine untouched)'
        return 0
    fi
    _warn 'Graceful cutover did not complete -- falling back to a SIGTERM-graceful systemctl restart'
    return 1
}

case "$ACTION" in
    install)
        _ensure_runtime
        _service_cutover || _ensure_running
        _install_logon_autostart
        _skip 'Durable engine runtime unchanged; use engine / engine-update for the heavy stack'
        ;;
    update)                                                         # Thread B: installer-driven graceful zdd cutover (a version update must never kill in-flight work)
        _downgrade_guard
        _ensure_runtime
        _service_cutover || _ensure_running
        _install_logon_autostart
        _skip 'Durable engine runtime unchanged; use engine / engine-update for the heavy stack'
        ;;
    ensure)  # user-mode safety net + default durable auto-start registration; also
             # stamps the binstub on a never-provisioned machine (fits a sessionStart
             # hook's grace window -- no venv, no uv, no heavy runtime build here)
        [ -x "$STUB" ] || {
            mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
            deploy_binstub
        }
        _ensure_running
        _install_logon_autostart
        ;;
    stamp) do_stamp ;;
    provision)
        _ensure_runtime
        _service_cutover || _ensure_running
        _skip 'Durable engine runtime unchanged; use engine / engine-update for the heavy stack'
        ;;
    engine) _install_engine || true; _register_engine_daemon ;;     # explicit host-side provisioning (role-independent)
    engine-update)                                                  # rebuild durable engine venv + restart daemon (decoupled from service update)
        if _install_engine upgrade; then _restart_engine_daemon; fi ;;
    status) _status ;;
    start) _start ;;
    stop) _stop ;;
    uninstall) _uninstall ;;
    *) _fail "Unknown action: $ACTION"; exit 2 ;;
esac
