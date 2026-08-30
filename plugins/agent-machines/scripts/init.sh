#!/usr/bin/env bash
# Bootstrap the agent-machines runtime (Linux / WSL / macOS).
#
# Creates the shared runtime at ~/.agent-machines/ -- a venv with the
# agent_machines package installed (via uv pip install) -- and deploys the
# `agent-machines` binstub into ~/.local/bin.
#
# Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.
#
# Usage:
#   ./init.sh [--force] [--install-dir DIR]

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

FORCE=0
INSTALL_DIR=""
CONTEXT=""
EXPECTED_MARKETPLACE_ID=""
DURABLE_HOME=""
ORIGINAL_ARGS=("$@")
# Honor an inherited action: the install-contract:v4 self-stage below re-execs
# this script with an already-shifted (empty) "$@", so a positional action would
# be lost across the staging boundary. Carry it through the exec via the env.
ACTION="${AGENT_MACHINES_ACTION:-init}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        --context) CONTEXT="${2:-}"; shift 2 ;;
        --expected-marketplace-id) EXPECTED_MARKETPLACE_ID="${2:-}"; shift 2 ;;
        --durable-home) DURABLE_HOME="${2:-}"; shift 2 ;;
        stamp|provision|init|slot-provision|slot-validate|slot-complete|slot-completion-validate) ACTION="$1"; shift ;;
        *) shift ;;
    esac
