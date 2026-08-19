#!/usr/bin/env bash
# =============================================================================
# install.sh -- Agent Bridge -- plugin installer for Linux/WSL
# =============================================================================
# Manages the agent-bridge service lifecycle: install, uninstall, start, stop,
# status, update.
#
# Runtime lives at ~/.agent-bridge/ (venv, config, DB, auth).
# Binstub goes to ~/.local/bin/agent-bridge.
#
# On first install, detects and migrates from a legacy project-service
# installer (services/agent-bridge/) if present, preserving config, auth,
# and DB.
#
# Usage:
#   bash plugins/agent-bridge/scripts/install.sh install
#   bash plugins/agent-bridge/scripts/install.sh status
#   bash plugins/agent-bridge/scripts/install.sh update
#
# Options:
#   --purge    On uninstall: also delete config, DB, and auth token
#   --force    On install/update: bypass the downgrade guard and install an
#              older version over a newer one (see #1790). The sanctioned
#              update path is the marketplace flow
#              (`test-chamber services agent-bridge update`), NOT a raw
#              checkout installer -- the guard exists to stop a stale checkout
#              silently downgrading (and de-featuring) the running daemon.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

INSTALL_DIR="$HOME/.agent-bridge"
VENV_DIR="$INSTALL_DIR/venv"
LOCAL_BIN="$HOME/.local/bin"
BINSTUB="$LOCAL_BIN/agent-bridge"
PID_FILE="$INSTALL_DIR/agent-bridge.pid"
# Effective listen port. A host is 9280; only a WSL guest (which shares the
# Windows host's TCP port namespace) uses 9281 -- matching
# agent_bridge.models.default_port(). Prefer the deployed config's explicit
# port (source of truth: honors an operator override AND catches config drift
# where the running service is on a non-default port), else the WSL-guest
# discriminator ("am I a WSL guest?", not "am I non-Windows?").
_cfg_yaml="${AGENT_BRIDGE_CONFIG_DIR:-$INSTALL_DIR}/config.yaml"
PORT=""
if [[ -f "$_cfg_yaml" ]]; then
    PORT="$(sed -n 's/^[[:space:]]*port:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$_cfg_yaml" | head -1)"
fi
if [[ -z "$PORT" ]]; then
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null; then
        PORT=9281
    else
        PORT=9280
    fi
fi
RELAY_PORT=9857   # integrated credential relay (in-process with the bridge)
SYSTEMD_UNIT="agent-bridge.service"

# === install-contract:v3 versioned-venv (agent-bridge: venv-as-symlink) ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and make the historical `venv` path a symlink into it, so the binstub, systemd
# unit, deploy-manifest, and `agent-bridge deploy` cutover -- all of which
# reference `venv` -- resolve through the link unchanged. LINK_DIR is the stable
# `venv` path (runtime-facing, never a versions/<v> absolute a `gc` could
# remove); VENV_DIR is redirected to the versions/<v> slot (build + health-gate).
# ALWAYS versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED /
# AGENT_BRIDGE_VERSIONED) and the legacy in-place fork are retired; a symlink is
# not a reparse point, so the model needs no opt-out. The stdlib-only
# scripts/versioned_runtime.py owns the swap + legacy migration + gc.
LINK_DIR="$VENV_DIR"
VERSIONED_RUNTIME=1
SRC_VERSION=""
if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
    SRC_VERSION="$(grep -m1 '^version' "$PLUGIN_DIR/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/')"
fi
if [[ -z "$SRC_VERSION" ]]; then
    echo "[FAIL] Cannot determine plugin version from pyproject.toml (required for the versioned runtime)." >&2
    exit 1
fi
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
# === end install-contract:v3 versioned-venv ===

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    export PATH="$LOCAL_BIN:$PATH"
fi

# -- Parse arguments ---------------------------------------------------------

ACTION="${1:-status}"
shift || true

PURGE=false
# Bypass the downgrade guard (#1790). Env var lets the marketplace/ZDD paths
# opt in without threading a flag; the CLI flag is the interactive escape hatch.
FORCE="${AGENT_BRIDGE_ALLOW_DOWNGRADE:-false}"
[[ "$FORCE" == "1" ]] && FORCE=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=true; shift ;;
        --force) FORCE=true; shift ;;
        *)       echo "[FAIL] Unknown option: $1" >&2; exit 1 ;;
    esac
done

# -- Helpers -----------------------------------------------------------------

_ok()   { echo "  [OK]   $*"; }
_skip() { echo "  [SKIP] $*"; }
_fail() { echo "  [FAIL] $*" >&2; }
_step() { echo "  ...    $*"; }
_warn() { echo "  [WARN] $*" >&2; }

# === install-contract:v3 versioned-venv helpers (agent-bridge) ===
_versioned_activate() {
    # Swap the stable `venv` symlink to this version's freshly-built slot, moving
    # a legacy real `venv` aside on first migration (--replace-nonlink). No-op in
    # legacy mode. The caller must have stopped/cut over any daemon holding the
    # old venv first.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name "venv" activate "$SRC_VERSION" --replace-nonlink; then
        _fail "Failed to activate versioned venv (venv -> versions/$SRC_VERSION)"
        return 1
    fi
    _ok "Runtime version $SRC_VERSION active (venv -> versions/$SRC_VERSION)"
}

