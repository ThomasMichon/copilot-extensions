#!/usr/bin/env bash
# Install/update the agent-ssh runtime (Linux / WSL / macOS).
# Usage: ./install.sh [install|update|status|uninstall] [--force] [--install-dir DIR]

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve a vendored library path (libs/<name>) across multiple layouts.
# Prints the resolved directory path to stdout (nothing else).
# Returns 0 if found, 1 if not. Mirrors plugins/agent-bridge/scripts/
# install.sh's identically-named helper -- needed here because
# ssh-manager, agent-procutil, and venue-copilot are now consumed as
# `uv`-editable canonical references (vendor-pointer-generalization
# effort, Phase 1): a dev checkout has no `$PLUGIN_DIR/libs/<lib>` copy
# at all for any of them.
_resolve_vendored_lib() {
    local lib_name="$1"
    local candidate

    # 1. Vendored inside agent-ssh (marketplace install layout)
    candidate="$PLUGIN_DIR/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 2. Relative path (git checkout layout: plugins/agent-ssh/../../libs/<name>)
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
_resolve_ssh_manager() { _resolve_vendored_lib ssh-manager; }
_resolve_agent_procutil() { _resolve_vendored_lib agent-procutil; }
_resolve_venue_copilot() { _resolve_vendored_lib venue-copilot; }
_resolve_zdd() { _resolve_vendored_lib zdd; }
_resolve_remote_login_shell() { _resolve_vendored_lib remote-login-shell; }

_install_agent_ssh_package() {
    local agent_procutil_dir ssh_manager_dir venue_copilot_dir zdd_dir remote_login_shell_dir
    agent_procutil_dir="$(_resolve_agent_procutil)" || {
        _fail 'Cannot locate agent-procutil library'
        return 1
    }
    ssh_manager_dir="$(_resolve_ssh_manager)" || {
        _fail 'Cannot locate ssh-manager library'
        return 1
    }
    venue_copilot_dir="$(_resolve_venue_copilot)" || {
        _fail 'Cannot locate venue-copilot library'
        return 1
    }
    zdd_dir="$(_resolve_zdd)" || {
        _fail 'Cannot locate zdd library'
        return 1
    }
    remote_login_shell_dir="$(_resolve_remote_login_shell)" || {
        _fail 'Cannot locate remote-login-shell library'
        return 1
    }
    if [[ -n "${UV_CMD:-}" ]]; then
        local install_target lib_out pkg_out uv_ok=1
        for install_target in \
            "pyyaml>=6.0.3" \
            "$agent_procutil_dir" \
            "$PLUGIN_DIR/libs/dropin-registry" \
            "$ssh_manager_dir" \
            "$venue_copilot_dir" \
            "$zdd_dir" \
            "$remote_login_shell_dir"
        do
            if [[ "$install_target" == "$venue_copilot_dir" ]]; then
                lib_out="$(invoke_uv_pip_install_resilient "$UV_CMD" --python "$VENV_PYTHON" --reinstall-package agent-venue-copilot "$install_target" --quiet)" || {
                    [[ -n "$lib_out" ]] && printf '%s\n' "$lib_out" >&2
                    _step 'uv package install failed -- falling back to python -m pip'
                    uv_ok=0
                    break
                }
                continue
            fi
            if ! lib_out=$(invoke_uv_pip_install_resilient "$UV_CMD" --python "$VENV_PYTHON" "$install_target" --quiet); then
                [[ -n "$lib_out" ]] && printf '%s\n' "$lib_out" >&2
                _step 'uv package install failed -- falling back to python -m pip'
                uv_ok=0
                break
            fi
        done
        if [[ "$uv_ok" -eq 1 ]]; then
            if pkg_out=$(INSTALLER_ENGINE_PAYLOAD_DIR_TO_SCRUB="$PLUGIN_DIR" invoke_uv_pip_install_resilient "$UV_CMD" --python "$VENV_PYTHON" --no-deps "$PLUGIN_DIR" --quiet); then
                return 0
            fi
            [[ -n "$pkg_out" ]] && printf '%s\n' "$pkg_out" >&2
            _step 'uv package install failed -- falling back to python -m pip'
        fi
    fi
    "$VENV_PYTHON" -m pip install --quiet \
        "$agent_procutil_dir" \
        "$PLUGIN_DIR/libs/dropin-registry" \
        "$ssh_manager_dir" \
        "$venue_copilot_dir" \
        "$zdd_dir" \
        "$remote_login_shell_dir" \
        "$PLUGIN_DIR" 2>/dev/null
}

ACTION="${AGENT_SSH_ACTION:-install}"
FORCE=0
DRY_RUN="${AGENT_SSH_DRY_RUN:-0}"
INSTALL_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        install|update|status|uninstall|stamp|provision) ACTION="$1"; shift ;;
        --force) FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) _fail "unknown argument: $1"; exit 2 ;;
    esac
