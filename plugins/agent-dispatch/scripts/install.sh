#!/usr/bin/env bash
# =============================================================================
# install.sh -- agent-dispatch -- plugin installer for Linux / WSL / macOS
# =============================================================================
# Manages the agent-dispatch coordinator lifecycle: install, update, status,
# start, stop, uninstall -- the same shape as the agent-bridge installer, so
# the agent-worktrees plugin reconciler (runtimeScope: machine-gated) and the
# `test-chamber services agent-dispatch <action>` path both drive it.
#
# On a standalone Linux deploy host it ALSO installs the **embody supervisor**
# (`agent-dispatch-supervisor.service`), a second systemd user unit that runs
# `agent-dispatch supervise --all-repos` as a serve loop so dispatched, LABELED
# tasks are turned into host embody autopilots unattended. The supervisor is
# **label-gated for safety** -- a label-less supervisor would embody every queued
# task -- so it is enabled only when `AGENT_DISPATCH_SUPERVISE_LABELS` is set in
# `supervisor.env`; with none set the unit is installed but left inert (#2869).
#
# Runtime lives at ~/.agent-dispatch/ (venv, config, DB). Binstub goes to
# ~/.local/bin/agent-dispatch. A STANDALONE Linux host (e.g. Mantis-Counter) runs the
# full coordinator as a systemd **user** service (loopback 127.0.0.1, an
# OS-assigned dynamic port advertised via the rendezvous file -- Stage C). A
# WSL guest, by default, does the SAME -- it runs its OWN per-environment
# coordinator on a dynamic port, coexisting with the Windows host's coordinator
# (the fixed-shared-port collision behind #2777/#2818 is gone under dynamic
# ports). A WSL guest can opt back into being a client of the Windows coordinator
# with AGENT_DISPATCH_WSL_WINDOWS_CLIENT=1.
#
# Usage:
#   bash scripts/install.sh install        # venv + binstub + service + pivot
#   bash scripts/install.sh update         # idempotent refresh (downgrade-guarded)
#   bash scripts/install.sh status
#   bash scripts/install.sh start | stop
#   bash scripts/install.sh uninstall [--purge]
#
# Options:
#   --no-service       Install/update the client (venv + binstub) but do NOT
#                      install/start the coordinator service (client-only host).
#   --no-supervisor    Install everything EXCEPT the embody supervisor unit
#                      (the coordinator still installs on an eligible host).
#   --purge            On uninstall: also delete config, DB, and env file.
#   --force            On update: bypass the downgrade guard (deliberate
#                      rollback). Env: AGENT_DISPATCH_ALLOW_DOWNGRADE=1.
#   --install-dir DIR  Override the runtime root (default ~/.agent-dispatch).
# =============================================================================

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

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

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_dispatch"

# -- Parse arguments ---------------------------------------------------------
ACTION="${1:-status}"
shift || true

NO_SERVICE=0
NO_SUPERVISOR=0
PURGE=0
INSTALL_DIR=""
FORCE="${AGENT_DISPATCH_ALLOW_DOWNGRADE:-0}"
[[ "$FORCE" == "1" ]] && FORCE=1 || FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) NO_SERVICE=1; shift ;;
        --no-supervisor) NO_SUPERVISOR=1; shift ;;
        --purge) PURGE=1; shift ;;
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-dispatch}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-dispatch"
BOARD_STUB="$LOCAL_BIN/agent-dispatch-board"
SYSTEMD_UNIT="agent-dispatch.service"
SUPERVISOR_UNIT="agent-dispatch-supervisor.service"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_FILE="$INSTALL_DIR/service.env"
SUPERVISOR_ENV_FILE="$INSTALL_DIR/supervisor.env"
SUPERVISOR_PROFILE_DIR="$INSTALL_DIR/supervisors"
SUPERVISOR_LAUNCHER="$INSTALL_DIR/supervise-service.sh"
# PATH baked into the supervisor unit + launcher so embody spawns can find
# `agent-worktrees` and `copilot` (installed under ~/.local/bin and ~/.bun/bin).
# A systemd --user unit's default PATH omits both, so every embody spawn failed
# with "CLI not found on PATH" and dead-lettered the task (ThomasMichon/
# copilot-extensions#89). Placed BEFORE the EnvironmentFile in the unit so an
# operator can still override PATH in supervisor.env.
SUPERVISOR_PATH="$LOCAL_BIN:$HOME/.bun/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# === install-contract:v3 versioned-venv (agent-dispatch: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and make the historical `.venv` path a symlink into it, so the binstub, the
# coordinator + supervisor systemd units, and the deploy-manifest resolve through
# the link unchanged. LINK_DIR is the stable `.venv` path (runtime-facing, never a
# versions/<v> absolute a `gc` could remove); VENV_DIR is the versions/<v> slot
# (build + health-gate). ALWAYS versioned -- the env opt-out
# (COPILOT_EXT_NO_VERSIONED / AGENT_DISPATCH_VERSIONED) and the legacy in-place
# fork are retired; scripts/versioned_runtime.py owns the swap + migration + gc.
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

