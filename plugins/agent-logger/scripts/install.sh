#!/usr/bin/env bash
# Agent Logger -- session-sync installer (Linux / WSL).
#
# Creates a venv at ~/.agent-logger, installs the agent-logger package, and
# registers a systemd *user* timer that runs `session-sync run --prune`
# every 4 hours. Idempotent.
#
# Usage:
#   bash scripts/install.sh install     # first time
#   bash scripts/install.sh update      # re-install package, keep timer
#   bash scripts/install.sh uninstall   # remove timer (keeps config)
#   bash scripts/install.sh status
set -euo pipefail

ACTION="${1:-status}"
SKIP_PACKAGE_INSTALL=0
SKIP_STAMP=0
INSTALL_DIR="${HOME}/.agent-logger"
VENV="${INSTALL_DIR}/.venv"
LOCAL_BIN="${HOME}/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ "$ACTION" == uninstall ]]; then
    __uninstall_args=("$@")
    for (( __arg=1; __arg<${#__uninstall_args[@]}; __arg++ )); do
        if [[ "${__uninstall_args[$__arg]}" == --install-dir ]]; then
            __arg=$((__arg + 1))
            __root="${__uninstall_args[$__arg]:-}"
            if [[ -z "$__root" || "$__root" == -* ]]; then
                printf '%s\n' '--install-dir requires a directory' >&2
                exit 2
            fi
            case "/$__root/" in
                */../*) printf '%s\n' 'Logger uninstall requires a root without parent-traversal components' >&2; exit 2 ;;
            esac
            [[ "$__root" == /* ]] || __uninstall_args[$__arg]="$PWD/$__root"
        fi
    done
    set -- "${__uninstall_args[@]}"
fi

# Staging itself writes installation state, so publication admission precedes it.
case "$ACTION" in
    install|update|stamp|provision)
        # Preserve the existing Windows shell readiness check before rerooting.
        case "${OSTYPE:-}:$PWD" in
            msys*:*/.copilot/installed-plugins/*|cygwin*:*/.copilot/installed-plugins/*)
                printf 'self-stage failed: Windows shell launch CWD pins the payload; launch from HOME or use install.ps1\n' >&2
                exit 1
                ;;
        esac
        __publication_project="${COPILOT_PROJECT_DIR:-$PWD}"
        if [[ -n "${COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT:-}" &&
              ( -n "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}" ||
                ( -n "${COPILOT_PLUGIN_INSTALL_STAGED:-}" && "$SCRIPT_DIR" == */.install-stage/* ) ) ]]; then
            __publication_project="$COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"
        fi
        __publication_project="$(cd -P -- "$__publication_project" && pwd)" || exit 1
        export COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT="$__publication_project"
        __publication_args=("$@")
        __publication_root="$INSTALL_DIR"
        for ((__publication_i=1; __publication_i<${#__publication_args[@]}; __publication_i++)); do
            if [[ "${__publication_args[__publication_i]}" == --install-dir ]]; then
                __publication_i=$((__publication_i + 1))
                __publication_root="${__publication_args[__publication_i]:?--install-dir requires a directory}"
            fi
        done
        [[ "$__publication_root" == /* ]] || __publication_root="$PWD/$__publication_root"
        for ((__publication_i=1; __publication_i<${#__publication_args[@]}; __publication_i++)); do
            if [[ "${__publication_args[__publication_i]}" == --install-dir ]]; then
                __publication_i=$((__publication_i + 1))
                __publication_args[__publication_i]="$__publication_root"
            fi
        done
        set -- "${__publication_args[@]}"
        __publication_payload="$PLUGIN_DIR"
        if [[ -n "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}" &&
              -n "${COPILOT_PLUGIN_STAGED_FROM:-}" &&
              "$PLUGIN_DIR" != */.copilot/installed-plugins/* ]]; then
            __publication_payload="$COPILOT_PLUGIN_STAGED_FROM"
        fi
        __publication_python=""
        while IFS= read -r __publication_candidate; do
            [[ -f "$__publication_candidate" && -x "$__publication_candidate" ]] || continue
            case "$__publication_candidate" in */Microsoft/WindowsApps/*) continue ;; esac
            __publication_candidate="$(cd -P -- "$(dirname -- "$__publication_candidate")" &&
                printf '%s/%s\n' "$PWD" "$(basename -- "$__publication_candidate")")" || exit 1
            "$__publication_candidate" -I -B -c 'import sys; assert sys.version_info >= (3,10)' \
                >/dev/null 2>&1 || continue
            __publication_python="$__publication_candidate"
            break
        done < <(type -ap python3 python)
        if [[ -n "$__publication_python" && -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
            __publication_binding=(
                -I -B -X utf8 "$SCRIPT_DIR/command_binding.py"
                --payload-root "$PLUGIN_DIR" --plugin-id agent-logger
                --legacy-root "$__publication_root" --source-payload-root "$__publication_payload"
                --project-root "$__publication_project"
            )
            if [[ -n "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}" ]]; then
                "$__publication_python" "${__publication_binding[@]}" \
                    --check-lifecycle --lifecycle-operation publish || exit $?
            else
                unset COPILOT_PLUGIN_STAGED_FROM
                [[ "$ACTION" != stamp ]] || __publication_binding+=(--deferred-runtime)
                __publication_bash="$(type -P bash)" || exit 1
                __publication_bash="$(cd -P -- "$(dirname -- "$__publication_bash")" &&
                    printf '%s/%s\n' "$PWD" "$(basename -- "$__publication_bash")")" || exit 1
                cd "$HOME" || exit 1
                # MSYS may retain a native-exec parent; close its script descriptor first.
                exec "$__publication_bash" -c 'exec "$@"' bash \
                    "$__publication_python" "${__publication_binding[@]}" --run-lifecycle -- \
                    "$__publication_bash" "$SCRIPT_DIR/install.sh" "$@"
            fi
        elif [[ -n "${COPILOT_EXTENSIONS_COMMAND_LIFECYCLE:-}" ]]; then
            printf '%s\n' 'Runtime publication refused: lifecycle custody requires an existing Python 3.10+ executable.' >&2
            exit 3
        fi
        if [[ -z "$__publication_python" || -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
            __publication_helper="$SCRIPT_DIR/installation-context/installation-context.sh"
            __publication_query="$SCRIPT_DIR/installation-context/json-query.awk"
            [[ -f "$__publication_helper" && -f "$__publication_query" ]] || {
                printf '%s\n' 'Runtime publication refused: installation helper is unavailable.' >&2
                exit 3
            }
            __publication_native_payload="$__publication_payload"
            if [[ -n "${COPILOT_PLUGIN_INSTALL_STAGED:-}" &&
                  -n "${COPILOT_PLUGIN_STAGED_FROM:-}" &&
                  "$PLUGIN_DIR" != */.copilot/installed-plugins/* ]]; then
                __publication_native_payload="$COPILOT_PLUGIN_STAGED_FROM"
            fi
            __publication_code=0
            __publication_json="$(bash "$__publication_helper" probe-plugin-publication \
                --payload-root "$__publication_native_payload" --plugin-id agent-logger \
                --legacy-root "$__publication_root" --project-root "$__publication_project")" || __publication_code=$?
            __publication_allowed="$(LC_ALL=C awk -f "$__publication_query" -v mode=get \
                -v query_path=$'deployment\034allowPluginPublication' <<<"$__publication_json")" || exit 3
            __publication_type="$(LC_ALL=C awk -f "$__publication_query" -v mode=type \
                -v query_path=$'deployment\034allowPluginPublication' <<<"$__publication_json")" || exit 3
            __publication_root_query=$'legacy\034root'
            [[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]] || __publication_root_query=runtimeRoot
            __publication_actual="$(LC_ALL=C awk -f "$__publication_query" -v mode=get \
                -v "query_path=$__publication_root_query" <<<"$__publication_json")" || exit 3
            __publication_path="$(LC_ALL=C awk -f "$__publication_query" -v mode=get \
                -v query_path=$'deployment\034path' <<<"$__publication_json")" || exit 3
            if [[ "$__publication_code" != 0 || "$__publication_type" != boolean ||
                  "$__publication_allowed" != true || "$__publication_actual" != /* ||
                  "$__publication_path" != "$__publication_actual/runtime-deployment.json" ||
                  ( -n "${COPILOT_EXTENSIONS_CONTEXT:-}" && "$__publication_actual" != "$__publication_root" ) ]]; then
                printf 'Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' "$__publication_json" >&2
                exit 3
            fi
            for ((__publication_i=1; __publication_i<${#__publication_args[@]}; __publication_i++)); do
                if [[ "${__publication_args[__publication_i]}" == --install-dir ]]; then
                    __publication_i=$((__publication_i + 1))
                    __publication_args[__publication_i]="$__publication_actual"
                fi
            done
            set -- "${__publication_args[@]}"
            __publication_payload="$__publication_native_payload"
        fi
        ;;
esac

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
if [[ -z "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ||
    "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" == */.copilot/installed-plugins/* ]]; then
    __ss_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    __ss_payload="$(cd "$__ss_self_dir/.." && pwd)"
    case "$(printf '%s' "$__ss_payload" | tr '\\' '/')" in
        */.copilot/installed-plugins/*)
            __ss_name="$(sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$__ss_payload/plugin.json" 2>/dev/null | head -1)"
            if [[ -n "$__ss_name" ]]; then
                case "${OSTYPE:-}:$PWD" in
                    msys*:*/.copilot/installed-plugins/*|cygwin*:*/.copilot/installed-plugins/*)
                        printf 'self-stage failed: Windows shell launch CWD pins the payload; launch from HOME or use install.ps1\n' >&2
                        exit 1
                        ;;
                esac
                # Maintenance keeps HOME as CWD; build inputs are absolute staged paths.
                if ! cd "$HOME"; then
                    printf 'self-stage failed: cannot leave the replaceable payload\n' >&2
                    exit 1
                fi
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
                    # Re-exec the watchdog without a payload script descriptor.
                    # Merely changing CWD leaves bash's script handle open on Windows.
                    __ss_watchdog="$(cat <<'INSTALL_WATCHDOG'
                    __ss_entry=$1
                    __ss_name=$2
                    __ss_stage=$3
                    __ss_deadline=$4
                    shift 4
                    set -m
                    bash "$__ss_entry" "$@" &
                    __ss_child=$!
                    set +m
                    __ss_watcher=""
                    __ss_cancel() {
                        local signal_rc="$1"
                        kill -- -"$__ss_child" 2>/dev/null ||
                            kill "$__ss_child" 2>/dev/null || true
                        wait "$__ss_child" 2>/dev/null || true
                        if [[ -n "$__ss_watcher" ]]; then
                            kill "$__ss_watcher" 2>/dev/null || true
                            wait "$__ss_watcher" 2>/dev/null || true
                        fi
                        trap - INT TERM
                        exit "$signal_rc"
                    }
                    trap '__ss_cancel 130' INT
                    trap '__ss_cancel 143' TERM
                    if [[ "$__ss_deadline" -gt 0 ]]; then
                        (
                            __ss_waited=0
                            while kill -0 "$__ss_child" 2>/dev/null; do
                                sleep 1
                                __ss_waited=$((__ss_waited + 1))
                                if [[ "$__ss_waited" -ge "$__ss_deadline" ]]; then
                                    : > "$__ss_stage/.watchdog-fired"
                                    printf '[%sZ] WATCHDOG-KILL %s: install exceeded %ss deadline (child pid %s); killed tree. Slot lacks a completion marker -> will be tossed + retried. Stage: %s\n' \
                                        "$(date -u +%Y-%m-%dT%H:%M:%S)" "$__ss_name" "$__ss_deadline" "$__ss_child" "$__ss_stage" \
                                        >> "$HOME/.$__ss_name/reconcile.err.log" 2>/dev/null || true
                                    kill -- -"$__ss_child" 2>/dev/null || kill "$__ss_child" 2>/dev/null || true
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
INSTALL_WATCHDOG
                    )"
                    exec bash -c "$__ss_watchdog" bash \
                        "$__ss_entry" "$__ss_name" "$__ss_stage" "$__ss_deadline" "$@"
                else
                    printf 'self-stage failed; refusing to run from the replaceable payload\n' >&2
                    exit 1
                fi
            else
                printf 'self-stage requires a plugin identity before leaving the payload\n' >&2
                exit 1
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
    printf '{"ran_from":"%s","staged_from":"%s","staged":%s,"working_dir":"%s","child_pid":%s,"grandchild_pid":%s}\n' \
        "$__sm_self_dir" "${COPILOT_PLUGIN_STAGED_FROM:-}" "$__sm_staged" "$PWD" "$$" "$__sm_grand_pid" \
        > "$__sm_home/smoke.json"
    sleep "$__sm_sleep"
    exit 0
fi
# === end install-contract:v4 smoke seam ===

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if [[ -z "${UV_HTTP_TIMEOUT:-}" ]]; then export UV_HTTP_TIMEOUT=60; fi

_bootstrap_python() {
    # Keep construction supervision outside the candidate being built.
    local __c
    for __c in python3 python; do
        if command -v "$__c" >/dev/null 2>&1; then command -v "$__c"; return 0; fi
    done
    return 1
}

if [[ $# -gt 0 ]]; then
    shift
fi
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)
            INSTALL_DIR="${2:?--install-dir requires a directory}"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        *)
            printf 'ERROR: unknown option: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done
if [[ "$ACTION" == uninstall ]]; then
    case "/$INSTALL_DIR/" in
        */../*) printf '%s\n' 'Logger uninstall requires a root without parent-traversal components' >&2; exit 2 ;;
    esac
fi
if [[ "$INSTALL_DIR" != /* ]]; then
    INSTALL_DIR="$PWD/$INSTALL_DIR"
fi
_normalize_install_root() {
    local remaining="$1" component normalized=""
    while [[ -n "$remaining" ]]; do
        component="${remaining%%/*}"
        if [[ "$remaining" == */* ]]; then remaining="${remaining#*/}"; else remaining=""; fi
        case "$component" in ""|.) ;; *) normalized="$normalized/$component" ;; esac
    done
    printf '%s\n' "${normalized:-/}"
}
INSTALL_DIR="$(_normalize_install_root "$INSTALL_DIR")"
if [[ "$ACTION" == uninstall ]]; then
    py="$(_bootstrap_python)" || {
        printf '%s\n' 'Cannot validate Logger uninstall ownership without a bootstrap Python' >&2
        exit 1
    }
    INSTALL_DIR="$("$py" -I -B "$SCRIPT_DIR/uninstall_guard.py" \
        --root "$INSTALL_DIR" --resolve-root)" || exit 1