done
# Honor an inherited action/flags across the install-contract:v4 self-stage:
# the arg loop above shifts "$@" empty before the self-stage re-execs, so a
# positional action (or --dry-run) would be lost. Carry them through via env.
export AGENT_SSH_ACTION="$ACTION"
export AGENT_SSH_DRY_RUN="$DRY_RUN"

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

# shellcheck source=/dev/null
. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if [[ -z "${UV_HTTP_TIMEOUT:-}" ]]; then export UV_HTTP_TIMEOUT=60; fi

PKG_SRC_DIR="$PLUGIN_DIR/src/agent_ssh"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-ssh}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-ssh"
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"

# === install-contract:v3 versioned-venv (agent-ssh: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build into versions/<version> and make the
# `.venv` path a symlink into it, so the binstub + manifest resolve through the
# link. CLI (no daemon). LINK_DIR = stable `.venv`; VENV_DIR = the versions/<v>
# slot. ALWAYS versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED /
# AGENT_SSH_VERSIONED) and the legacy in-place fork are retired;
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
# created); LINK_DIR is kept ONLY to derive the `--link-name` for versioned_runtime
# so activate/gc can still find and REMOVE any pre-existing `.venv` link.
LINK_PYTHON="$VENV_PYTHON"

_versioned_activate() {
    # CLI (no daemon): health-gate the slot, swap the `.venv` symlink onto it
    # (first migration moves a legacy real `.venv` aside), gc keeping current +
    # previous-good. Returns non-zero on failure. No-op in legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || return 0
    if ! "$VENV_PYTHON" -c 'import agent_ssh' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    _versioned_mark_complete
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
    return 0
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
_git_info() {
    local path="$1" commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
    echo "$commit $branch $dirty"
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
_resolve_snapshot_installer_engine_source() {
    local ext="$1"
    local local_engine="$SCRIPT_DIR/installer-engine.$ext"
    if [[ -f "$local_engine" ]]; then
        printf '%s\n' "$local_engine"
    else
        printf '%s\n' "$PLUGIN_DIR/../../libs/installer-engine/installer-engine.$ext"
    fi
}

_materialize_snapshot_vendored_libs() {
    local snapshot_dir="$1"
    mkdir -p "$snapshot_dir/libs"
    local lib source destination
    for lib in agent-procutil ssh-manager venue-copilot zdd remote-login-shell; do
        source="$(_resolve_vendored_lib "$lib")" || {
            _fail "Cannot locate required snapshot library: $lib"
            return 1
        }
        destination="$snapshot_dir/libs/$lib"
        if [[ "$(cd "$source" && pwd)" == "$(cd "$destination" 2>/dev/null && pwd || printf '%s\n' '')" ]]; then
            continue
        fi
        if ! rm -rf "$destination"; then
            _fail "Failed to remove stale snapshot library path: $destination"
            return 1
        fi
        if ! cp -a "$source" "$destination"; then
            _fail "Failed to copy required snapshot library: $lib"
            return 1
        fi
    done
}

_materialize_snapshot_installer_engine() {
    local snapshot_dir="$1"
    mkdir -p "$snapshot_dir/scripts"
    local ext source destination
    for ext in ps1 sh; do
        source="$(_resolve_snapshot_installer_engine_source "$ext")"
        destination="$snapshot_dir/scripts/installer-engine.$ext"
        if [[ "$(cd "$(dirname "$source")" && pwd)/$(basename "$source")" != "$(cd "$(dirname "$destination")" && pwd)/$(basename "$destination")" ]]; then
            cp -f "$source" "$destination"
        fi
    done
    local install_sh="$snapshot_dir/scripts/install.sh"
    if [[ -f "$install_sh" ]]; then
        sed 's|\. "\$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"|. "$SCRIPT_DIR/installer-engine.sh"|g' "$install_sh" > "$install_sh.tmp"
        mv -f "$install_sh.tmp" "$install_sh"
    fi
    local install_ps1="$snapshot_dir/scripts/install.ps1"
    if [[ -f "$install_ps1" ]]; then
        sed "s|\. (Join-Path \$PSScriptRoot '..\\\\..\\\\..\\\\libs\\\\installer-engine\\\\installer-engine.ps1')|. (Join-Path \$PSScriptRoot 'installer-engine.ps1')|g" "$install_ps1" > "$install_ps1.tmp"
        mv -f "$install_ps1.tmp" "$install_ps1"
    fi
}

_snapshot_source_marker() {
    printf '%s\n' "$1/.source-payload-path"
}

_snapshot_version_marker() {
    printf '%s\n' "$1/.snapshot-version"
}

_current_snapshot() {
    cat "$INSTALL_DIR/payload-dir" 2>/dev/null || true
}

_write_stamped_version_marker() {
    printf '%s\n' "$SRC_VERSION" > "$INSTALL_DIR/stamped-version.$$.tmp"
    mv -f "$INSTALL_DIR/stamped-version.$$.tmp" "$INSTALL_DIR/stamped-version"
}

_acquire_stamp_publication_lock() {
    local lock_base="$INSTALL_DIR/.stamp-publication.lock"
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != "1" ]]; then
        exec 8>"$lock_base"
        flock 8
        STAMP_PUBLICATION_LOCK_MODE="flock"
        return 0
    fi
    local lock_dir="${lock_base}.d" owner_file
    owner_file="$lock_dir/owner"
    if mkdir "$lock_dir" 2>/dev/null; then
        if ! printf '%s\n' "$$" > "$owner_file"; then
            rm -rf "$lock_dir" 2>/dev/null || true
            _fail "Failed to initialize stamp-publication lock owner: $owner_file"
            return 1
        fi
        STAMP_PUBLICATION_LOCK_MODE="directory"
        STAMP_PUBLICATION_LOCK_PATH="$lock_dir"
        return 0
    fi
    local owner
    owner="$(cat "$owner_file" 2>/dev/null || true)"
    if [[ -z "$owner" ]]; then
        _fail "stamp-publication lock exists without an owner file; refusing unsafe no-flock recovery: $lock_dir"
        return 1
    fi
    if kill -0 "$owner" 2>/dev/null; then
        _fail "stamp-publication lock already held by pid $owner (no-flock fallback cannot wait safely): $lock_dir"
    else
        _fail "stale stamp-publication lock belongs to dead pid $owner; refusing unsafe no-flock recovery: $lock_dir"
    fi
    return 1
}

