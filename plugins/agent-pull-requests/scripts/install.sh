#!/usr/bin/env bash
# Install/update the agent-pull-requests runtime.
set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ACTION="${AGENT_PULL_REQUESTS_ACTION:-install}"
FORCE="${AGENT_PULL_REQUESTS_FORCE:-0}"
INSTALL_DIR="${AGENT_PULL_REQUESTS_INSTALL_DIR:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        install|update|status|uninstall|stamp|provision) ACTION="$1"; shift ;;
        --force) FORCE=1; shift ;;
        --install-dir)
            [[ $# -ge 2 ]] || { _fail "--install-dir requires a value"; exit 2; }
            INSTALL_DIR="$2"
            shift 2
            ;;
        *) _fail "unknown argument: $1"; exit 2 ;;
    esac
done
export AGENT_PULL_REQUESTS_ACTION="$ACTION"
export AGENT_PULL_REQUESTS_FORCE="$FORCE"
export AGENT_PULL_REQUESTS_INSTALL_DIR="$INSTALL_DIR"

__install_lock_link=""
__install_lock_fd=""

_exit_install_lock() {
    if [[ -n "$__install_lock_link" ]]; then
        local __owner
        __owner="$(readlink "$__install_lock_link" 2>/dev/null || true)"
        [[ "$__owner" == "$$" ]] && rm -f "$__install_lock_link"
        __install_lock_link=""
    elif [[ -n "$__install_lock_fd" ]]; then
        flock -u "$__install_lock_fd" 2>/dev/null || true
        eval "exec $__install_lock_fd>&-"
        __install_lock_fd=""
    fi
}

_enter_install_lock() {
    mkdir -p "$INSTALL_DIR" 2>/dev/null || true
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != "1" ]]; then
        __install_lock_fd=8
        exec 8>"$INSTALL_DIR/.install.lock" 2>/dev/null || return 1
        flock -w 300 8 || return 1
        return 0
    fi
    __install_lock_link="$INSTALL_DIR/.install.lock.pid"
    until ln -s "$$" "$__install_lock_link" 2>/dev/null; do
        local __owner __live
        __owner="$(readlink "$__install_lock_link" 2>/dev/null || true)"
        case "$__owner" in
            *[!0-9]*|"") __live=0 ;;
            *) if kill -0 "$__owner" 2>/dev/null; then __live=1; else __live=0; fi ;;
        esac
        if [[ "$__live" == 0 && "$(readlink "$__install_lock_link" 2>/dev/null || true)" == "$__owner" ]]; then
            rm -f "$__install_lock_link"
        else
            sleep 1
        fi
    done
}

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

if [[ -z "${UV_HTTP_TIMEOUT:-}" ]]; then export UV_HTTP_TIMEOUT=60; fi

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_pull_requests"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-pull-requests}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"

# === install-contract:v3 versioned-venv (agent-pull-requests: .venv-as-symlink) ===
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
LINK_PYTHON="$VENV_PYTHON"

_versioned_activate() {
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    if ! "$VENV_PYTHON" -c 'import agent_pull_requests' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    if ! _versioned_mark_complete; then
        return 1
    fi
    local prev
    prev="$("$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo "")"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink --no-link; then
        _fail "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        return 1
    fi
    _ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
    if [[ -n "$prev" ]]; then
        "$VENV_PYTHON" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$prev" 2>&1 | sed 's/^/  gc: /' || true
    else
        "$VENV_PYTHON" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  gc: /' || true
    fi
}
# === end install-contract:v3 versioned-venv ===

_bootstrap_python() {
    if [[ -x "$VENV_DIR/bin/python" ]]; then echo "$VENV_DIR/bin/python"; return 0; fi
    if [[ -x "$LINK_DIR/bin/python" ]]; then echo "$LINK_DIR/bin/python"; return 0; fi
    local __c
    for __c in python3 python; do
        if command -v "$__c" >/dev/null 2>&1; then command -v "$__c"; return 0; fi
    done
    return 1
}