_versioned_current() {
    # The version the `venv` link currently points at (empty for a legacy real
    # venv or a fresh box).
    [[ "$VERSIONED_RUNTIME" == 1 ]] || { echo ""; return 0; }
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || { echo ""; return 0; }
    "$py" "$vr" --root "$INSTALL_DIR" --link-name "venv" current 2>/dev/null || echo ""
}

_versioned_gc() {
    # Prune old version slots, keeping current + the given previous-good +
    # live-pid-pinned slots. Best-effort. No-op in legacy mode.
    local keep_prev="${1:-}"
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || return 0
    if [[ -n "$keep_prev" ]]; then
        "$py" "$vr" --root "$INSTALL_DIR" --link-name "venv" gc --protect-pids --keep "$keep_prev" 2>&1 | sed 's/^/  ...    gc: /' || true
    else
        "$py" "$vr" --root "$INSTALL_DIR" --link-name "venv" gc --protect-pids 2>&1 | sed 's/^/  ...    gc: /' || true
    fi
}

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
# === end install-contract:v3 versioned-venv helpers ===

_get_pid() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid="$(cat "$PID_FILE")"
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

_active_port() {
    # Resolve the daemon's LIVE port from the routing table ($INSTALL_DIR/active.json).
    # Post-#694 a primary daemon binds an OS-assigned ephemeral port and advertises the
    # actual bound port there (the `agent-bridge` CLI client already resolves it the same
    # way). Health probes MUST use it, or a dynamic-port daemon looks dead on the pinned
    # $PORT and a routine redeploy health-gate false-fails a healthy daemon and can
    # self-inflict an outage (dotfiles #856). Echoes the live port, or nothing when there
    # is no routing table (fresh install / pinned-port deployment) so callers fall back to $PORT.
    local aj="$INSTALL_DIR/active.json"
    [[ -f "$aj" ]] || return 1
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3 || command -v python || true)"
    [[ -n "$py" ]] || return 1
    "$py" - "$aj" <<'PYEOF' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    p = int((d.get("active") or {}).get("port") or 0)
except Exception:
    p = 0
if p > 0:
    print(p)
else:
    sys.exit(1)
PYEOF
}

_health_check() {
    local retries=5 port
    for i in $(seq 1 $retries); do
        # Re-resolve each iteration: during a stop-restart active.json initially
        # advertises the old (now-stopped) daemon until the new one publishes.
        port="$(_active_port || true)"
        [[ -n "$port" ]] || port="$PORT"
        if curl -sf "http://127.0.0.1:${port}/health" > /dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# Wait until the port is free (no listener). Returns 0 once clear, 1 on timeout.
_wait_port_free() {
    local retries=10
    for i in $(seq 1 $retries); do
        if ! ss -tlnp 2>/dev/null | grep -q ":${PORT} " && \
           ! curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Best-effort graceful drain before a stop: give in-flight turns a chance to
# settle so a routine update does not hard-kill an active session (Phase 1
# zero-downtime). Bounded by --timeout and --force so an update never blocks
# indefinitely. Non-fatal -- the stop that follows is the backstop.
_drain_service() {
    local timeout="${1:-120}"
    # Drain the RUNNING daemon, resolved through the stable `venv` link.
    [[ -x "$LINK_DIR/bin/agent-bridge" ]] || return 0
    _step "Draining in-flight sessions (up to ${timeout}s)..."
    if "$LINK_DIR/bin/agent-bridge" drain --timeout "$timeout" --force \
            > /dev/null 2>&1; then
        _ok "Drain window complete"
    else
        _warn "Drain reported busy sessions -- proceeding with swap"
    fi
}

# Resolve a vendored library path (libs/<name>) across multiple layouts.
# Prints the resolved directory path to stdout (nothing else).
# Returns 0 if found, 1 if not.
_resolve_vendored_lib() {
    local lib_name="$1"
    local candidate

    # 1. Vendored inside agent-bridge (marketplace install layout)
    candidate="$PLUGIN_DIR/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 2. Relative path (git checkout layout: plugins/agent-bridge/../../libs/<name>)
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

# Resolve the ssh-manager / credential-relay vendored libs (thin wrappers).
_resolve_ssh_manager() { _resolve_vendored_lib ssh-manager; }
_resolve_credential_relay() { _resolve_vendored_lib credential-relay; }
# zero-downtime cutover primitives (module ``zdd``), extracted from this plugin.
_resolve_zdd() { _resolve_vendored_lib zdd; }
# config schema versioning + migration (module ``config_migrate``).
_resolve_config_migrate() { _resolve_vendored_lib config-migrate; }

# Check if ssh-manager is already importable in the venv.
# Returns 0 if the key symbols can be imported successfully.
_ssh_manager_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from ssh_manager import SSHProfileSource, get_default_manager' 2>/dev/null
}

# Check if credential-relay is already importable in the venv.
_credential_relay_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from credential_relay import RelayBuilder' 2>/dev/null
}

# Check if the zdd cutover lib is already importable in the venv.
_zdd_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from zdd.cutover import CutoverOrchestrator' 2>/dev/null
}

# Check if config-migrate is already importable in the venv.
_config_migrate_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from config_migrate import migrate_file' 2>/dev/null
}

# #892 Increment 4: agent-bridge no longer VENDORS sibling plugins into its venv.
# The `codespace:` / `container:` namespace resolvers AND the credential-relay
# profiles are now driven over a PROCESS BOUNDARY -- the `agent-<sibling>
# namespace-* / relay-profile` CLIs, run from each sibling's OWN immutable venv.
# So a sibling bugfix reaches the dispatch path with NO agent-bridge redeploy,
# and the #929 vendored-copy-drift / #828 installed-but-unimportable classes are
# structurally gone. Kept as a no-op (call sites unchanged) for a minimal diff.
# Sibling CLI binstubs remain owned by their own installers (~/.agent-<sibling>).
_install_sibling_plugins() {
    _step "Sibling plugins not vendored -- process-boundary CLI seams (#892)"
}

