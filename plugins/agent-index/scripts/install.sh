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
case "$__legacy_action" in
    start|ensure)
        _skip 'Host service lifecycle is managed by an already-running agent-dispatch supervisor; this installer cannot launch it'
        [[ "$__legacy_action" == ensure ]] && exit 0
        exit 2 ;;
esac
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
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
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
        local __descriptor __opened __opened_after __current __file_digest
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
            __descriptor="/proc/$BASHPID/fd/$__fd"
            [[ -e "$__descriptor" ]] || __descriptor="/dev/fd/$__fd"
            __opened="$(__payload_stat "$__descriptor" true)" || {
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
            __opened_after="$(__payload_stat "$__descriptor" true)" || true
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
    command -v uv >/dev/null 2>&1 && return 0
    local tooldir="$INSTALL_DIR/tool"
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; return 0; fi
    _step "uv not found -- vendoring a standalone uv into $tooldir"
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
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; _ok "Vendored uv into $tooldir"; return 0; fi
    _fail "uv is required but not found, and vendoring failed (no reachable uv installer). Install uv, then retry."
    return 1
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

# Cheap 'stamp': splat the binstub + payload marker, defer the venv build until
# explicit setup (fits a sessionStart hook's grace window). No venv, no uv.
do_stamp() {
    echo ''; echo '=== agent-index stamp (defer runtime to explicit setup) ==='; echo ''
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
    deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions after explicit setup."
}

_ensure_runtime() {
    if [[ ! -d "$PKG_SRC_DIR" ]]; then
        _fail "Package source not found at $PKG_SRC_DIR"
        exit 1
    fi
    local py
    py="$(_find_python)" || { _fail 'Python not found on PATH (need 3.10+)'; exit 1; }
    _ok "Python: $py"
    # Self-acquire uv (vendored if absent) + mirror the governed pip index to uv
    # so a solo/standalone install works on a pristine or governed box.
    _ensure_uv || exit 1
    _ensure_uv_index
    local have_uv=0
    command -v uv >/dev/null 2>&1 && have_uv=1

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    _ok "Directories: $INSTALL_DIR"

    # Detach an invalid active marker before any rebuild. If provisioning fails
    # later, no success-shaped current-version pointer remains.
    local active_version="" active_ready=0 active_python=""
    [[ -f "$INSTALL_DIR/current-version" ]] && active_version="$(tr -d ' \t\r\n' < "$INSTALL_DIR/current-version")"
    if [[ "$VERSIONED_RUNTIME" == 1 && -n "$active_version" ]]; then
        active_python="$INSTALL_DIR/versions/$active_version/bin/python"
        [[ -x "$active_python" ]] || active_python="$INSTALL_DIR/versions/$active_version/Scripts/python.exe"
        if [[ -x "$active_python" ]] \
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
        if [[ -x "$VENV_PYTHON" ]] \
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

    if [[ ! -x "$VENV_PYTHON" ]]; then
        if [[ "$have_uv" -eq 1 ]]; then
            _step 'Creating venv via uv...'
            _versioned_slot_clean
            uv venv "$VENV_DIR" --allow-existing >/dev/null 2>&1 \
                || "$py" -m venv "$VENV_DIR" >/dev/null 2>&1
        else
            _step 'Creating venv via python -m venv...'
            "$py" -m venv "$VENV_DIR" >/dev/null 2>&1
        fi
        [[ -x "$VENV_PYTHON" ]] || { _fail "Venv creation failed -- $VENV_PYTHON not found"; exit 1; }
        _ok 'Venv created'
    else
        _skip 'Venv already exists'
    fi


    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            uv pip install --python "$VENV_PYTHON" "$zdd_dir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet
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

    _pip_install() {
        # This installer owns only the lightweight base/client footprint.
        local pkg="$PLUGIN_DIR"
        # Dispatch alone installs host dependencies; every plugin-side build is base-only.
        if [[ "$have_uv" -eq 1 ]]; then
            uv pip install --python "$VENV_PYTHON" "$pkg"
        else
            "$VENV_PYTHON" -m pip install "$pkg"
        fi
    }
    if ! pkg_out="$(_pip_install 2>&1)"; then
        _fail 'Failed to install agent-index package into venv'
        printf '%s\n' "$pkg_out" >&2
        exit 1
    fi
    _ok 'Package installed: agent-index'

    deploy_binstub

    local prev_version=""
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        prev_version="$(_versioned_current)"
        if ! _runtime_origin_under "$VENV_PYTHON" "$VENV_DIR"; then
            _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
            exit 1
        fi
        _versioned_mark_complete
        if ! "$VENV_PYTHON" "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" --link-name ".venv" is-complete "$SRC_VERSION" >/dev/null 2>&1; then
            _fail "Runtime completion marker was not published for versions/$SRC_VERSION -- not activating"
            exit 1
        fi
        _versioned_activate || exit 1
    fi

    _write_manifest

    if _runtime_origin_under "$LINK_PYTHON" "$(dirname "$(dirname "$LINK_PYTHON")")"; then
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

_write_manifest() {
    _git_info() {
        local path="$1" commit branch dirty
        commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
        branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        dirty="false"
        [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
        echo "$commit $branch $dirty"
    }
    local manifest="$INSTALL_DIR/deploy-manifest.json"
    local kind ver commit branch dirty
    kind="$(_source_kind "$PLUGIN_DIR")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root _c _b _d
        repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
        read -r _c _b _d <<< "$(_git_info "$repo_root")"
        commit="\"$_c\""; branch="\"$_b\""; dirty="$_d"
    fi
    local tmp="$manifest.tmp"
    cat > "$tmp" << EOF
{
  "schema_version": 3,
  "service": "agent-index",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-index",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest"
    _ok "Deploy manifest written (source: $kind)"
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
    local repo_root repo_cfg me hosts
    repo_root="${AGENT_INDEX_REPO:-}"
    [[ -n "$repo_root" ]] || repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -z "$repo_root" ]]; then _machine_role; return 0; fi
    repo_cfg="$repo_root/.agent-index/config.yaml"
    [[ -f "$repo_cfg" ]] || { printf 'unconfigured'; return 0; }
    me="$(printf '%s' "${AGENT_INDEX_MACHINE:-$(hostname -s)}" |
        tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    local py role
    py="$(command -v python3 || command -v python || true)"
    [[ -n "$py" ]] || { printf 'unconfigured'; return 0; }
    role="$("$py" "$SCRIPT_DIR/resolve-activation-role.py" --config "$repo_cfg" --machine "$me" 2>/dev/null || true)"
    case "$role" in host|client) printf '%s' "$role" ;; *) printf 'unconfigured' ;; esac
}