done
export AGENT_MACHINES_ACTION="$ACTION"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Cell-local slot actions authorize themselves from the explicit context
# transaction below. Every legacy mutation still requires the legacy probe.
if [[ "$ACTION" != "slot-provision" &&
      "$ACTION" != "slot-validate" &&
      "$ACTION" != "slot-complete" &&
      "$ACTION" != "slot-completion-validate" ]]; then
    LEGACY_PROBE="$SCRIPT_DIR/installation-context/legacy-entrypoint-probe.sh"
    if [[ ! -f "$LEGACY_PROBE" ]]; then
        _fail 'Legacy mutation probe is unavailable'
        exit 1
    fi
    set +e
    LEGACY_ROOT="${INSTALL_DIR:-$HOME/.agent-machines}"
    if [[ "$LEGACY_ROOT" != /* ]]; then
        LEGACY_ROOT="$PWD/$LEGACY_ROOT"
    fi
    bash "$LEGACY_PROBE" --payload-root "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" \
        --legacy-root "$LEGACY_ROOT"
    LEGACY_PROBE_STATUS=$?
    set -e
    if [[ "$LEGACY_PROBE_STATUS" -ne 0 ]]; then
        exit "$LEGACY_PROBE_STATUS"
    fi
fi
set -- "${ORIGINAL_ARGS[@]}"

# The dependency-light cell-slot runner does not need the legacy installer's
# payload self-stage, whose staging root is itself legacy state.
__cell_slot_direct=0
if [[ "$ACTION" == "slot-provision" ||
      "$ACTION" == "slot-validate" ||
      "$ACTION" == "slot-complete" ||
      "$ACTION" == "slot-completion-validate" ]]; then
    cd "$HOME"
    if [[ -z "${COPILOT_PLUGIN_INSTALL_STAGED:-}" ]]; then
        export COPILOT_PLUGIN_INSTALL_STAGED=cell-slot-action
        __cell_slot_direct=1
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
if [[ "$__cell_slot_direct" -eq 1 ]]; then
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

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_machines"

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-machines}"
if [[ "$INSTALL_DIR" != /* ]]; then
    INSTALL_DIR="$PWD/$INSTALL_DIR"
fi
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"

# === install-contract:v3 versioned-venv -- keep byte-identical across plugins ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and publish the active one via the <root>/current-version plain-text marker. On
# POSIX a .venv symlink (not a reparse point) publishes the active slot as the
# stable runtime-facing path the binstub + deploy-manifest resolve through, but the
# marker is authoritative (on Windows there is no junction at all -- a reparse
# point was blocked by RedirectionGuard/WinError 448 on managed devices). A version
# bump builds a new slot beside the old one and republishes the marker (never
# mutates a live venv). ALWAYS versioned -- the COPILOT_EXT_NO_VERSIONED opt-out
# and the legacy in-place fork are retired. scripts/versioned_runtime.py owns the
# marker publish + migration.
LINK_DIR="$VENV_DIR"                        # stable path the binstub/manifest reference
VERSIONED_RUNTIME=1
SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || true)"
if [[ -z "$SRC_VERSION" ]]; then
    echo "[FAIL] Cannot determine plugin version from pyproject.toml (required for the versioned runtime)." >&2
    exit 1
fi
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
VENV_PYTHON="$VENV_DIR/bin/python"
# === end install-contract:v3 versioned-venv ===

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
        --expected-plugin-id agent-machines
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

echo ''
# --- self-provisioning (runtime-self-provisioning pattern) -------------------
# Vendor a standalone uv when absent (pristine box has neither uv nor pip/venv).
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
    return 1
}
# Mirror pip's configured index to uv on a governed box (public PyPI TLS-blocked).
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
# Deploy the self-provisioning binstub (install-on-first-use). Fast path execs the
# venv's `python -m agent_machines`; otherwise it provisions on first use --
# announcing (a machine-readable ::agent-provisioning:: signal so a caller can
# extend its timeout), lock-serialized, fail-fast.
# Co-deploy the canonical marker-only resolver so the binstub (and any launcher)
# resolves the interpreter the ONE uniform way (uniform-runtime-resolution, #765).
deploy_resolver() {
    mkdir -p "$INSTALL_DIR/bin"
    for r in resolve-runtime.sh resolve-runtime.ps1; do
        [ -f "$SCRIPT_DIR/$r" ] && cp -f "$SCRIPT_DIR/$r" "$INSTALL_DIR/bin/$r"
    done
}

deploy_binstub() {
    STUB="$LOCAL_BIN/agent-machines"
    mkdir -p "$LOCAL_BIN"
    deploy_resolver
    cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
# agent-machines binstub -- self-provisioning (install-on-first-use).
# Resolves the interpreter SOLELY via the junction-free versioned-runtime marker
# (the deployed resolve-runtime.sh; uniform-runtime-resolution, #765): current-
# version -> last-known-good -> newest complete slot. NEVER a `.venv` link, NEVER
# a PATH python -- when no slot is installed AGENT_RT_PY is empty and we self-
# provision on first use rather than silently binding the system interpreter.
export PYTHONUTF8=1
_name="agent-machines"
_root="$HOME/.$_name"
_resolver="$_root/bin/resolve-runtime.sh"
_resolve() {
    AGENT_RT_PY=""
    if [ -f "$_resolver" ]; then
        AGENT_RT_ROOT="$_root"
        . "$_resolver"
    fi
}
_resolve
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_machines "$@"
_install="$(cat "$_root/payload-dir" 2>/dev/null)/scripts/init.sh"
[ -f "$_install" ] || _install="$(ls "$HOME"/.copilot/installed-plugins/*/"$_name"/scripts/init.sh 2>/dev/null | head -n1)"
if [ ! -f "$_install" ]; then
    printf '%s\n' "[$_name] cannot self-provision: installer not found in plugin payload. Ensure the plugin is enabled, then retry." >&2
    exit 127
fi
_payload="${_install%/scripts/init.sh}"
_probe="$_payload/scripts/installation-context/legacy-entrypoint-probe.sh"
[ -f "$_probe" ] || { printf '%s\n' "[$_name] legacy mutation probe is unavailable." >&2; exit 1; }
bash "$_probe" --payload-root "$_payload" --legacy-root "$_root" || exit $?
mkdir -p "$_root"
_status="$_root/.provision-status"
printf '%s\n' "[$_name] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout." >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' "$_name" "$_status" >&2
_lock="$_root/.provision.lock"
exec 9>"$_lock"
command -v flock >/dev/null 2>&1 && flock 9 2>/dev/null
_resolve
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_machines "$@"
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
bash "$_install" provision >&2
_rc=$?
_resolve
if [ "$_rc" -eq 0 ] && [ -n "$AGENT_RT_PY" ]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
    exec "$AGENT_RT_PY" -m agent_machines "$@"
fi
printf 'failed rc=%s %s\n' "$_rc" "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
if [ "$_rc" -eq 0 ]; then
    printf '%s\n' "[$_name] provisioning reported success but no runtime slot resolved." >&2
    _rc=1
else
    printf '%s\n' "[$_name] provisioning FAILED (rc=$_rc). See the log above; retry, or run: bash \"$_install\" provision" >&2
fi
exit "$_rc"
STUBEOF
    chmod +x "$STUB"
    _ok "Binstub: $STUB (self-provisioning)"
}
# Cheap 'stamp': splat the binstub + payload marker, defer the venv build to first
# use (fits a sessionStart hook's grace window). No venv, no uv.
if [[ "$ACTION" == "stamp" ]]; then
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
    deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
    exit 0
fi

echo '=== agent-machines init ==='
echo ''

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    _fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

# Find a Python interpreter
PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" --version 2>&1 | grep -qi python; then
            PYTHON_CMD="$candidate"
            break
        fi
    fi
done
if [[ -z "$PYTHON_CMD" ]]; then
    _fail 'Python not found on PATH (need 3.10+)'
    exit 1
fi
_ok "Python: $PYTHON_CMD"

_ensure_uv_index
HAVE_UV=0
if _ensure_uv; then HAVE_UV=1; fi