fi
LEGACY_INSTALL_DIR="$(_normalize_install_root "$HOME/.agent-logger")"
legacy_cmp="$(printf '%s' "$LEGACY_INSTALL_DIR" | tr '\\' '/' | tr '[:upper:]' '[:lower:]')"
install_cmp="$(printf '%s' "$INSTALL_DIR" | tr '\\' '/' | tr '[:upper:]' '[:lower:]')"
PUBLISH_GLOBAL_BINSTUBS=1
SERVICE_SUFFIX=""
if [[ "$install_cmp" != "$legacy_cmp" ]]; then
    PUBLISH_GLOBAL_BINSTUBS=0
    if command -v sha256sum >/dev/null 2>&1; then
        SERVICE_SUFFIX="$(printf '%s' "$install_cmp" | sha256sum | awk '{print substr($1,1,12)}')"
    elif command -v shasum >/dev/null 2>&1; then
        SERVICE_SUFFIX="$(printf '%s' "$install_cmp" | shasum -a 256 | awk '{print substr($1,1,12)}')"
    else
        SERVICE_SUFFIX="$(printf '%s' "$install_cmp" | cksum | awk '{print $1}')"
    fi
fi
VENV="${INSTALL_DIR}/.venv"

UNIT_DIR="${HOME}/.config/systemd/user"
TIMER_NAME="agent-logger-sync${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}"
export AGENT_LOGGER_HOME="$INSTALL_DIR"

log()  { printf '  [%s] %s\n' "$1" "$2"; }
ok()   { log "OK" "$1"; }
chg()  { log "->" "$1"; }
warn() { log "WARN" "$1"; }

_ok()  { ok "$1"; }
_step(){ log '...' "$1"; }
_warn(){ warn "$1"; }
_fail(){ printf '  [FAIL] %s\n' "$1" >&2; }

# shellcheck source=/dev/null
. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"

# === install-contract:v3 versioned-venv (agent-logger: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build the venv into versions/<version> and
# make the `.venv` path a symlink into it, so the binstub symlinks, the systemd
# timer unit, and the deploy-manifest resolve through the link. LINK_DIR is the
# stable `.venv` path; VENV is redirected to the versions/<v> slot (build +
# health-gate). ALWAYS versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED /
# AGENT_LOGGER_VERSIONED) and the legacy in-place fork are retired;
# scripts/versioned_runtime.py owns the swap + migration + gc.
LINK_DIR="$VENV"
VERSIONED_RUNTIME=1
SRC_VERSION=""
if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
    SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" | head -n1)"
fi
if [[ -z "$SRC_VERSION" ]]; then
    echo "[FAIL] Cannot determine plugin version from pyproject.toml (required for the versioned runtime)." >&2
    exit 1
fi
VENV="$INSTALL_DIR/versions/$SRC_VERSION"

_test_runtime_healthy() {
    local output
    if ! output="$("$VENV/bin/python" -I -m agent_logger --help 2>&1)"; then  # runtime-resolution: allow install-time slot health-gate
        _fail "Isolated agent_logger CLI health probe failed: $output"
        return 1
    fi
    return 0
}

_versioned_activate() {
    _assert_source_runtime || return 1
    # Health-gate the slot, swap the `.venv` symlink onto it (first migration moves
    # a legacy real `.venv` aside), gc keeping current + previous-good. POSIX rename
    # tolerates the timer's open files, and systemd restarts on the new slot.
    # Returns non-zero on failure. No-op in legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV/bin/python"  # runtime-resolution: allow install-time slot health-gate (VENV is the versioned slot)
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || {
        _fail "Runtime slot interpreter is unavailable: $py"; return 1;
    }
    if ! _test_runtime_healthy; then
        warn "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    deploy_runtime_helpers || return 1
    _assert_source_runtime || return 1
    _versioned_mark_complete || return 1
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        _publish_source_snapshot >/dev/null || return 1
    fi
    _assert_source_runtime || return 1
    local prev
    prev="$("$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo "")"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink --no-link; then
        warn "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        return 1
    fi
    ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
    if [[ -n "$prev" ]]; then
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$prev" 2>&1 | sed 's/^/  gc: /' || true
    else
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  gc: /' || true
    fi
    return 0
}
# === end install-contract:v3 versioned-venv ===

_rt_python() {
    # The versioned-slot interpreter, resolved as the binstubs do (deployed
    # bin/resolve-runtime.sh: current-version -> last-known-good -> newest slot).
    # `_versioned_activate` runs with `--no-link`, so there is no `.venv` link to
    # resolve a console script through (#765 uniform-runtime-resolution).
    local resolver="$INSTALL_DIR/bin/resolve-runtime.sh"
    local py=""
    if [[ -f "$resolver" ]]; then
        py="$(AGENT_RT_ROOT="$INSTALL_DIR"; . "$resolver" >/dev/null 2>&1; printf '%s' "${AGENT_RT_PY:-}")"
    fi
    # No mid-install fallback: `status` is the only caller and always runs
    # after bin/ is deployed. Empty -> the caller reports "not installed".
    [[ -n "$py" && -x "$py" ]] || return 1
    printf '%s' "$py"
}

