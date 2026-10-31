#!/usr/bin/env bash
# =============================================================================
# install.sh -- agent-vault -- plugin installer for Linux / WSL / macOS
# =============================================================================
# Manages the agent-vault runtime lifecycle: install, update, status, start,
# stop, uninstall. Runtime lives at ~/.agent-vault/ (venv + daemon state), the
# CLI binstub goes to ~/.local/bin/agent-vault, and the persistent daemon runs
# as a systemd user service when systemd is available.
# =============================================================================

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=/dev/null
. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"

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

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_vault"

ACTION="${1:-status}"
shift || true

NO_SERVICE=0
PURGE=0
DRY_RUN=0
INSTALL_DIR=""
FORCE="${AGENT_VAULT_ALLOW_DOWNGRADE:-0}"
[[ "$FORCE" == "1" ]] && FORCE=1 || FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) NO_SERVICE=1; shift ;;
        --purge) PURGE=1; shift ;;
        --force) FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --install-dir) INSTALL_DIR="${2:?--install-dir requires a directory}"; shift 2 ;;
        *) _fail "Unknown option: $1"; exit 2 ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-vault}"
if [[ "$INSTALL_DIR" != /* ]]; then
    INSTALL_DIR="$PWD/$INSTALL_DIR"
fi
LEGACY_INSTALL_DIR="$HOME/.agent-vault"
legacy_cmp="$(printf '%s' "$LEGACY_INSTALL_DIR" | tr '\\' '/' | tr '[:upper:]' '[:lower:]')"
install_cmp="$(printf '%s' "$INSTALL_DIR" | tr '\\' '/' | tr '[:upper:]' '[:lower:]')"
if [[ "$install_cmp" == "$legacy_cmp" ]]; then
    SERVICE_SUFFIX=""
    SYSTEMD_UNIT="agent-vault.service"
else
    if command -v sha256sum >/dev/null 2>&1; then
        SERVICE_SUFFIX="$(printf '%s' "$install_cmp" | sha256sum | awk '{print substr($1,1,12)}')"
    elif command -v shasum >/dev/null 2>&1; then
        SERVICE_SUFFIX="$(printf '%s' "$install_cmp" | shasum -a 256 | awk '{print substr($1,1,12)}')"
    else
        SERVICE_SUFFIX="$(printf '%s' "$install_cmp" | cksum | awk '{print $1}')"
    fi
    SYSTEMD_UNIT="agent-vault-${SERVICE_SUFFIX}.service"
fi
INSTALLATION_ID="${AGENT_VAULT_INSTALLATION_ID:-}"
RUN_DIR="$INSTALL_DIR/run"
SOCKET_PATH="$RUN_DIR/agent-vault.sock"
PIPE_PATH="\\\\.\\pipe\\agent-vault${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}"
PID_FILE="$RUN_DIR/agent-vault-service.pid"
LOG_FILE="$INSTALL_DIR/logs/agent-vault-service.log"
PORT_ENV_LINE=""
if [[ -n "$INSTALLATION_ID" ]]; then
    PORT_ENV_LINE="Environment=AGENT_VAULT_PORT=0"
fi
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-vault"
ASKPASS="$LOCAL_BIN/vault-askpass"
UNIT_DIR="$HOME/.config/systemd/user"

# === install-contract:v3 versioned-venv (agent-vault: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and make the historical `.venv` path a symlink into it, so the binstub, systemd
# unit, and deploy-manifest resolve through the link unchanged. LINK_DIR is the
# stable `.venv` path (runtime-facing, never a versions/<v> absolute a `gc` could
# remove); VENV_DIR is redirected to the versions/<v> slot (build + health-gate).
# ALWAYS versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED /
# AGENT_VAULT_VERSIONED) and the legacy in-place fork are retired;
# scripts/versioned_runtime.py owns the swap + migration + gc.
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
# created) -- this is what the systemd ExecStart + version checks use. LINK_DIR is
# kept ONLY to derive `--link-name` so activate/gc can still find and REMOVE any
# pre-existing `.venv` link.
LINK_PYTHON="$VENV_PYTHON"
# === end install-contract:v3 versioned-venv ===

# === install-contract:v3 versioned-venv helpers (agent-vault) ===
_versioned_activate() {
    _assert_source_runtime || return 1
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        _publish_source_snapshot >/dev/null || return 1
    fi
    _assert_source_runtime || return 1
    # Swap the stable `.venv` symlink to this version's freshly-built slot, moving
    # a legacy real `.venv` aside on first migration (--replace-nonlink). No-op in
    # legacy mode. On POSIX a rename tolerates a daemon's open files, and
    # _install_service `systemctl restart`s onto the new slot, so no stop needed.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
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
# === end install-contract:v3 versioned-venv helpers ===

_bootstrap_python() {
    # Keep construction supervision outside the candidate being built.
    local __c
    for __c in python3 python; do
        if command -v "$__c" >/dev/null 2>&1; then command -v "$__c"; return 0; fi
    done
    return 1
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
                --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-vault \
                --expected-runtime-version "$SRC_VERSION" \
                --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" --format shell-fields)" || return 1
            write_deploy_manifest agent-vault agent-vault "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV_DIR" \
                "$fields" "$SOURCE_MANIFEST_ORIGIN" "$PACKAGE_VERSION"
        ) || return 1
        return 0
    fi
    _assert_source_runtime || return 1
    write_deploy_manifest agent-vault agent-vault "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV_DIR" \
        "" "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" "$PACKAGE_VERSION"
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

_installed_version() {
    # The version currently ACTIVE (via the `.venv` link), for the downgrade guard.
    [[ -x "$LINK_PYTHON" ]] || return 1
    local v
    v="$("$LINK_PYTHON" -c \
        'from importlib.metadata import version; print(version("agent-vault"))' \
        2>/dev/null)" || return 1
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
    a="${a%%[+.]source.*}"; b="${b%%[+.]source.*}"
    [[ "$a" == "$b" ]] && return 1
    local -a pa pb
    IFS='.' read -r -a pa <<< "$a"
    IFS='.' read -r -a pb <<< "$b"
    local len=${#pa[@]}
    (( ${#pb[@]} > len )) && len=${#pb[@]}
    local i ca cb na nb
    for (( i = 0; i < len; i++ )); do
        ca="${pa[i]:-}"; cb="${pb[i]:-}"
        [[ -z "$ca" && -z "$cb" ]] && continue
        [[ -z "$ca" ]] && return 1
        [[ -z "$cb" ]] && return 0
        na="${ca//[!0-9]/}"; nb="${cb//[!0-9]/}"
        na="${na:-0}"; nb="${nb:-0}"
        na=$((10#$na)); nb=$((10#$nb))
        (( na < nb )) && return 0
        (( na > nb )) && return 1
    done
    return 1
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
            _warn "Downgrade $installed -> $source forced (--force / AGENT_VAULT_ALLOW_DOWNGRADE)"
            return 0
        fi
        echo ""
        _fail "Refusing to downgrade agent-vault: installed $installed > source $source"
        _fail "Override intentionally (deliberate rollback):"
        _fail "    $0 $ACTION --force"
        echo ""
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

_check_keepassxc() {
    if command -v keepassxc-cli >/dev/null 2>&1; then
        _ok 'Prerequisite: keepassxc-cli found'
    else
        _warn 'Prerequisite missing: keepassxc-cli (KeePassXC). agent-vault installed, but unlocks will fail until KeePassXC is present.'
    fi
}

_deploy_binstub() {
    write_simple_binstub \
        "agent-vault" \
        "agent_vault" \
        "$INSTALL_DIR" \
        "$LOCAL_BIN" \
        "$INSTALL_DIR/bin" \
        "scripts/install.sh" \
        "AGENT_VAULT_NO_SELFPROVISION" \
        "$SCRIPT_DIR/resolve-runtime.ps1" \
        "$SCRIPT_DIR/resolve-runtime.sh"
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

# Cheap 'stamp': splat the binstub + payload marker, defer the venv build to first
# use (fits a sessionStart hook's grace window). No venv, no uv.
do_stamp() {
    _assert_source_runtime || return 1
    _step 'agent-vault stamp; runtime deferred to first use'
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN" || {
        _fail "Stamp directories cannot be prepared: $INSTALL_DIR"; return 1;
    }
    _deploy_binstub || return 1
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        _stamp_source_payload || return 1
    else
        printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir" || return 1
    fi
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
}

_write_askpass() {
    local temporary
    mkdir -p "$LOCAL_BIN" || return 1
    [[ ! -d "$ASKPASS" ]] || {
        _fail "SUDO_ASKPASS helper destination is a directory: $ASKPASS"; return 1;
    }
    temporary="$(mktemp "$LOCAL_BIN/.vault-askpass.XXXXXX")" || return 1
    [[ -f "$temporary" && ! -L "$temporary" ]] || {
        _fail "SUDO_ASKPASS helper staging is not a regular file: $temporary"; return 1;
    }
    if ! cat > "$temporary" << 'EOF'
#!/usr/bin/env bash
export VAULT_NONINTERACTIVE=1
exec "$HOME/.local/bin/agent-vault" get "${VAULT_SUDO_ENTRY:?set VAULT_SUDO_ENTRY to your sudo KeePass entry path}" password
EOF
    then
        rm -f "$temporary"
        _fail "SUDO_ASKPASS helper write failed: $ASKPASS"
        return 1
    fi
    chmod +x "$temporary" && [[ ! -d "$ASKPASS" ]] && mv -f "$temporary" "$ASKPASS" &&
        [[ ! -e "$temporary" && ! -L "$temporary" && -f "$ASKPASS" ]] || {
        rm -f "$temporary"
        _fail "SUDO_ASKPASS helper publication failed: $ASKPASS"
        return 1
    }
    _ok "SUDO_ASKPASS helper: $ASKPASS"
    _step 'To enable sudo askpass, export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass" and export VAULT_SUDO_ENTRY="<their entry>"'
}

_test_runtime_healthy() {
    local output
    if ! output="$("$VENV_PYTHON" -I -m agent_vault --help 2>&1)"; then
        _fail "Isolated agent_vault CLI health probe failed: $output"
        return 1
    fi
    return 0
}

_ensure_runtime() {
    _assert_source_runtime || return 1
    if [[ ! -d "$PKG_SRC_DIR" ]]; then
        _fail "Package source not found at $PKG_SRC_DIR"
        exit 1
    fi
    local py have_uv=0
    py="$(_find_python)" || { _fail 'Python not found on PATH (need 3.10+)'; exit 1; }
    _ok "Python: $py"
    # Self-acquire uv (vendored if absent) + mirror the governed pip index to uv
    # so a solo/standalone install works on a pristine or governed box.
    local uv_cmd=""
    _ensure_uv_index
    uv_cmd="$(ensure_uv "$INSTALL_DIR" tool 1 || true)"
    if [[ -z "$uv_cmd" ]]; then
        _fail 'uv is required but could not be resolved or acquired'
        exit 1
    fi
    have_uv=1

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN" || {
        _fail "Vault runtime directories cannot be prepared: $INSTALL_DIR"; return 1;
    }
    _ok "Directories: $INSTALL_DIR"

    _versioned_slot_clean || return 1
    if [[ "$_RUNTIME_CONSTRUCTION_REUSE" == 1 ]]; then
        if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
            _test_runtime_healthy || return 1
            _deploy_binstub || return 1
            _write_askpass || return 1
            _assert_source_runtime || return 1
            if [[ ! -f "$INSTALL_DIR/current-version" || "$(cat "$INSTALL_DIR/current-version")" != "$SRC_VERSION" ]]; then
                _versioned_activate || return 1
            fi
            _stamp_source_payload || return 1
            _write_runtime_manifest || return 1
        fi
        return 0
    fi

    if [[ ! -x "$VENV_PYTHON" ]]; then
        _versioned_slot_clean || return 1
        if ! new_signed_venv "$uv_cmd" "$VENV_DIR" "3.10"; then
            _fail "Failed to create venv at $VENV_DIR"
            exit 1
        fi
        [[ -x "$VENV_PYTHON" ]] || { _fail "Venv creation failed -- $VENV_PYTHON not found"; exit 1; }
        _ok 'Venv created'
    else
        _skip 'Venv already exists'
    fi

    # Every `[tool.uv.sources]` workspace path dep -- both the zdd
    # canonical-on-dev / materialized-on-release reference and the other
    # `uv`-editable canonical references (``agent-procutil``,
    # ``agent-single-instance-lease``: vendor-pointer-generalization
    # effort, Phase 1) -- needs an explicit pre-install here: `uv`
    # resolves `[tool.uv.sources]` fine when installing the main package
    # directly, but the non-uv (bare `pip install`) fallback below ignores
    # that table entirely and would otherwise try (and fail) to resolve
    # each as an ordinary index package.
    for _lib in zdd agent-procutil single-instance-lease; do
        _lib_dir="$PLUGIN_DIR/libs/$_lib"
        if [[ ! -f "$_lib_dir/pyproject.toml" ]]; then
            _lib_dir="$(cd "$PLUGIN_DIR/../.." && pwd)/libs/$_lib"
        fi
        if [[ -f "$_lib_dir/pyproject.toml" ]]; then
            if [[ "$have_uv" -eq 1 ]]; then
                _lib_out=""
                if ! _lib_out=$(invoke_uv_pip_install_resilient "${uv_cmd:-uv}" --python "$VENV_PYTHON" "$_lib_dir" --quiet); then
                    [[ -n "$_lib_out" ]] && printf '%s\n' "$_lib_out" >&2
                    _fail "$_lib library install failed"
                    exit 1
                fi
            else
                "$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" --uv uv -- --python "$VENV_PYTHON" --quiet "$_lib_dir" \
                    || { _fail "$_lib library install failed"; exit 1; }
            fi
        fi
    done

    if [[ "$have_uv" -eq 1 ]]; then
        pkg_out=""
        if ! pkg_out=$(INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB="$PLUGIN_DIR" invoke_uv_pip_install_resilient "${uv_cmd:-uv}" --python "$VENV_PYTHON"  "$PLUGIN_DIR" --quiet); then
            [[ -n "$pkg_out" ]] && printf '%s\n' "$pkg_out" >&2
            _fail 'Failed to install agent-vault package into venv'
            exit 1
        fi
    else
        "$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" --uv uv -- --python "$VENV_PYTHON" --quiet "$PLUGIN_DIR" 2>/dev/null \
            || { _fail 'Failed to install agent-vault package into venv'; exit 1; }
    fi
    _ok 'Package installed: agent-vault'

    # Versioned layout (#581): health-gate the freshly-built slot in isolation,
    # then swap the stable `.venv` symlink onto it. Everything below resolves
    # through `.venv` (the link). No-op in legacy mode. Remember the previous
    # active version as the gc keep target.
    local prev_version=""
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        prev_version="$(_versioned_current)"
        if ! _test_runtime_healthy; then
            _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
            exit 1
        fi
    fi

    _deploy_binstub || return 1
    _write_askpass || return 1
    _assert_source_runtime || return 1
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        _versioned_mark_complete || exit 1
        _versioned_activate || exit 1
    fi
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        _stamp_source_payload || return 1
    else
        _assert_source_runtime || return 1
    fi
    _write_runtime_manifest || return 1
    _check_keepassxc

    if "$LINK_PYTHON" -I -c 'import agent_vault' 2>/dev/null; then
        _ok 'Verification: module imports successfully'
    else
        _fail 'Verification: module import failed'
        exit 1
    fi

    # Versioned: prune old slots, keeping current + previous-good + live-pinned.
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        _versioned_gc "$prev_version"
    fi

    case ":$PATH:" in
        *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
        *) _step "Add $LOCAL_BIN to your PATH: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
}

_install_service() {
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "agent-vault service skipped (--no-service): this host is a client only"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- the CLI can cold-start the daemon on demand"
        return 0
    fi
    # $RUN_DIR/logs must exist before the daemon's first bind (the unix socket
    # bind fails outright with a bare FileNotFoundError if $RUN_DIR is
    # missing, and the log file open warns-and-continues without $INSTALL_DIR/
    # logs) -- neither was created here before.
    mkdir -p "$UNIT_DIR" "$RUN_DIR" "$INSTALL_DIR/logs" || {
        _fail "Vault service directories cannot be prepared: $UNIT_DIR"; return 1;
    }
    local unit_path="$UNIT_DIR/$SYSTEMD_UNIT" unit_tmp
    if [[ -d "$unit_path" ]]; then
        _fail "Vault service unit destination is a directory: $unit_path"
        return 1
    fi
    unit_tmp="$(mktemp "$UNIT_DIR/.$SYSTEMD_UNIT.XXXXXX")" || {
        _fail "Vault service unit staging failed: $unit_path"; return 1;
    }
    [[ -f "$unit_tmp" && ! -L "$unit_tmp" ]] || {
        _fail "Vault service unit staging is not a regular file: $unit_tmp"; return 1;
    }

    # Drain-safe cutover (#743): if a generation is already active, capture its
    # unlocked state BEFORE we touch the unit -- `--export-handoff` dials the
    # OUTGOING daemon over its owner-gated AF_UNIX control socket and prints
    # one line of JSON (or nothing, if there is nothing unlocked, or the
    # transport isn't owner-gated). Held only in this shell variable, never
    # written to a file.
    local handoff_json=""
    if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
        # config.SOCKET_PATH/run_dir() etc. resolve from these env vars at
        # import time, so they must be set for THIS invocation explicitly --
        # unlike the systemd-managed daemon (which gets them from the unit's
        # own `Environment=` lines), a plain subprocess call here inherits
        # only this script's shell variables, not an environment the child
        # process can see.
        handoff_json="$(
            AGENT_VAULT_HOME="$INSTALL_DIR" \
            AGENT_VAULT_RUN_DIR="$RUN_DIR" \
            AGENT_VAULT_SOCKET="$SOCKET_PATH" \
            AGENT_VAULT_INSTALLATION_ID="$INSTALLATION_ID" \
            "$LINK_PYTHON" -m agent_vault.service --export-handoff 2>/dev/null
        )" || handoff_json=""
    fi

    if ! cat > "$unit_tmp" << EOF
[Unit]
Description=agent-vault -- local KeePassXC-backed secret store
After=default.target

[Service]
Type=simple
Environment=PYTHONUTF8=1
Environment=AGENT_VAULT_HOME=$INSTALL_DIR
Environment=AGENT_VAULT_RUN_DIR=$RUN_DIR
Environment=AGENT_VAULT_CORE_RUN_DIR=$INSTALL_DIR/core
Environment=AGENT_VAULT_CACHE_DIR=$INSTALL_DIR/cache
Environment=AGENT_VAULT_SOCKET=$SOCKET_PATH
Environment=AGENT_VAULT_PIPE=$PIPE_PATH
Environment=AGENT_VAULT_PID=$PID_FILE
Environment=AGENT_VAULT_LOG=$LOG_FILE
Environment=AGENT_VAULT_SYSTEMD_UNIT=$SYSTEMD_UNIT
Environment=AGENT_VAULT_TASK_NAME=AgentVault${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}
Environment=AGENT_VAULT_INSTALLATION_ID=$INSTALLATION_ID
$PORT_ENV_LINE
ExecStart=$LINK_PYTHON -m agent_vault.service --foreground --persistent --handoff-stdin
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF
    then
        rm -f "$unit_tmp"
        _fail "Vault service unit write failed: $unit_path"
        return 1
    fi
    if [[ -d "$unit_path" ]] || ! mv -f "$unit_tmp" "$unit_path" ||
        [[ -e "$unit_tmp" || ! -f "$unit_path" ]]; then
        rm -f "$unit_tmp"
        _fail "Vault service unit publication failed: $unit_path"
        return 1
    fi
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    # Drain-safe cutover (#743), continued: hand the captured payload to the
    # NEW generation via systemd's own per-manager environment -- set right
    # before the restart spawns it, unset right after (`restart` blocks until
    # the start job completes, and `--handoff-stdin` consumes/pops the var at
    # the very start of `main()`, before serving) -- so the plaintext secret
    # sits in the systemd --user manager's environment for the shortest
    # practical window, never in the unit file, never on disk.
    if [[ -n "$handoff_json" ]]; then
        systemctl --user set-environment "AGENT_VAULT_HANDOFF_JSON=$handoff_json" 2>/dev/null || true
    fi
    systemctl --user restart "$SYSTEMD_UNIT" 2>/dev/null || true
    if [[ -n "$handoff_json" ]]; then
        systemctl --user unset-environment AGENT_VAULT_HANDOFF_JSON 2>/dev/null || true
    fi
    if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
        if [[ -n "$handoff_json" ]]; then
            _ok "agent-vault service installed + started ($SYSTEMD_UNIT, drain-safe cutover: no re-unlock)"
        else
            _ok "agent-vault service installed + started ($SYSTEMD_UNIT)"
        fi
    else
        _warn "agent-vault service installed but not active -- check: systemctl --user status $SYSTEMD_UNIT"
    fi
}

do_install() {
    _step 'agent-vault install'
    _ensure_runtime || return 1
    _install_service || return 1
    _ok 'agent-vault install'
}

do_update() {
    _step 'agent-vault update'
    _downgrade_guard
    _ensure_runtime || return 1
    _install_service || return 1
    _ok 'agent-vault update'
}

do_start() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    if [[ ! -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        _fail "No service unit installed -- run: $0 install"
        exit 1
    fi
    systemctl --user start "$SYSTEMD_UNIT"
    systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1 \
        && _ok "agent-vault service started" || { _fail "Failed to start agent-vault service"; exit 1; }
}

do_stop() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok "agent-vault service stopped"
    else
        _skip "agent-vault service not running"
    fi
}

do_status() {
    echo ''; echo '=== agent-vault status ==='
    if [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
        local kind ver
        kind=$(grep -o '"kind": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        ver=$(grep -o '"version": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ok "Deployed: $ver (source: $kind)"
    else
        _skip "No deploy manifest -- not installed?"
    fi
    [[ -x "$STUB" ]] && _ok "Binstub: $STUB" || _skip "No binstub at $STUB"
    [[ -x "$ASKPASS" ]] && _ok "SUDO_ASKPASS helper: $ASKPASS" || _skip "No SUDO_ASKPASS helper at $ASKPASS"
    _check_keepassxc
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        local state enabled
        state=$(systemctl --user is-active "$SYSTEMD_UNIT" 2>/dev/null || echo inactive)
        enabled=$(systemctl --user is-enabled "$SYSTEMD_UNIT" 2>/dev/null || echo disabled)
        _ok "Service: $state ($enabled)"
    else
        _skip "No systemd user service (client-only host, or systemd unavailable)"
    fi
}

do_uninstall() {
    echo ''; echo '=== agent-vault uninstall ==='
    [[ "$DRY_RUN" -eq 1 ]] && echo '(dry run -- nothing will be changed)'
    echo ''
    . "$SCRIPT_DIR/uninstall-runtime.sh"
    _finite_runtime_uninstall --check || return 1
    _vault_uninstall_preflight || return 1
    if command -v systemctl >/dev/null 2>&1; then
        if [[ "$DRY_RUN" -eq 1 ]]; then
            [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]] && echo "[dry-run] would stop + disable + remove: $SYSTEMD_UNIT"
        else
            if [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
                if [[ "${_vault_uninstall_unit_state:-}" == loaded ]]; then
                    systemctl --user stop "$SYSTEMD_UNIT" || return 1
                    systemctl --user disable "$SYSTEMD_UNIT" || return 1
                fi
                rm -f "$UNIT_DIR/$SYSTEMD_UNIT" || return 1
            fi
            systemctl --user daemon-reload 2>/dev/null || true
            _ok "Service removed"
        fi
    fi
    [[ "$DRY_RUN" -eq 1 ]] || _finite_stop_owned || return 1
    if [[ "$PURGE" -eq 1 ]]; then
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[dry-run] would purge runtime/config contents (coordination retained): $INSTALL_DIR"
        else
            _finite_runtime_uninstall --remove-runtime || return 1
            remove_install_runtime_content "$INSTALL_DIR" || return 1
            _ok "Runtime purged (transaction/staging coordination retained): $INSTALL_DIR"
        fi
    else
        _remove_vault_runtime_code || return 1
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "[dry-run] state at $INSTALL_DIR would be kept (--purge to delete)"
            echo "agent-vault uninstall dry run complete -- nothing was changed"
        else
            _ok "Venv removed (state kept; --purge to delete)"
        fi
    fi
}

_vault_uninstall_preflight() {
    local unit properties
    _vault_uninstall_unit_state=unloaded
    unit="$UNIT_DIR/$SYSTEMD_UNIT"
    if [[ -e "$unit" || -L "$unit" ]]; then
        _finite_stop_tool check-unit --unit "$unit" || return 1
        if command -v systemctl >/dev/null 2>&1; then
            properties="$(systemctl --user show --all --no-pager --property=LoadState,ActiveState,FragmentPath,DropInPaths,ExecStart,WorkingDirectory -- "$SYSTEMD_UNIT")" || return 1
            _vault_uninstall_unit_state="$(printf '%s\n' "$properties" |
                _finite_stop_tool check-effective-unit --unit "$unit")" || return 1
            case "$_vault_uninstall_unit_state" in
                loaded|unloaded) ;;
                *) _fail "Effective Vault service ownership was not admitted"; return 1 ;;
            esac
        else
            _fail "Cannot verify effective Vault service ownership without systemctl"
            return 1
        fi
    fi
    _finite_owned_pids >/dev/null
}

_remove_vault_runtime_code() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[dry-run] would remove owned runtime code at: $INSTALL_DIR"
        return 0
    fi
    _finite_runtime_uninstall --remove-runtime
}

_remove_vault_helper_files() {
    local directory="$1" name
    shift
    [[ -d "$directory" && ! -L "$directory" ]] || { _fail "Refusing linked runtime helpers"; return 1; }
    for name in "$@"; do
        [[ -e "$directory/$name" || -L "$directory/$name" ]] || continue
        [[ -f "$directory/$name" && ! -L "$directory/$name" ]] || {
            _fail "Refusing linked/non-file runtime helper"; return 1;
        }
    done
    for name in "$@"; do rm -f -- "$directory/$name" || return 1; done
}

# === install-transaction lock (fail-closed) ===
# Serializes every mutating action of this runtime (stamp/install/update/
# provision): an exclusive lock on $INSTALL_DIR/.install.lock (flock on fd 8, or
# an exact-path PID receipt fallback where flock is unavailable) held for the whole build +
# publish transaction. A concurrent run waits up to the timeout
# (COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC, default 300s) and then FAILS -- it
# never proceeds unlocked. The OS drops the flock if this process dies; a dead
# owner's receipt is reclaimed; legacy dangling PID symlinks remain readable.
__install_lock_link=""
__install_lock_fd=""

_install_lock_timeout() {
    local __t="${COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC:-}"
    case "$__t" in
        ''|*[!0-9]*) __t=300 ;;
    esac
    [[ "$__t" -gt 0 ]] 2>/dev/null || __t=300
    printf '%s' "$__t"
}

_create_install_lock_owner() {
    local __path="$1" __private __result=1
    __private="$(mktemp -d "$INSTALL_DIR/.install-lock-create.XXXXXX")" || return 1
    # link(1), unlike ln(1), creates exactly the named entry, never a child
    # beneath an existing directory or directory symlink (also on BSD/macOS).
    if printf 'copilot-extensions.install-lock-pid:v1:%s\n' "$$" > "$__private/owner" &&
       link "$__private/owner" "$__path" 2>/dev/null; then
        __result=0
    fi
    # The published link is a second, independent directory entry on the same
    # inode: once link(1) above succeeds, admission already happened and must
    # be reported regardless of what happens to this private staging
    # directory next. A concurrent purge is free to reclaim unreferenced
    # staging scratch at any time (it never follows the published link), so a
    # best-effort cleanup failure here must never flip a successful
    # publication back into a reported failure -- that would abandon the
    # mutex this call just admitted, with nothing left to ever release it.
    rm -f "$__private/owner" 2>/dev/null
    rmdir "$__private" 2>/dev/null
    return "$__result"
}

_read_install_lock_owner() {
    local __path="$1" __private __record="" __owner="" __result=1
    if [[ -L "$__path" ]]; then
        # Legacy PID symlinks represent IDs, not references to existing filesystem nodes.
        [[ ! -e "$__path" ]] || return 1
        __private="$(mktemp -d "$INSTALL_DIR/.install-lock-read.XXXXXX")" || return 1
        # Preserve target bytes before command substitution can trim newlines.
        if readlink "$__path" > "$__private/owner" &&
           [[ -L "$__path" && ! -e "$__path" ]] &&
           ! IFS= LC_ALL=C read -r -d '' -n 65 __record < "$__private/owner" &&
           [[ "$__record" == *$'\n' ]]; then
            __owner="${__record%$'\n'}"
            case "$__owner" in
                *[!0-9]*|"") ;;
                *) __result=0 ;;
            esac
        fi
        rm -f "$__private/owner" || __result=1
        rmdir "$__private" || __result=1
        [[ "$__result" != 0 ]] || printf '%s\n' "$__owner"
        return "$__result"
    fi
    [[ -f "$__path" ]] || return 1
    __private="$(mktemp -d "$INSTALL_DIR/.install-lock-read.XXXXXX")" || return 1
    # Capture the inode before opening it. A raced FIFO/device/link remains
    # non-regular in this private snapshot and cannot cause a blocking open.
    if link "$__path" "$__private/owner" 2>/dev/null &&
       [[ -f "$__private/owner" && ! -L "$__private/owner" && ! -L "$__path" &&
          "$__private/owner" -ef "$__path" ]]; then
        # A NUL delimiter reports NUL instead of silently dropping it.
        # Only EOF within the byte bound can yield a complete receipt.
        if ! IFS= LC_ALL=C read -r -d '' -n 65 __record < "$__private/owner" &&
           [[ "$__record" == *$'\n' ]]; then
            __record="${__record%$'\n'}"
            case "$__record" in
                copilot-extensions.install-lock-pid:v1:*)
                    __owner="${__record#copilot-extensions.install-lock-pid:v1:}"
                    case "$__owner" in
                        *[!0-9]*|"") ;;
                        *) __result=0 ;;
                    esac
                    ;;
            esac
        fi
    fi
    rm -f "$__private/owner" || __result=1
    rmdir "$__private" || __result=1
    [[ "$__result" != 0 ]] || printf '%s\n' "$__owner"
    return "$__result"
}

_exit_install_lock() {
    if [[ -n "$__install_lock_link" ]]; then
        local __owner
        __owner="$(_read_install_lock_owner "$__install_lock_link" 2>/dev/null || true)"
        [[ "$__owner" == "$$" ]] && rm -f "$__install_lock_link"
        __install_lock_link=""
    elif [[ -n "$__install_lock_fd" ]]; then
        flock -u "$__install_lock_fd" 2>/dev/null || true
        eval "exec $__install_lock_fd>&-"
        __install_lock_fd=""
    fi
}

_enter_install_lock() {
    local __timeout="${1:-$(_install_lock_timeout)}"
    mkdir -p "$INSTALL_DIR" 2>/dev/null || return 1
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != "1" ]]; then
        { exec 8>"$INSTALL_DIR/.install.lock"; } 2>/dev/null || return 1
        if ! flock -w "$__timeout" 8; then
            exec 8>&-
            return 1
        fi
        __install_lock_fd=8
        return 0
    fi
    if ! command -v link >/dev/null 2>&1 || ! command -v mktemp >/dev/null 2>&1; then
        _fail "PID-fallback install admission requires link(1) and mktemp; refusing to proceed unlocked"
        return 1
    fi
    local __link="$INSTALL_DIR/.install.lock.pid" __deadline __owner __live __acquired
    __deadline=$(( $(date +%s) + __timeout ))
    # Always attempt admission once before enforcing the deadline: computing
    # the deadline and this loop's own `date +%s` are two separate calls, so a
    # short timeout (e.g. 1s) can straddle a second boundary between them and
    # make a pre-check-only loop condition skip even an uncontended
    # acquisition. Checking the deadline only after the first attempt (below)
    # removes that race.
    while :; do
        __live=0
        if _create_install_lock_owner "$__link.reap"; then
            # Admission and stale cleanup share this mutex; no acquirer may
            # replace the owner between a reaper's read and unlink.
            __acquired=0
            if _create_install_lock_owner "$__link"; then
                __acquired=1
            else
                __owner="$(_read_install_lock_owner "$__link" 2>/dev/null || true)"
                case "$__owner" in
                    *[!0-9]*|"") ;; # Missing or malformed evidence never authorizes deletion.
                    *)
                        if kill -0 "$__owner" 2>/dev/null; then
                            __live=1
                        elif [[ "$(_read_install_lock_owner "$__link" 2>/dev/null || true)" == "$__owner" ]] &&
                             rm -f "$__link" && _create_install_lock_owner "$__link"; then
                            __acquired=1
                        fi
                        ;;
                esac
            fi
            if [[ "$__acquired" == 1 ]]; then
                __install_lock_link="$__link"
            fi
            if ! rm -f "$__link.reap"; then
                _exit_install_lock
                return 1
            fi
            [[ "$__acquired" != 1 ]] || return 0
        fi
        # A dead admission-mutex owner requires explicit repair, never another reaper.
        [[ "$(date +%s)" -lt "$__deadline" ]] || break
        if [[ "$__live" == 1 ]]; then sleep 1; else sleep 0.1; fi
    done
    return 1
}
# === end install-transaction lock ===

. "$SCRIPT_DIR/source-runtime.sh"
PACKAGE_VERSION="$SRC_VERSION"
SOURCE_RUNTIME_IDENTITY=""
case "$ACTION" in
    install|update|provision|stamp) _initialize_source_runtime || exit 1 ;;
esac
case "$ACTION:${DRY_RUN:-0}" in
    uninstall:1) ;;
    install:*|update:*|stamp:*|provision:*|uninstall:*)
        if ! _enter_install_lock; then
            _fail "Could not acquire the install transaction lock ($INSTALL_DIR/.install.lock) within $(_install_lock_timeout)s -- another install is still running; refusing to proceed unlocked"
            exit 1
        fi
        trap '_exit_install_lock' EXIT
        trap '_exit_install_lock; exit 130' INT
        trap '_exit_install_lock; exit 143' TERM
        ;;
esac

case "$ACTION" in
    install|update|provision|stamp) _assert_source_runtime || exit 1 ;;
esac
case "$ACTION:${DRY_RUN:-0}" in
    install:0|update:0|provision:0)
        . "$SCRIPT_DIR/installer-instance-reconcile.sh"
        reconcile_installer_instances "$SCRIPT_DIR"
        ;;
esac
case "$ACTION" in
    install)   do_install || exit 1 ;;
    stamp)     do_stamp || exit 1 ;;
    provision) do_install || exit 1 ;;
    update)    do_update || exit 1 ;;
    start)     do_start ;;
    stop)      do_stop ;;
    status)    do_status ;;
    uninstall) do_uninstall ;;
    *) _fail "Unknown action: $ACTION (use: install|stamp|provision|update|status|start|stop|uninstall)"; exit 2 ;;
esac
exit 0