# -- 1. Directories ----------------------------------------------------
mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"

# -- 1b. Deploy the session-start hook (version-gated runtime reconcile) --
# hooks.json runs ~/.agent-machines/bin/bootstrap-check.sh at session start; it
# re-runs this installer only when the deployed version drifts from the payload.
BIN_HOOK_DIR="$INSTALL_DIR/bin"
mkdir -p "$BIN_HOOK_DIR"
for h in bootstrap-check.ps1 bootstrap-check.sh; do
    [ -f "$SCRIPT_DIR/$h" ] && cp -f "$SCRIPT_DIR/$h" "$BIN_HOOK_DIR/$h"
done
_ok "Session-start hook: $BIN_HOOK_DIR/bootstrap-check.sh"

# -- 2. Venv -----------------------------------------------------------
if [[ "$FORCE" -eq 1 || ! -x "$VENV_PYTHON" ]]; then
    if [[ "$HAVE_UV" -eq 1 ]]; then
        _step 'Creating venv via uv...'
        _versioned_slot_clean
        uv venv "$VENV_DIR" --allow-existing >/dev/null 2>&1 || {
            _step 'uv venv failed -- falling back to python -m venv'
            "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1
        }
    else
        _step 'Creating venv via python -m venv...'
        "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1
    fi
    if [[ ! -x "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed -- $VENV_PYTHON not found"
        exit 1
    fi
    _ok 'Venv created'
else
    _skip 'Venv already exists'
fi

# -- 3. Install the package into the venv ------------------------------
if [[ "$HAVE_UV" -eq 1 ]]; then
    if ! uv pip install --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet 2>/dev/null; then
        _fail 'Failed to install agent-machines package into venv'
        exit 1
    fi
else
    if ! "$VENV_PYTHON" -m pip install --quiet "$PLUGIN_DIR" 2>/dev/null; then
        _fail 'Failed to install agent-machines package into venv'
        exit 1
    fi
fi
_ok 'Package installed: agent-machines'

# === install-contract:v3 versioned-venv activate -- keep byte-identical across plugins ===
if [[ "$VERSIONED_RUNTIME" -eq 1 ]]; then
    VR_SCRIPT="$SCRIPT_DIR/versioned_runtime.py"
    # Capture the currently-active version so gc can retain it as previous-good.
    PREV_VERSION="$("$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' current 2>/dev/null || echo "")"
    # Health-gate: never swap the stable .venv link onto a slot whose package
    # does not import -- a broken build must not become the live runtime.
    if ! "$VENV_PYTHON" -c 'import agent_machines' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        exit 1
    fi
    _versioned_mark_complete
    # Point the stable .venv link at this version's freshly-built slot, moving a
    # legacy real .venv aside on the first migration. Run via the slot's own
    # python (stdlib-only helper); a CLI plugin has no daemon holding the link.
    if ! "$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' \
            activate "$SRC_VERSION" --replace-nonlink --no-link >/dev/null 2>&1; then
        _fail "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        exit 1
    fi
    _ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
    # GC superseded version slots, keeping the current + previous-good and any
    # slot with a live process (--protect-pids), so old versions do not pile up.
    if [[ -n "$PREV_VERSION" ]]; then
        "$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' gc --protect-pids --keep "$PREV_VERSION" 2>&1 | sed 's/^/  ...    gc: /' || true
    else
        "$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' gc --protect-pids 2>&1 | sed 's/^/  ...    gc: /' || true
    fi
fi
# === end install-contract:v3 versioned-venv activate ===

# -- 4. Binstub --------------------------------------------------------
deploy_binstub

# -- 5. Deploy manifest ------------------------------------------------

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

# Unified schema_version 3 manifest (install-contract): records the source
# footprint (marketplace vs local) so deploys are auditable like the siblings.
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"
KIND="$(_source_kind "$PLUGIN_DIR")"
VER="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
COMMIT="null"; BRANCH="null"; DIRTY="false"
if [[ "$KIND" == "local" ]]; then
    REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
    read -r _c _b _d <<< "$(_git_info "$REPO_ROOT")"
    COMMIT="\"$_c\""; BRANCH="\"$_b\""; DIRTY="$_d"
fi
TMP="$MANIFEST_PATH.tmp"
cat > "$TMP" << EOF
{
  "schema_version": 3,
  "service": "agent-machines",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-machines",
    "version": "$VER",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
EOF
mv -f "$TMP" "$MANIFEST_PATH"
_ok "Deploy manifest written (source: $KIND)"

# -- 6. Verify ---------------------------------------------------------
echo ''
if "$VENV_PYTHON" -c 'import agent_machines' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo ''
echo '=== agent-machines init complete ==='
echo '  Try: agent-machines version'
exit 0