_install_engine() {
    # Provision the DURABLE engine venv (agent-index[engine], the torch stack) at
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
    if [[ -x "$ENGINE_VENV_PYTHON" && "$upgrade" -eq 0 ]]; then
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
    local have_uv=0
    command -v uv >/dev/null 2>&1 && have_uv=1
    if [[ "$have_uv" -eq 1 ]]; then
        uv venv "$ENGINE_VENV" --allow-existing >/dev/null 2>&1 || "$py" -m venv "$ENGINE_VENV" >/dev/null 2>&1
    else
        "$py" -m venv "$ENGINE_VENV" >/dev/null 2>&1
    fi
    [[ -x "$ENGINE_VENV_PYTHON" ]] || { _warn "Engine venv creation failed -- $ENGINE_VENV_PYTHON not found"; return 1; }

    # zdd is a declared dependency of agent-index but is not on PyPI -- install it
    # from the vendored lib first so pip can satisfy the requirement.
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if [[ "$have_uv" -eq 1 ]]; then
            uv pip install --python "$ENGINE_VENV_PYTHON" "$zdd_dir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet >/dev/null 2>&1 || true
        else
            "$ENGINE_VENV_PYTHON" -m pip install "$zdd_dir" >/dev/null 2>&1 || true
        fi
    fi

    # agent-index[engine] -- the heavy embedding stack into the DURABLE venv only.
    #
    # Torch install is TWO STEPS so a GPU host works even behind a managed/CFS
    # package feed:
    #   1. Install agent-index[engine] from the DEFAULT feed (governed mirror or
    #      public PyPI) -- CPU torch wheel + ALL of torch's pure-python deps
    #      (sympy, networkx, ...) + the rest of the engine stack.
    #   2. If AGENT_INDEX_TORCH_INDEX is set (a CUDA wheel index), SWAP the torch
    #      wheel ONLY from that index with --no-deps. Essential on a CFS box: the
    #      CUDA index links torch's pure-python deps to files.pythonhosted.org
    #      (often network-blocked), so we take deps from the reachable default feed
    #      (step 1) and only the reachable CUDA torch wheel here; --no-deps skips
    #      re-resolving the CUDA build's exact dep pins through the blocked host.
    local rc=0
    local torch_idx="${AGENT_INDEX_TORCH_INDEX:-}"
    if [[ "$have_uv" -eq 1 ]]; then
        local uv_args=(pip install --python "$ENGINE_VENV_PYTHON" "$PLUGIN_DIR[store,engine]")
        [[ "$upgrade" -eq 1 ]] && uv_args+=(--upgrade)
        uv "${uv_args[@]}" || rc=$?
        if [[ "$rc" -eq 0 && -n "$torch_idx" ]]; then
            _step "Swapping in CUDA torch from the configured CUDA wheel index (wheel only, --no-deps)"
            uv pip install --python "$ENGINE_VENV_PYTHON" --index-url "$torch_idx" --no-deps --reinstall-package torch torch || rc=$?
        fi
    else
        local pip_args=(-m pip install "$PLUGIN_DIR[store,engine]")
        [[ "$upgrade" -eq 1 ]] && pip_args+=(--upgrade)
        "$ENGINE_VENV_PYTHON" "${pip_args[@]}" || rc=$?
        if [[ "$rc" -eq 0 && -n "$torch_idx" ]]; then
            _step "Swapping in CUDA torch from the configured CUDA wheel index (wheel only, --no-deps)"
            "$ENGINE_VENV_PYTHON" -m pip install --index-url "$torch_idx" --no-deps --force-reinstall torch || rc=$?
        fi
    fi
    if [[ "$rc" -ne 0 ]]; then
        _warn 'Engine runtime install failed (torch stack) -- light service unaffected; provision later with the "engine" action'
        return 1
    fi
    if ! "$ENGINE_VENV_PYTHON" -c 'import torch' 2>/dev/null; then
        _warn 'Engine venv built but torch import failed'
        return 1
    fi
    if [[ "$upgrade" -eq 1 ]]; then
        _ok "Engine runtime updated (durable venv): $ENGINE_VENV"
    else
        _ok "Engine runtime provisioned (durable venv): $ENGINE_VENV"
    fi
    return 0
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

_register_engine_daemon() {
    # Register the persistent systemd --user unit that runs the warm engine from
    # the durable venv. A warm engine is left untouched (never restarted) when it
    # is already active.
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Engine daemon skipped (--no-service)"
        return 0
    fi
    if [[ ! -x "$ENGINE_VENV_PYTHON" ]]; then
        _skip "Engine runtime not provisioned -- daemon not registered"
        return 0
    fi
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
ExecStart=$ENGINE_VENV_PYTHON -I -X utf8 -m agent_index engine run
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
    cat > "$UNIT_DIR/$SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-index -- portable indexing/search service shell
After=network.target

[Service]
Type=simple
EnvironmentFile=-$ENV_FILE
Environment=PYTHONUTF8=1
ExecStart=$VENV_PYTHON -I -X utf8 -m agent_index start
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
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok "Service stopped ($SYSTEMD_UNIT)"
    fi
}

_uninstall() {
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
        _skip 'Host service is dispatch-managed; independent embedding engine unchanged'
        ;;
    update)                                                         # Thread B: installer-driven graceful zdd cutover (a version update must never kill in-flight work)
        _downgrade_guard
        _ensure_runtime
        _skip 'Host service is dispatch-managed; independent embedding engine unchanged'
        ;;
    ensure) _ensure_running ;;  # user-mode auto-run safety net (sessionStart hook) -- start if not already healthy
    stamp) do_stamp ;;
    provision)
        _ensure_runtime
        _skip 'Only the lightweight client runtime was provisioned; host service is dispatch-managed'
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