# Sibling plugin binstubs (e.g. agent-codespaces) are owned by their own
# installer (~/.agent-codespaces), not by agent-bridge. Uninstall leaves them.
_remove_sibling_binstubs() {
    _step "Leaving sibling CLI binstubs in place (owned by their own installers)"
}

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

# Unified schema_version 3 manifest writer. Self-contained per plugin (no shared
# module -- plugins are pulled independently from the marketplace). Records the
# source footprint (local vs marketplace) and is written atomically (temp+move).
_write_deploy_manifest_for() {
    local service="$1" plugin="$2" install_path="$3" plugin_path="$4" venv_path="$5"
    local manifest="$install_path/deploy-manifest.json"
    local kind
    kind="$(_source_kind "$plugin_path")"

    local ver="0.0.0"
    if [[ -f "$plugin_path/pyproject.toml" ]]; then
        ver=$(grep -m1 '^version' "$plugin_path/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/' || echo "0.0.0")
    fi

    # Git provenance only applies to a local checkout.
    local commit="null" branch="null" dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root c b d
        repo_root="$(cd "$plugin_path/.." && pwd)"
        read -r c b d <<< "$(_git_info "$repo_root")"
        commit="\"$c\""; branch="\"$b\""; dirty="$d"
    fi

    local tmp="$manifest.tmp"
    cat > "$tmp" << EOF
{
  "schema_version": 3,
  "service": "$service",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$plugin_path",
    "repo": "copilot-extensions",
    "plugin": "$plugin",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$venv_path",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest"
    _ok "Deploy manifest written (source: $kind)"
}

_write_deploy_manifest() {
    # The manifest `venv` field records the stable `venv` link ($LINK_DIR), never
    # a versions/<v> slot.
    _write_deploy_manifest_for "agent-bridge" "agent-bridge" \
        "$INSTALL_DIR" "$PLUGIN_DIR" "$LINK_DIR"
}

_install_systemd_unit() {
    # Only install systemd unit if systemd is available and we have user units
    if ! command -v systemctl &>/dev/null; then
        _skip "systemd not available -- skipping unit installation"
        return
    fi

    local unit_dir="$HOME/.config/systemd/user"
    mkdir -p "$unit_dir"

    # Resolve the daemon through the stable `venv` link (a symlink to the active
    # versions/<v> slot in the immutable-versioned layout), never a versions/<v>
    # absolute a `gc` could remove.
    local venv_bridge="$LINK_DIR/bin/agent-bridge"

    cat > "$unit_dir/$SYSTEMD_UNIT" << EOF
[Unit]
Description=Agent-Bridge -- inter-agent communication service
After=network.target

[Service]
Type=simple
# KillMode=process: on stop/restart, signal ONLY the main daemon process, not
# the whole cgroup. This lets a survivable Session Host (session_host_enabled)
# and its Copilot --acp child outlive an agent-bridge restart so the new daemon
# can reattach (effort agent-bridge-version-mux, #1759; fixes #1780 -- the
# default KillMode=control-group cgroup-kills the host). The daemon's own
# lifespan shutdown gracefully stops SSH masters, the credential relay, and
# non-host sessions, so nothing else leaks. This is the systemd analog of the
# Windows Job Object breakaway.
KillMode=process
ExecStart=$venv_bridge start
ExecStopPost=/bin/sleep 2
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUTF8=1

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    _ok "systemd user unit installed and enabled"
}

_migration_check() {
    local old_manifest="$INSTALL_DIR/deploy-manifest.json"
    [[ -f "$old_manifest" ]] || return 0

    if grep -q '"installer_path".*services/agent-bridge' "$old_manifest" 2>/dev/null; then
        _step "Migrating from legacy project-service installer"
        _step "  Preserving config, auth, and DB"

        # Stop old instance
        if pid=$(_get_pid); then
            _step "  Stopping running instance (pid=$pid)"
            kill "$pid" 2>/dev/null || true
            sleep 2
            rm -f "$PID_FILE"
        fi

        # Stop old systemd unit if managed by the legacy installer
        if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
            systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        fi

        _ok "Migration from legacy project-service installer detected"
    fi
}

# -- Actions -----------------------------------------------------------------

# --- self-provisioning helpers (runtime-self-provisioning pattern) -----------
# Vendor a standalone uv into the runtime tool dir when uv is absent (pristine or
# governed box) instead of dead-ending; add it to PATH for this run.
_ensure_uv() {
    command -v uv &>/dev/null && return 0
    local tooldir="$INSTALL_DIR/tool"
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; return 0; fi
    _step "uv not found -- vendoring a standalone uv into $tooldir"
    mkdir -p "$tooldir"
    local url="https://astral.sh/uv/install.sh" script="$tooldir/uv-install.sh" got=""
    if command -v curl &>/dev/null; then curl -LsSf "$url" -o "$script" 2>/dev/null && got=1; fi
    if [[ -z "$got" ]] && command -v wget &>/dev/null; then wget -qO "$script" "$url" 2>/dev/null && got=1; fi
    if [[ -z "$got" ]] && command -v python3 &>/dev/null; then
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
    if command -v pip &>/dev/null; then idx="$(pip config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [[ -z "$idx" ]] && command -v pip3 &>/dev/null; then idx="$(pip3 config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
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
# venv console script; otherwise it provisions on first use -- announcing (a human
# line + a machine-readable ::agent-provisioning:: signal so a caller can extend
# its timeout), lock-serialized, fail-fast. Replaces the old thin exec stub.
deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    cat > "$BINSTUB" << 'STUB'
#!/usr/bin/env bash
# agent-bridge binstub -- self-provisioning (install-on-first-use).
export PYTHONUTF8=1
_name="agent-bridge"
_root="$HOME/.$_name"
_console="$_root/venv/bin/$_name"
[ -x "$_console" ] && exec "$_console" "$@"
mkdir -p "$_root"
_status="$_root/.provision-status"
printf '%s\n' "[$_name] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout." >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' "$_name" "$_status" >&2
_install="$(cat "$_root/payload-dir" 2>/dev/null)/scripts/install.sh"
[ -f "$_install" ] || _install="$(ls "$HOME"/.copilot/installed-plugins/*/"$_name"/scripts/install.sh 2>/dev/null | head -n1)"
if [ ! -f "$_install" ]; then
    printf '%s\n' "[$_name] cannot self-provision: installer not found in plugin payload. Ensure the plugin is enabled, then retry." >&2
    exit 127
fi
_lock="$_root/.provision.lock"
exec 9>"$_lock"
command -v flock >/dev/null 2>&1 && flock 9 2>/dev/null
[ -x "$_console" ] && exec "$_console" "$@"
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
bash "$_install" provision >&2
_rc=$?
if [ "$_rc" -eq 0 ] && [ -x "$_console" ]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
    exec "$_console" "$@"
fi
printf 'failed rc=%s %s\n' "$_rc" "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
if [ "$_rc" -eq 0 ]; then
    printf '%s\n' "[$_name] provisioning reported success but the CLI is still missing ($_console)." >&2
    _rc=1
else
    printf '%s\n' "[$_name] provisioning FAILED (rc=$_rc). See the log above; retry, or run: bash \"$_install\" provision" >&2
fi
exit "$_rc"
STUB
    chmod +x "$BINSTUB"
    _ok "Binstub: $BINSTUB (self-provisioning)"
}

# Cheap "splat the binstub, defer the runtime" install (fits a sessionStart hook's
# grace window): record the REAL payload path + deploy the self-provisioning
# binstub -- NO venv, NO uv. The runtime builds itself on the binstub's first use.
do_stamp() {
    echo ""; echo "=== agent-bridge stamp (defer runtime to first use) ==="; echo ""
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
    deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
}

do_install() {
    echo ""
    echo "=== agent-bridge install ==="
    echo ""

    # Prerequisite: uv (self-acquired if absent) + governed-feed index
    _ensure_uv || exit 1
    _ensure_uv_index

    _migration_check

    # Guard against a stale checkout downgrading an existing healthy install
    # (#1790). No-op on first install (no installed version to compare).
    _downgrade_guard

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"

    # #935: toss an INCOMPLETE prior slot first so we never `uv venv
    # --allow-existing` over a half-built corpse (the current/active slot is
    # never tossed). No-op in legacy mode.
    _versioned_slot_clean

    # Create venv via uv
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        _step "Creating venv via uv..."
        if ! uv venv "$VENV_DIR" --python 3.10 --allow-existing; then
            if ! uv venv "$VENV_DIR" --allow-existing; then
                _fail "Failed to create venv at $VENV_DIR"
                exit 1
            fi
        fi
        _ok "Venv created"
    else
        _skip "Venv already exists"
    fi

    # Install package via uv (ssh-manager library first, then agent-bridge)
    _step "Installing agent-bridge package..."
    local ssh_manager_dir
    if ssh_manager_dir="$(_resolve_ssh_manager)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" "$ssh_manager_dir" --quiet; then
            _fail "ssh-manager install failed"
            exit 1
        fi
    elif _ssh_manager_installed; then
        _step "ssh-manager already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        exit 1
    fi
    # credential-relay (the relay framework agent-bridge runs in its daemon).
    local cred_relay_dir
    if cred_relay_dir="$(_resolve_credential_relay)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" "$cred_relay_dir" --quiet; then
            _fail "credential-relay install failed"
            exit 1
        fi
    elif _credential_relay_installed; then
        _step "credential-relay already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        exit 1
    fi
    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" "$zdd_dir" --quiet; then
            _fail "zdd install failed"
            exit 1
        fi
    elif _zdd_installed; then
        _step "zdd already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate zdd library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        exit 1
    fi
    # config-migrate (config schema versioning + migration).
    local cfg_migrate_dir
    if cfg_migrate_dir="$(_resolve_config_migrate)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" "$cfg_migrate_dir" --quiet; then
            _fail "config-migrate install failed"
            exit 1
        fi
    elif _config_migrate_installed; then
        _step "config-migrate already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate config-migrate library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        exit 1
    fi
    if ! uv pip install --python "$VENV_DIR/bin/python" "$PLUGIN_DIR" --quiet; then
        _fail "Package install failed"
        exit 1
    fi
    _ok "Package installed"

    # Versioned layout (#581): health-gate the freshly-built slot in isolation,
    # then swap the stable `venv` symlink onto it. Everything below resolves
    # through `venv` (the link). No-op in legacy mode.
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        if ! _runtime_healthy; then
            _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
            exit 1
        fi
        _versioned_mark_complete
        _versioned_activate || exit 1
    fi

    # Machine-local config schema migration (idempotent + atomic; never touches
    # repo config). Non-fatal.
    if PYTHONUTF8=1 "$VENV_DIR/bin/python" -m agent_bridge config migrate 2>/dev/null; then
        :
    else
        _step "Config migration skipped"
    fi

    # Create binstub
    deploy_binstub

    # Generate default config
    "$VENV_DIR/bin/python" -c \
        "from agent_bridge.config import load_config, write_default_config; write_default_config(load_config())" \
        2>/dev/null || true
    _ok "Default config generated"

    # Install systemd unit
    _install_systemd_unit

    # Write deploy manifest
    _write_deploy_manifest

    echo ""
    _ok "agent-bridge installed"
    echo "  Install dir: $INSTALL_DIR"
    echo "  Binstub:     $BINSTUB"
    echo "  Config:      agent-bridge config show"
    echo "  API:         http://127.0.0.1:$PORT"

    # Start service and verify health
    echo ""
    _step "Starting service after install..."
    do_start
}

do_uninstall() {
    echo ""
    echo "=== agent-bridge uninstall ==="
    echo ""

    do_stop

    # Remove systemd unit
    if command -v systemctl &>/dev/null; then
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
        _ok "systemd unit removed"
    fi

    rm -f "$BINSTUB"
    _ok "Binstub removed"

    _remove_sibling_binstubs

    # Remove the runtime venv. In the versioned layout this means the `venv`
    # symlink AND the whole versions/ tree; otherwise the single real venv dir.
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        [[ -L "$LINK_DIR" ]] && rm -f "$LINK_DIR"
        [[ -d "$LINK_DIR" && ! -L "$LINK_DIR" ]] && rm -rf "$LINK_DIR"
        [[ -d "$INSTALL_DIR/versions" ]] && rm -rf "$INSTALL_DIR/versions"
        _ok "Venv removed"
    elif [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        _ok "Venv removed"
    fi

    if $PURGE; then
        _warn "Purging config, DB, and auth"
        rm -rf "$INSTALL_DIR"
    else
        _skip "Preserved config/DB at $INSTALL_DIR (use --purge to remove)"
    fi

    _ok "agent-bridge uninstalled"
}

do_start() {
    if pid=$(_get_pid); then
        _warn "agent-bridge is already running (pid=$pid)"
        return 0
    fi

    if [[ ! -x "$LINK_DIR/bin/agent-bridge" ]]; then
        _fail "agent-bridge not installed. Run: install.sh install"
        exit 1
    fi

    _step "Starting agent-bridge..."

    # Prefer systemd if available
    if command -v systemctl &>/dev/null && [[ -f "$HOME/.config/systemd/user/$SYSTEMD_UNIT" ]]; then
        systemctl --user start "$SYSTEMD_UNIT"
        sleep 2
        if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
            if _health_check; then
                _ok "agent-bridge started via systemd (port=$PORT)"
            else
                _warn "agent-bridge started via systemd but health check failed"
            fi
            return 0
        fi
        _warn "systemd start failed -- falling back to direct start"
    fi

    # Direct start -- launch through the stable `venv` link.
    nohup "$LINK_DIR/bin/agent-bridge" start > "$INSTALL_DIR/agent-bridge.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 2

    if kill -0 "$pid" 2>/dev/null; then
        if _health_check; then
            _ok "agent-bridge started (pid=$pid, port=$PORT)"
        else
            _warn "agent-bridge started (pid=$pid) but health check failed"
        fi
    else
        _fail "agent-bridge failed to start -- check $INSTALL_DIR/agent-bridge.log"
        rm -f "$PID_FILE"
        exit 1
    fi
}

do_stop() {
    # Try systemd first
    if command -v systemctl &>/dev/null; then
        if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
            _step "Stopping agent-bridge via systemd..."
            systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
            _wait_port_free || _warn "Port $PORT still in use after stop"
            _ok "agent-bridge stopped (systemd)"
            rm -f "$PID_FILE"
            return
        fi
    fi

    # Direct stop via PID
    if pid=$(_get_pid); then
        _step "Stopping agent-bridge (pid=$pid)..."
        kill "$pid" 2>/dev/null || true
        _wait_port_free || _warn "Port $PORT still in use after stop"
        rm -f "$PID_FILE"
        _ok "agent-bridge stopped"
    else
        # Last resort: find orphan by port binding (PID file lost)
        local port_pid
        port_pid="$(ss -tlnp 2>/dev/null | grep ":${PORT} " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)"
        if [[ -n "$port_pid" ]]; then
            _step "Stopping orphaned agent-bridge (pid=$port_pid, found by port)..."
            kill "$port_pid" 2>/dev/null || true
            _wait_port_free || _warn "Port $PORT still in use after stop"
            _ok "agent-bridge stopped"
        else
            _skip "agent-bridge is not running"
        fi
    fi

    # Also ensure the integrated credential relay is down. It runs in-process
    # with the bridge, but free its port explicitly to catch an orphaned relay.
    local relay_pid
    relay_pid="$(ss -tlnp 2>/dev/null | grep ":${RELAY_PORT} " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)"
    if [[ -n "$relay_pid" ]]; then
        _warn "Credential relay port $RELAY_PORT still in use -- killing (pid=$relay_pid)"
        kill "$relay_pid" 2>/dev/null || true
    fi
}

do_status() {
    local running=false

    # Check systemd
    if command -v systemctl &>/dev/null && systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
        _ok "agent-bridge is running (systemd)"
        running=true
    elif pid=$(_get_pid); then
        _ok "agent-bridge is running (pid=$pid)"
        running=true
    else
        _step "agent-bridge is not running"
    fi

    if $running; then
        if _health_check; then
            _ok "Health check passed (port $PORT)"
        else
            _warn "Process running but health check failed"
        fi
    fi

    # Install state -- the currently-active runtime (via the `venv` link).
    if [[ -x "$LINK_DIR/bin/agent-bridge" ]]; then
        local version
        version=$("$LINK_DIR/bin/agent-bridge" version 2>/dev/null || echo "unknown")
        _ok "Installed: $version"
    else
        _step "Not installed"
    fi

    # Runtime source footprint (local checkout vs marketplace)
    if [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
        local _kind _ver
        _kind=$(grep -o '"kind": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ver=$(grep -o '"version": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        [[ -n "$_kind" ]] && _ok "Source: $_kind ($_ver)"
    fi

    # Config
    if [[ -f "$INSTALL_DIR/config.yaml" ]]; then
        _ok "Config: $INSTALL_DIR/config.yaml"
    fi

    # Systemd unit
    if command -v systemctl &>/dev/null && [[ -f "$HOME/.config/systemd/user/$SYSTEMD_UNIT" ]]; then
        local state
        state=$(systemctl --user is-enabled "$SYSTEMD_UNIT" 2>/dev/null || echo "not found")
        _ok "systemd unit: $state"
    fi

    # Exit non-zero when not installed (used by module update orchestrator)
    if [[ ! -x "$LINK_DIR/bin/agent-bridge" ]]; then
        exit 1
    fi
}

_runtime_healthy() {
    # True if the venv python can import the agent-bridge runtime + key deps.
    # Used to decide whether to snapshot the current venv and to verify a fresh
    # install before declaring the update good (#52). uvicorn + credential_relay
    # are the modules that went missing in the observed broken-venv outage.
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'import agent_bridge, uvicorn, credential_relay, zdd' 2>/dev/null
}

# Version of the agent-bridge package currently installed in the runtime venv.
# Prints the version to stdout; returns 1 if it cannot be determined (e.g. no
# venv, or a broken install) so the caller can skip the downgrade guard.
_installed_version() {
    # The version currently ACTIVE (via the `venv` link), for the downgrade guard.
    [[ -x "$LINK_DIR/bin/python" ]] || return 1
    local v
    v="$("$LINK_DIR/bin/python" -c \
        'from importlib.metadata import version; print(version("agent-bridge"))' \
        2>/dev/null)" || return 1
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

# Version of the agent-bridge source about to be installed (this checkout).
# Read from plugin.json (single source of truth for the plugin build). Prints
# the version to stdout; returns 1 if it cannot be determined.
_source_version() {
    local manifest="$PLUGIN_DIR/plugin.json"
    [[ -f "$manifest" ]] || return 1
    local v
    v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$manifest" | head -n1)"
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

# True (0) if version $1 is strictly older than version $2. Normalizes the PEP
# 440 dev separator first -- plugin.json carries `0.4.0-dev93` (hyphen) but
# importlib.metadata reports the normalized `0.4.0.dev93` (dot), so without this
# an equal version would not compare equal. `sort -V` then orders our
# `0.4.0.devN` build stream correctly (dev71 < dev93 < dev100).
_version_lt() {
    local a="${1//-/.}" b="${2//-/.}"
    [[ "$a" == "$b" ]] && return 1
    local lower
    lower="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"
    [[ "$lower" == "$a" ]]
}

# Downgrade guard (#1790). A stress test caught an agent running the raw
# installer from a STALE checkout (dev71) over a live dev87 daemon, silently
# downgrading it -- reverting the Session-Host survival code and the
# KillMode=process fix, and stranding the agent's own session. Refuse to
# install an OLDER version over a newer running one unless --force
# (AGENT_BRIDGE_ALLOW_DOWNGRADE=1) is given, and steer to the marketplace path.
# Non-fatal when either version is unknown -- the guard only fires on a
# confirmed downgrade.
_downgrade_guard() {
    local installed source
    installed="$(_installed_version)" || return 0
    source="$(_source_version)" || {
        _warn "Could not read source version from plugin.json -- skipping downgrade guard"
        return 0
    }
    if _version_lt "$source" "$installed"; then
        if [[ "$FORCE" == true ]]; then
            _warn "Downgrade $installed -> $source forced (--force / AGENT_BRIDGE_ALLOW_DOWNGRADE)"
            return 0
        fi
        echo ""
        _fail "Refusing to downgrade agent-bridge: installed $installed > source $source"
        _fail "This checkout is OLDER than the running daemon. Installing it would"
        _fail "revert live features (e.g. Session-Host survival, KillMode=process)"
        _fail "and can strand active Copilot sessions (#1790)."
        _fail ""
        _fail "Use the sanctioned marketplace update instead:"
        _fail "    test-chamber services agent-bridge update"
        _fail "Or, to override intentionally (e.g. a deliberate rollback):"
        _fail "    $0 $ACTION --force"
        echo ""
        exit 1
    fi
}

_backup_venv() {
    # Snapshot $VENV_DIR so a failed update can roll back. Clears any stale copy.
    rm -rf "$VENV_DIR.bak"
    cp -a "$VENV_DIR" "$VENV_DIR.bak" 2>/dev/null
}

_restore_venv() {
    # Replace a broken $VENV_DIR with the snapshot at $VENV_DIR.bak.
    [[ -d "$VENV_DIR.bak" ]] || return 1
    rm -rf "$VENV_DIR" && mv "$VENV_DIR.bak" "$VENV_DIR"
}

_remove_venv_backup() {
    rm -rf "$VENV_DIR.bak"
}

# Core update steps (venv repair + package installs + verify). Returns non-zero
# on any failure WITHOUT exiting, so the caller can roll back. The service must
# already be stopped before this runs.
_update_core() {
    # Build/repair the venv. Versioned layout: $VENV_DIR is a FRESH per-version
    # slot (never the running daemon's), so an absent slot is normal -- always
    # build it. Legacy layout: repair the single venv in place if python is gone.
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        if [[ ! -x "$VENV_DIR/bin/python" ]]; then
            _step "Building runtime slot versions/$SRC_VERSION..."
            _versioned_slot_clean
            if ! uv venv "$VENV_DIR" --python 3.10 --allow-existing; then
                uv venv "$VENV_DIR" --allow-existing || { _fail "Venv build failed (versions/$SRC_VERSION)"; return 1; }
            fi
            _ok "Built runtime slot versions/$SRC_VERSION"
        fi
    elif [[ ! -x "$VENV_DIR/bin/python" ]]; then
        if [[ -d "$VENV_DIR" ]]; then
            _step "Repairing venv (python binary missing)..."
        else
            _fail "agent-bridge not installed. Run: install.sh install"
            return 1
        fi
        if ! uv venv "$VENV_DIR" --python 3.10 --allow-existing; then
            uv venv "$VENV_DIR" --allow-existing || { _fail "Venv repair failed"; return 1; }
        fi
        _ok "Venv repaired"
    fi

    _step "Updating agent-bridge package..."
    local ssh_manager_dir
    if ssh_manager_dir="$(_resolve_ssh_manager)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-ssh-manager \
                "$ssh_manager_dir" --quiet; then
            _fail "ssh-manager update failed"
            return 1
        fi
    elif _ssh_manager_installed; then
        _step "ssh-manager already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        return 1
    fi
    # credential-relay: force-reinstall so a local code change propagates even
    # without a version bump (uv otherwise skips a same-version path dep).
    local cred_relay_dir
    if cred_relay_dir="$(_resolve_credential_relay)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-credential-relay \
                "$cred_relay_dir" --quiet; then
            _fail "credential-relay update failed"
            return 1
        fi
    elif _credential_relay_installed; then
        _step "credential-relay already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        return 1
    fi
    # zdd: force-reinstall so a local code change propagates even without a
    # version bump (uv otherwise skips a same-version path dep).
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-zdd \
                "$zdd_dir" --quiet; then
            _fail "zdd update failed"
            return 1
        fi
    elif _zdd_installed; then
        _step "zdd already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate zdd library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        return 1
    fi
    # config-migrate: force-reinstall so a local code change propagates.
    local cfg_migrate_dir
    if cfg_migrate_dir="$(_resolve_config_migrate)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-config-migrate \
                "$cfg_migrate_dir" --quiet; then
            _fail "config-migrate update failed"
            return 1
        fi
    elif _config_migrate_installed; then
        _step "config-migrate already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate config-migrate library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        return 1
    fi
    if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-bridge \
            "$PLUGIN_DIR" --quiet; then
        _fail "Package update failed"
        return 1
    fi

    # Verify the freshly-installed runtime imports before declaring success --
    # catches a half-installed venv (e.g. a wheel/dependency gap) while we can
    # still roll back, rather than starting a broken service.
    if ! _runtime_healthy; then
        _fail "Post-install verification failed (agent_bridge / uvicorn / credential_relay not importable)"
        return 1
    fi
    # Machine-local config schema migration (idempotent + atomic). Non-fatal.
    if PYTHONUTF8=1 "$VENV_DIR/bin/python" -m agent_bridge config migrate 2>/dev/null; then
        :
    else
        _step "Config migration skipped"
    fi
    _ok "Package updated"
    return 0
}

do_update() {
    echo ""
    echo "=== agent-bridge update ==="
    echo ""

    # Prerequisite: uv (self-acquired if absent) + governed-feed index
    _ensure_uv || exit 1
    _ensure_uv_index

    # Refuse a downgrade from a stale checkout before touching the live daemon
    # (#1790). Runs first so a rejected update never drains/stops the service.
    _downgrade_guard

    # Is the service currently running?
    local was_running=false
    if pid=$(_get_pid) || (command -v systemctl &>/dev/null && systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null); then
        was_running=true
    fi

    # Versioned layout: remember the currently-active version (rollback + gc keep).
    local prev_version=""
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        prev_version="$(_versioned_current)"
    fi

    # Snapshot the current healthy venv so a failed install can roll back to the
    # previous-good runtime instead of leaving the service DOWN with a broken/
    # empty venv (#52). Only snapshot a venv that actually works. Skipped in the
    # versioned layout: rollback there is "leave the `venv` link on the previous
    # slot" (the link is only swapped after a healthy build).
    local have_backup=false
    if [[ "$VERSIONED_RUNTIME" != 1 ]] && _runtime_healthy; then
        if _backup_venv; then have_backup=true; fi
    fi

    # Decide the swap strategy:
    #   - Graceful zero-downtime cutover (Thread B DEFAULT whenever a live daemon
    #     is running -- the AGENT_BRIDGE_ZERO_DOWNTIME opt-in is RETIRED, matching
    #     install.ps1): leave the old daemon RUNNING, build the new venv, then
    #     `agent-bridge deploy` (the installer-internal cutover seam) stands the new
    #     daemon up beside it on a fresh port, flips the routing table, drains the
    #     old daemon, and retires it. No API-unavailable window and no hard-killed
    #     turns. Under systemd the old (unit-tracked) daemon exits cleanly (0), so
    #     Restart=on-failure never resurrects it; the detached survivor serves and
    #     the refreshed unit starts the new slot at next boot (the POSIX twin of
    #     Windows conhost detaching the daemon from its Scheduled Task).
    #   - Fallback (drain-then-swap): when a cutover cannot run or fails, drain
    #     in-flight work for a grace window, then stop/reinstall/start -- no active
    #     turn is hard-killed up to the drain timeout, though a brief API-unavailable
    #     window remains. (AGENT_BRIDGE_ZERO_DOWNTIME is still ACCEPTED as a
    #     deprecated no-op so existing callers don't break.)
    #
    # Versioned layout: the new version builds into its OWN slot, so cutover no
    # longer needs the (new) venv to pre-exist -- gate only on "running".
    local cutover=false
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        if [[ "$was_running" == true ]]; then
            cutover=true
            # Cutover onto the same slot is impossible; downgrade to stop-and-rebuild.
            if [[ "$SRC_VERSION" == "$prev_version" ]]; then
                _step "Cutover skipped: version $SRC_VERSION is already active; using classic stop-and-rebuild"
                cutover=false
            fi
        fi
    elif [[ "$was_running" == true && -x "$VENV_DIR/bin/agent-bridge" ]]; then
        cutover=true
    fi

    # Stop the running instance before the in-place reinstall, UNLESS we are
    # doing a cutover (which keeps the old daemon up and retires it afterward).
    # Either way, drain first so in-flight turns get a chance to settle.
    if [[ "$was_running" == true && "$cutover" == false ]]; then
        _drain_service "${AGENT_BRIDGE_DRAIN_TIMEOUT:-120}"
        do_stop
    fi

    # Run the protected update; on any failure, roll back to the snapshot.
    if ! _update_core; then
        _fail "Update failed"
        if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
            # The `venv` link was never swapped (activate runs only after a healthy
            # build), so the previous slot is still active. Discard the half-built
            # new slot (unless it IS the active one) and restart the previous
            # version if the classic path stopped it.
            if [[ -n "$SRC_VERSION" && "$SRC_VERSION" != "$prev_version" ]]; then
                rm -rf "$INSTALL_DIR/versions/$SRC_VERSION"
            fi
            if [[ "$was_running" == true && "$cutover" == false ]]; then
                _step "Restarting the previous version..."
                do_start
            fi
            _warn "Update failed; kept the previous runtime (venv -> versions/${prev_version:-<previous>})."
        elif [[ "$have_backup" == true ]]; then
            _step "Rolling back to the previous venv..."
            if _restore_venv; then
                _ok "Previous venv restored"
                # Only restart in the default path -- in cutover mode the old
                # daemon was never stopped, so it is still serving.
                if [[ "$was_running" == true && "$cutover" == false ]]; then
                    _step "Restarting the previous service..."
                    do_start
                fi
            else
                _fail "Rollback failed -- run install.sh install to rebuild the runtime"
            fi
        else
            _warn "No healthy venv snapshot to roll back to -- run install.sh install to rebuild"
        fi
        exit 1
    fi

    # Versioned layout: the new slot is built + verified; atomically swap the
    # stable `venv` symlink onto it. In cutover mode the old daemon keeps serving
    # from its own immutable slot until drained. No-op in legacy mode.
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        _versioned_mark_complete
        _versioned_activate || { _fail "Versioned activate failed"; exit 1; }
    fi

    # Success: discard the rollback snapshot.
    _remove_venv_backup

    # Update sibling plugins (e.g. agent-codespaces for codespace: namespace)
    _install_sibling_plugins reinstall

    # Update binstub
    deploy_binstub

    # Update systemd unit
    _install_systemd_unit

    # Update deploy manifest
    _write_deploy_manifest

    # Bring the new version into service. Launch through the stable `venv` link.
    if [[ "$cutover" == true ]]; then
        _step "Zero-downtime cutover (agent-bridge deploy)..."
        if "$LINK_DIR/bin/agent-bridge" deploy \
                --drain-timeout "${AGENT_BRIDGE_DRAIN_TIMEOUT:-300}"; then
            _ok "Cutover complete -- new daemon active, old retired"
        else
            _warn "Cutover failed -- falling back to drain/stop/start"
            _drain_service 30
            do_stop
            do_start
        fi
    else
        _step "Starting service..."
        do_start
    fi

    # Versioned layout: prune old version slots now that the new one is healthy
    # and active, keeping current + the previous-good + any live-pid-pinned slot.
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        _versioned_gc "$prev_version"
    fi

    _ok "Update complete"
}

# -- Dispatch ----------------------------------------------------------------

case "$ACTION" in
    install)   do_install ;;
    stamp)     do_stamp ;;
    provision) do_install ;;
    uninstall) do_uninstall ;;
    start)     do_start ;;
    stop)      do_stop ;;
    status)    do_status ;;
    update)    do_update ;;
    *)
        echo "Usage: $0 {install|stamp|provision|uninstall|start|stop|status|update} [options]" >&2
        exit 1
        ;;
esac
