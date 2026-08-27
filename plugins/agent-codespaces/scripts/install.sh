#!/usr/bin/env bash
# =============================================================================
# install.sh -- Agent Codespaces -- standardized installer interface
# =============================================================================
# Manages the agent-codespaces infrastructure lifecycle: install, uninstall,
# status, update.
#
# Runtime at ~/.agent-codespaces/; binstub at ~/.local/bin/agent-codespaces.
#
# Usage:
#   bash plugins/agent-codespaces/scripts/install.sh install
#   bash plugins/agent-codespaces/scripts/install.sh stamp      # cheap: binstub only, defer runtime to first use
#   bash plugins/agent-codespaces/scripts/install.sh provision  # heavy: build the runtime venv (what the binstub calls on first use)
#   bash plugins/agent-codespaces/scripts/install.sh status
#   bash plugins/agent-codespaces/scripts/install.sh update
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

REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# -- Parse arguments -------------------------------------------------------

ACTION="${1:-status}"
shift || true

FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        *)       echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# -- Metadata --------------------------------------------------------------

SERVICE_NAME="Agent Codespaces"
INSTALL_DIR="$HOME/.agent-codespaces"
LOCAL_BIN="$HOME/.local/bin"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_BIN="$VENV_DIR/bin/agent-codespaces"

# === install-contract:v3 versioned-venv (agent-codespaces: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build the venv into versions/<version>
# and make the historical `.venv` path a symlink into it, so the binstub and
# deploy-manifest resolve through the link unchanged. agent-codespaces is a CLI
# (its SSH masters are ssh, not python), so no process to drain. LINK_DIR is the
# stable `.venv` path; VENV_DIR is the versions/<v> slot (build + health-gate).
# ALWAYS versioned -- the env opt-out (COPILOT_EXT_NO_VERSIONED /
# AGENT_CODESPACES_VERSIONED) and the legacy in-place fork are retired;
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
# created); LINK_DIR is kept ONLY to derive the `--link-name` so activate/gc can
# still find and REMOVE any pre-existing `.venv` link.
LINK_PYTHON="$VENV_PYTHON"
VENV_BIN="$VENV_DIR/bin/agent-codespaces"

_versioned_activate() {
    # CLI (no daemon): health-gate the freshly-built slot, swap the stable `.venv`
    # symlink onto it (first migration moves a legacy real `.venv` aside), then gc
    # old slots keeping current + previous-good. Returns non-zero on failure. No-op
    # in legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    if [[ ! -x "$py" ]]; then
        _fail "Fresh runtime slot has no interpreter (versions/$SRC_VERSION)"
        return 1
    fi
    if ! "$py" -c 'import agent_codespaces' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    _versioned_mark_complete || return 1
    local prev
    prev="$("$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo "")"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink --no-link; then
        _fail "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        return 1
    fi
    _ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
    if [[ -n "$prev" ]]; then
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$prev" 2>&1 | sed 's/^/  gc: /' || true
    else
        "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  gc: /' || true
    fi
    return 0
}
# === end install-contract:v3 versioned-venv ===
# ssh-manager dir (contains pyproject.toml): plugin-vendored (marketplace
# layout) or repo-root (git checkout layout).
SSH_MGR_DIR="$PLUGIN_DIR/libs/ssh-manager"
if [[ ! -f "$SSH_MGR_DIR/pyproject.toml" ]]; then
    SSH_MGR_DIR="$REPO_ROOT/libs/ssh-manager"
fi
# credential-relay dir (vendored like ssh-manager): plugin-vendored or repo-root.
CRED_RELAY_DIR="$PLUGIN_DIR/libs/credential-relay"
if [[ ! -f "$CRED_RELAY_DIR/pyproject.toml" ]]; then
    CRED_RELAY_DIR="$REPO_ROOT/libs/credential-relay"
fi
# config-migrate dir (vendored like ssh-manager): plugin-vendored or repo-root.
CFG_MIGRATE_DIR="$PLUGIN_DIR/libs/config-migrate"
if [[ ! -f "$CFG_MIGRATE_DIR/pyproject.toml" ]]; then
    CFG_MIGRATE_DIR="$REPO_ROOT/libs/config-migrate"
