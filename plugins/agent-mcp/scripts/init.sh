#!/usr/bin/env bash
# Bootstrap the agent-mcp runtime (Linux / WSL / macOS).
#
# Creates the shared runtime at ~/.agent-mcp/ -- a venv with the
# agent_mcp package installed (via uv pip install) -- and deploys the
# `agent-mcp` binstub into ~/.local/bin.
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
# Honor an inherited action: the install-contract:v4 self-stage below re-execs
# this script with an already-shifted (empty) "$@", so a positional action would
# be lost across the staging boundary. Carry it through the exec via the env.
ACTION="${AGENT_MCP_ACTION:-init}"
UNINSTALL_REQUESTED=0
EXPLICIT_ACTION=""
INSTALL_DIR_SET=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --install-dir)
            if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == -* ]]; then
                _fail '--install-dir requires a nonempty path value'
                exit 2
            fi
            if [[ "$INSTALL_DIR_SET" == 1 ]]; then
                _fail 'Duplicate --install-dir is not supported'
                exit 2
            fi
            INSTALL_DIR="$2"; INSTALL_DIR_SET=1; shift 2 ;;
        uninstall|install|stamp|provision|init)
            if [[ -n "$EXPLICIT_ACTION" && "$EXPLICIT_ACTION" != "$1" ]]; then
                _fail 'Conflicting installer actions are not supported'
                exit 2
            fi
            ACTION="$1"; EXPLICIT_ACTION="$1"
            [[ "$1" != uninstall ]] || UNINSTALL_REQUESTED=1
            shift ;;
        *)
            _fail "Unsupported installer argument: $1"
            exit 2 ;;
    esac
done
if [[ "$ACTION" == uninstall && "$UNINSTALL_REQUESTED" != 1 ]]; then
    _fail 'Uninstall must be an explicit action, not inherited from the environment'
    exit 2
fi
if [[ "$ACTION" == uninstall && "/$INSTALL_DIR/" == *"/../"* ]]; then
    _fail 'Uninstall refuses parent-traversal path components'
    exit 2
fi
export AGENT_MCP_ACTION="$ACTION"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ "$ACTION" != uninstall ]]; then
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
fi

if [[ "$ACTION" != uninstall ]]; then
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
fi

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if [[ -z "${UV_HTTP_TIMEOUT:-}" ]]; then export UV_HTTP_TIMEOUT=60; fi

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_mcp"

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-mcp}"
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
# venv's `python -m agent_mcp`; otherwise it provisions on first use -- announcing
# (a machine-readable ::agent-provisioning:: signal so a caller can extend its
# timeout), lock-serialized, fail-fast.
# Co-deploy the canonical marker-only resolver so the binstub (and any launcher)
# resolves the interpreter the ONE uniform way (uniform-runtime-resolution, #765).
deploy_resolver() {
    mkdir -p "$INSTALL_DIR/bin"
    for r in resolve-runtime.sh resolve-runtime.ps1; do
        [ -f "$SCRIPT_DIR/$r" ] && cp -f "$SCRIPT_DIR/$r" "$INSTALL_DIR/bin/$r"
    done
}

deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    deploy_resolver
    cat > "$LOCAL_BIN/agent-mcp" << 'STUBEOF'
#!/usr/bin/env bash
# agent-mcp binstub -- self-provisioning (install-on-first-use).
# Resolves the interpreter SOLELY via the junction-free versioned-runtime marker
# (the deployed resolve-runtime.sh; uniform-runtime-resolution, #765): current-
# version -> last-known-good -> newest complete slot. NEVER a `.venv` link, NEVER
# a PATH python -- when no slot is installed AGENT_RT_PY is empty and we self-
# provision on first use rather than silently binding the system interpreter.
export PYTHONUTF8=1
_name="agent-mcp"
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
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_mcp "$@"
mkdir -p "$_root"
_status="$_root/.provision-status"
printf '%s\n' "[$_name] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout." >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' "$_name" "$_status" >&2
_install="$(cat "$_root/payload-dir" 2>/dev/null)/scripts/init.sh"
[ -f "$_install" ] || _install="$(ls "$HOME"/.copilot/installed-plugins/*/"$_name"/scripts/init.sh 2>/dev/null | head -n1)"
if [ ! -f "$_install" ]; then
    printf '%s\n' "[$_name] cannot self-provision: installer not found in plugin payload. Ensure the plugin is enabled, then retry." >&2
    exit 127