_release_stamp_publication_lock() {
    if [[ "${STAMP_PUBLICATION_LOCK_MODE:-}" == "directory" ]]; then
        local owner
        owner="$(cat "${STAMP_PUBLICATION_LOCK_PATH:-}/owner" 2>/dev/null || true)"
        if [[ "$owner" == "$$" ]]; then
            rm -rf "${STAMP_PUBLICATION_LOCK_PATH:-}"
        else
            unset STAMP_PUBLICATION_LOCK_PATH
            unset STAMP_PUBLICATION_LOCK_MODE
            return 0
        fi
        unset STAMP_PUBLICATION_LOCK_PATH
    elif [[ "${STAMP_PUBLICATION_LOCK_MODE:-}" == "flock" ]]; then
        flock -u 8 2>/dev/null || true
        exec 8>&-
    fi
    unset STAMP_PUBLICATION_LOCK_MODE
}

_version_sort_key() {
    awk '
      {
        original = $0
        if (original ~ /^[0-9]+\.[0-9]+\.[0-9]+(-dev[0-9]+)?$/) {
          count = split(original, part, /[.-]/)
          phase = (count == 4) ? 0 : 1
          dev = (count == 4) ? part[4] : "dev0"
          sub(/^dev/, "", dev)
          printf "0:%020d.%020d.%020d.%d.%020d\n", part[1] + 0, part[2] + 0, part[3] + 0, phase, dev + 0
          next
        }
        print "1:" original
      }
    ' <<<"$1"
}