fi

DEPLOY_SOURCE_PATHS=("plugins/agent-codespaces/")
INSTALLER_REL_PATH="plugins/agent-codespaces/scripts/install.sh"

# -- Status output helpers -------------------------------------------------

_ok()      { echo "  [OK]   $*"; }
_changed() { echo "  [->]   $*"; }
_skip()    { echo "  [SKIP] $*"; }
_warn()    { echo "  [WARN] $*"; }
_fail()    { echo "  [FAIL] $*" >&2; }
_step()    { echo "  ...    $*"; }
_header()  { echo ""; echo "=== $* ==="; }

# -- Helpers ---------------------------------------------------------------

_bootstrap_python() {
    # A python to run the stdlib-only versioned_runtime.py helper BEFORE the slot
    # venv exists (e.g. the pre-build toss). Only returns a PATH interpreter or
    # a completed active runtime; an incomplete target slot is never trusted to
    # inspect or delete itself. Prints nothing + returns 1 if none found (#935).
    local __c
    for __c in python3 python; do
        if command -v "$__c" >/dev/null 2>&1; then command -v "$__c"; return 0; fi
    done
    if [[ -f "$LINK_DIR/.install-complete.json" && -x "$LINK_DIR/bin/python" ]]; then
        echo "$LINK_DIR/bin/python"
        return 0
    fi
    return 1
}

_run_versioned_runtime() {
    local py
    if py="$(_bootstrap_python)" && [[ -n "$py" ]]; then
        "$py" "$SCRIPT_DIR/versioned_runtime.py" "$@"
        return
    fi
    if command -v uv >/dev/null 2>&1; then
        uv run --no-project --python 3.11 "$SCRIPT_DIR/versioned_runtime.py" "$@"
        return
    fi
    return 127
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
    # over a corpse. The canonical helper protects a slot owned by a live process
    # and otherwise detaches stale marker references before rebuilding. No-op in
    # legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    [[ -d "$VENV_DIR" ]] || return 0
    if ! _run_versioned_runtime --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" \
        slot "$SRC_VERSION" --clean-incomplete 2>&1 | sed 's/^/  ...    /'; then
        _fail "Failed to clean incomplete runtime slot (versions/$SRC_VERSION)"
        return 1
    fi
}