fi
_lock="$_root/.provision.lock"
exec 9>"$_lock"
command -v flock >/dev/null 2>&1 && flock 9 2>/dev/null
_resolve
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_mcp "$@"
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
bash "$_install" provision >&2
_rc=$?
_resolve
if [ "$_rc" -eq 0 ] && [ -n "$AGENT_RT_PY" ]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
    exec "$AGENT_RT_PY" -m agent_mcp "$@"
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
    chmod +x "$LOCAL_BIN/agent-mcp"
    _ok "Binstub: $LOCAL_BIN/agent-mcp (self-provisioning)"
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

_bootstrap_python() {
    if [[ "${1:-}" != external && -x "$LINK_DIR/bin/python" ]]; then echo "$LINK_DIR/bin/python"; return 0; fi
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then command -v "$candidate"; return 0; fi
    done
    return 1
}

source "$SCRIPT_DIR/source-runtime.sh"
_uninstall_external_python() {
    local root="${1:?runtime root required}" candidate selected probe
    probe='import sys
from pathlib import Path
assert sys.version_info >= (3, 10)
root = Path(sys.argv[1]).absolute()
assert ".." not in root.parts
roots = (root, root.resolve())
paths = (sys.argv[2], sys.executable, sys.prefix, sys.base_prefix,
         getattr(sys, "_base_executable", sys.executable))
for value in paths:
    path = Path(value).absolute()
    for actual in (path, path.resolve(strict=True)):
        assert all(not actual.is_relative_to(boundary) for boundary in roots)
sys.stdout.write(str(Path(sys.executable).resolve(strict=True)))'
    while IFS= read -r candidate; do
        case "$candidate" in "$root"/*|*WindowsApps*) continue ;; esac
        case "${OSTYPE:-}" in
            msys*|cygwin*) [[ "$candidate" == *.exe || ! -f "$candidate.exe" ]] || candidate="$candidate.exe" ;;
        esac
        selected="$("$candidate" -I -B -c "$probe" "$root" "$candidate" 2>/dev/null)" || continue
        [[ -n "$selected" && -x "$selected" ]] || continue
        printf '%s' "$selected"
        return 0
    done < <(type -aP python3 python 2>/dev/null)
    printf '%s\n' 'Uninstall requires an existing external Python outside runtime and prefix closure' >&2
    return 1
}
UNINSTALL_PYTHON=""
if [[ "$ACTION" == uninstall ]]; then
    UNINSTALL_PYTHON="$(_uninstall_external_python "$INSTALL_DIR")" || exit 1
    if [[ -z "$UNINSTALL_PYTHON" ]]; then
        _fail 'Uninstall requires an existing Python 3.10+ outside the runtime'
        exit 1
    fi
    uninstall_status="$("$UNINSTALL_PYTHON" -I -B "$SCRIPT_DIR/uninstall_runtime.py" check \
        --root "$INSTALL_DIR" --shell "$BASH")" || exit 1
    if [[ "$uninstall_status" == absent ]]; then
        _ok 'Legacy MCP runtime is already absent'
        exit 0
    fi
else
    _initialize_source_runtime || exit 1
fi
if ! _enter_install_lock; then
    _fail "Could not acquire the install transaction lock ($INSTALL_DIR/.install.lock) within $(_install_lock_timeout)s -- another install is still running; refusing to proceed unlocked"
    exit 1
fi
trap '_exit_install_lock' EXIT
trap '_exit_install_lock; exit 130' INT
trap '_exit_install_lock; exit 143' TERM
if [[ "$ACTION" == uninstall ]]; then
    "$UNINSTALL_PYTHON" -I -B "$SCRIPT_DIR/uninstall_runtime.py" remove \
        --root "$INSTALL_DIR" --shell "$BASH"
    exit $?
fi
_assert_source_runtime || exit 1

case "$ACTION" in
    install|init|provision)
        . "$SCRIPT_DIR/installer-instance-reconcile.sh"
        reconcile_installer_instances "$SCRIPT_DIR"
        ;;
esac
# Cheap 'stamp': splat the binstub + payload marker, defer the venv build to first
# use (fits a sessionStart hook's grace window). No venv, no uv.
if [[ "$ACTION" == "stamp" ]]; then
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    if [[ -n "$SOURCE_RUNTIME_IDENTITY" ]]; then
        _stamp_source_snapshot || exit 1
    else
        printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
    fi
    deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
    exit 0
fi

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
    py="$(_bootstrap_python external)" || return 1
    [[ -n "$py" ]] || return 1
    if ! declare -F _runtime_construction_begin >/dev/null; then
        helpers="$("$py" -I "$vr" --root "$INSTALL_DIR" construction-shell --shell sh)" || return 1
        eval "$helpers"
    fi
    _runtime_construction_begin "$py" "$vr" "$INSTALL_DIR" "$SRC_VERSION" \
        "$(_payload_hash)" "$(basename "$LINK_DIR")" "" \
        "$SCRIPT_DIR/uninstall-profile.json" agent-mcp || return 1
    SRC_VERSION="$_RUNTIME_CONSTRUCTION_VERSION"
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
    LINK_DIR="$VENV_DIR"
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

_step 'agent-mcp init'

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

# -- 2. Venv -----------------------------------------------------------
_versioned_slot_clean || exit 1
if [[ "$_RUNTIME_CONSTRUCTION_REUSE" != 1 ]]; then
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

# -- 2b. Preinstall workspace path deps (non-uv fallback) --------------
_assert_source_runtime || exit 1
# `agent-credential-relay`/`agent-procutil`/`agent-single-instance-lease`
# are `uv`-editable canonical references (vendor-pointer-generalization
# effort, Phase 1: no local copy in a dev checkout at all); `agent-zdd` is
# a real local copy. All 4 are `[tool.uv.sources]` workspace path deps, so
# when `uv` is unavailable the fallback below (bare `python -m pip
# install`) cannot resolve any of them without this explicit preinstall --
# it does NOT honor `[tool.uv.sources]` at all.
for _lib_entry in \
    'credential-relay:agent-credential-relay' \
    'agent-procutil:agent-procutil' \
    'single-instance-lease:agent-single-instance-lease' \
    'zdd:agent-zdd' \
    'update-driver:agent-update-driver'; do
    _lib_dir_name="${_lib_entry%%:*}"
    _lib_pkg_name="${_lib_entry#*:}"
    _lib_path="$PLUGIN_DIR/libs/$_lib_dir_name"
    if [[ ! -f "$_lib_path/pyproject.toml" ]]; then
        _lib_path="$(cd "$PLUGIN_DIR/../.." && pwd)/libs/$_lib_dir_name"
    fi
    if [[ -f "$_lib_path/pyproject.toml" ]]; then
        if [[ "$HAVE_UV" -eq 1 ]]; then
            "$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" --uv uv -- --python "$VENV_PYTHON" --reinstall-package "$_lib_pkg_name" \
                "$_lib_path" --quiet || _lib_install_rc=$?
        else
            "$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" --uv uv -- --python "$VENV_PYTHON" --quiet "$_lib_path" || _lib_install_rc=$?
        fi
        if [[ "${_lib_install_rc:-0}" -ne 0 ]]; then
            _fail "$_lib_dir_name library install failed"
            exit 1
        fi
        _lib_install_rc=0
    fi
done

# -- 3. Install the package into the venv ------------------------------
if [[ "$HAVE_UV" -eq 1 ]]; then
    if ! "$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" --uv uv -- --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet 2>/dev/null; then
        _fail 'Failed to install agent-mcp package into venv'
        exit 1
    fi
else
    if ! "$VENV_PYTHON" -I "$SCRIPT_DIR/installer_dependency_policy.py" --uv uv -- --python "$VENV_PYTHON" --quiet "$PLUGIN_DIR" 2>/dev/null; then
        _fail 'Failed to install agent-mcp package into venv'
        exit 1
    fi
fi
_ok 'Package installed: agent-mcp'

fi

_git_info() {
    local path="$1" commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
    echo "$commit $branch $dirty"
}

SOURCE_MANIFEST_PATH="$PLUGIN_DIR"
if [[ -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN:-}" ]]; then
    SOURCE_MANIFEST_PATH="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
fi

# === install-contract:v3 versioned-venv activate -- keep byte-identical across plugins ===
if [[ "$VERSIONED_RUNTIME" -eq 1 ]]; then
    # Point the stable .venv link at this version's freshly-built slot, moving a
    # legacy real .venv aside on the first migration. Run via the slot's own
    # python (stdlib-only helper); a CLI plugin has no daemon holding the link.
    VR_SCRIPT="$SCRIPT_DIR/versioned_runtime.py"
    # Health-gate (#935): never swap the stable .venv link onto a slot whose
    # package does not import -- a broken build must not become the live runtime.
    # The marker is written only after this gate passes (so "marked" == healthy).
    if ! "$VENV_PYTHON" -c 'import agent_mcp' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        exit 1
    fi
    _versioned_mark_complete || exit 1
    # Source manifest inputs are captured before activation.
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        SOURCE_MANIFEST_GIT="$(_git_info "$(cd "$SOURCE_MANIFEST_PATH/../.." && pwd)")" || exit 1
    fi
    _assert_source_runtime || exit 1
    if ! "$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' \
            activate "$SRC_VERSION" --replace-nonlink --no-link >/dev/null 2>&1; then
        _fail "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        exit 1
    fi
    _ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
fi
# === end install-contract:v3 versioned-venv activate ===

# agent-mcp-specific (Phase 2, agent-mcp-graceful-cutover): before the hard
# reap below, give a live `serve` daemon on a stale (non-current) version a
# graceful zero-downtime handoff instead of just killing it. --require-live
# makes this call safe to run unconditionally on every activation: it never
# starts a resident daemon where none was running (serve is optional,
# on-demand warmth, not a registered service), and no-ops if the live daemon
# is already on this exact version. Only a genuinely live, differently-
# versioned daemon actually cuts over here; the reap step right after this
# still runs unchanged as the safety net for anything cutover didn't handle
# (a pre-feature daemon with no control channel, a failed cutover, or a
# leaked/orphaned bridge tree that was never a `serve` daemon at all).
# Best-effort: never fails the install. Opt out with AGENT_MCP_NO_CUTOVER.
# The CLI's own defaults (60s health / 300s drain) are tuned for a human
# operator explicitly watching a manual `agent-mcp cutover`; an unattended
# activation pass must not silently block for up to ~6 minutes on a lightly-
# used bridge, so this uses much shorter install-appropriate defaults
# (still overridable, e.g. for a host with slow-starting upstream MCP
# servers) via AGENT_MCP_CUTOVER_HEALTH_TIMEOUT / AGENT_MCP_CUTOVER_DRAIN_TIMEOUT.
if [[ "$VERSIONED_RUNTIME" -eq 1 && "${_RUNTIME_CONSTRUCTION_MODE:-}" != fresh && -z "${AGENT_MCP_NO_CUTOVER:-}" ]]; then
    _cutover_health_timeout="${AGENT_MCP_CUTOVER_HEALTH_TIMEOUT:-15}"
    _cutover_drain_timeout="${AGENT_MCP_CUTOVER_DRAIN_TIMEOUT:-30}"
    _cutover_json="$("$VENV_PYTHON" -I -X utf8 -m agent_mcp cutover \
        --require-live --force --json \
        --health-timeout "$_cutover_health_timeout" \
        --drain-timeout "$_cutover_drain_timeout" 2>/dev/null || true)"
    if [[ -n "$_cutover_json" ]]; then
        _cutover_parsed="$("$VENV_PYTHON" -I -X utf8 -c 'import sys,json
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
print(d.get("skipped") or "")
print("1" if d.get("ok") else "0")' "$_cutover_json" 2>/dev/null || printf '\n0\n')"
        _cutover_skipped="$(printf '%s\n' "$_cutover_parsed" | sed -n '1p')"
        _cutover_ok="$(printf '%s\n' "$_cutover_parsed" | sed -n '2p')"
        if [[ -n "$_cutover_skipped" ]]; then
            _skip "Cutover skipped: $_cutover_skipped"
        elif [[ "$_cutover_ok" == "1" ]]; then
            _ok "Cut over the live serve daemon to the new version (routing flipped; old drained + retired)"
        else
            _step "Cutover attempted -- not fully successful (see reap step below)"
        fi
    fi
fi

_settle_runtime_owners() {
    [[ "$VERSIONED_RUNTIME" -eq 1 && "${_RUNTIME_CONSTRUCTION_MODE:-}" == fresh ]] || return 0
    local arguments=("$SCRIPT_DIR/reap_versions.py" --root "$INSTALL_DIR" --link-name '.venv' --fresh --reset-control)
    [[ -z "${AGENT_MCP_NO_VERSION_REAP:-}" ]] || arguments+=(--check-only)
    if ! "$VENV_PYTHON" -I "${arguments[@]}"; then
        _fail 'Fresh MCP replacement could not settle its old runtime owners and control state'
        return 1
    fi
    _ok 'Old MCP runtime owners and control state settled'
}
_settle_runtime_owners || exit 1

# agent-mcp-specific (NOT part of the byte-identical activate block above): reap
# processes still running from a now-stale (non-current) slot -- leaked/orphaned
# bridge trees or a warmth daemon from the prior version -- so an upgrade never
# leaves "two runtime versions resident". Best-effort; opt out with
# AGENT_MCP_NO_VERSION_REAP. Runs via the new slot's own python, so it is
# attributed to the current version and never reaps itself.
if [[ "$VERSIONED_RUNTIME" -eq 1 && "${_RUNTIME_CONSTRUCTION_MODE:-}" != fresh && -z "${AGENT_MCP_NO_VERSION_REAP:-}" ]]; then
    _reap_script="$SCRIPT_DIR/reap_versions.py"
    if ! _reaped="$("$VENV_PYTHON" -I "$_reap_script" --root "$INSTALL_DIR" --link-name '.venv')"; then
        _step 'Version-reap incomplete: old MCP runtime retirement was not confirmed'
    elif [[ -n "$_reaped" ]]; then
        _n="$(printf '%s\n' "$_reaped" | grep -c . || true)"
        _ok "Reaped $_n stale-version process(es)"
    fi
    # Collect the now-idle stale version *directories* the reap above freed. The
    # process-reap alone leaves the dirs on disk (the primitive's gc deliberately
    # protects any slot a live process runs from), so without this step stale
    # slots accumulate unbounded across upgrades. --protect-pids keeps the current
    # version + any slot a live process still runs from; --min-age-days is a
    # recency floor so a *concurrent* peer install's freshly-built (not-yet-live)
    # slot is never reaped mid-build. Best-effort; runs via the new slot's python.
    _gc_json="$("$VENV_PYTHON" "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
        --link-name '.venv' --json gc --protect-pids --min-age-days 0.05 2>/dev/null || true)"
    _gc_n="$("$VENV_PYTHON" -c 'import sys,json; a=sys.argv[1] if len(sys.argv)>1 else ""; print(len(json.loads(a).get("removed",[])) if a.strip()[:1]=="{" else 0)' "$_gc_json" 2>/dev/null || echo 0)"
    [[ "${_gc_n:-0}" -gt 0 ]] && _ok "Collected $_gc_n stale version dir(s)"
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
if [[ -z "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then _assert_source_runtime || exit 1; fi
SOURCE_FIELDS="\"runtime_version\": \"$SRC_VERSION\","
if [[ -n "$SOURCE_RUNTIME_IDENTITY" ]]; then
    SOURCE_FIELDS="$("$VENV_PYTHON" -I -c 'import json,sys
d=json.loads(sys.argv[1]); d["runtime_version"]=sys.argv[2]
print(json.dumps(d)[1:-1] + ",")' "$SOURCE_RUNTIME_IDENTITY" "$SRC_VERSION")" || exit 1
fi
KIND="$(_source_kind "$SOURCE_MANIFEST_PATH")"
if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
    VER="$PACKAGE_VERSION"
else
    VER="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
fi
COMMIT="null"; BRANCH="null"; DIRTY="false"
if [[ "$KIND" == "local" ]]; then
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
        read -r _c _b _d <<< "$SOURCE_MANIFEST_GIT"
    else
        REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
        read -r _c _b _d <<< "$(_git_info "$REPO_ROOT")"
    fi
    COMMIT="\"$_c\""; BRANCH="\"$_b\""; DIRTY="$_d"
fi
TMP="$MANIFEST_PATH.tmp"
manifest_json="$(cat << EOF
{
  "schema_version": 3,
  "service": "agent-mcp",
  $SOURCE_FIELDS
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$SOURCE_MANIFEST_PATH",
    "repo": "copilot-extensions",
    "plugin": "agent-mcp",
    "version": "$VER",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
EOF
)" || exit 1
if [[ -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN:-}" ]]; then
    if ! printf '%s\n' "$manifest_json" |
        "$VENV_PYTHON" -I -B -X utf8 "$SOURCE_MANIFEST_PATH/scripts/command_binding.py" \
            --payload-root "$SOURCE_MANIFEST_PATH" --source-payload-root "$SOURCE_MANIFEST_PATH" \
            --plugin-id agent-mcp --legacy-root "$INSTALL_DIR" \
            --project-root "${COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT:-$PWD}" \
            --publish-legacy --write-manifest >/dev/null; then
        _fail 'Command ownership validation refused MCP source manifest publication.'
        exit 1
    fi
else
    printf '%s\n' "$manifest_json" > "$TMP"
    mv -f "$TMP" "$MANIFEST_PATH"
fi
_ok "Deploy manifest written (source: $KIND)"

# -- 6. Verify ---------------------------------------------------------
echo ''
if "$VENV_PYTHON" -c 'import agent_mcp' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo '  Try: agent-mcp version'
exit 0