_version_gt() {
    local left right
    left="$(_version_sort_key "$1")"
    right="$(_version_sort_key "$2")"
    [[ "$left" > "$right" ]]
}

_published_snapshot_is_newer() {
    local current_snapshot="$1" source_path="$2" source_version="$3"
    [[ -n "$current_snapshot" && -d "$current_snapshot" ]] || return 1
    [[ -f "$(_snapshot_source_marker "$current_snapshot")" ]] || return 1
    [[ -f "$(_snapshot_version_marker "$current_snapshot")" ]] || return 1
    local current_source current_version
    current_source="$(cat "$(_snapshot_source_marker "$current_snapshot")" 2>/dev/null || true)"
    current_version="$(cat "$(_snapshot_version_marker "$current_snapshot")" 2>/dev/null || true)"
    [[ -n "$current_source" && -n "$current_version" ]] || return 1
    [[ "$current_source" == "$source_path" ]] || return 1
    _version_gt "$current_version" "$source_version"
}

_snapshot_is_reusable() {
    local current_snapshot="$1" source_kind="$2" source_path="$3" source_version="$4"
    [[ "$source_kind" != "local" ]] || return 1
    [[ -n "$current_snapshot" && -d "$current_snapshot" ]] || return 1
    local rel
    for rel in \
        "scripts/installer-engine.sh" \
        "scripts/installer-engine.ps1" \
        "libs/agent-procutil/pyproject.toml" \
        "libs/ssh-manager/pyproject.toml" \
        "libs/venue-copilot/pyproject.toml" \
        "libs/zdd/pyproject.toml" \
        "libs/remote-login-shell/pyproject.toml"
    do
        [[ -f "$current_snapshot/$rel" ]] || return 1
    done
    [[ -f "$(_snapshot_version_marker "$current_snapshot")" ]] || return 1
    [[ "$(cat "$(_snapshot_version_marker "$current_snapshot")" 2>/dev/null || true)" == "$source_version" ]] || return 1
    [[ -f "$(_snapshot_source_marker "$current_snapshot")" ]] || return 1
    [[ "$(cat "$(_snapshot_source_marker "$current_snapshot")" 2>/dev/null || true)" == "$source_path" ]] || return 1
}

_deploy_binstub() {
    write_simple_binstub \
        "agent-ssh" \
        "agent_ssh" \
        "$INSTALL_DIR" \
        "$LOCAL_BIN" \
        "$INSTALL_DIR/bin" \
        "scripts/install.sh" \
        "AGENT_SSH_NO_SELFPROVISION" \
        "$SCRIPT_DIR/resolve-runtime.ps1" \
        "$SCRIPT_DIR/resolve-runtime.sh"
}