_payload_hash() {
    # Cheap payload fingerprint for the completion marker (#935): sha256 of
    # pyproject.toml + the vendored-lib version set. Detects a dev-checkout that
    # changed the payload WITHOUT bumping the version. Fails on any error.
    local __parts=""
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then __parts="$(cat "$PLUGIN_DIR/pyproject.toml")"; fi
    if [[ -d "$PLUGIN_DIR/libs" ]]; then
        local __f
        while IFS= read -r __f; do
            __parts="$__parts"$'\n'"$(cat "$__f")"
        done < <(find "$PLUGIN_DIR/libs" -name pyproject.toml 2>/dev/null | sort)
    fi
    # Fails (no output) when no SHA-256 implementation works: the caller must
    # then refuse to publish a slot without its fingerprint.
    local __d
    if command -v sha256sum >/dev/null 2>&1; then
        __d="$(printf '%s' "$__parts" | sha256sum 2>/dev/null)" || return 1
    elif command -v shasum >/dev/null 2>&1; then
        __d="$(printf '%s' "$__parts" | shasum -a 256 2>/dev/null)" || return 1
    elif command -v openssl >/dev/null 2>&1; then
        __d="$(printf '%s' "$__parts" | openssl dgst -sha256 -r 2>/dev/null)" || return 1
    else
        return 1
    fi
    __d="${__d%% *}"
    [[ "$__d" =~ ^[0-9a-f]{64}$ ]] || return 1
    printf '%s\n' "$__d"
}

_versioned_slot_clean() {
    _assert_source_runtime || return 1
    [[ "$VERSIONED_RUNTIME" == 1 ]] || {
        printf '%s\n' 'Construction requires a versioned runtime candidate' >&2; return 1;
    }
    local vr="$SCRIPT_DIR/versioned_runtime.py" py helpers
    py="$(_bootstrap_python)" || return 1
    [[ -n "$py" ]] || return 1
    if ! declare -F _runtime_construction_begin >/dev/null; then
        helpers="$("$py" -I "$vr" --root "$INSTALL_DIR" construction-shell --shell sh)" || return 1
        eval "$helpers"
    fi
    _runtime_construction_begin "$py" "$vr" "$INSTALL_DIR" "$SRC_VERSION" \
        "$(_payload_hash)" "$(basename "$LINK_DIR")" || return 1
    _assert_source_runtime
}

_versioned_mark_complete() {
    _assert_source_runtime || return 1
    local actual_hash
    actual_hash="$(_payload_hash)" || return 1
    if [[ -n "${1:-}" && "$1" != "$actual_hash" ]]; then
        printf '%s\n' 'Payload changed during construction; completion refused' >&2
        return 1
    fi
    _runtime_construction_complete "$actual_hash"
}

_write_runtime_manifest() {
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        [[ -n "${SOURCE_RUNTIME_SNAPSHOT:-}" ]] || {
            _fail "Source manifest requires a committed immutable snapshot"; return 1;
        }
        (
            PLUGIN_DIR="$SOURCE_RUNTIME_SNAPSHOT"
            SCRIPT_DIR="$PLUGIN_DIR/scripts"
            _git_info() { printf '%s\n' "$SOURCE_MANIFEST_GIT_INFO"; }
            _assert_source_runtime || return 1
            local fields py
            py="$(_bootstrap_python)" || return 1
            fields="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
                --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-logger \
                --expected-runtime-version "$SRC_VERSION" \
                --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" --format shell-fields)" || return 1
            write_deploy_manifest agent-logger agent-logger "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV" \
                "$fields" "$SOURCE_MANIFEST_ORIGIN" "$PACKAGE_VERSION"
        ) || return 1
        return 0
    fi
    _assert_source_runtime || return 1
    if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
        write_deploy_manifest agent-logger agent-logger "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV" \
            "" "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" "$PACKAGE_VERSION"
        return "$?"
    fi
    local py source_path kind commit branch dirty content_hash manifest_json
    py="$(_bootstrap_python)" || return 1
    source_path="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
    kind="$(_source_kind "$source_path")" || return 1
    commit=""; branch=""; dirty="false"
    if [[ "$kind" == local ]]; then
        local git_info
        git_info="$(_git_info "$(dirname -- "$source_path")")" || return 1
        read -r commit branch dirty <<<"$git_info" || return 1
    fi
    content_hash="$(_payload_hash)" || return 1
    manifest_json="$("$py" -I -B -c '
import datetime,json,platform,socket,sys
kind,path,version,commit,branch,dirty,content_hash,venv = sys.argv[1:]
source = dict(kind=kind,path=path,repo="copilot-extensions",plugin="agent-logger",
              version=version,commit=commit or None,branch=branch or None,dirty=dirty=="true")
if content_hash:
    source["content_hash"] = content_hash
print(json.dumps(dict(
    schema_version=3,service="agent-logger",
    deployed_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    deployed_by=socket.gethostname()+"-"+platform.system().lower(),
    source=source,venv=venv,runtime="python",
)))
' "$kind" "$source_path" "$PACKAGE_VERSION" "$commit" "$branch" "$dirty" "$content_hash" "$VENV")" || return 1
    if ! printf '%s\n' "$manifest_json" |
        "$py" -I -B -X utf8 "$SCRIPT_DIR/command_binding.py" \
            --payload-root "$PLUGIN_DIR" --plugin-id agent-logger --legacy-root "$INSTALL_DIR" \
            --publish-legacy --write-manifest --source-payload-root "$source_path" \
            --project-root "$__publication_project" >/dev/null; then
        _fail 'Command ownership validation refused Logger deploy-manifest publication.'
        return 1
    fi
    ok "Deploy manifest written (source: $kind)"
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
    local path="$1"
    local commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    if [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]]; then
        dirty="true"
    fi
    echo "$commit $branch $dirty"
}

# Serialize the complete mutating installer. The session-start reconciler and
# agent-worktrees universal reconciler may discover the same version drift at
# once; flock is process-held and released automatically on every exit path.
. "$SCRIPT_DIR/source-runtime.sh"
PACKAGE_VERSION="$SRC_VERSION"
SOURCE_RUNTIME_IDENTITY=""
case "$ACTION" in
    install|update|provision|stamp) _initialize_source_runtime || exit 1 ;;