_versioned_mark_complete() {
    # #935: write the slot's completion marker AFTER its isolated health gate
    # passed, so "marker present" == "healthy, complete build". A crashed /
    # watchdog-killed install never reaches here, leaving its slot markerless and
    # thus tossable + retryable. No-op in legacy mode. Runs the stdlib-only
    # versioned_runtime.py via any bootstrap python (the marker is slot-scoped, so
    # this helper is portable byte-identically across plugins).
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local py="$VENV_PYTHON"
    if [[ ! -x "$py" ]] && ! py="$(_bootstrap_python)"; then
        _fail "Cannot mark runtime complete: no bootstrap Python is available"
        return 1
    fi
    local ph
    ph="$(_payload_hash)"
    local args=("$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" --link-name "$(basename "$LINK_DIR")" mark-complete "$SRC_VERSION")
    if [[ -n "$ph" ]]; then args+=(--payload-hash "$ph"); fi
    if ! "$py" "${args[@]}" 2>&1 | sed 's/^/  ...    /'; then
        _fail "Failed to mark runtime slot complete (versions/$SRC_VERSION)"
        return 1
    fi
    return 0
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

_assert_uv() {
    command -v uv &>/dev/null && return 0
    # Vendor a standalone uv into the runtime tool dir. A governed/pristine box
    # ships no uv (the #1 provisioning blocker), so rather than dead-end we fetch
    # a self-contained uv via the official installer (curl/wget/python3 -- no pip,
    # no venv needed) into ~/.agent-codespaces/tool and put it on PATH for this run.
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
        # The installer honors UV_INSTALL_DIR for a self-contained, unmanaged drop
        # and INSTALLER_NO_MODIFY_PATH so it never edits the user's shell profile.
        env UV_INSTALL_DIR="$tooldir" UV_UNMANAGED_INSTALL="$tooldir" INSTALLER_NO_MODIFY_PATH=1 sh "$script" >/dev/null 2>&1 || true
    fi
    # The installer may drop uv directly in tooldir or under tooldir/bin.
    [[ -x "$tooldir/bin/uv" && ! -x "$tooldir/uv" ]] && ln -sf "$tooldir/bin/uv" "$tooldir/uv" 2>/dev/null || true
    if [[ -x "$tooldir/uv" ]]; then export PATH="$tooldir:$PATH"; _ok "Vendored uv into $tooldir"; return 0; fi
    _fail "uv is required but not found, and vendoring failed (no reachable uv installer). Install uv, then retry."
    exit 1
}

# uv pip install the vendored libs (ssh-manager, credential-relay) then
# agent-codespaces into the given venv python. Non-editable; deps resolved from
# pyproject.toml. The vendored libs are force-reinstalled so a local code change
# propagates even without a version bump (uv otherwise skips a same-version path
# dep, leaving the venv stale).
_install_package_into() {
    local py="$1"
    if [[ ! -f "$SSH_MGR_DIR/pyproject.toml" ]]; then
        _fail "ssh-manager source not found at $SSH_MGR_DIR"
        return 1
    fi
    if [[ ! -f "$CRED_RELAY_DIR/pyproject.toml" ]]; then
        _fail "credential-relay source not found at $CRED_RELAY_DIR"
        return 1
    fi
    if [[ ! -f "$CFG_MIGRATE_DIR/pyproject.toml" ]]; then
        _fail "config-migrate source not found at $CFG_MIGRATE_DIR"
        return 1
    fi
    uv pip install --python "$py" --reinstall-package agent-ssh-manager "$SSH_MGR_DIR" --quiet || {
        _fail "ssh-manager install failed"; return 1; }
    uv pip install --python "$py" --reinstall-package agent-credential-relay "$CRED_RELAY_DIR" --quiet || {
        _fail "credential-relay install failed"; return 1; }
    uv pip install --python "$py" --reinstall-package agent-config-migrate "$CFG_MIGRATE_DIR" --quiet || {
        _fail "config-migrate install failed"; return 1; }
    uv pip install --python "$py" --reinstall-package agent-codespaces "$PLUGIN_DIR" --quiet || {
        _fail "agent-codespaces install failed"; return 1; }
}

# Stamp _build_info.py into the INSTALLED site-packages copy (post-install).
_stamp_build_info() {
    local py="$1" pkg_dir ts commit branch src_norm ver
    pkg_dir="$("$py" -c 'import agent_codespaces, os; print(os.path.dirname(agent_codespaces.__file__))' 2>/dev/null || true)"
    [[ -z "$pkg_dir" ]] && { _warn "Could not locate installed agent_codespaces -- build info not stamped"; return; }
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    commit="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    src_norm="$(printf '%s' "$PLUGIN_DIR" | tr '\\' '/')"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
    cat > "$pkg_dir/_build_info.py" <<PYEOF
"""Build provenance -- auto-generated at deploy time. Do not edit."""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$ver",
    "commit": "$commit",
    "branch": "$branch",
    "build_timestamp": "$ts",
    "source": "$src_norm",
}
PYEOF
}