# Cheap 'stamp': splat the binstub + payload marker, defer the venv build to first
# use (fits a sessionStart hook's grace window). No venv, no uv.
if [[ "$ACTION" == "stamp" ]]; then
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    SOURCE_PATH="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
    SOURCE_KIND="$(_source_kind "$SOURCE_PATH")"
    _acquire_stamp_publication_lock || exit 1
    trap '_release_stamp_publication_lock' EXIT
    CURRENT_SNAPSHOT="$(_current_snapshot)"
    if _published_snapshot_is_newer "$CURRENT_SNAPSHOT" "$SOURCE_PATH" "$SRC_VERSION"; then
        _skip "Published snapshot $CURRENT_SNAPSHOT is newer than $SRC_VERSION; leaving payload-dir unchanged"
        _write_stamped_version_marker
        _deploy_binstub
        exit 0
    fi
    if _snapshot_is_reusable "$CURRENT_SNAPSHOT" "$SOURCE_KIND" "$SOURCE_PATH" "$SRC_VERSION"; then
        printf '%s\n' "$CURRENT_SNAPSHOT" > "$INSTALL_DIR/payload-dir.$$.tmp"
        mv -f "$INSTALL_DIR/payload-dir.$$.tmp" "$INSTALL_DIR/payload-dir"
        _write_stamped_version_marker
        _deploy_binstub
        _ok "Stamped: reused snapshot $CURRENT_SNAPSHOT"
        exit 0
    fi
    SNAPSHOT_DIR="$INSTALL_DIR/snapshots/$SRC_VERSION-$(date -u +%Y%m%dT%H%M%S)-$$"
    SNAPSHOT_TMP="$SNAPSHOT_DIR.tmp-$$"
    rm -rf "$SNAPSHOT_TMP"
    mkdir -p "$SNAPSHOT_TMP"
    shopt -s dotglob nullglob
    for entry in "$PLUGIN_DIR"/*; do
        case "${entry##*/}" in
            .git|__pycache__|.pytest_cache|.venv|tests) continue ;;
        esac
        if ! cp -a "$entry" "$SNAPSHOT_TMP/"; then
            shopt -u dotglob nullglob
            rm -rf "$SNAPSHOT_TMP"
            _fail "Failed to copy snapshot payload entry: ${entry##*/}"
            exit 1
        fi
    done
    shopt -u dotglob nullglob
    _materialize_snapshot_vendored_libs "$SNAPSHOT_TMP" || {
        rm -rf "$SNAPSHOT_TMP"
        exit 1
    }
    _materialize_snapshot_installer_engine "$SNAPSHOT_TMP"
    printf '%s\n' "$SOURCE_PATH" > "$(_snapshot_source_marker "$SNAPSHOT_TMP")"
    printf '%s\n' "$SRC_VERSION" > "$(_snapshot_version_marker "$SNAPSHOT_TMP")"
    CURRENT_SNAPSHOT="$(_current_snapshot)"
    if _published_snapshot_is_newer "$CURRENT_SNAPSHOT" "$SOURCE_PATH" "$SRC_VERSION"; then
        rm -rf "$SNAPSHOT_TMP"
        _skip "Published snapshot $CURRENT_SNAPSHOT is newer than $SRC_VERSION; skipping older snapshot publication"
        _write_stamped_version_marker
        _deploy_binstub
        exit 0
    fi
    mv -f "$SNAPSHOT_TMP" "$SNAPSHOT_DIR"
    printf '%s\n' "$SNAPSHOT_DIR" > "$INSTALL_DIR/payload-dir.$$.tmp"
    mv -f "$INSTALL_DIR/payload-dir.$$.tmp" "$INSTALL_DIR/payload-dir"
    _write_stamped_version_marker
    _deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
    exit 0
fi