_versioned_activate() {
    # Swap the stable `.venv` symlink to this version's freshly-built slot, moving
    # a legacy real `.venv` aside on first migration (--replace-nonlink). No-op in
    # legacy mode. On POSIX a rename tolerates the daemons' open files, and
    # _install_service / _install_supervisor_service `systemctl restart` onto the
    # new slot, so no stop is needed.
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

# -- Version helpers + downgrade guard (parity with agent-bridge #1790) ------
_installed_version() {
    # The version currently ACTIVE (via the current-version marker), for the
    # downgrade guard. Marker-only -- the `.venv` link is retired (#765).
    local ver="" py=""
    [[ -f "$INSTALL_DIR/current-version" ]] && ver="$(tr -d ' \t\r\n' < "$INSTALL_DIR/current-version" 2>/dev/null)"
    [[ -n "$ver" ]] && py="$INSTALL_DIR/versions/$ver/bin/python"
    [[ -x "$py" ]] || return 1
    local v
    v="$("$py" -c \
        'from importlib.metadata import version; print(version("agent-dispatch"))' \
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

# True (0) if version $1 is strictly older than $2. Normalizes the PEP 440 dev
# separator (plugin.json `0.1.0-dev19` vs importlib `0.1.0.dev19`) so `sort -V`
# orders the devN build stream correctly.
_version_lt() {
    local a="${1//-/.}" b="${2//-/.}"
    [[ "$a" == "$b" ]] && return 1
    local lower
    lower="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"
    [[ "$lower" == "$a" ]]
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
            _warn "Downgrade $installed -> $source forced (--force / AGENT_DISPATCH_ALLOW_DOWNGRADE)"
            return 0
        fi
        echo ""
        _fail "Refusing to downgrade agent-dispatch: installed $installed > source $source"
        _fail "This checkout is OLDER than the deployed runtime. Use the sanctioned path:"
        _fail "    test-chamber services agent-dispatch update"
        _fail "Or override intentionally (deliberate rollback):"
        _fail "    $0 $ACTION --force"
        echo ""
        exit 1
    fi
}

# -- Python + package -------------------------------------------------------
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
#
# `pip config get <key>` exits NON-ZERO when the key is unset (the common case on
# a host with no governed feed). Under this script's `set -euo pipefail`, an
# unguarded `idx="$(pip config get ... | tr ...)"` therefore aborts the whole
# install right after the Python check -- a benign probe turned fatal (it blocked
# every Linux upgrade on such a host). The `|| true` keeps the probe advisory.
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

# The adopted-project names from agent-worktrees' adoption registry, one per
# line (empty when the registry is absent). Used only to give `agent-worktrees
# get machine` a project so it can resolve the machine registry from any CWD.
_adopted_projects() {
    local reg="$HOME/.agent-worktrees/projects.yaml"
    [[ -f "$reg" ]] || return 0
    awk '
        /^projects:[[:space:]]*$/ { inp = 1; next }
        /^[^[:space:]#]/          { inp = 0 }
        inp && /^  [A-Za-z0-9._-]+:[[:space:]]*$/ {
            sub(/:[[:space:]]*$/, "", $1); print $1
        }
    ' "$reg" 2>/dev/null || true
}

# Deploy the self-provisioning binstub (install-on-first-use). Fast path execs the
# venv's `python -m agent_dispatch`; otherwise it provisions on first use --
# announcing (a human line + a machine-readable ::agent-provisioning:: signal so a
# caller can extend its timeout), lock-serialized, fail-fast.
deploy_binstub() {
    mkdir -p "$LOCAL_BIN" "$INSTALL_DIR/bin"
    local machine="${AGENT_DISPATCH_SUPERVISE_MACHINE:-}"
    if [[ -z "$machine" ]] && command -v agent-worktrees >/dev/null 2>&1; then # marketplace-isolation: allow installer-management
        machine="$(agent-worktrees get machine 2>/dev/null | head -n1 || true)"
        # `get machine` resolves the machine registry THROUGH a project and
        # discovers context from the CWD, so it yields nothing when the installer
        # runs outside an adopted repo/worktree -- the common case. Falling
        # straight through to `hostname` then pins the raw OS name, which on a
        # host reporting a domain suffix (e.g. mDNS `host.local`) never matches
        # the registry key (`host`) the Picker substitutes for `{machine}`; the
        # board then reads this host as a remote peer and tries to SSH to itself.
        # Every adopted project resolves the same machine identity, so retry with
        # an explicit --project before giving up on the authority.
        if [[ -z "$machine" ]]; then
            local p
            for p in $(_adopted_projects); do
                machine="$(agent-worktrees --project "$p" get machine 2>/dev/null | head -n1 || true)" # marketplace-isolation: allow installer-management
                [[ -n "$machine" ]] && break
            done
        fi
    fi
    [[ -n "$machine" ]] || machine="$(hostname 2>/dev/null || true)"
    [[ -n "$machine" ]] &&
        printf '%s' "$(printf '%s' "$machine" | tr '[:upper:]' '[:lower:]')" \
            > "$INSTALL_DIR/machine"
    # Co-deploy the canonical marker-only resolver (uniform-runtime-resolution, #765).
    for r in resolve-runtime.sh resolve-runtime.ps1; do
        [ -f "$SCRIPT_DIR/$r" ] && cp -f "$SCRIPT_DIR/$r" "$INSTALL_DIR/bin/$r"
    done
    cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
# agent-dispatch binstub -- self-provisioning (install-on-first-use).
# Resolves the interpreter SOLELY via the junction-free versioned-runtime marker
# (the deployed resolve-runtime.sh; uniform-runtime-resolution, #765): current-
# version -> last-known-good -> newest complete slot. NEVER a `.venv` link, NEVER
# a PATH python -- when no slot is installed AGENT_RT_PY is empty and we self-
# provision on first use rather than silently binding the system interpreter.
export PYTHONUTF8=1
_name="agent-dispatch"
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
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_dispatch "$@"
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
_resolve
[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_dispatch "$@"
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
bash "$_install" provision >&2
_rc=$?
_resolve
if [ "$_rc" -eq 0 ] && [ -n "$AGENT_RT_PY" ]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
    exec "$AGENT_RT_PY" -m agent_dispatch "$@"
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
    sed 's/-m agent_dispatch /-m agent_dispatch.board_cli /g' "$STUB" > "$BOARD_STUB"
    chmod +x "$BOARD_STUB"
    _ok "Binstub: $STUB (self-provisioning)"
    _ok "Fast board binstub: $BOARD_STUB"
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

    # The [mcp] extra ships the `agent-dispatch mcp` stdio server dependency,
    # which transitively pulls `cryptography` (via `pyjwt[crypto]`). That has no
    # prebuilt wheel on some platforms (e.g. win-arm64) and needs a Rust + native
    # toolchain to build from source. Per the plugin-services vision's
    # `degrade-gracefully` behavior, a build failure of the OPTIONAL MCP server
    # surface must not abort the whole install: fall back to a base install so
    # the coordinator CLI still deploys; only `agent-dispatch mcp` stays dark
    # until the toolchain is present.
    _pip_install() {  # $1 = package spec
        if [[ "$have_uv" -eq 1 ]]; then
            uv pip install --python "$VENV_PYTHON" "$1"
        else
            "$VENV_PYTHON" -m pip install "$1"
        fi
    }
    if _pip_install "${PLUGIN_DIR}[mcp]" >/dev/null 2>&1; then
        _ok 'Package installed: agent-dispatch [mcp]'
    else
        _warn 'Could not install the [mcp] extra (its native deps may not build on this platform) -- falling back to a base install without the MCP server surface'
        if ! pkg_out="$(_pip_install "$PLUGIN_DIR" 2>&1)"; then
            _fail 'Failed to install agent-dispatch package into venv'
            printf '%s\n' "$pkg_out" >&2
            exit 1
        fi
        _ok 'Package installed: agent-dispatch (base -- `agent-dispatch mcp` server unavailable on this platform)'
    fi

    # -- stamp build provenance (version from pyproject -- the single source of
    # truth -- plus git commit/branch) into the deployed package, so the runtime
    # reports its version without importlib.metadata. Best-effort; mirrors
    # agent-worktrees' stamp_build_info. --
    if pkg_dir="$("$VENV_PYTHON" -c 'import agent_dispatch, os; print(os.path.dirname(agent_dispatch.__file__))' 2>/dev/null)" && [[ -n "$pkg_dir" ]]; then
        repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
        "$VENV_PYTHON" "$SCRIPT_DIR/stamp_build_info.py" \
            --package-dir "$pkg_dir" --plugin-dir "$PLUGIN_DIR" --git-dir "$repo_root" >/dev/null 2>&1 || true
    fi

    deploy_binstub

    # Versioned layout (#581): health-gate the freshly-built slot in isolation,
    # then swap the stable `.venv` symlink onto it. Everything below (manifest,
    # systemd units, binstub) resolves through `.venv` (the link). No-op in legacy
    # mode. Remember the previous active version as the gc keep target.
    local prev_version=""
    if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
        prev_version="$(_versioned_current)"
        if ! "$VENV_PYTHON" -c 'import agent_dispatch' 2>/dev/null; then
            _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
            exit 1
        fi
        _versioned_mark_complete
        _versioned_activate || exit 1
    fi

    _write_manifest

    if "$LINK_PYTHON" -c 'import agent_dispatch' 2>/dev/null; then
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

    _register_pivot
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
  "service": "agent-dispatch",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-dispatch",
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

# Register the worktree-picker "Tasks" pivot (best-effort; never fatal).
_register_pivot() {
    local src="$PLUGIN_DIR/pivots/agent-dispatch.json"
    local dir="$HOME/.agent-worktrees/pivots"
    if [[ -f "$src" ]]; then
        if mkdir -p "$dir" 2>/dev/null && cp -f "$src" "$dir/agent-dispatch.json" 2>/dev/null; then
            _ok "Picker pivot registered: $dir/agent-dispatch.json"
        else
            _skip "Could not register picker pivot (agent-worktrees runtime root not writable)"
        fi
    else
        _skip "Picker pivot manifest not found at $src"
    fi
}

# -- Coordinator service (systemd user unit; default-on on deploy machines) --

# True (0) on a WSL guest -- a Linux env hosted by a Windows box. By default a
# WSL guest now installs its OWN coordinator (per-environment ownership) on an
# OS-assigned dynamic port, coexisting with the Windows host's coordinator; the
# fixed-shared-port collision that made #2777/#2818 gate it off is gone under
# dynamic ports. A box that deliberately wants to stay a *client* of the Windows
# coordinator opts in with AGENT_DISPATCH_WSL_WINDOWS_CLIENT=1. A standalone Linux
# host (e.g. Mantis-Counter) is NOT WSL and installs the full coordinator. Detect
# via WSL_DISTRO_NAME or `microsoft` in the kernel osrelease / /proc/version
# (case-insensitive) -- mirrors netinfo.is_wsl().
_is_wsl() {
    [[ -n "${WSL_DISTRO_NAME:-}" ]] && return 0
    local f
    for f in /proc/sys/kernel/osrelease /proc/version; do
        [[ -r "$f" ]] || continue
        if grep -qi microsoft "$f" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

# True (0) when a WSL guest is opted in to remain a *client* of the Windows
# host's coordinator (legacy behavior) instead of running its own. Mirrors
# config.wsl_windows_client().
_wsl_windows_client() {
    case "${AGENT_DISPATCH_WSL_WINDOWS_CLIENT:-0}" in
        1 | true | yes | on | TRUE | YES | ON) return 0 ;;
        *) return 1 ;;
    esac
}

_install_service() {
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Coordinator service skipped (--no-service): this host is a client only"
        return 0
    fi
    if _is_wsl && _wsl_windows_client; then
        # Opt-in only: this WSL guest stays a client of the Windows-owned
        # coordinator. Never install a systemd unit here; remove a stale one so
        # the two don't split-brain.
        if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
            systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
            systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
            rm -f "$UNIT_DIR/$SYSTEMD_UNIT"
            systemctl --user daemon-reload 2>/dev/null || true
            _ok "WSL guest (Windows-client opt-in): removed stale coordinator unit -- the Windows host owns the coordinator"
        else
            _skip "WSL guest (Windows-client opt-in): client-only -- the Windows host owns the coordinator"
        fi
        return 0
    fi
    # A WSL guest without the opt-in installs its OWN coordinator, exactly like a
    # standalone Linux host (dynamic port; coexists with the Windows one).
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- run 'agent-dispatch serve' manually if this host hosts a coordinator"
        return 0
    fi
    mkdir -p "$UNIT_DIR"
    if [[ ! -f "$ENV_FILE" ]]; then
        cat > "$ENV_FILE" << 'ENVEOF'
# agent-dispatch coordinator service environment (edit + `systemctl --user restart agent-dispatch`)
AGENT_DISPATCH_HOST=127.0.0.1
# AGENT_DISPATCH_PORT=9847  # unset = OS-assigned dynamic port (Stage C), advertised via the rendezvous file; uncomment to pin
# AGENT_DISPATCH_DB=%h/.agent-dispatch/tasks.db   # default; uncomment to override
# AGENT_DISPATCH_TOKEN=                            # set to require bearer auth
# AGENT_DISPATCH_CONTROL_TOKEN=                    # required to manage producer scopes
ENVEOF
        _ok "Service env: $ENV_FILE (defaults; edit to expose on the network / add a token)"
    else
        # Migrate the stale old-default port pin (durable-service-transport Stage C):
        # early installs wrote AGENT_DISPATCH_PORT=9847, which defeats the dynamic
        # bind. The coordinator now binds an OS-assigned port and advertises it via
        # the rendezvous file, so drop the old-default pin (discovery-capable clients
        # follow the dynamic port). Leave any operator-chosen custom port untouched.
        if grep -qE '^[[:space:]]*AGENT_DISPATCH_PORT[[:space:]]*=[[:space:]]*9847[[:space:]]*$' "$ENV_FILE"; then
            sed -i -E 's|^[[:space:]]*AGENT_DISPATCH_PORT[[:space:]]*=[[:space:]]*9847[[:space:]]*$|# AGENT_DISPATCH_PORT=9847  # migrated (Stage C): unset = OS-assigned dynamic port advertised via the rendezvous file; uncomment to pin|' "$ENV_FILE"
            _ok "Service env: migrated stale AGENT_DISPATCH_PORT=9847 pin (Stage C) -> OS-assigned dynamic port"
        else
            _skip "Service env already exists: $ENV_FILE"
        fi
    fi
    cat > "$UNIT_DIR/$SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-dispatch -- portable agent task-queue coordinator
After=network.target

[Service]
Type=simple
EnvironmentFile=-$ENV_FILE
Environment=PYTHONUTF8=1
ExecStart=$VENV_PYTHON -m agent_dispatch serve
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    # --no-restart: refresh the unit (for next boot) but do NOT restart now -- used
    # after a graceful cutover already stood the new coordinator up beside the old
    # (Thread B). A `systemctl restart` here would spawn a SECOND coordinator.
    if [[ "${1:-}" == "--no-restart" ]]; then
        _ok "Coordinator unit refreshed ($SYSTEMD_UNIT); not restarted -- the graceful cutover already brought the new coordinator up"
        return 0
    fi
    systemctl --user restart "$SYSTEMD_UNIT" 2>/dev/null || true
    if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
        _ok "Coordinator service installed + started ($SYSTEMD_UNIT)"
    else
        _warn "Coordinator service installed but not active -- check: systemctl --user status agent-dispatch"
    fi
}

# Thread B graceful cutover (parity with install.ps1 Invoke-CoordinatorCutover):
# if a LIVE Thread-B coordinator (one that published the zdd routing table + has a
# /drain seam) is serving, stand the new slot up beside it and flip routing via the
# OS-agnostic `_cutover` seam -- so NO in-flight claim is dropped -- instead of a
# systemctl restart. Returns 0 when the cutover brought the new coordinator up (so
# the caller refreshes the unit with --no-restart); 1 to fall back to the SIGTERM-
# graceful `systemctl restart` (which uvicorn drains, so the invariant still holds).
# A pre-Thread-B coordinator has no routing entry -> returns 1 (fall back). The
# supervisor is a SEPARATE unit -- deliberately left running so it outlives the
# coordinator swap and re-adopts via the durable queue DB + routing table.
_coordinator_cutover() {
    [[ "$NO_SERVICE" -eq 1 ]] && return 1
    command -v systemctl >/dev/null 2>&1 || return 1
    local py="$VENV_PYTHON"
    [[ -x "$py" ]] || py="$LINK_PYTHON"
    [[ -x "$py" ]] || return 1
    # Only cut over a coordinator that has published the routing table (Thread-B,
    # /drain-capable). read_active_endpoint verifies a live listener too.
    "$py" -c 'import sys; from zdd.routing import read_active_endpoint; from agent_dispatch.config import routing_dir; sys.exit(0 if read_active_endpoint(routing_dir()) else 1)' 2>/dev/null || return 1
    _step 'Graceful cutover: standing the new coordinator up beside the old, then flipping routing...'
    if "$py" -m agent_dispatch _cutover >/dev/null 2>&1; then
        _ok 'Coordinator cut over to the new build (no in-flight claim dropped; supervisor/workers re-adopt via the queue DB + routing table)'
        return 0
    fi
    _warn 'Graceful cutover did not complete -- falling back to a SIGTERM-graceful systemctl restart'
    return 1
}

# -- Embody supervisor service (systemd user unit; label-gated) --------------
#
# The supervisor turns queued, LABELED tasks into host embody autopilots via
# `agent-dispatch supervise --all-repos`. Running with `--all-repos` avoids the
# lane-scoping gotcha (a short `--repo owner/name` form silently filters every
# task out), but that makes the label opt-in the ONLY thing standing between the
# supervisor and embodying *every* queued task -- so each unit is enabled only
# when its own env file declares at least one label. See #2869.

# True (0) when an env file declares a non-empty AGENT_DISPATCH_SUPERVISE_LABELS.
_supervisor_labels_configured() {
    local env_file="${1:-$SUPERVISOR_ENV_FILE}"
    [[ -f "$env_file" ]] || return 1
    local v
    v="$(sed -n 's/^[[:space:]]*AGENT_DISPATCH_SUPERVISE_LABELS[[:space:]]*=//p' \
        "$env_file" | tail -n1)"
    v="${v//\"/}"; v="${v//\'/}"
    v="$(printf '%s' "$v" | tr -d '[:space:],')"
    [[ -n "$v" ]]
}

# The supervisor MODE: 'serve' runs the single master registrar daemon
# (`supervise serve --legacy-env`, which reconciles declared pools + legacy env
# profiles, each in its own subprocess); anything else (blank/'legacy') runs the
# classic direct `supervise --label...` embody loop. Echoes the resolved mode.
_supervisor_mode() {
    local env_file="${1:-$SUPERVISOR_ENV_FILE}"
    local v=""
    if [[ -f "$env_file" ]]; then
        v="$(sed -n 's/^[[:space:]]*AGENT_DISPATCH_SUPERVISE_MODE[[:space:]]*=//p' \
            "$env_file" | tail -n1)"
        v="${v//\"/}"; v="${v//\'/}"
        v="$(printf '%s' "$v" | tr -d '[:space:]')"
    fi
    [[ "$v" == "serve" ]] && printf 'serve' || printf 'legacy'
}

_supervisor_profile_name_valid() {
    [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]
}

_supervisor_unit_for_profile() {
    local name="$1"
    printf 'agent-dispatch-supervisor-%s.service' "$name"
}

_supervisor_profile_env_files() {
    [[ -d "$SUPERVISOR_PROFILE_DIR" ]] || return 0
    local env_file name
    for env_file in "$SUPERVISOR_PROFILE_DIR"/*.env; do
        [[ -e "$env_file" ]] || continue
        name="${env_file##*/}"
        name="${name%.env}"
        if _supervisor_profile_name_valid "$name"; then
            printf '%s\n' "$env_file"
        else
            _warn "Skipping unsafe supervisor profile name: $name"
        fi
    done
}

_write_supervisor_default_env() {
    if [[ ! -f "$SUPERVISOR_ENV_FILE" ]]; then
        cat > "$SUPERVISOR_ENV_FILE" << 'ENVEOF'
# agent-dispatch embody supervisor environment
# (edit + `systemctl --user restart agent-dispatch-supervisor`)
#
# SAFETY: the supervisor turns queued tasks into AUTONOMOUS embody sessions. It
# runs with --all-repos, so it is GATED by an explicit label opt-in: only queued
# tasks carrying one of these labels are embodied. With NO labels set, the
# service is left DISABLED -- a label-less supervisor would embody EVERY queued
# task (handoffs, interactive worktree-pinned tasks, ...), which is unsafe.
#
# Opt-in labels, comma- or space-separated (REQUIRED to enable the service):
AGENT_DISPATCH_SUPERVISE_LABELS=
# Poll interval, seconds (default 30):
AGENT_DISPATCH_SUPERVISE_INTERVAL=30
# Max concurrent in-flight embodies (default 1 = max-one-active):
AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT=1
# Max failed spawn attempts before a task is dead-lettered (default 3; 0=disable):
AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS=3
# Per-label overrides of MAX_ATTEMPTS (space- or comma-separated LABEL=N pairs),
# e.g. "code-review=3 nightly-scan=1" so raising one
# label's bound never revives another label's stale tasks (N=0 = retry forever):
AGENT_DISPATCH_SUPERVISE_LABEL_MAX_ATTEMPTS=
# Default embody backend for this supervisor: 'headless' (default) embodies each
# claimed task as a headless agent-bridge ACP session -- the right body for a
# self-contained, autonomous dispatched task (no mux, no CLI-start-prompt); 'cli'
# makes the lane a CLI-backed autopilot worktree session (attachable). Leave blank
# for the headless default.
AGENT_DISPATCH_SUPERVISE_EMBODY_BACKEND=
# Per-label overrides of the default backend (comma- or space-separated; each must
# also appear in SUPERVISE_LABELS):
#   CLI_LABELS      -- force these labels to a CLI autopilot (the opt-out on a
#                      headless-by-default lane).
#   HEADLESS_LABELS -- force these labels headless (the opt-in when EMBODY_BACKEND=cli).
AGENT_DISPATCH_SUPERVISE_CLI_LABELS=
AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS=
# agent-bridge agent name used for headless embody bodies (default: task-worker):
AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT=
# Extra raw flags appended to the invocation (advanced; e.g. fleet mode:
#   --pool host-a,host-b --origin mantis-counter):
AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=
# Supervisor MODE (migration opt-in; default: the classic direct embody loop):
#   serve -- run the single MASTER registrar daemon (`supervise serve
#            --legacy-env`) instead of the direct loop. The daemon reconciles
#            declared pools (registrar pointers) PLUS this host's legacy profiles
#            (this supervisor.env + supervisors/*.env, read via --legacy-env),
#            each in its own subprocess, and hot-reconciles on change. It is
#            self-gating (only labeled units run), so it needs no label opt-in.
#            In this mode the installer stops creating per-profile units (the
#            daemon runs them) and retires any it previously created.
# Leave blank for the classic per-host direct supervisor.
AGENT_DISPATCH_SUPERVISE_MODE=
# MODE=serve only: explicit machine scope for this host's daemon. Recommended in a
# service context -- CWD-based identity resolution can fail there, and without a
# machine the daemon SKIPS every machine-pinned declaration (aperture-labs #5001).
# Leave blank to fall back to the host node name at runtime; set to this host's
# alias (e.g. mantis-counter) to pin it explicitly.
AGENT_DISPATCH_SUPERVISE_MACHINE=
ENVEOF
        _ok "Supervisor env: $SUPERVISOR_ENV_FILE (no labels -> service stays inert; add a label to enable)"
    else
        _skip "Supervisor env already exists: $SUPERVISOR_ENV_FILE"
    fi
}

_write_supervisor_launcher() {
    # Launcher reads only process env, so the unit's EnvironmentFile decides
    # which primary/profile config is active. A defense-in-depth guard refuses
    # to run label-less (the install below won't enable it, but a hand-enable
    # must not embody everything).
    cat > "$SUPERVISOR_LAUNCHER" << LAUNCHEOF
#!/usr/bin/env bash
# agent-dispatch embody supervisor launcher -- GENERATED by install.sh (#2869).
# Do not edit; edit supervisor.env or supervisors/<name>.env instead.
set -euo pipefail
export PYTHONUTF8=1
# Guarantee the embody toolchain (agent-worktrees, copilot) is reachable even
# if this launcher is run outside the unit (hand-enable / different invocation):
# ~/.local/bin and ~/.bun/bin are prepended (copilot-extensions#89).
export PATH="\$HOME/.local/bin:\$HOME/.bun/bin:\$PATH"

labels="\${AGENT_DISPATCH_SUPERVISE_LABELS:-}"
interval="\${AGENT_DISPATCH_SUPERVISE_INTERVAL:-30}"
max_concurrent="\${AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT:-1}"
max_attempts="\${AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS:-3}"
label_max_attempts="\${AGENT_DISPATCH_SUPERVISE_LABEL_MAX_ATTEMPTS:-}"
embody_backend="\${AGENT_DISPATCH_SUPERVISE_EMBODY_BACKEND:-}"
cli_labels="\${AGENT_DISPATCH_SUPERVISE_CLI_LABELS:-}"
headless_labels="\${AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS:-}"
headless_agent="\${AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT:-}"
extra="\${AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS:-}"

# MODE=serve (opt-in): run the single MASTER registrar daemon instead of the
# classic direct embody loop. The daemon reconciles declared pools (discovered
# pointers) + this host's legacy env profiles (--legacy-env: supervisor.env +
# supervisors/*.env), each in its own subprocess. It is self-gating -- only
# labeled declarations/profiles run -- so, unlike the direct loop below, it needs
# no label opt-in to be safe.
mode="\${AGENT_DISPATCH_SUPERVISE_MODE:-}"
if [[ "\$mode" == "serve" ]]; then
    serve_args=(supervise serve --legacy-env --interval "\$interval")
    # Explicit machine scope (recommended for a service context, where CWD-based
    # identity resolution can fail and leave the daemon unable to scope
    # machine-pinned declarations -- aperture-labs #5001). Falls back to the host
    # node name at runtime when unset.
    smachine="\${AGENT_DISPATCH_SUPERVISE_MACHINE:-}"
    [[ -n "\$smachine" ]] && serve_args+=(--machine "\$smachine")
    # shellcheck disable=SC2206
    [[ -n "\$extra" ]] && serve_args+=(\$extra)
    exec "$VENV_PYTHON" -m agent_dispatch "\${serve_args[@]}"
fi

args=(supervise --all-repos --interval "\$interval" \\
      --max-concurrent "\$max_concurrent" --max-attempts "\$max_attempts")

labels="\${labels//,/ }"
have_label=0
for l in \$labels; do
    [[ -n "\$l" ]] || continue
    args+=(--label "\$l")
    have_label=1
done
if [[ "\$have_label" -eq 0 ]]; then
    echo "agent-dispatch-supervisor: refusing to run with no opt-in label." >&2
    echo "  A label-less supervisor would embody EVERY queued task." >&2
    echo "  Set AGENT_DISPATCH_SUPERVISE_LABELS in \${AGENT_DISPATCH_SUPERVISOR_ENV_FILE:-the unit EnvironmentFile}." >&2
    exit 78  # EX_CONFIG
fi

# Per-label max-attempts overrides (LABEL=N pairs, comma- or space-separated).
label_max_attempts="\${label_max_attempts//,/ }"
for lm in \$label_max_attempts; do
    [[ -n "\$lm" ]] || continue
    args+=(--label-max-attempts "\$lm")
done

# Default embody backend: headless (the default) unless EMBODY_BACKEND=cli.
[[ -n "\$embody_backend" ]] && args+=(--embody-backend "\$embody_backend")

# Per-label backend overrides. --cli-label opts a label OUT to a CLI autopilot on a
# headless-by-default lane; --headless-label forces a label headless (for a
# --embody-backend cli lane). Each must also be watched (in SUPERVISE_LABELS).
cli_labels="\${cli_labels//,/ }"
for cl in \$cli_labels; do
    [[ -n "\$cl" ]] || continue
    args+=(--cli-label "\$cl")
done
headless_labels="\${headless_labels//,/ }"
for hl in \$headless_labels; do
    [[ -n "\$hl" ]] || continue
    args+=(--headless-label "\$hl")
done
[[ -n "\$headless_agent" ]] && args+=(--headless-agent "\$headless_agent")

# shellcheck disable=SC2206
[[ -n "\$extra" ]] && args+=(\$extra)

exec "$VENV_PYTHON" -m agent_dispatch "\${args[@]}"
LAUNCHEOF
    chmod +x "$SUPERVISOR_LAUNCHER"
}

_remove_supervisor_unit() {
    local unit="${1:-$SUPERVISOR_UNIT}"
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$unit" ]]; then
        systemctl --user stop "$unit" 2>/dev/null || true
        systemctl --user disable "$unit" 2>/dev/null || true
        rm -f "$UNIT_DIR/$unit"
        systemctl --user daemon-reload 2>/dev/null || true
    elif [[ -f "$UNIT_DIR/$unit" ]]; then
        rm -f "$UNIT_DIR/$unit"
    fi
}

_remove_all_supervisor_units() {
    _remove_supervisor_unit "$SUPERVISOR_UNIT"
    local unit_path unit
    if [[ -d "$UNIT_DIR" ]]; then
        for unit_path in "$UNIT_DIR"/agent-dispatch-supervisor-*.service; do
            [[ -e "$unit_path" ]] || continue
            unit="${unit_path##*/}"
            _remove_supervisor_unit "$unit"
        done
    fi
    rm -f "$SUPERVISOR_LAUNCHER"
}

_install_supervisor_unit() {
    local unit="$1"
    local env_file="$2"
    local label="$3"

    cat > "$UNIT_DIR/$unit" << EOF
[Unit]
Description=agent-dispatch -- embody spawn supervisor (labeled queued tasks -> host embody autopilots)
After=network.target $SYSTEMD_UNIT
Wants=$SYSTEMD_UNIT

[Service]
Type=simple
Environment=PATH=$SUPERVISOR_PATH
Environment=AGENT_DISPATCH_SUPERVISOR_ENV_FILE=$env_file
EnvironmentFile=-$env_file
Environment=PYTHONUTF8=1
ExecStart=$SUPERVISOR_LAUNCHER
Restart=on-failure
RestartSec=10
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true

    local mode; mode="$(_supervisor_mode "$env_file")"
    if [[ "$mode" == "serve" ]] || _supervisor_labels_configured "$env_file"; then
        systemctl --user enable "$unit" 2>/dev/null || true
        systemctl --user restart "$unit" 2>/dev/null || true
        if systemctl --user is-active "$unit" &>/dev/null; then
            if [[ "$mode" == "serve" ]]; then
                _ok "$label installed + started ($unit -- MODE=serve: master registrar daemon)"
            else
                _ok "$label installed + started ($unit)"
            fi
        else
            _warn "$label installed but not active -- check: systemctl --user status $unit"
        fi
    else
        systemctl --user stop "$unit" 2>/dev/null || true
        systemctl --user disable "$unit" 2>/dev/null || true
        _ok "$label installed (INERT: no opt-in label). To enable: set"
        _step "AGENT_DISPATCH_SUPERVISE_LABELS in $env_file, then re-run update"
        _step "(or: systemctl --user enable --now $unit)"
    fi
}

_install_supervisor_profiles() {
    local env_file name unit
    mkdir -p "$SUPERVISOR_PROFILE_DIR"
    while IFS= read -r env_file; do
        [[ -n "$env_file" ]] || continue
        name="${env_file##*/}"
        name="${name%.env}"
        unit="$(_supervisor_unit_for_profile "$name")"
        _install_supervisor_unit "$unit" "$env_file" "Embody supervisor profile '$name'"
    done < <(_supervisor_profile_env_files)
}

_reconcile_supervisor_profiles() {
    local unit_path unit name env_file
    [[ -d "$UNIT_DIR" ]] || return 0
    for unit_path in "$UNIT_DIR"/agent-dispatch-supervisor-*.service; do
        [[ -e "$unit_path" ]] || continue
        unit="${unit_path##*/}"
        name="${unit#agent-dispatch-supervisor-}"
        name="${name%.service}"
        env_file="$SUPERVISOR_PROFILE_DIR/$name.env"
        if ! _supervisor_profile_name_valid "$name" || [[ ! -f "$env_file" ]]; then
            _remove_supervisor_unit "$unit"
            _ok "Removed orphan supervisor profile unit: $unit"
        fi
    done
}

# MODE=serve: the master daemon runs each legacy profile itself (--legacy-env), so
# every per-profile unit (agent-dispatch-supervisor-<name>.service) is redundant.
# Retire them all; their .env files stay in place for the daemon to read. The glob
# requires a char after the dash, so the primary unit (agent-dispatch-supervisor
# .service) is never matched.
_retire_supervisor_profile_units() {
    local unit_path unit
    [[ -d "$UNIT_DIR" ]] || return 0
    for unit_path in "$UNIT_DIR"/agent-dispatch-supervisor-*.service; do
        [[ -e "$unit_path" ]] || continue
        unit="${unit_path##*/}"
        _remove_supervisor_unit "$unit"
        _ok "Retired per-profile unit (MODE=serve; the daemon runs it): $unit"
    done
}

_start_supervisor_unit_if_enabled() {
    local unit="$1"
    local label="$2"
    if [[ -f "$UNIT_DIR/$unit" ]] && systemctl --user is-enabled "$unit" &>/dev/null; then
        systemctl --user start "$unit" 2>/dev/null || true
        systemctl --user is-active "$unit" &>/dev/null \
            && _ok "$label started" \
            || _warn "$label did not start -- check: systemctl --user status $unit"
    fi
}

_stop_supervisor_unit_if_active() {
    local unit="$1"
    local label="$2"
    if [[ -f "$UNIT_DIR/$unit" ]] && systemctl --user is-active "$unit" &>/dev/null; then
        systemctl --user stop "$unit" 2>/dev/null || true
        _ok "$label stopped"
    fi
}

_status_supervisor_unit() {
    local unit="$1"
    local env_file="$2"
    local label="$3"
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$unit" ]]; then
        local sstate senabled
        sstate=$(systemctl --user is-active "$unit" 2>/dev/null || echo inactive)
        senabled=$(systemctl --user is-enabled "$unit" 2>/dev/null || echo disabled)
        if _supervisor_labels_configured "$env_file"; then
            _ok "$label: $unit $sstate ($senabled)"
        else
            _ok "$label: $unit $sstate ($senabled -- INERT: no opt-in label set)"
        fi
    else
        _skip "No $label unit: $unit"
    fi
}

_for_each_present_supervisor_unit() {
    local callback="$1"
    local env_file name unit label
    "$callback" "$SUPERVISOR_UNIT" "$SUPERVISOR_ENV_FILE" "Embody supervisor"
    while IFS= read -r env_file; do
        [[ -n "$env_file" ]] || continue
        name="${env_file##*/}"
        name="${name%.env}"
        unit="$(_supervisor_unit_for_profile "$name")"
        label="Embody supervisor profile '$name'"
        "$callback" "$unit" "$env_file" "$label"
    done < <(_supervisor_profile_env_files)
}

_start_supervisor_callback() {
    _start_supervisor_unit_if_enabled "$1" "$3"
}

_stop_supervisor_callback() {
    _stop_supervisor_unit_if_active "$1" "$3"
}

_status_supervisor_callback() {
    _status_supervisor_unit "$1" "$2" "$3"
}

_install_supervisor_service() {
    if [[ "$NO_SUPERVISOR" -eq 1 ]]; then
        _remove_all_supervisor_units
        _skip "Embody supervisor skipped (--no-supervisor)"
        return 0
    fi
    # The supervisor spawns embody autopilots on THIS host. Install it wherever we
    # install the full coordinator -- a standalone Linux host AND (by default) a
    # WSL guest, which now owns its own per-environment coordinator. Skip only a
    # true client-only host (--no-service) or a WSL guest opted into Windows-client
    # mode. Remove a stale unit if this host became client-only.
    if [[ "$NO_SERVICE" -eq 1 ]] || { _is_wsl && _wsl_windows_client; }; then
        _remove_all_supervisor_units
        _skip "Embody supervisor skipped (client-only / Windows-client WSL host)"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- run 'agent-dispatch supervise --all-repos --label <L>' manually"
        return 0
    fi
    mkdir -p "$UNIT_DIR" "$SUPERVISOR_PROFILE_DIR"

    _write_supervisor_default_env
    _write_supervisor_launcher
    _install_supervisor_unit "$SUPERVISOR_UNIT" "$SUPERVISOR_ENV_FILE" "Embody supervisor"
    if [[ "$(_supervisor_mode "$SUPERVISOR_ENV_FILE")" == "serve" ]]; then
        # MODE=serve: the master daemon runs the legacy profiles itself (via
        # --legacy-env), so the per-profile units are redundant and would
        # double-run -- retire them (their .env files stay; the daemon reads them).
        _retire_supervisor_profile_units
    else
        _install_supervisor_profiles
        _reconcile_supervisor_profiles
    fi
}

# -- Actions ----------------------------------------------------------------
# Cheap 'stamp': splat the binstub + payload marker, defer the venv build to first
# use (fits a sessionStart hook's grace window). No venv, no uv.
do_stamp() {
    echo ''; echo '=== agent-dispatch stamp (defer runtime to first use) ==='; echo ''
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
    deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
}

do_install() {
    echo ''; echo '=== agent-dispatch install ==='; echo ''
    _ensure_runtime
    _install_service
    _install_supervisor_service
    echo ''; echo '=== agent-dispatch install complete ==='
    echo '  Coordinator: systemctl --user status agent-dispatch'
    echo '  Supervisor:  systemctl --user status agent-dispatch-supervisor'
}

do_update() {
    echo ''; echo '=== agent-dispatch update ==='; echo ''
    _downgrade_guard
    _ensure_runtime
    # Thread B (parity with install.ps1): a version update must never kill an
    # in-flight claim. _ensure_runtime built + activated the new slot WITHOUT
    # stopping the daemon; now, if a live Thread-B coordinator is serving, cut it
    # over gracefully (new slot beside old -> flip routing -> drain between claims
    # -> retire) and refresh the unit WITHOUT restarting. Otherwise fall back to
    # _install_service's SIGTERM-graceful `systemctl restart` (uvicorn drains
    # in-flight requests, so the invariant holds either way). The supervisor is a
    # SEPARATE unit -- never stopped here; it outlives the swap + re-adopts.
    if _coordinator_cutover; then
        _install_service --no-restart
    else
        _install_service
    fi
    _install_supervisor_service
    echo ''; echo '=== agent-dispatch update complete ==='
}

do_start() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    if [[ ! -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        _fail "No service unit installed -- run: $0 install"
        exit 1
    fi
    systemctl --user start "$SYSTEMD_UNIT"
    systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null \
        && _ok "Coordinator started" || { _fail "Failed to start coordinator"; exit 1; }
    # Start every supervisor that is enabled (label-gated). Inert/disabled
    # primary/profile supervisors are left alone.
    _for_each_present_supervisor_unit _start_supervisor_callback
}

do_stop() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    _for_each_present_supervisor_unit _stop_supervisor_callback
    if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok "Coordinator stopped"
    else
        _skip "Coordinator not running"
    fi
}

do_status() {
    echo ''; echo '=== agent-dispatch status ==='
    if [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
        local kind ver
        kind=$(grep -o '"kind": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        ver=$(grep -o '"version": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ok "Deployed: $ver (source: $kind)"
    else
        _skip "No deploy manifest -- not installed?"
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        local state
        state=$(systemctl --user is-active "$SYSTEMD_UNIT" 2>/dev/null || echo inactive)
        _ok "Coordinator service: $state ($(systemctl --user is-enabled "$SYSTEMD_UNIT" 2>/dev/null || echo disabled))"
    else
        _skip "No coordinator service unit (client-only host, or systemd unavailable)"
    fi
    if command -v systemctl >/dev/null 2>&1; then
        _for_each_present_supervisor_unit _status_supervisor_callback
    else
        _skip "No embody supervisor units (client-only host, --no-supervisor, or systemd unavailable)"
    fi
}

do_uninstall() {
    echo ''; echo '=== agent-dispatch uninstall ==='; echo ''
    if command -v systemctl >/dev/null 2>&1; then
        _remove_all_supervisor_units
        _ok "Embody supervisor services removed"
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$UNIT_DIR/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
        _ok "Coordinator service removed"
    fi
    rm -f "$STUB" "$BOARD_STUB"; _ok "Binstubs removed"
    rm -f "$HOME/.agent-worktrees/pivots/agent-dispatch.json" 2>/dev/null || true
    if [[ "$PURGE" -eq 1 ]]; then
        rm -rf "$INSTALL_DIR"; _ok "Runtime purged: $INSTALL_DIR (config + DB deleted)"
    else
        # Versioned: the `.venv` link + the versions/ tree; else the real venv dir.
        if [[ "$VERSIONED_RUNTIME" == 1 ]]; then
            [[ -L "$LINK_DIR" ]] && rm -f "$LINK_DIR"
            [[ -d "$LINK_DIR" && ! -L "$LINK_DIR" ]] && rm -rf "$LINK_DIR"
            [[ -d "$INSTALL_DIR/versions" ]] && rm -rf "$INSTALL_DIR/versions"
        else
            rm -rf "$VENV_DIR"
        fi
        _ok "Venv removed (config + DB kept; --purge to delete)"
    fi
}

case "$ACTION" in
    install)   do_install ;;
    stamp)     do_stamp ;;
    provision) do_install ;;
    update)    do_update ;;
    start)     do_start ;;
    stop)      do_stop ;;
    status)    do_status ;;
    uninstall) do_uninstall ;;
    *) _fail "Unknown action: $ACTION (use: install|stamp|provision|update|status|start|stop|uninstall)"; exit 2 ;;
esac
exit 0