# Mirror pip's configured index to uv when uv has none of its own. A governed box
# sets pip's internal feed (a system/global pip.conf index-url) but NOT uv, so
# uv/`uv pip install` still default to the TLS-blocked public PyPI and provisioning
# fails. Best-effort: if neither UV_INDEX_URL nor UV_DEFAULT_INDEX is set but pip
# has an index-url, export it for uv. No-op where pip is absent (e.g. pristine --
# there the index arrives via env/the clean-room fixture).
_ensure_uv_index() {
    [[ -n "${UV_INDEX_URL:-}${UV_DEFAULT_INDEX:-}" ]] && return 0
    local idx=""
    # Prefer `pip config get` when pip is on PATH.
    if command -v pip &>/dev/null; then idx="$(pip config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    if [[ -z "$idx" ]] && command -v pip3 &>/dev/null; then idx="$(pip3 config get global.index-url 2>/dev/null | tr -d '[:space:]' || true)"; fi
    # Else parse the standard pip.conf files directly -- a governed box carries the
    # conf (index-url policy) but may not have pip on PATH in this context.
    if [[ -z "$idx" ]]; then
        local f
        for f in "${PIP_CONFIG_FILE:-}" "$HOME/.config/pip/pip.conf" "$HOME/.pip/pip.conf" /etc/pip.conf /etc/xdg/pip/pip.conf; do
            [[ -n "$f" && -f "$f" ]] || continue
            idx="$(sed -n 's/^[[:space:]]*index-url[[:space:]]*=[[:space:]]*//p' "$f" | head -n1 | tr -d '[:space:]')"
            [[ -n "$idx" ]] && break
        done
    fi
    if [[ -n "$idx" ]]; then
        export UV_DEFAULT_INDEX="$idx"
        _step "uv index derived from pip config (governed-feed bridge)"
    fi
}