esac
if [[ "$ACTION" != "status" ]]; then
    mkdir -p "$INSTALL_DIR"
    if ! command -v flock >/dev/null 2>&1; then
        printf 'ERROR: flock is required to serialize agent-logger installation\n' >&2
        exit 1
    fi
    exec 8>"$INSTALL_DIR/.install.lock"
    __install_lock_contended=0
    if ! flock -n 8; then
        __install_lock_contended=1
    fi
    if [[ "$__install_lock_contended" = 1 ]] && ! flock -w 300 8; then
        printf 'ERROR: timed out waiting for agent-logger install lock\n' >&2
        exit 1
    fi
    # Reject stale payloads after every lock acquisition. A delayed older
    # process may acquire an uncontended lock after the newer install exits.
    if [[ "$ACTION" =~ ^(install|update|provision|stamp)$ ]]; then
        _assert_source_runtime || exit 1
    fi
    if [[ "$ACTION" =~ ^(install|update|provision|stamp)$ && -z "$SOURCE_RUNTIME_IDENTITY" ]]; then
        __desired="$SRC_VERSION"
        __current=""
        if [[ -f "$INSTALL_DIR/current-version" ]]; then
            __current="$(tr -d '[:space:]' < "$INSTALL_DIR/current-version")"
        fi
        __deployed=""
        __lock_py="$(command -v python3 || command -v python || true)"
        if [[ -n "$__lock_py" && -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
            __deployed="$("$__lock_py" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("source",{}).get("version",""))' "$INSTALL_DIR/deploy-manifest.json" 2>/dev/null || true)"
        elif [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
            __deployed="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$INSTALL_DIR/deploy-manifest.json" | head -1)"
        fi
        __version_cmp() {
            local left="$1" right="$2"
            # Source hashes are opaque, never an ordering dimension.
            left="${left%%+*}"; right="${right%%+*}"
            if [[ -z "$__lock_py" ]]; then
                local left_major left_minor left_patch left_dev
                local right_major right_minor right_patch right_dev
                if [[ "$left" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)(-dev([0-9]+))?$ ]]; then
                    left_major="${BASH_REMATCH[1]}"; left_minor="${BASH_REMATCH[2]}"
                    left_patch="${BASH_REMATCH[3]}"; left_dev="${BASH_REMATCH[5]:-}"
                else
                    printf '%s' -1
                    return
                fi
                if [[ "$right" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)(-dev([0-9]+))?$ ]]; then
                    right_major="${BASH_REMATCH[1]}"; right_minor="${BASH_REMATCH[2]}"
                    right_patch="${BASH_REMATCH[3]}"; right_dev="${BASH_REMATCH[5]:-}"
                else
                    printf '%s' -1
                    return
                fi
                local left_parts=("$left_major" "$left_minor" "$left_patch")
                local right_parts=("$right_major" "$right_minor" "$right_patch")
                local index left_part right_part
                for index in 0 1 2; do
                    left_part="${left_parts[$index]}"
                    right_part="${right_parts[$index]}"
                    if (( 10#$left_part > 10#$right_part )); then printf '%s' 1; return; fi
                    if (( 10#$left_part < 10#$right_part )); then printf '%s' -1; return; fi
                done
                if [[ -z "$left_dev" && -n "$right_dev" ]]; then printf '%s' 1; return; fi
                if [[ -n "$left_dev" && -z "$right_dev" ]]; then printf '%s' -1; return; fi
                if [[ -n "$left_dev" ]]; then
                    if (( 10#$left_dev > 10#$right_dev )); then printf '%s' 1; return; fi
                    if (( 10#$left_dev < 10#$right_dev )); then printf '%s' -1; return; fi
                fi
                printf '%s' 0
                return
            fi
            "$__lock_py" -c '
import re, sys
def key(value):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?", value)
    if not match:
        return None
    major, minor, patch, dev = match.groups()
    return (int(major), int(minor), int(patch), int(dev) if dev is not None else sys.maxsize)
left, right = key(sys.argv[1]), key(sys.argv[2])
print((left > right) - (left < right) if left is not None and right is not None else -1)
' "$left" "$right" 2>/dev/null || printf '%s' -1
        }
        __stamped=""
        if [[ -f "$INSTALL_DIR/stamped-version" ]]; then
            __stamped="$(tr -d '[:space:]' < "$INSTALL_DIR/stamped-version")"
        fi
        if [[ "$ACTION" =~ ^(install|update|provision)$ && -n "$__desired" ]]; then
            if [[ -n "$__current" && -f "$INSTALL_DIR/versions/$__current/.install-complete.json" ]]; then
                __active_cmp="$(__version_cmp "$__current" "$__desired")"
                if [[ "$__active_cmp" = 1 || ( "$__install_lock_contended" = 1 && "$__active_cmp" = 0 && "$__deployed" = "$__current" ) ]]; then
                    SKIP_PACKAGE_INSTALL=1
                    # Action-specific unit work must target the active newer
                    # slot, not the queued payload's stale SRC_VERSION.
                    VENV="$INSTALL_DIR/versions/$__current"
                fi
            fi
            if [[ "$SKIP_PACKAGE_INSTALL" = 0 && -n "$__stamped" && -f "$INSTALL_DIR/payload-dir" ]]; then
                __stamped_cmp="$(__version_cmp "$__stamped" "$__desired")"
                if [[ "$__stamped_cmp" = 1 ]]; then
                    printf 'ERROR: refusing stale agent-logger %s payload %s; stamped payload %s is newer\n' "$ACTION" "$__desired" "$__stamped" >&2
                    exit 1
                fi
            fi
        elif [[ "$ACTION" = stamp && -n "$__desired" ]]; then
            if [[ -n "$__current" && -f "$INSTALL_DIR/versions/$__current/.install-complete.json" ]]; then
                __current_cmp="$(__version_cmp "$__current" "$__desired")"
                if [[ "$__current_cmp" = 1 || ( "$__install_lock_contended" = 1 && "$__current_cmp" = 0 ) ]]; then
                    SKIP_STAMP=1
                fi
            fi
            if [[ "$SKIP_STAMP" = 0 && -n "$__stamped" && -f "$INSTALL_DIR/payload-dir" ]]; then
                __stamped_cmp="$(__version_cmp "$__stamped" "$__desired")"
                if [[ "$__stamped_cmp" = 1 || ( "$__install_lock_contended" = 1 && "$__stamped_cmp" = 0 ) ]]; then
                    SKIP_STAMP=1
                fi
            fi
        fi
    fi
fi

# Mirror pip's configured index to uv on a governed box (public PyPI TLS-blocked):
# uv does not read pip.conf, so derive index-url from pip config / the pip.conf
# files and export it. No-op where pip has no index (e.g. pristine -- the index
# then arrives via env / the clean-room fixture).
_ensure_uv_index() {
    [ -n "${UV_INDEX_URL:-}${UV_DEFAULT_INDEX:-}" ] && return 0
    local idx=""
    if command -v pip >/dev/null 2>&1; then idx="$(pip config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [ -z "$idx" ] && command -v pip3 >/dev/null 2>&1; then idx="$(pip3 config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [ -z "$idx" ]; then
        local f
        for f in "${PIP_CONFIG_FILE:-}" "$HOME/.config/pip/pip.conf" "$HOME/.pip/pip.conf" /etc/pip.conf /etc/xdg/pip/pip.conf; do
            [ -n "$f" ] && [ -f "$f" ] || continue
            idx="$(sed -n 's/^[[:space:]]*index-url[[:space:]]*=[[:space:]]*//p' "$f" | head -n1 | tr -d '[:space:]')"
            [ -n "$idx" ] && break
        done
    fi
    if [ -n "$idx" ]; then export UV_DEFAULT_INDEX="$idx"; chg "uv index derived from pip config (governed-feed bridge)"; fi
}

# Deploy the self-provisioning `agent-logger` binstub (install-on-first-use). Fast
# path execs the venv's `agent-logger` console script; otherwise it provisions on
# first use -- announcing (a human line + a machine-readable ::agent-provisioning::
# signal so a caller can extend its timeout). Cold commands delegate to the
# selected snapshot's queued dispatcher, as do the five auxiliary wrappers.
deploy_binstub() {
    local temporary
    mkdir -p "${LOCAL_BIN}" || return 1
    [[ ! -d "$LOCAL_BIN/agent-logger" ]] || {
        _fail "Logger binstub destination is a directory: $LOCAL_BIN/agent-logger"; return 1;
    }
    temporary="$(mktemp "$LOCAL_BIN/.agent-logger.XXXXXX")" || return 1
    [[ -f "$temporary" && ! -L "$temporary" ]] || {
        _fail "Logger binstub staging is not a regular file: $temporary"; return 1;
    }
    if ! cat > "$temporary" << 'STUBEOF'
#!/usr/bin/env bash
# agent-logger binstub -- self-provisioning (install-on-first-use).
# Resolves the interpreter SOLELY via the junction-free versioned-runtime marker
# (the deployed resolve-runtime.sh; uniform-runtime-resolution, #765): current-
# version -> last-known-good -> newest complete slot. NEVER a `.venv` link, NEVER
# a PATH python -- when no slot is installed AGENT_RT_PY is empty and we self-
# provision on first use rather than silently binding the system interpreter.
export PYTHONUTF8=1
_name="agent-logger"
_root="$HOME/.$_name"
_resolver="$_root/bin/resolve-runtime.sh"
if [ ! -f "$_resolver" ] ||
   ! grep -Fq '# Trace contract: normal/read-only/ready-v1' "$_resolver"; then
    printf '%s\n' "[$_name] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry." >&2
    exit 126
fi
_resolve() {
    AGENT_RT_PY=""
    if [ -f "$_resolver" ]; then
        AGENT_RT_ROOT="$_root"
        local AGENT_RT_RESOLVE_TRACE_MODE="${1:-ready}"
        . "$_resolver"
    fi
}
_resolve
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_logger "$@"
if [ -n "${AGENT_LOGGER_NO_SELFPROVISION:-}" ]; then
    printf '%s\n' "[$_name] runtime not provisioned (AGENT_LOGGER_NO_SELFPROVISION set)." >&2
    exit 1
fi
_payload="$(cat "$_root/payload-dir" 2>/dev/null)"
_shim="$_payload/bin/agent-logger"
_gate="$_payload/scripts/runtime-gate.sh"
if [ -z "$_payload" ] || [ ! -x "$_shim" ]; then
    printf '%s\n' "[$_name] cannot self-provision: selected snapshot command not found. Re-enable/update the plugin, then retry." >&2
    exit 127
fi
if [ ! -f "$_gate" ] ||
   ! grep -Fq '# Installation driver contract: logger-first-use-v1' "$_gate"; then
    printf '%s\n' "[$_name] selected snapshot lacks the queued command contract. Re-enable/update the plugin, then retry." >&2
    exit 126
fi
COPILOT_PLUGIN_ROOT="$_payload"
export COPILOT_PLUGIN_ROOT
exec "$_shim" "$@"
STUBEOF
    then
        rm -f "$temporary"
        _fail "Logger binstub write failed: $LOCAL_BIN/agent-logger"
        return 1
    fi
    chmod +x "$temporary" && [[ ! -d "$LOCAL_BIN/agent-logger" ]] &&
        mv -f "$temporary" "$LOCAL_BIN/agent-logger" &&
        [[ ! -e "$temporary" && ! -L "$temporary" && -f "$LOCAL_BIN/agent-logger" ]] || {
        rm -f "$temporary"
        _fail "Logger binstub publication failed: $LOCAL_BIN/agent-logger"
        return 1
    }
    ok "binstub: ${LOCAL_BIN}/agent-logger (self-provisioning)"
}

deploy_runtime_helpers() {
    local r
    local required=(resolve-runtime.ps1 resolve-runtime.sh versioned_runtime.py versioned_runtime_source.py versioned_runtime_publication.py versioned_runtime_leases.py versioned_runtime_construction.py)
    for r in "${required[@]}"; do
        [[ -f "$SCRIPT_DIR/$r" ]] || {
            _fail "Required Logger runtime helper is unavailable: $r"; return 1;
        }
        [[ ! -d "$INSTALL_DIR/bin/$r" ]] || {
            _fail "Logger runtime helper destination is a directory: $r"; return 1;
        }
    done
    mkdir -p "${INSTALL_DIR}/bin" || return 1
    for r in "${required[@]}"; do
        _runtime_publication_copy "$SCRIPT_DIR/$r" "$INSTALL_DIR/bin/$r" || {
            _fail "Logger runtime helper publication failed: $r"
            return 1
        }
    done
}

_resolve_snapshot_engine_source() {
    local ext="$1"
    local local_engine="$SCRIPT_DIR/installer-engine.${ext}"
    local canonical_engine="$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.${ext}"
    if [[ -f "$local_engine" ]]; then
        printf '%s' "$local_engine"
    elif [[ -f "$canonical_engine" ]]; then
        printf '%s' "$canonical_engine"
    else
        _fail "Snapshot installer engine source is unavailable: $canonical_engine"
        return 1
    fi
}

_rewrite_snapshot_engine_ref() {
    local path="$1" canonical_ref="$2" local_ref="$3"
    local tmp="$path.tmp.$$"
    [[ -f "$path" && -r "$path" ]] || {
        _fail "Snapshot installer cannot be read: $path"; return 1;
    }
    [[ ! -d "$tmp" && ! -L "$tmp" ]] || {
        _fail "Snapshot installer rewrite staging is not a regular file path: $tmp"; return 1;
    }
    : > "$tmp" || {
        _fail "Snapshot installer rewrite cannot be prepared: $tmp"; return 1;
    }
    while IFS= read -r line || [[ -n "$line" ]]; do
        local cr=""
        if [[ "$line" == *$'\r' ]]; then
            cr=$'\r'
            line=${line%$'\r'}
        fi
        if [[ "$line" == "$canonical_ref" ]]; then
            printf '%s%s\n' "$local_ref" "$cr" >> "$tmp" || {
                _fail "Snapshot installer rewrite failed: $path"; return 1;
            }
        else
            printf '%s%s\n' "$line" "$cr" >> "$tmp" || {
                _fail "Snapshot installer rewrite failed: $path"; return 1;
            }
        fi
    done < "$path" || {
        _fail "Snapshot installer read failed: $path"; return 1;
    }
    [[ ! -d "$path" ]] && mv -f "$tmp" "$path" && [[ ! -e "$tmp" && -f "$path" ]] || {
        _fail "Snapshot installer rewrite cannot be published: $path"; return 1;
    }
}

_materialize_snapshot_engine() {
    local snapshot_dir="$1"
    local scripts_dir="$snapshot_dir/scripts"
    local sh_src ps1_src sh_dest ps1_dest
    mkdir -p "$scripts_dir" || {
        _fail "Snapshot scripts directory cannot be prepared: $scripts_dir"; return 1;
    }
    sh_src="$(_resolve_snapshot_engine_source sh)" || return 1
    ps1_src="$(_resolve_snapshot_engine_source ps1)" || return 1
    sh_dest="$scripts_dir/installer-engine.sh"
    ps1_dest="$scripts_dir/installer-engine.ps1"
    if [[ "$sh_src" != "$sh_dest" ]]; then
        cp -f "$sh_src" "$sh_dest" || {
            _fail "Snapshot installer engine copy failed: $sh_src"; return 1;
        }
    fi
    if [[ "$ps1_src" != "$ps1_dest" ]]; then
        cp -f "$ps1_src" "$ps1_dest" || {
            _fail "Snapshot installer engine copy failed: $ps1_src"; return 1;
        }
    fi
    _rewrite_snapshot_engine_ref \
        "$scripts_dir/install.sh" \
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"' \
        '. "$SCRIPT_DIR/installer-engine.sh"' || return 1
    _rewrite_snapshot_engine_ref \
        "$scripts_dir/install.ps1" \
        ". (Join-Path \$PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')" \
        ". (Join-Path \$PSScriptRoot 'installer-engine.ps1')" || return 1
}

deploy_auxiliary_compatibility_binstubs() {
    mkdir -p "${LOCAL_BIN}" || return 1
    local name temporary
    for name in session-sync collate-session read-session-digest prepare-session-log ramp-up-session; do
        [[ ! -d "$LOCAL_BIN/$name" ]] || {
            _fail "Logger auxiliary binstub destination is a directory: $LOCAL_BIN/$name"; return 1;
        }
    done
    for name in session-sync collate-session read-session-digest prepare-session-log ramp-up-session; do
        [[ ! -d "$LOCAL_BIN/$name" ]] || {
            _fail "Logger auxiliary binstub destination is a directory: $LOCAL_BIN/$name"; return 1;
        }
        temporary="$(mktemp "$LOCAL_BIN/.$name.XXXXXX")" || return 1
        [[ -f "$temporary" && ! -L "$temporary" ]] || {
            _fail "Logger auxiliary binstub staging is not a regular file: $temporary"; return 1;
        }
        if ! cat > "$temporary" << 'STUBEOF'
#!/usr/bin/env bash
# Legacy/global compatibility wrapper. The owning payload shim selects and
# self-provisions its own runtime; this file never resolves a same-named command
# through PATH.
set -eu
_command="$(basename -- "$0")"
_root="$HOME/.agent-logger"
_payload="$(cat "$_root/payload-dir" 2>/dev/null || true)"
_shim="$_payload/bin/$_command"
if [ -z "$_payload" ] || [ ! -x "$_shim" ]; then
    printf '[%s] owning payload shim not found: %s\n' "$_command" "$_shim" >&2
    printf '[%s] re-enable/update agent-logger, then retry.\n' "$_command" >&2
    exit 127
fi
_gate="$_payload/scripts/runtime-gate.sh"
if [ ! -f "$_gate" ] ||
   ! grep -Fq '# Installation driver contract: logger-first-use-v1' "$_gate"; then
    if [ ! -f "$_gate" ] ||
       ! grep -Fq 'AGENT_LOGGER_NO_SELFPROVISION' "$_gate"; then
        printf '[%s] selected snapshot lacks non-provisioning dispatch support; re-enable/update agent-logger.\n' "$_command" >&2
        exit 126
    fi
    _resolver="$_payload/scripts/resolve-runtime.sh"
    if [ ! -f "$_resolver" ] ||
       ! grep -Fq '# Trace contract: normal/read-only/ready-v1' "$_resolver"; then
        printf '[%s] read-only runtime resolver is unavailable; re-enable/update agent-logger.\n' "$_command" >&2
        exit 126
    fi
    # The old gate owns effective-context resolution, but never construction.
    AGENT_LOGGER_NO_SELFPROVISION=1
    export AGENT_LOGGER_NO_SELFPROVISION
fi
COPILOT_PLUGIN_ROOT="$_payload"
export COPILOT_PLUGIN_ROOT
exec "$_shim" "$@"
STUBEOF
        then
            rm -f "$temporary"
            _fail "Logger auxiliary binstub write failed: $LOCAL_BIN/$name"
            return 1
        fi
        chmod +x "$temporary" && [[ ! -d "$LOCAL_BIN/$name" ]] &&
            mv -f "$temporary" "$LOCAL_BIN/$name" &&
            [[ ! -e "$temporary" && ! -L "$temporary" && -f "$LOCAL_BIN/$name" ]] || {
            rm -f "$temporary"
            _fail "Logger auxiliary binstub publication failed: $LOCAL_BIN/$name"
            return 1
        }
    done
    ok "auxiliary compatibility binstubs: 5 commands on PATH"
}

_snapshot_requires_materialized_engine() {
    local snapshot_dir="$1"
    local sh="$snapshot_dir/scripts/install.sh"
    local ps1="$snapshot_dir/scripts/install.ps1"
    {
        [[ -f "$sh" ]] && (
            grep -Fq '. "$SCRIPT_DIR/installer-engine.sh"' "$sh" ||
            grep -Fq '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"' "$sh"
        )
    } && return 0
    {
        [[ -f "$ps1" ]] && (
            grep -Fq ". (Join-Path \$PSScriptRoot 'installer-engine.ps1')" "$ps1" ||
            grep -Fq ". (Join-Path \$PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')" "$ps1"
        )
    } && return 0
    return 1
}

_materialize_snapshot_libs() {
    local snapshot_dir="$1"
    local libs_dir="$snapshot_dir/libs"
    local lib src package project temporary
    project="$snapshot_dir/pyproject.toml"
    mkdir -p "$libs_dir" || {
        _fail "Snapshot libraries directory cannot be prepared: $libs_dir"; return 1;
    }
    for lib in config-migrate agent-procutil dropin-registry plugin-resolve plugin-activation single-instance-lease update-driver; do
        src="$PLUGIN_DIR/libs/$lib"
        if [[ ! -f "$src/pyproject.toml" ]]; then
            src="$(cd "${PLUGIN_DIR}/../.." && pwd)/libs/$lib" || {
                _fail "Snapshot library source cannot be resolved: $lib"; return 1;
            }
        fi
        [[ -f "$src/pyproject.toml" ]] || continue
        if [[ "$src" != "$libs_dir/$lib" ]]; then
            rm -rf "$libs_dir/$lib" || {
                _fail "Snapshot library staging cannot be prepared: $lib"; return 1;
            }
            cp -a "$src" "$libs_dir/$lib" || {
                _fail "Snapshot library copy failed: $lib"; return 1;
            }
            package="agent-$lib"
            [[ "$lib" != agent-procutil ]] || package=agent-procutil
            temporary="$project.tmp.$$"
            sed "s|$package = { path = \"../../libs/$lib\", editable = true }|$package = { path = \"libs/$lib\" }|" \
                "$project" >"$temporary" && mv -f "$temporary" "$project" || {
                _fail "Snapshot dependency mapping failed: $lib"; return 1;
            }
            project="$libs_dir/$lib/pyproject.toml"
            temporary="$project.tmp.$$"
            sed 's/, editable = true }/ }/g' "$project" >"$temporary" &&
                mv -f "$temporary" "$project" || {
                _fail "Snapshot library mapping failed: $lib"; return 1;
            }
            project="$snapshot_dir/pyproject.toml"
        fi
    done
}

_publish_snapshot_source() {
    publish_legacy_snapshot_source "$1" "$SCRIPT_DIR" "$__publication_payload" \
        agent-logger "$INSTALL_DIR" "$__publication_project"
}

publish_payload_snapshot() {
    _assert_source_runtime || return 1
    mkdir -p "${INSTALL_DIR}/snapshots" || {
        _fail "Snapshot directory cannot be prepared: $INSTALL_DIR/snapshots"; return 1;
    }
    local snapshot_dir="${INSTALL_DIR}/snapshots/${SRC_VERSION}"
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        snapshot_dir="$(_publish_source_snapshot)" || return 1
        _publish_snapshot_source "$snapshot_dir" || return 1
        local py
        py="$(_bootstrap_python)" || return 1
        _prepare_source_manifest_context || return 1
        _assert_source_runtime || return 1
        "$py" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
            publish-redirects --payload-dir "$snapshot_dir" --version "$SRC_VERSION" \
            --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" || return 1
        SOURCE_RUNTIME_SNAPSHOT="$snapshot_dir"
        ok "snapshot: ${snapshot_dir}"
        return 0
    elif [ -d "$snapshot_dir" ]; then
        if [ ! -f "$snapshot_dir/plugin.json" ] || \
           [ ! -x "$snapshot_dir/bin/agent-logger" ]; then
            printf 'ERROR: existing agent-logger snapshot is incomplete; refusing replacement: %s\n' \
                "$snapshot_dir" >&2
            return 1
        fi
        if _snapshot_requires_materialized_engine "$snapshot_dir" && \
           { [ ! -f "$snapshot_dir/scripts/installer-engine.sh" ] || \
             [ ! -f "$snapshot_dir/scripts/installer-engine.ps1" ]; }; then
            printf 'ERROR: existing agent-logger snapshot is incomplete; refusing replacement: %s\n' \
                "$snapshot_dir" >&2
            return 1
        fi
        _publish_snapshot_source "$snapshot_dir" || return 1
    else
        local snapshot_tmp="${snapshot_dir}.tmp.$$"
        rm -rf "$snapshot_tmp" || {
            _fail "Snapshot staging cannot be reset: $snapshot_tmp"; return 1;
        }
        mkdir -p "$snapshot_tmp" || {
            _fail "Snapshot staging cannot be prepared: $snapshot_tmp"; return 1;
        }
        # PLUGIN_DIR is the invocation's self-staged copy. The original
        # marketplace singleton is provenance only and may be replaced while
        # this installer runs.
        cp -a "${PLUGIN_DIR}/." "$snapshot_tmp/" || {
            _fail "Snapshot payload copy failed: $PLUGIN_DIR"; return 1;
        }
        rm -rf \
            "$snapshot_tmp/.git" \
            "$snapshot_tmp/__pycache__" \
            "$snapshot_tmp/.venv" \
            "$snapshot_tmp/node_modules" \
            "$snapshot_tmp/build" \
            "$snapshot_tmp/dist" \
            "$snapshot_tmp/.pytest_cache" \
            "$snapshot_tmp/.mypy_cache" \
            "$snapshot_tmp/tests" || {
                _fail "Snapshot generated-input pruning failed: $snapshot_tmp"; return 1;
            }
        _materialize_snapshot_libs "$snapshot_tmp" || return 1
        _materialize_snapshot_engine "$snapshot_tmp" || return 1
        _publish_snapshot_source "$snapshot_tmp" || return 1
        mv "$snapshot_tmp" "$snapshot_dir" || {
            _fail "Snapshot publication failed: $snapshot_dir"; return 1;
        }
    fi

    local payload_tmp="${INSTALL_DIR}/payload-dir.tmp.$$"
    local version_tmp="${INSTALL_DIR}/stamped-version.tmp.$$"
    _assert_source_runtime || return 1
    local marker
    for marker in payload-dir stamped-version; do
        if [[ -d "$INSTALL_DIR/$marker" || -L "$INSTALL_DIR/$marker" ]]; then
            _fail "Source redirect must be an ordinary file: $INSTALL_DIR/$marker"
            return 1
        fi
    done
    for marker in "$payload_tmp" "$version_tmp"; do
        [[ ! -d "$marker" && ! -L "$marker" ]] || {
            _fail "Snapshot redirect staging is not a regular file path: $marker"; return 1;
        }
    done
    printf '%s\n' "$snapshot_dir" > "$payload_tmp" || return 1
    printf '%s\n' "$SRC_VERSION" > "$version_tmp" || return 1
    [[ ! -d "$INSTALL_DIR/payload-dir" ]] &&
        mv -f "$payload_tmp" "${INSTALL_DIR}/payload-dir" &&
        [[ ! -e "$payload_tmp" && -f "$INSTALL_DIR/payload-dir" ]] || return 1
    [[ ! -d "$INSTALL_DIR/stamped-version" ]] &&
        mv -f "$version_tmp" "${INSTALL_DIR}/stamped-version" &&
        [[ ! -e "$version_tmp" && -f "$INSTALL_DIR/stamped-version" ]] || return 1
    ok "snapshot: ${snapshot_dir}"
}

# Cheap 'stamp': splat the agent-logger binstub + payload marker, defer the venv
# build to first use (fits a sessionStart hook's grace window). No venv, no uv.
do_stamp() {
    mkdir -p "${INSTALL_DIR}" "${LOCAL_BIN}" || {
        _fail "Stamp directories cannot be prepared: $INSTALL_DIR"; return 1;
    }
    if [[ "$PUBLISH_GLOBAL_BINSTUBS" == 1 ]]; then
        local command_name
        for command_name in agent-logger session-sync collate-session read-session-digest prepare-session-log ramp-up-session; do
            [[ ! -d "$LOCAL_BIN/$command_name" ]] || {
                _fail "Logger command destination is a directory: $command_name"; return 1;
            }
        done
    fi
    _assert_source_runtime || return 1
    deploy_runtime_helpers || return 1
    if [[ "$PUBLISH_GLOBAL_BINSTUBS" == 1 ]]; then
        deploy_binstub || return 1
        deploy_auxiliary_compatibility_binstubs || return 1
        publish_payload_snapshot || return 1
        ok "stamped: agent-logger command family on PATH; runtime provisions on first use."
    else
        publish_payload_snapshot || return 1
        ok "stamped: installation-scoped payload snapshot published without global PATH wrappers."
    fi
}

install_package() {
  _assert_source_runtime || return 1
  mkdir -p "${INSTALL_DIR}" "${LOCAL_BIN}" || {
      _fail "Logger runtime directories cannot be prepared: $INSTALL_DIR"; return 1;
  }
  if [[ "$PUBLISH_GLOBAL_BINSTUBS" == 1 ]]; then
      local command_name
      for command_name in agent-logger session-sync collate-session read-session-digest prepare-session-log ramp-up-session; do
          [[ ! -d "$LOCAL_BIN/$command_name" ]] || {
              _fail "Logger command destination is a directory: $command_name"; return 1;
          }
      done
  fi

  # Prerequisite: uv (venv + package management per the install contract).
  # Self-acquire uv (vendored if absent) + mirror the governed pip index to uv so
  # a solo/standalone install works on a pristine or governed box.
  _ensure_uv_index
  UV_CMD="$(ensure_uv "${INSTALL_DIR}" tool 1 || true)"
  if [[ -z "${UV_CMD}" ]]; then
    printf 'ERROR: uv is required but could not be resolved or acquired\n' >&2
    exit 1
  fi

    _versioned_slot_clean || return 1
    if [[ "$_RUNTIME_CONSTRUCTION_REUSE" == 1 ]]; then
        if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
            if [[ ! -f "$INSTALL_DIR/current-version" || "$(cat "$INSTALL_DIR/current-version")" != "$SRC_VERSION" ]]; then
                _versioned_activate || return 1
            else
                _test_runtime_healthy || return 1
                deploy_runtime_helpers || return 1
                _assert_source_runtime || return 1
            fi
            publish_payload_snapshot || return 1
            if [[ "$PUBLISH_GLOBAL_BINSTUBS" == 1 ]]; then
                deploy_auxiliary_compatibility_binstubs || return 1
                deploy_binstub || return 1
            fi
        fi
        return 0
    fi

  if [[ ! -x "${VENV}/bin/python" || ! -f "${VENV}/pyvenv.cfg" ]]; then
    _versioned_slot_clean || return 1
    if ! new_signed_venv "${UV_CMD}" "${VENV}" "3.10"; then
      printf 'ERROR: Failed to create venv at %s\n' "${VENV}" >&2
      exit 1
    fi
    chg "created venv at ${VENV}"
  fi
  local bootstrap_out
  if ! bootstrap_out=$(invoke_uv_pip_install_resilient "${UV_CMD}" --python "${VENV}/bin/python" 'setuptools>=83.0.0' 'pyyaml>=6.0' --quiet); then
    printf '%s\n' "$bootstrap_out" >&2
    exit 1
  fi
  # Local first-party code still resolves its external and build dependencies
  # through the configured, seasoned index, in one retained transaction.
  local -a install_args=(--python "${VENV}/bin/python" --no-editable)
  local lib_spec lib_dir lib_package
  for lib_spec in \
    config-migrate:agent-config-migrate \
    agent-procutil:agent-procutil \
    single-instance-lease:agent-single-instance-lease \
    update-driver:agent-update-driver \
    dropin-registry:agent-dropin-registry \
    plugin-resolve:agent-plugin-resolve \
    plugin-activation:agent-plugin-activation; do
    lib_dir="${PLUGIN_DIR}/libs/${lib_spec%%:*}"
    lib_package="${lib_spec#*:}"
    if [[ ! -f "${lib_dir}/pyproject.toml" ]]; then
      lib_dir="$(cd "${PLUGIN_DIR}/../.." && pwd)/libs/${lib_spec%%:*}"
    fi
    if [[ -f "${lib_dir}/pyproject.toml" ]]; then
      install_args+=(--reinstall-package "$lib_package" "$lib_dir")
    fi
  done
  install_args+=(--reinstall-package agent-logger "${PLUGIN_DIR}" --quiet)
  export INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB="${PLUGIN_DIR}"
  local install_out
  if ! install_out=$(invoke_uv_pip_install_resilient "${UV_CMD}" "${install_args[@]}"); then
    unset INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB
    printf '%s\n' "$install_out" >&2
    exit 1
  fi
  unset INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB
  ok "installed agent-logger package"

  # Versioned layout (#581): health-gate the slot + swap the `.venv` symlink.
  _versioned_activate || exit 1

  _assert_source_runtime || return 1
  publish_payload_snapshot || return 1
  if [[ "$PUBLISH_GLOBAL_BINSTUBS" == 1 ]]; then
    # Keep auxiliary PATH fallbacks payload-attributed after provisioning. The
    # scheduled service uses the runtime directly; interactive compatibility
    # commands continue through their owning payload shims.
    deploy_auxiliary_compatibility_binstubs || return 1
    # The primary `agent-logger` entrypoint is a self-provisioning binstub (not a
    # plain symlink) so it can rebuild the runtime on first use in a confined host.
    deploy_binstub || return 1
    ok "published compatibility binstubs into ${LOCAL_BIN}"
  else
    ok "scoped install keeps runtime helpers inside ${INSTALL_DIR}"
  fi

  # Machine-local config schema migration (idempotent + atomic). Non-fatal.
  if PYTHONUTF8=1 "${VENV}/bin/python" -I -m agent_logger config-migrate 2>/dev/null; then
    :
  else
    warn "config migration skipped"
  fi
}

write_units() {
  mkdir -p "${UNIT_DIR}" || {
    _fail "Logger unit directory cannot be prepared: $UNIT_DIR"; return 1;
  }
  local service_path="$UNIT_DIR/$TIMER_NAME.service" timer_path="$UNIT_DIR/$TIMER_NAME.timer"
  local service_tmp timer_tmp
  if [[ -d "$service_path" || -d "$timer_path" ]]; then
    _fail "Logger unit destination is a directory: $UNIT_DIR/$TIMER_NAME"
    return 1
  fi
  service_tmp="$(mktemp "$UNIT_DIR/.$TIMER_NAME.service.XXXXXX")" || return 1
  timer_tmp="$(mktemp "$UNIT_DIR/.$TIMER_NAME.timer.XXXXXX")" || {
    rm -f "$service_tmp"; return 1;
  }
  if [[ ! -f "$service_tmp" || -L "$service_tmp" || ! -f "$timer_tmp" || -L "$timer_tmp" ]]; then
    _fail "Logger unit staging is not a regular file"
    return 1
  fi
  # Optionally discover a facility-designated multi-machine config repo via
  # the agent-worktrees registry, if this machine has one adopted -- so the
  # scheduled sync (invoked with no useful working directory of its own)
  # still discovers that repo's schema v3 sync.local_path declaration.
  # Which repo (if any) is left to machine-local installer configuration
  # (config_repo: <name> in ${INSTALL_DIR}/config.yaml) rather than a
  # hardcoded name -- this is a generic, publicly-distributed plugin and
  # must not assume any specific private repo. AGENT_LOGGER_REPO_CONFIG's
  # explicit-file path still goes through the same registered-project +
  # default-branch trust gate as normal discovery (see
  # agent_logger.repo_trust) -- this only tells it WHERE to look, never
  # bypasses WHETHER to trust it. No config_repo set, agent-worktrees
  # absent, or the named repo not adopted here: silently a no-op (today's
  # behavior, unaffected).
  local repo_config_env=""
  local config_repo_name=""
  if [ -f "${INSTALL_DIR}/config.yaml" ] && [ -x "${VENV}/bin/python" ]; then
    # Parsed with real YAML semantics (the venv's own pyyaml, the same
    # library agent_logger.config uses) rather than a line-oriented sed/tr
    # extraction -- a bare regex/tr pass mishandles a trailing "# comment"
    # or a quoted scalar containing '#'/'"', silently yielding the wrong
    # (or no) repo name. `-I` (isolated mode) keeps the `import yaml` tied
    # to the venv's own installed package: without it, a same-named
    # yaml.py/yaml/ reachable from this installer's current directory
    # could shadow the real dependency and execute arbitrary code during
    # installation.
    config_repo_name="$("${VENV}/bin/python" -I - "${INSTALL_DIR}/config.yaml" <<'PYEOF' 2>/dev/null || true
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
except Exception:
    sys.exit(0)
value = data.get("config_repo") if isinstance(data, dict) else None
if isinstance(value, str) and value.strip():
    print(value.strip())
PYEOF
)"
  fi
  if [ -n "${config_repo_name}" ] && command -v agent-worktrees >/dev/null 2>&1; then
    local config_repo_dir
    config_repo_dir="$(agent-worktrees repos find "${config_repo_name}" 2>/dev/null || true)"
    # `repos find` also resolves `reference`-class registrations, which are
    # not guaranteed to be a git checkout at all -- so this discovery MUST
    # NOT wire the result into the service unless it passes the same
    # registered-project + default-branch trust decision normal (CWD-based)
    # discovery applies. Deferring entirely to find_repo_config()'s own
    # runtime trust check would still be *safe* (it re-derives this same
    # verdict from the env var at consumption time), but embedding an
    # untrusted path here regardless is needless exposure this installer
    # can avoid outright by checking first.
    #
    # A single isolated python invocation both canonicalizes and checks
    # trust, printing the resolved directory on success:
    #   - Path.resolve() canonicalizes physically (follows symlinks all
    #     the way through), unlike a plain `cd ... && pwd` (no -P), which
    #     only normalizes textually and would leave a symlinked checkout's
    #     LOGICAL path embedded -- find_repo_config() rejects a symlink
    #     ancestor outright, so the scheduled sync would silently ignore
    #     perfectly good repo config that normal (physically-resolving)
    #     discovery honors.
    #   - `-I` (isolated mode; implies -E/-P/-s) keeps this security
    #     decision tied to the INSTALLED package: without it, an ambient
    #     PYTHONPATH or a same-named `agent_logger` package reachable from
    #     the installer's current directory could shadow the real
    #     `repo_trust` module and forge a trusted verdict.
    local config_repo_trusted=0
    if [ -n "${config_repo_dir}" ] && [ -x "${VENV}/bin/python" ]; then
      local resolved_config_repo_dir
      if resolved_config_repo_dir="$("${VENV}/bin/python" -I - "${config_repo_dir}" <<'PYEOF' 2>/dev/null
import sys
from pathlib import Path
try:
    from agent_logger.repo_trust import repo_config_is_trusted
except Exception:
    sys.exit(1)
root = Path(sys.argv[1]).resolve()
if repo_config_is_trusted(root):
    print(root)
    sys.exit(0)
sys.exit(1)
PYEOF
      )"; then
        config_repo_dir="${resolved_config_repo_dir}"
        config_repo_trusted=1
      fi
    fi
    if [ "${config_repo_trusted}" = 1 ]; then
      # Mirrors agent_logger.config.REPO_CONFIG_FILENAMES's alias set and
      # precedence order -- a config repo may use any of these filenames,
      # not just the root .agent-logger.yaml. A candidate whose leaf (or,
      # for the .config/ aliases, whose .config ancestor) is a symlink is
      # skipped in favor of the next alias, mirroring find_repo_config()'s
      # own symlink rejection exactly -- selecting a symlinked candidate
      # here would embed a path the real loader immediately rejects
      # outright, instead of falling through to a valid lower-priority
      # alias the way normal discovery does.
      local candidate
      for candidate in \
        ".agent-logger.yaml" \
        ".agent-logger.yml" \
        ".config/agent-logger.yaml" \
        ".config/agent-logger.yml"
      do
        local candidate_path="${config_repo_dir}/${candidate}"
        if [ ! -f "${candidate_path}" ] || [ -L "${candidate_path}" ]; then
          continue
        fi
        case "${candidate}" in
          */*)
            if [ -L "${config_repo_dir}/${candidate%/*}" ]; then
              continue
            fi
            ;;
        esac
        # Escape systemd.exec(5) Environment= special characters (\, ",
        # the specifier-escape %, and a literal newline/CR -- POSIX
        # permits either in a directory name, and `agent-worktrees repos
        # find` output is otherwise copied verbatim into this here-doc; an
        # embedded newline would split the Environment= assignment across
        # physical lines in the generated unit file, which can fail
        # daemon-reload or be misread as a bogus additional directive.
        # Quoting the whole assignment alone only protects whitespace, not
        # any of this -- and a shell/sed pipeline can't safely see an
        # embedded newline in the first place (sed operates line-by-line),
        # so this uses the venv's own python for a single, complete escape
        # pass instead. `-I` (isolated mode) is not needed here: this step
        # only manipulates a string, importing no plugin code, so there is
        # nothing for an ambient PYTHONPATH/CWD package to shadow.
        local repo_config_value=""
        if [ -x "${VENV}/bin/python" ]; then
          repo_config_value="$("${VENV}/bin/python" - "AGENT_LOGGER_REPO_CONFIG=${candidate_path}" <<'PYEOF' 2>/dev/null
import sys
value = sys.argv[1]
value = value.replace("\\", "\\\\")
value = value.replace('"', '\\"')
value = value.replace("%", "%%")
value = value.replace("\r\n", "\\n")
value = value.replace("\n", "\\n")
value = value.replace("\r", "\\r")
sys.stdout.write(value)
PYEOF
          )"
        fi
        if [ -z "${repo_config_value}" ]; then
          continue
        fi
        repo_config_env="Environment=\"${repo_config_value}\""
        # Preserve REGISTRY LOCATION context, never a trust bypass: the
        # scheduled unit doesn't inherit the installer process's own
        # AGENT_WORKTREES_REPOS_YAML, so if the operator pointed this
        # install at a non-default registry file, the runtime
        # repo_config_is_trusted() re-check (every scheduled run, from the
        # checkout's LIVE git remotes/default branch -- never bypassed
        # here) would look in the wrong place and reject a genuinely
        # registered repo. This is safe to carry forward unconditionally:
        # unlike an AGENT_LOGGER_TRUST_REPO_CONFIG override, it never
        # short-circuits the remote/default-branch match itself, only
        # which registry file that match is read from. The default
        # registry location needs no propagation -- both processes read
        # the same well-known on-disk path already.
        if [ -n "${AGENT_WORKTREES_REPOS_YAML:-}" ]; then
          local repos_yaml_value=""
          if [ -x "${VENV}/bin/python" ]; then
            repos_yaml_value="$("${VENV}/bin/python" - "AGENT_WORKTREES_REPOS_YAML=${AGENT_WORKTREES_REPOS_YAML}" <<'PYEOF' 2>/dev/null
import sys
value = sys.argv[1]
value = value.replace("\\", "\\\\")
value = value.replace('"', '\\"')
value = value.replace("%", "%%")
value = value.replace("\r\n", "\\n")
value = value.replace("\n", "\\n")
value = value.replace("\r", "\\r")
sys.stdout.write(value)
PYEOF
            )"
          fi
          if [ -n "${repos_yaml_value}" ]; then
            repo_config_env="${repo_config_env}
Environment=\"${repos_yaml_value}\""
          fi
        fi
        break
      done
    fi
  fi
  if ! cat > "$service_tmp" <<EOF
[Unit]
Description=Agent Logger session-sync -- push Copilot session data to the configured target
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=AGENT_LOGGER_HOME=${INSTALL_DIR}
${repo_config_env}
ExecStart=${VENV}/bin/session-sync run --prune
# Generous start timeout: the FIRST sync cold-copies the entire session
# history (potentially thousands of sessions over a CIFS mount) and can take
# 10+ minutes; 120s killed it mid-copy. Incremental runs finish in seconds.
# (RuntimeMaxSec has no effect on Type=oneshot -- systemd ignores it -- so the
# run is bounded by TimeoutStartSec instead.)
TimeoutStartSec=1800
SyslogIdentifier=${TIMER_NAME}
EOF
  then
    rm -f "$service_tmp" "$timer_tmp"
    _fail "Logger service unit write failed: $service_path"
    return 1
  fi
  if ! cat > "$timer_tmp" <<EOF
[Unit]
Description=Agent Logger session-sync catch-up (periodic)

[Timer]
OnBootSec=5min
OnUnitActiveSec=4h
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  then
    rm -f "$service_tmp" "$timer_tmp"
    _fail "Logger timer unit write failed: $timer_path"
    return 1
  fi
  if [[ -d "$service_path" ]] || ! mv -f "$service_tmp" "$service_path" ||
      [[ -e "$service_tmp" || ! -f "$service_path" ]]; then
    rm -f "$service_tmp" "$timer_tmp"
    _fail "Logger service unit publication failed: $service_path"
    return 1
  fi
  if [[ -d "$timer_path" ]] || ! mv -f "$timer_tmp" "$timer_path" ||
      [[ -e "$timer_tmp" || ! -f "$timer_path" ]]; then
    rm -f "$timer_tmp"
    _fail "Logger timer unit publication failed: $timer_path"
    return 1
  fi
  chg "wrote systemd user units to ${UNIT_DIR}"
}

case "$ACTION" in
  install|update|provision)
    . "$SCRIPT_DIR/installer-instance-reconcile.sh"
    reconcile_installer_instances "$SCRIPT_DIR"
    ;;
esac
case "${ACTION}" in
  install)
    if [[ "$SKIP_PACKAGE_INSTALL" = 0 ]]; then install_package || exit 1; fi
    write_units || exit 1
    systemctl --user daemon-reload || exit 1
    systemctl --user enable --now "${TIMER_NAME}.timer" || exit 1
    if [[ "$SKIP_PACKAGE_INSTALL" = 0 ]]; then _write_runtime_manifest || exit 1; fi
    ok "timer enabled (every 4h)"
    ;;
  stamp)
    if [[ "$SKIP_STAMP" = 0 ]]; then do_stamp || exit 1; fi
    ;;
  provision)
    if [[ "$SKIP_PACKAGE_INSTALL" = 0 ]]; then install_package || exit 1; fi
    if [[ "$SKIP_PACKAGE_INSTALL" = 0 ]]; then _write_runtime_manifest || exit 1; fi
    ok "runtime provisioned"
    ;;
  update)
    if [[ "$SKIP_PACKAGE_INSTALL" = 0 ]]; then install_package || exit 1; fi
    write_units || exit 1
    systemctl --user daemon-reload || true
    if [[ "$SKIP_PACKAGE_INSTALL" = 0 ]]; then _write_runtime_manifest || exit 1; fi
    ok "package + units updated"
    ;;
  uninstall)
    py="$(_bootstrap_python)" || {
      _fail "Cannot validate Logger uninstall ownership without a bootstrap Python"; exit 1;
    }
    "$py" -I -B "$SCRIPT_DIR/uninstall_guard.py" --root "$INSTALL_DIR" || exit 1
    legacy_units="$("$py" -I -B "$SCRIPT_DIR/uninstall_guard.py" \
        --root "$INSTALL_DIR" --unit-names --unit-dir "$UNIT_DIR")" || exit 1
    uninstall_units="$(printf '%s\n' "$TIMER_NAME" "$legacy_units" | sed '/^$/d' | sort -u)"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "(dry run -- nothing will be changed)"
      while IFS= read -r unit; do
        if systemctl --user is-enabled "${unit}.timer" >/dev/null 2>&1 || \
           systemctl --user is-active --quiet "${unit}.timer" >/dev/null 2>&1 || \
           [[ -f "${UNIT_DIR}/${unit}.timer" ]]; then
          echo "[dry-run] would disable + remove: ${unit}.service / .timer"
        fi
      done <<< "$uninstall_units"
      if [[ "$PUBLISH_GLOBAL_BINSTUBS" == 1 ]]; then
        for name in session-sync agent-logger collate-session read-session-digest prepare-session-log ramp-up-session; do
          [[ -e "${LOCAL_BIN}/${name}" ]] && echo "[dry-run] would remove binstub: ${LOCAL_BIN}/${name}"
        done
      fi
      echo "[dry-run] would remove runtime generations, snapshots, generated dispatchers and runtime selectors"
      echo "[dry-run] config/session-state at ${INSTALL_DIR} would be kept (agent-logger uninstall never removes it)"
      echo "agent-logger uninstall dry run complete -- nothing was changed"
    else
      while IFS= read -r unit; do
        timer_active=0
        if systemctl --user is-active --quiet "${unit}.timer" >/dev/null 2>&1; then timer_active=1; fi
        if [[ "$timer_active" == 1 || -f "${UNIT_DIR}/${unit}.timer" || -f "${UNIT_DIR}/${unit}.service" ]]; then
          if [[ "$timer_active" == 1 || -f "${UNIT_DIR}/${unit}.timer" ]]; then
            systemctl --user disable --now "${unit}.timer" || exit 1
          fi
          service_state=0
          systemctl --user is-active --quiet "${unit}.service" || service_state=$?
          case "$service_state" in
            0) _fail 'Active Logger sync service; uninstall refused after disabling new scheduled starts'; exit 1 ;;
            3|4) ;;
            *) _fail 'Cannot determine Logger sync service state; uninstall refused'; exit 1 ;;
          esac
        fi
      done <<< "$uninstall_units"
      "$py" -I -B "$SCRIPT_DIR/uninstall_guard.py" --root "$INSTALL_DIR" --idle --prepare || exit 1
      while IFS= read -r unit; do
        rm -f "${UNIT_DIR}/${unit}.service" "${UNIT_DIR}/${unit}.timer" || exit 1
      done <<< "$uninstall_units"
      systemctl --user daemon-reload || true
      chg "timer removed (config at ${INSTALL_DIR} kept)"
      if [[ "$PUBLISH_GLOBAL_BINSTUBS" == 1 ]]; then
        for name in session-sync agent-logger collate-session read-session-digest prepare-session-log ramp-up-session; do
          rm -f "${LOCAL_BIN}/${name}" || exit 1
        done
        chg "binstubs removed from ${LOCAL_BIN}"
      else
        chg "scoped install left legacy global binstubs unchanged"
      fi
      "$py" -I -B "$SCRIPT_DIR/uninstall_guard.py" --root "$INSTALL_DIR" \
        --idle --prepare --remove-runtime --shell "$BASH" || exit 1
      chg "runtime generations and generated dispatchers removed (config/session-state kept)"
    fi
    ;;
  status)
    if _rt_py="$(_rt_python)"; then
      ok "installed: $("$_rt_py" -m agent_logger version 2>/dev/null || echo unknown)"
      "$_rt_py" -m agent_logger.sync.engine status || true
    else
      warn "not installed (run: bash scripts/install.sh install)"
    fi
    if [[ "$PUBLISH_GLOBAL_BINSTUBS" != 1 ]]; then
      ok "global compatibility binstubs suppressed for installation-scoped runtime"
    fi
    systemctl --user is-active "${TIMER_NAME}.timer" 2>/dev/null \
      && ok "timer active" || warn "timer not active"
    ;;
  *)
    echo "usage: install.sh {install|stamp|provision|update|uninstall|status}" >&2
    exit 2
    ;;
esac
