#!/usr/bin/env bash
# Bootstrap the agent-containers runtime (Linux / WSL / macOS).
#
# Creates the shared runtime at ~/.agent-containers/ -- a venv with the
# agent_containers package installed (via uv pip install) -- and deploys the
# `agent-containers` binstub into ~/.local/bin.
#
# Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.
#
# Usage:
#   ./init.sh [--force] [--install-dir DIR] [--profile base|provider-exec]

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

FORCE=0
INSTALL_DIR=""
PROFILE=""
# Honor an inherited action: the install-contract:v4 self-stage below re-execs
# this script with an already-shifted (empty) "$@", so a positional action would
# be lost across the staging boundary. Carry it through the exec via the env.
ACTION="${AGENT_CONTAINERS_ACTION:-init}"
parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --force) FORCE=1; shift ;;
            --install-dir) INSTALL_DIR="${2:?--install-dir requires a path}"; shift 2 ;;
            --profile) PROFILE="${2:?--profile requires base or provider-exec}"; shift 2 ;;
            install|uninstall|stamp|provision|init) ACTION="$1"; shift ;;
            *) _fail "Unknown installer argument: $1"; exit 2 ;;
        esac
    done
}
# Resolve caller-relative roots before self-staging changes the working directory.
parse_arguments "$@"
if [[ "$ACTION" != stamp && "$ACTION" != uninstall && -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN+x}" &&
    -z "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET+x}" ]]; then
    _fail "Prepared-source transaction requires a frozen source target; upgrade the update engine before profile construction."
    exit 1