_payload_hash() {
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
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || py=""
    if [[ -z "$py" ]]; then
        [[ -e "$VENV_DIR" ]] || return 0
        _fail "Cannot inspect or clean versions/$SRC_VERSION without a bootstrap Python interpreter"
        return 1
    fi
    "$py" "$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" slot "$SRC_VERSION" --clean-incomplete 2>&1 | sed 's/^/  ...    /'
    local rc=$?
    if [[ "$rc" -ne 0 ]]; then
        _fail "Failed to clean incomplete runtime slot versions/$SRC_VERSION"
        return "$rc"
    fi
    return 0
}

_versioned_mark_complete() {
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || py=""
    if [[ -z "$py" ]]; then
        _fail "Cannot mark versions/$SRC_VERSION complete without a bootstrap Python interpreter"
        return 1
    fi
    local ph
    ph="$(_payload_hash)"
    local args=("$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" mark-complete "$SRC_VERSION")
    if [[ -n "$ph" ]]; then args+=(--payload-hash "$ph"); fi
    "$py" "${args[@]}" 2>&1 | sed 's/^/  ...    /'
    local rc=$?
    if [[ "$rc" -ne 0 ]]; then
        _fail "Failed to mark versions/$SRC_VERSION complete"
        return "$rc"
    fi
    return 0
}