deploy_venv() {
    _assert_uv
    _ensure_uv_index
    mkdir -p "$VENV_DIR"
    _versioned_slot_clean || return 1
    if ! uv venv "$VENV_DIR" --python 3.11 --allow-existing 2>/dev/null; then
        uv venv "$VENV_DIR" --allow-existing 2>/dev/null || true
    fi
    if [[ ! -f "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed"
        return 1
    fi
    _ok "Venv ready at $VENV_DIR"
}

deploy_package() {
    _install_package_into "$VENV_PYTHON" || return 1
    _stamp_build_info "$VENV_PYTHON"
    _ok "Package installed into venv"

    # #1643: agent-codespaces is a PURE providers.d marker -- the bridge daemon
    # drives our binstub over a process boundary and NEVER imports agent_codespaces.
    # So we install ONLY into our own venv and drop the providers.d marker; we
    # deliberately do NOT vendor a copy into the agent-bridge venv (the retired
    # issue-#14 sync). agent-bridge's own installer prunes any stale copy and
    # guards against one lingering.
}

deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    local stub_path="$LOCAL_BIN/agent-codespaces"
    # Self-provisioning binstub (install-on-first-use). When the runtime venv is
    # present it is a thin exec (fast path). When it is NOT, the shim provisions
    # the runtime on first use -- announcing LOUDLY (never a silent block) with a
    # machine-readable signal so a caller can extend its timeout instead of
    # killing us -- then execs. Concurrent first-invocations are serialized.
    cat > "$stub_path" << 'STUB'
#!/usr/bin/env bash
# agent-codespaces binstub -- self-provisioning (install-on-first-use).
export PYTHONUTF8=1
_name="agent-codespaces"
_root="$HOME/.$_name"
_marker_valid() {
    [ -n "$1" ] || return 1
    awk -v expected="$1" '
      NR != 1 { bad = 1 }
      NR == 1 {
        if ($0 !~ /^\{"version": "[^"\\]+", "completed_at": "[^"\\]+", "pid": [0-9]+(, "payload_hash": "[^"\\]+")?\}$/) {
          bad = 1; next
        }
        version = $0
        sub(/^\{"version": "/, "", version)
        sub(/".*$/, "", version)
      }
      END { exit !(NR == 1 && !bad && version == expected) }
    ' "$_root/versions/$1/.install-complete.json" 2>/dev/null
}
_version_key() {
    awk '
      {
        original = $0
        if (original ~ /^[0-9]+\.[0-9]+\.[0-9]+(-dev[0-9]+)?$/) {
          count = split(original, part, /[.-]/)
          phase = (count == 4) ? 0 : 1
          dev = (count == 4) ? part[4] : "dev0"
          sub(/^dev/, "", dev)
          printf "0:%020d.%020d.%020d.%d.%020d\t%s\n", \
            part[1] + 0, part[2] + 0, part[3] + 0, phase, dev + 0, original
          next
        }
        key = ""; rest = $0
        while (match(rest, /[0-9]+/)) {
          key = key substr(rest, 1, RSTART - 1)
          number = substr(rest, RSTART, RLENGTH)
          key = key sprintf("%020d", number + 0)
          rest = substr(rest, RSTART + RLENGTH)
        }
        print "1:" key rest "\t" original
      }
    '
}
_resolve_python() {
    for _marker in current-version last-known-good; do
        _ver=""
        [ -f "$_root/$_marker" ] && _ver="$(tr -d ' \t\r\n' < "$_root/$_marker")"
        _candidate="$_root/versions/$_ver/bin/python"
        if [ -n "$_ver" ] && _marker_valid "$_ver" && [ -x "$_candidate" ]; then
            printf '%s\n' "$_candidate"
            return
        fi
    done
    for _ver in $(
      for _slot in "$_root"/versions/*; do
        [ -d "$_slot" ] || continue
        printf '%s\n' "${_slot##*/}"
      done | _version_key | LC_ALL=C sort | cut -f2-
    ); do
        _candidate="$_root/versions/$_ver/bin/python"
        _marker_valid "$_ver" &&
            [ -x "$_candidate" ] && _resolved="$_candidate"
    done
    [ -n "${_resolved:-}" ] && printf '%s\n' "$_resolved"
}
_python="$(_resolve_python)"
# Fast path: runtime already provisioned.
[ -n "$_python" ] && exec "$_python" -m agent_codespaces "$@"
# --- not provisioned: self-install on first use --------------------------
mkdir -p "$_root"
_status="$_root/.provision-status"
# Announce -- human line (stderr) + machine-readable signal. NEVER silent: a
# caller seeing ::agent-provisioning:: should extend its timeout, not kill us.
printf '%s\n' "[$_name] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout." >&2
printf '::agent-provisioning:: plugin=%s eta_seconds=120 reason=first-use status=%s\n' "$_name" "$_status" >&2
# Locate the plugin payload installer (recorded at stamp; else glob the marketplace).
_install="$(cat "$_root/payload-dir" 2>/dev/null)/scripts/install.sh"
[ -f "$_install" ] || _install="$(ls "$HOME"/.copilot/installed-plugins/*/"$_name"/scripts/install.sh 2>/dev/null | head -n1)"
if [ ! -f "$_install" ]; then
    printf '%s\n' "[$_name] cannot self-provision: installer not found in plugin payload. Ensure the plugin is enabled, then retry." >&2
    exit 127
fi
# Serialize concurrent first-invocations (avoid a thundering-herd double-build).
_lock="$_root/.provision.lock"
exec 9>"$_lock"
command -v flock >/dev/null 2>&1 && flock 9 2>/dev/null
# Re-check under the lock -- another invocation may have finished provisioning.
_python="$(_resolve_python)"
[ -n "$_python" ] && exec "$_python" -m agent_codespaces "$@"
printf 'provisioning %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
bash "$_install" provision >&2
_rc=$?
_python="$(_resolve_python)"
if [ "$_rc" -eq 0 ] && [ -n "$_python" ]; then
    printf 'ready %s\n' "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
    exec "$_python" -m agent_codespaces "$@"
fi
printf 'failed rc=%s %s\n' "$_rc" "$(date -u +%FT%TZ 2>/dev/null)" > "$_status" 2>/dev/null || true
if [ "$_rc" -eq 0 ]; then
    printf '%s\n' "[$_name] provisioning reported success but no marker-resolved runtime python is available." >&2
    _rc=1
else
    printf '%s\n' "[$_name] provisioning FAILED (rc=$_rc). See the log above; retry, or run: bash \"$_install\" provision" >&2
fi
exit "$_rc"
STUB
    chmod +x "$stub_path"
    _ok "Binstub: $stub_path (self-provisioning)"
}

write_deploy_manifest() {
    local manifest_path="$INSTALL_DIR/deploy-manifest.json"
    local kind ver commit branch dirty
    kind="$(_source_kind "$PLUGIN_DIR")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local c b d
        read -r c b d <<< "$(_git_info "$REPO_ROOT")"
        commit="\"$c\""; branch="\"$b\""; dirty="$d"
    fi
    local tmp="$manifest_path.tmp"
    cat > "$tmp" << MANIFEST
{
  "schema_version": 3,
  "service": "agent-codespaces",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-codespaces",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
MANIFEST
    mv -f "$tmp" "$manifest_path"
    _ok "Deploy manifest written (source: $kind)"
}

# -- Actions ---------------------------------------------------------------

# -- Connection Owner service (config-gated; default off) ------------------
# The persistent per-machine Connection Owner relay daemon (dotfiles#1320/#1333)
# is provisioned as a systemd --user service, but ONLY when connection_owner is
# enabled in config. Default off -> the unit is ensured ABSENT, so a machine with
# the feature disabled is unchanged (truly inert). Enabling it is "flip the
# config, run update" (the install/update convergence contract, ce#488). ExecStart
# resolves through the stable `.venv` symlink so it survives version cutover.
SYSTEMD_OWNER_UNIT="agent-codespaces-owner.service"

# Echo "1" if the Connection Owner is enabled in the merged config, else "0".
# Never fails the caller (disabled on any error).
_owner_enabled() {
    PYTHONUTF8=1 "$LINK_PYTHON" -m agent_codespaces owner --status 2>/dev/null \
        | "$LINK_PYTHON" -c 'import sys, json
try:
    print("1" if json.load(sys.stdin).get("enabled") else "0")
except Exception:
    print("0")' 2>/dev/null || echo "0"
}

_remove_owner_service() {
    if command -v systemctl &>/dev/null; then
        if [[ -f "$HOME/.config/systemd/user/$SYSTEMD_OWNER_UNIT" ]]; then
            systemctl --user disable --now "$SYSTEMD_OWNER_UNIT" 2>/dev/null || true
            rm -f "$HOME/.config/systemd/user/$SYSTEMD_OWNER_UNIT"
            systemctl --user daemon-reload 2>/dev/null || true
            _changed "Removed Connection Owner service ($SYSTEMD_OWNER_UNIT)"
        fi
    fi
}

_sync_owner_service() {
    # Config-gated provisioning: enabled -> install + (re)start the systemd --user
    # unit; disabled (default) -> ensure it is absent. Idempotent + additive.
    if [[ "$(_owner_enabled)" != "1" ]]; then
        _remove_owner_service
        return 0
    fi
    if ! command -v systemctl &>/dev/null; then
        _skip "Connection Owner enabled but systemd unavailable -- start it manually: agent-codespaces owner"
        return 0
    fi
    local unit_dir="$HOME/.config/systemd/user"
    mkdir -p "$unit_dir"
    # Stable `.venv` symlink -> the active versions/<v> slot; never a versions/<v>
    # absolute a `gc` could remove.
    local venv_py="$VENV_PYTHON"
    cat > "$unit_dir/$SYSTEMD_OWNER_UNIT" << EOF
[Unit]
Description=agent-codespaces Connection Owner -- persistent per-machine CodeSpace credential-relay owner
After=network.target

[Service]
Type=simple
ExecStart=$venv_py -m agent_codespaces owner
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUTF8=1

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_OWNER_UNIT" 2>/dev/null || true
    systemctl --user restart "$SYSTEMD_OWNER_UNIT" 2>/dev/null || true
    _ok "Connection Owner service installed + started ($SYSTEMD_OWNER_UNIT)"
}

do_install() {
    _header "$SERVICE_NAME Install"

    # Create directories
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"

    # Deploy venv
    deploy_venv || return 1

    # Deploy package
    deploy_package || return 1

    # Versioned layout (#581): health-gate the slot + swap the `.venv` symlink.
    _versioned_activate || return 1

    # Deploy binstub
    deploy_binstub

    # Machine-local config schema migration (idempotent + atomic; never touches
    # repo-committed .agent-codespaces/config.yaml -- that is an adopt concern). Non-fatal.
    PYTHONUTF8=1 "$VENV_PYTHON" -m agent_codespaces config-migrate 2>&1 \
        | sed 's/^/  /' || _warn "Config migration skipped"

    # Write manifest
    write_deploy_manifest

    # Verify (import from the venv -- no PYTHONPATH)
    local check
    check="$("$LINK_PYTHON" -c 'import agent_codespaces; print("OK")' 2>/dev/null || true)"
    if [[ "$check" == "OK" ]]; then
        _ok "Verification: module imports successfully"
    else
        _fail "Verification: module import failed"
        return 1
    fi

    # Connection Owner daemon (config-gated; default off -> ensured absent).
    _sync_owner_service

    echo ""
    _ok "$SERVICE_NAME installed"
}

do_uninstall() {
    _header "$SERVICE_NAME Uninstall"

    # Remove the Connection Owner systemd unit (if provisioned).
    _remove_owner_service

    # Stop managed SSH ControlMaster connections before removing files. They
    # multiplex connections to CodeSpaces via sockets under
    # ~/.agent-codespaces/sockets. Close each via `ssh -O exit` (best-effort),
    # then kill any lingering ssh master referencing the socket dir.
    local socket_dir="$INSTALL_DIR/sockets"
    if [[ -d "$socket_dir" ]]; then
        for sock in "$socket_dir"/*; do
            [[ -e "$sock" ]] || continue
            ssh -o "ControlPath=$sock" -O exit placeholder >/dev/null 2>&1 || true
        done
    fi
    if command -v pkill &>/dev/null; then
        pkill -f "ControlPath=$INSTALL_DIR/sockets" 2>/dev/null && \
            _changed "Stopped managed SSH ControlMaster processes" || true
    fi

    # Remove binstub
    local stub_path="$LOCAL_BIN/agent-codespaces"
    if [[ -f "$stub_path" ]]; then
        rm -f "$stub_path"
        _changed "Removed binstub: $stub_path"
    else
        _skip "Binstub not found"
    fi

    # Remove install directory
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        _changed "Removed: $INSTALL_DIR"
    else
        _skip "Install directory not found"
    fi

    _ok "$SERVICE_NAME uninstalled"
}

do_status() {
    _header "$SERVICE_NAME Status"

    # Install dir
    if [[ -d "$INSTALL_DIR" ]]; then
        _ok "Install dir: $INSTALL_DIR"
    else
        _fail "Not installed ($INSTALL_DIR not found)"
        return
    fi

    # Venv
    if [[ -f "$LINK_PYTHON" ]]; then
        _ok "Runtime: $VENV_DIR"
    else
        _fail "Venv missing"
    fi

    # Package (installed into the venv)
    if "$LINK_PYTHON" -c 'import agent_codespaces' 2>/dev/null; then
        _ok "Package: agent_codespaces importable in venv"
    else
        _fail "Package not importable in venv"
    fi

    # ssh-manager
    if "$LINK_PYTHON" -c 'import ssh_manager' 2>/dev/null; then
        _ok "ssh-manager: importable in venv"
    else
        _fail "ssh-manager not importable in venv"
    fi

    # credential-relay
    if "$VENV_PYTHON" -c 'import credential_relay' 2>/dev/null; then
        _ok "credential-relay: importable in venv"
    else
        _fail "credential-relay not importable in venv"
    fi

    # Console script
    if [[ -x "$VENV_BIN" ]]; then
        _ok "Console script: $VENV_BIN"
    else
        _fail "Console script missing: $VENV_BIN"
    fi

    # Binstub
    local stub_path="$LOCAL_BIN/agent-codespaces"
    if [[ -f "$stub_path" ]]; then
        _ok "Binstub: $stub_path"
    else
        _warn "Binstub not found at $stub_path"
    fi

    # Version (from the installed package)
    if [[ -x "$VENV_BIN" ]]; then
        local ver_info
        ver_info="$("$VENV_BIN" version 2>/dev/null || true)"
        [[ -n "$ver_info" ]] && _ok "Version: $ver_info"
    fi

    # Deploy manifest + source footprint (local checkout vs marketplace)
    local manifest="$INSTALL_DIR/deploy-manifest.json"
    if [[ -f "$manifest" ]]; then
        local _kind _ver _dep
        _kind=$(grep -o '"kind": *"[^"]*"' "$manifest" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ver=$(grep -o '"version": *"[^"]*"' "$manifest" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        [[ -n "$_kind" ]] && _ok "Source: $_kind ($_ver)"
        _dep=$(grep -o '"deployed_at": *"[^"]*"' "$manifest" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        [[ -n "$_dep" ]] && _ok "Deployed: $_dep"
    fi

    # gh CLI
    if command -v gh &>/dev/null; then
        _ok "gh CLI: $(command -v gh)"
    else
        _warn "gh CLI not found"
    fi

    # ssh
    if command -v ssh &>/dev/null; then
        _ok "ssh: $(command -v ssh)"
    else
        _warn "ssh not found"
    fi
}

do_update() {
    _header "$SERVICE_NAME Update"

    if [[ ! -d "$INSTALL_DIR" ]]; then
        _warn "Not installed -- running full install"
        do_install
        return
    fi

    # Re-deploy venv
    deploy_venv || return 1

    # Re-deploy package
    deploy_package || return 1

    # Versioned layout (#581): health-gate the slot + swap the `.venv` symlink.
    _versioned_activate || return 1

    # Re-deploy binstub
    deploy_binstub

    # Machine-local config schema migration (idempotent + atomic; never touches
    # repo-committed .agent-codespaces/config.yaml -- that is an adopt concern). Non-fatal.
    PYTHONUTF8=1 "$VENV_PYTHON" -m agent_codespaces config-migrate 2>&1 \
        | sed 's/^/  /' || _warn "Config migration skipped"

    # Update manifest
    write_deploy_manifest

    # Connection Owner daemon (config-gated; default off -> ensured absent).
    _sync_owner_service

    _ok "$SERVICE_NAME updated"
}

# Cheap "splat the binstub, defer the runtime" install (fits a sessionStart hook's
# grace window): create dirs, record the payload path so the smart binstub can
# find this installer, and deploy the self-provisioning binstub -- NO venv, NO uv.
# The runtime then builds itself on the binstub's first use.
do_stamp() {
    _header "$SERVICE_NAME Stamp (defer runtime to first use)"
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    # Record the REAL payload dir so the binstub can find this installer on first
    # use. Under the install-contract:v4 self-stage, $PLUGIN_DIR is an ephemeral
    # per-invocation staging copy that gets reaped -- COPILOT_PLUGIN_STAGED_FROM
    # holds the true (installed-plugins) payload path; prefer it.
    printf '%s\n' "${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}" > "$INSTALL_DIR/payload-dir"
    deploy_binstub
    _ok "Stamped: binstub on PATH; runtime provisions on first use."
}

# The heavy runtime build (venv + package + activate + manifest), WITHOUT
# rewriting the binstub -- this is what the self-provisioning binstub invokes on
# first use, so it must not touch the running shim.
do_provision() {
    _header "$SERVICE_NAME Provision (runtime)"
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    deploy_venv || return 1
    deploy_package || return 1
    _versioned_activate || return 1
    PYTHONUTF8=1 "$VENV_PYTHON" -m agent_codespaces config-migrate 2>&1 \
        | sed 's/^/  /' || _warn "Config migration skipped"
    write_deploy_manifest
    local check
    check="$("$LINK_PYTHON" -c 'import agent_codespaces; print("OK")' 2>/dev/null || true)"
    if [[ "$check" == "OK" ]]; then
        _ok "Verification: module imports successfully"
    else
        _fail "Verification: module import failed"
        return 1
    fi
    # Connection Owner daemon (config-gated; default off -> ensured absent).
    _sync_owner_service
    _ok "$SERVICE_NAME runtime provisioned"
}

# -- Dispatch --------------------------------------------------------------

case "$ACTION" in
    install)   do_install ;;
    stamp)     do_stamp ;;
    provision) do_provision ;;
    uninstall) do_uninstall ;;
    status)    do_status ;;
    update)    do_update ;;
    *)
        echo "Usage: $0 {install|stamp|provision|uninstall|status|update}" >&2
        exit 1
        ;;
esac