fi
case "$PROFILE" in ""|base|provider-exec) ;; *) _fail "Unknown runtime profile: $PROFILE"; exit 2 ;; esac
case "/$INSTALL_DIR/" in */../*) _fail 'Legacy runtime paths must not contain raw .. components'; exit 1 ;; esac
case "$INSTALL_DIR" in ""|/*) ;; *) INSTALL_DIR="$PWD/$INSTALL_DIR" ;; esac
set -- "$ACTION"
if [[ "$FORCE" == 1 ]]; then set -- "$@" --force; fi
if [[ -n "$PROFILE" ]]; then set -- "$@" --profile "$PROFILE"; fi
if [[ -n "$INSTALL_DIR" ]]; then set -- "$@" --install-dir "$INSTALL_DIR"; fi
export AGENT_CONTAINERS_ACTION="$ACTION"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "$ACTION" == uninstall ]]; then
    [[ "$FORCE" == 0 && -z "$PROFILE" ]] || { _fail 'Legacy uninstall does not accept force or profile'; exit 2; }
    exec bash "$SCRIPT_DIR/legacy-uninstall.sh" "${INSTALL_DIR:-$HOME/.agent-containers}"
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

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_containers"

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-containers}"
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
# mutates a live venv). ALWAYS versioned -- the COPILOT_EXT_NO_VERSIONED opt-out and
# the legacy in-place fork are retired. scripts/versioned_runtime.py owns the marker
# publish + migration.
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

PACKAGE_VERSION="$SRC_VERSION"
SOURCE_SELECTED=0
SOURCE_RUNTIME_IDENTITY='{}'
_source_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
                "$candidate" -I -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    _fail 'Source runtime selection requires a bootstrap Python interpreter'
    return 1
}
_source_runtime_version() {
    local py
    py="$(_source_python)" || return 1
    "$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-containers "$@"
}
if [[ -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN+x}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]]; then
    SOURCE_RUNTIME_IDENTITY="$(_source_runtime_version --format json)" || exit 1
    source_python="$(_source_python)" || exit 1
    SRC_VERSION="$("$source_python" -I -c 'import json,sys; print(json.loads(sys.argv[1])["runtime_version"])' \
        "$SOURCE_RUNTIME_IDENTITY")" || exit 1
    SOURCE_SELECTED=1
fi

_assert_source_runtime_identity() {
    if [[ "$SOURCE_SELECTED" == 1 || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]]; then
        local current
        current="$(_source_runtime_version --expected-runtime-version "$SRC_VERSION" --format json)" || return 1
        if [[ "$current" != "$SOURCE_RUNTIME_IDENTITY" ]]; then
            _fail 'Source runtime identity changed after runtime selection; refusing publication'
            return 1
        fi
    fi
}

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
# venv's `python -m agent_containers`; otherwise it provisions on first use --
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
    STUB="$LOCAL_BIN/agent-containers"
    mkdir -p "$LOCAL_BIN"
    deploy_resolver
    local source_root="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
    local bound_context="${COPILOT_EXTENSIONS_CONTEXT:-}" bound_mode=unknown bound_root="" decision="" durable=""
    local -a binding_args=(status --payload-root "$source_root" --plugin-id agent-containers --legacy-root "$INSTALL_DIR")
    if [[ -n "$bound_context" ]]; then
        durable="$bound_context"
        for _part in 1 2 3 4 5; do durable="$(dirname -- "$durable")"; done
        binding_args+=(--context "$bound_context" --durable-home "$durable")
    fi
    decision="$(bash "$SCRIPT_DIR/installation-context/installation-context.sh" "${binding_args[@]}")" || {
        _fail 'Cannot bind the global command to its installation context'
        return 126
    }
    binding_value() {
        LC_ALL=C awk -f "$SCRIPT_DIR/installation-context/json-query.awk" \
            -v mode="${2:-get}" -v "query_path=$1" <<<"$decision"
    }
    bound_root="$(binding_value $'legacy\034root')"
    if [[ "$bound_root" != /* ]]; then
        _fail 'Cannot bind the global command to a canonical installation root'
        return 126
    fi
    if [[ "$(binding_value actualMode)" == namespaced &&
          "$(binding_value runtimeRoot)" == "$bound_root" &&
          -n "$(binding_value context)" ]]; then
        bound_mode=namespaced
        bound_context="$(binding_value context)"
    elif [[ -z "$bound_context" && ! -e "$bound_root/install.json" ]] &&
         { [[ "$(binding_value status)" == ready && "$(binding_value actualMode)" == legacy &&
              "$(binding_value desiredMode)" == legacy ]] ||
           [[ "$(binding_value status)" == provenance-blocked &&
              "$(binding_value $'policy\034enabled' type)" == boolean &&
              "$(binding_value $'policy\034enabled')" == false &&
              "$(binding_value $'legacy\034disposition')" == active &&
              -z "$(binding_value $'legacy\034tombstone')" ]]; }; then
        bound_mode=legacy
    fi
    printf '#!/usr/bin/env bash\n_root=%q\n_source=%q\n_bound_context=%q\n_bound_mode=%q\n' \
        "$bound_root" "$source_root" "$bound_context" "$bound_mode" > "$STUB"
    cat >> "$STUB" << 'STUBEOF'
set -euo pipefail
export PYTHONUTF8=1
if [[ -n "$_bound_context" ]]; then
    if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" &&
          "$COPILOT_EXTENSIONS_CONTEXT" != "$_bound_context" ]]; then
        printf '[agent-containers] requested context does not match the bound global command.\n' >&2
        exit 126
    fi
    export COPILOT_EXTENSIONS_CONTEXT="$_bound_context"
fi
_snap="$(cat "$_root/payload-dir" 2>/dev/null || true)"
_payload="$_snap"
# Old ordinary snapshots have no canonical source receipt. Bind their exact
# publisher while it exists; never discover another marketplace through a glob.
if [[ -z "$_snap" || ! -f "$_snap/snapshot-provenance.json" ]]; then
    _payload=""
    if [[ -n "$_snap" && -f "$_source/scripts/runtime-gate.sh" ]]; then _payload="$_source"; fi
fi
if [[ -z "$_payload" || ! -f "$_payload/scripts/runtime-gate.sh" ]]; then
    if [[ "$_bound_mode" == legacy && -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
        _resolver="$_root/bin/resolve-runtime.sh"
        if [[ -f "$_resolver" ]] &&
           grep -Fq '# Trace contract: normal/read-only/ready-v1' "$_resolver"; then
            AGENT_RT_ROOT="$_root"
            AGENT_RT_RESOLVE_TRACE_MODE=ready
            AGENT_RT_PY=""
            if ! . "$_resolver"; then
                printf '[agent-containers] runtime resolver failed; update the owning installation.\n' >&2
                exit 126
            fi
            if [[ -n "$AGENT_RT_PY" ]]; then
                export AGENT_CONTAINERS_HOME="$_root"
                export AGENT_CONTAINERS_STATE_DIR="$_root"
                exec "$AGENT_RT_PY" -m agent_containers "$@"
            fi
        fi
    fi
    printf '[agent-containers] retained runtime gate is unavailable. Re-enable/update the owning plugin, then retry.\n' >&2
    exit 126
fi
export AGENT_CONTAINERS_HOME="$_root"
export AGENT_CONTAINERS_STATE_DIR="$_root"
export AGENT_CONTAINERS_PAYLOAD_ROOT="$_payload"
exec bash "$_payload/scripts/runtime-gate.sh" "$@"
STUBEOF
    chmod +x "$STUB"
    _ok "Binstub: $STUB (self-provisioning)"
}
# === install-transaction lock (fail-closed) ===
# Publishers and legacy removal use the same persistent fd-8 gate, whether
# acquisition uses native flock or the existing Python fcntl implementation.
. "$SCRIPT_DIR/legacy-transaction.sh"
# === end install-transaction lock ===

if ! _enter_install_lock; then
    _fail "Could not acquire the install transaction lock ($INSTALL_DIR/.install.lock) within $(_install_lock_timeout)s -- another install is still running; refusing to proceed unlocked"
    exit 1
fi
trap '_exit_install_lock' EXIT
trap '_exit_install_lock; exit 130' INT
trap '_exit_install_lock; exit 143' TERM

_assert_source_runtime_identity || exit 1

case "$ACTION" in
    install|init|provision)
        . "$SCRIPT_DIR/installer-instance-reconcile.sh"
        reconcile_installer_instances "$SCRIPT_DIR"
        ;;
esac
# Cheap 'stamp': splat the binstub + payload marker, defer the venv build to first
# use (fits a sessionStart hook's grace window). No venv, no uv.
if [[ "$ACTION" == "stamp" ]]; then
    if [[ -n "$PROFILE" ]]; then
        _fail 'Select a runtime profile with provision, not stamp'
        exit 2
    fi
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    stamp_payload="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
    if [[ "$SOURCE_SELECTED" == 1 ]]; then
        source_python="$(_source_python)" || exit 1
        stamp_payload="$("$source_python" -I -B "$SCRIPT_DIR/runtime_profile.py" \
            --root "$INSTALL_DIR" --payload "$PLUGIN_DIR" --stamp-source \
            --expected-source-version "$SRC_VERSION" \
            --expected-source-identity "$SOURCE_RUNTIME_IDENTITY")" || exit 1
        _assert_source_runtime_identity || exit 1
        "$source_python" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
            publish-redirects --payload-dir "$stamp_payload" --version "$SRC_VERSION" \
            --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" || exit 1
    else
        printf '%s\n' "$stamp_payload" > "$INSTALL_DIR/payload-dir"
    fi
    deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
    exit 0
fi

_step 'agent-containers init'

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

if command -v docker >/dev/null 2>&1; then
    if docker_ver="$(docker --version)"; then
        _ok "Docker: $docker_ver"
    else
        docker_exit=$?
        if [[ -n "$docker_ver" ]]; then _warn "Docker: $docker_ver"; fi
        _warn "docker --version failed (exit $docker_exit) -- agent-containers fleet operations unavailable on this machine (non-fatal)"
    fi
else
    _step 'docker CLI not found -- agent-containers requires Docker for fleet operations'
fi

_ensure_uv_index
HAVE_UV=0
if _ensure_uv; then HAVE_UV=1; fi

# -- 1. Directories ----------------------------------------------------
mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"

_git_info() {
    local path="$1" commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
    echo "$commit $branch $dirty"
}

# -- 2. Provision the selected immutable profile (uv pip install or pip) --
profile_args=(
    -I "$SCRIPT_DIR/runtime_profile.py" --root "$INSTALL_DIR"
    --payload "$PLUGIN_DIR" --python "$PYTHON_CMD" --tsv
    --expected-source-version "$SRC_VERSION"
    --expected-source-identity "$SOURCE_RUNTIME_IDENTITY"
)
if [[ "$HAVE_UV" -eq 1 ]]; then profile_args+=(--uv "$(command -v uv)"); fi
if [[ -n "$PROFILE" ]]; then profile_args+=(--profile "$PROFILE"); fi
# Source manifest inputs are captured before activation.
if [[ "$SOURCE_SELECTED" == 1 ]]; then
    _assert_source_runtime_identity || exit 1
    SOURCE_MANIFEST_GIT="$(_git_info "$(cd "$PLUGIN_DIR/../.." && pwd)")" || exit 1
fi
if ! profile_result="$("$PYTHON_CMD" "${profile_args[@]}")"; then
    _fail 'Failed to provision the selected agent-containers runtime profile'
    exit 1
fi
if [[ "$SOURCE_SELECTED" != 1 ]]; then _assert_source_runtime_identity || exit 1; fi
IFS=$'\t' read -r RUNTIME_PROFILE RUNTIME_VERSION VENV_DIR VENV_PYTHON <<< "$profile_result"
_ok "Runtime profile: $RUNTIME_PROFILE ($RUNTIME_VERSION)"

if [[ "$SOURCE_SELECTED" != 1 ]]; then
    "$PYTHON_CMD" -I "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
        publish-redirects --payload-dir "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" \
        --version "$RUNTIME_VERSION" || {
        _fail 'Failed to publish the runtime payload binding'
        exit 1
    }
fi

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
# Unified schema_version 3 manifest (install-contract): records the source
# footprint (marketplace vs local) so deploys are auditable like the siblings.
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"
KIND="$(_source_kind "$PLUGIN_DIR")"
if [[ "$SOURCE_SELECTED" == 1 ]]; then
    VER="$PACKAGE_VERSION"
else
    VER="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
fi
COMMIT="null"; BRANCH="null"; DIRTY="false"
if [[ "$KIND" == "local" ]]; then
    if [[ "$SOURCE_SELECTED" == 1 ]]; then
        read -r _c _b _d <<< "$SOURCE_MANIFEST_GIT"
    else
        REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
        read -r _c _b _d <<< "$(_git_info "$REPO_ROOT")"
    fi
    COMMIT="\"$_c\""; BRANCH="\"$_b\""; DIRTY="$_d"
fi
TMP="$MANIFEST_PATH.tmp"
SOURCE_FIELDS=""
if [[ "$SOURCE_SELECTED" == 1 ]]; then
    # The profile coordinator owns the profiled runtime_version; retain only source evidence here.
    SOURCE_FIELDS="$("$PYTHON_CMD" -I -c 'import json,sys; d=json.loads(sys.argv[1]); print(json.dumps({"runtime_source":d["runtime_source"]})[1:-1]+",")' "$SOURCE_RUNTIME_IDENTITY")" || exit 1
fi
cat > "$TMP" << EOF
{
  $SOURCE_FIELDS
  "schema_version": 3,
  "service": "agent-containers",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-containers",
    "version": "$VER",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$VENV_DIR",
  "runtime_version": "$RUNTIME_VERSION",
  "runtime_profile": "$RUNTIME_PROFILE",
  "runtime": "python"
}
EOF
mv -f "$TMP" "$MANIFEST_PATH"
_ok "Deploy manifest written (source: $KIND)"

# -- Machine-local config schema migration (idempotent + atomic; never touches
# a repo/cwd containers.yaml -- that is an adopt concern). Non-fatal. --
if PYTHONUTF8=1 "$VENV_PYTHON" -m agent_containers config-migrate 2>/dev/null; then
    :
else
    _step 'Config migration skipped'
fi

# -- 6. Verify ---------------------------------------------------------
echo ''
if "$VENV_PYTHON" -c 'import agent_containers' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

if "$VENV_PYTHON" -c 'import credential_relay' 2>/dev/null; then
    _ok 'credential-relay: importable in venv'
else
    _fail 'credential-relay not importable in venv'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo '  Try: agent-containers version'
exit 0