if [[ "$ACTION" == "status" ]]; then
    echo '=== agent-ssh status ==='
    [[ -x "$LINK_PYTHON" ]] && _ok "Runtime: $VENV_DIR" || _skip "Runtime missing: $VENV_DIR"
    [[ -x "$STUB" ]] && _ok "Binstub: $STUB" || _skip "Binstub missing: $STUB"
    [[ -f "$MANIFEST_PATH" ]] && _ok "Deploy manifest: $MANIFEST_PATH" || _skip "Deploy manifest missing"
    exit 0
fi

if [[ "$ACTION" == "uninstall" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo '(dry run -- nothing will be changed)'
        [[ -e "$STUB" ]] && echo "[dry-run] would remove binstub: $STUB"
        [[ -d "$INSTALL_DIR" ]] && echo "[dry-run] would remove (config + DB + venv): $INSTALL_DIR"
        echo "agent-ssh uninstall dry run complete -- nothing was changed"
        exit 0
    fi
    rm -f "$STUB"
    rm -rf "$INSTALL_DIR"
    _ok 'agent-ssh runtime removed'
    exit 0
fi

echo ''
echo '=== agent-ssh install ==='
echo ''

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    _fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

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
UV_CMD="$(ensure_uv "$INSTALL_DIR" tool 1 || true)"

mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"

# -- Deploy the session-start hook (version-gated runtime reconcile) --
# hooks.json runs ~/.agent-ssh/bin/bootstrap-check.sh at session start; it
# re-runs this installer only when the deployed version drifts from the payload.
BIN_HOOK_DIR="$INSTALL_DIR/bin"
mkdir -p "$BIN_HOOK_DIR"
for h in bootstrap-check.ps1 bootstrap-check.sh bootstrap-killswitch-guard.ps1 bootstrap-killswitch-guard.sh emit-mesh-pointer.ps1 emit-mesh-pointer.sh; do
    [ -f "$SCRIPT_DIR/$h" ] && cp -f "$SCRIPT_DIR/$h" "$BIN_HOOK_DIR/$h"
done
_ok "Session-start hook: $BIN_HOOK_DIR/bootstrap-check.sh"

if [[ "$FORCE" -eq 1 || ! -x "$VENV_PYTHON" ]]; then
    _versioned_slot_clean
    if [[ -n "$UV_CMD" ]]; then
        if ! new_signed_venv "$UV_CMD" "$VENV_DIR" "3.10"; then
            _step 'uv venv creation failed -- falling back to python -m venv'
            "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1 || {
                _fail "Failed to create venv at $VENV_DIR"
                exit 1
            }
        fi
    else
        _step 'uv unavailable -- falling back to python -m venv'
        "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1 || {
            _fail "Failed to create venv at $VENV_DIR"
            exit 1
        }
    fi
    if [[ ! -x "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed -- $VENV_PYTHON not found"
        exit 1
    fi
    _ok 'Venv created'
else
    _skip 'Venv already exists'
fi

if ! _install_agent_ssh_package; then
    _fail 'Failed to install agent-ssh package into venv'
    exit 1
fi
_ok 'Package installed: agent-ssh'

# Versioned layout (#581): health-gate the slot + swap the `.venv` symlink.
_versioned_activate || exit 1

_deploy_binstub

SOURCE_PATH="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
if [[ -f "$(_snapshot_source_marker "$PLUGIN_DIR")" ]]; then
    SOURCE_PATH="$(cat "$(_snapshot_source_marker "$PLUGIN_DIR")" 2>/dev/null || printf '%s\n' "$SOURCE_PATH")"
fi
write_deploy_manifest "agent-ssh" "agent-ssh" "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV_DIR" "" "$SOURCE_PATH" "$SRC_VERSION"

echo ''
if "$LINK_PYTHON" -c 'import agent_ssh' 2>/dev/null; then
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
echo '=== agent-ssh install complete ==='
echo '  Try: agent-ssh version'
exit 0