_versioned_is_complete() {
    local expect_hash="${1:-}"
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 1
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py
    py="$(_bootstrap_python)" || return 1
    [[ -n "$py" ]] || return 1
    local args=("$vr" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" is-complete "$SRC_VERSION")
    if [[ -n "$expect_hash" ]]; then args+=(--expect-hash "$expect_hash"); fi
    "$py" "${args[@]}" >/dev/null 2>&1
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

_git_info() {
    local path="$1" commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
    echo "$commit $branch $dirty"
}

# shellcheck source=/dev/null
. "$SCRIPT_DIR/installer-engine.sh"

_install_hook_files() {
    local bin_hook_dir="$INSTALL_DIR/bin"
    mkdir -p "$bin_hook_dir"
    for h in bootstrap-check.ps1 bootstrap-check.sh; do
        [[ -f "$SCRIPT_DIR/$h" ]] && cp -f "$SCRIPT_DIR/$h" "$bin_hook_dir/$h"
    done
    _ok "Session-start hook: $bin_hook_dir/bootstrap-check.sh"
}

if [[ "$ACTION" == "install" || "$ACTION" == "update" || "$ACTION" == "stamp" || "$ACTION" == "provision" ]]; then
    if ! _enter_install_lock; then
        _fail "Timed out waiting for the install transaction lock: $INSTALL_DIR/.install.lock"
        exit 1
    fi
    trap '_exit_install_lock' EXIT INT TERM
fi

if [[ "$ACTION" == "stamp" ]]; then
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    snap_dir="$INSTALL_DIR/snapshots/$SRC_VERSION"
    snap_tmp="$snap_dir.tmp-$$"
    rm -rf -- "$snap_tmp"
    mkdir -p "$snap_tmp"
    cp -a "$PLUGIN_DIR/." "$snap_tmp/"
    rm -rf -- "$snap_tmp/.git" "$snap_tmp/.venv" "$snap_tmp/__pycache__" "$snap_tmp/build" "$snap_tmp/dist" "$snap_tmp/node_modules" "$snap_tmp/tests" "$snap_tmp/.pytest_cache" "$snap_tmp/.mypy_cache"
    mkdir -p "$(dirname "$snap_dir")"
    rm -rf -- "$snap_dir"
    mv "$snap_tmp" "$snap_dir"
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$snap_dir/.payload-source"
    printf '%s\n' "$snap_dir" > "$INSTALL_DIR/payload-dir"
    printf '%s\n' "$SRC_VERSION" > "$INSTALL_DIR/stamped-version"
    _ok "Snapshot: $snap_dir"
    write_simple_binstub \
        "agent-pull-requests" "agent_pull_requests" "$INSTALL_DIR" "$LOCAL_BIN" "$INSTALL_DIR/bin" "scripts/install.sh" "AGENT_PULL_REQUESTS_NO_SELFPROVISION" \
        "$SCRIPT_DIR/resolve-runtime.ps1" "$SCRIPT_DIR/resolve-runtime.sh"
    _install_hook_files
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
    exit 0
fi

if [[ "$ACTION" == "status" ]]; then
    echo '=== agent-pull-requests status ==='
    [[ -x "$LINK_PYTHON" ]] && _ok "Runtime: $VENV_DIR" || _skip "Runtime missing: $VENV_DIR"
    [[ -x "$LOCAL_BIN/agent-pull-requests" ]] && _ok "Binstub: $LOCAL_BIN/agent-pull-requests" || _skip "Binstub missing: $LOCAL_BIN/agent-pull-requests"
    [[ -f "$MANIFEST_PATH" ]] && _ok "Deploy manifest: $MANIFEST_PATH" || _skip "Deploy manifest missing"
    exit 0
fi

if [[ "$ACTION" == "uninstall" ]]; then
    rm -f "$LOCAL_BIN/agent-pull-requests"
    rm -rf "$INSTALL_DIR"
    _ok 'agent-pull-requests runtime removed'
    exit 0
fi

echo ""
echo "=== agent-pull-requests install ==="
echo ""

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    _fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

UV_CMD="$(ensure_uv "$INSTALL_DIR" tool 1 || true)"
if [[ -z "$UV_CMD" ]]; then
    _fail 'uv is required but could not be resolved or acquired'
    exit 1
fi
mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"
_install_hook_files

PAYLOAD_HASH="$(_payload_hash)"
SLOT_ALREADY_COMPLETE=0
if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
    if _versioned_is_complete "$PAYLOAD_HASH"; then
        SLOT_ALREADY_COMPLETE=1
        _skip "Runtime slot versions/$SRC_VERSION already matches the current payload"
    elif _versioned_is_complete; then
        _fail "Runtime slot versions/$SRC_VERSION is already complete for different payload content; bump the plugin version instead of rebuilding an immutable slot in place"
        if [[ "$FORCE" -eq 1 ]]; then
            _fail "--force cannot rebuild a complete immutable runtime slot in place"
        fi
        exit 1
    elif [[ -e "$VENV_DIR" ]]; then
        if ! _versioned_slot_clean; then
            exit 1
        fi
    fi
fi

if [[ "$SLOT_ALREADY_COMPLETE" -eq 0 && ( "$FORCE" -eq 1 || ! -x "$VENV_PYTHON" || ! -f "$VENV_DIR/pyvenv.cfg" ) ]]; then
    if ! new_signed_venv "$UV_CMD" "$VENV_DIR" "3.10"; then
        _fail "Venv creation failed -- $VENV_PYTHON not found"
        exit 1
    fi
    _ok 'Venv created'
elif [[ "$SLOT_ALREADY_COMPLETE" -eq 0 ]]; then
    _skip 'Venv already exists'
fi

if [[ "$SLOT_ALREADY_COMPLETE" -eq 0 ]]; then
    export INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB="$PLUGIN_DIR"
    if ! invoke_uv_pip_install_resilient "$UV_CMD" --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet; then
        _fail 'Failed to install agent-pull-requests package into venv'
        exit 1
    fi
    unset INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB
    _ok 'Package installed: agent-pull-requests'
fi

if [[ "$SLOT_ALREADY_COMPLETE" -eq 0 ]]; then
    _versioned_activate || exit 1
fi
write_simple_binstub \
    "agent-pull-requests" "agent_pull_requests" "$INSTALL_DIR" "$LOCAL_BIN" "$INSTALL_DIR/bin" "scripts/install.sh" "AGENT_PULL_REQUESTS_NO_SELFPROVISION" \
    "$SCRIPT_DIR/resolve-runtime.ps1" "$SCRIPT_DIR/resolve-runtime.sh"
write_deploy_manifest "agent-pull-requests" "agent-pull-requests" "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV_DIR"

echo ""
if "$LINK_PYTHON" -c 'import agent_pull_requests' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo ""
echo '=== agent-pull-requests install complete ==='
echo '  Try: agent-pull-requests --version'
exit 0
