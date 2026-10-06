#!/bin/sh
set -eu
_ok() { printf "OK: %s\n" "$1"; }
_warn() { printf "WARN: %s\n" "$1" >&2; }
_skip() { printf "SKIP: %s\n" "$1"; }
_fail() { printf "FAIL: %s\n" "$1" >&2; }
_step() { printf "STEP: %s\n" "$1"; }
_versioned_current() { printf '%s' ""; }
_version_lt() {
    local a="${1//-/.}" b="${2//-/.}"
    [[ "$a" == "$b" ]] && return 1
    local -a pa pb
    IFS='.' read -r -a pa <<< "$a"
    IFS='.' read -r -a pb <<< "$b"
    local len=${#pa[@]}
    (( ${#pb[@]} > len )) && len=${#pb[@]}
    local i ca cb na nb
    for (( i = 0; i < len; i++ )); do
        ca="${pa[i]:-}"
        cb="${pb[i]:-}"
        [[ -z "$ca" && -z "$cb" ]] && continue
        # A version that ran out of components here (e.g. "0.1.2" vs
        # "0.1.2.dev5") is the finished release; a release outranks any
        # devN pre-release build of the same prefix.
        [[ -z "$ca" ]] && return 1
        [[ -z "$cb" ]] && return 0
        na="${ca//[!0-9]/}"
        nb="${cb//[!0-9]/}"
        na="${na:-0}"
        nb="${nb:-0}"
        na=$((10#$na))
        nb=$((10#$nb))
        (( na < nb )) && return 0
        (( na > nb )) && return 1
    done
    return 1
}
_versioned_activate() {
    # Swap the stable `.venv` symlink to this version's freshly-built slot, moving
    # a legacy real `.venv` aside on first migration (--replace-nonlink). No-op in
    # legacy mode. On POSIX a rename tolerates the daemons' open files, and
    # _install_service / _install_supervisor_service `systemctl restart` onto the
    # new slot, so no stop is needed.
    #
    # Cross-version ordering guard (parity with install.ps1's
    # Invoke-VersionedActivate): two concurrent POSIX installs for DIFFERENT
    # versions can legitimately build fully in parallel (nothing here
    # serializes the build itself), so a slower, older-version invocation
    # could still reach this activate call AFTER a faster, newer-version
    # invocation already activated -- silently regressing current-version.
    # versioned_runtime.py's own `activate` performs no version comparison of
    # its own, so the guard lives here, under a global (version-independent)
    # lock so the compare-then-publish sequence is atomic against another
    # concurrent activate call. `flock` is absent by default on macOS (a
    # plugin this installer explicitly supports), so a bare `command -v
    # flock` fallback would silently degrade to NO mutual exclusion there --
    # fall back to the same PID-symlink lock used by
    # plugins/agent-machines/scripts/init.sh's stamp lock and
    # plugins/agent-worktrees/scripts/invoke-payload-runtime.sh's provision
    # lock (an atomic `ln -s $$ <path>`, reclaimed only once its recorded
    # owner PID is confirmed dead) so every platform gets REAL mutual
    # exclusion, not a best-effort no-op.
    #
    # ACTIVATION_SUPERSEDED is the caller-visible signal that THIS ENTIRE
    # invocation lost the race, not merely that its activate call was a
    # no-op: _ensure_runtime and do_update both check it and abort their own
    # remaining steps (manifest/verify/PATH/pivot; coordinator cutover)
    # rather than proceeding as if this invocation's own
    # VENV_PYTHON/LINK_PYTHON were the one that's actually live -- otherwise
    # a superseded (older) invocation would still publish its own manifest
    # over the newer one's, or drive _coordinator_cutover from its own stale
    # build, rolling the live coordinator back even though activation itself
    # was correctly skipped.
    ACTIVATION_SUPERSEDED=0
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    mkdir -p "$INSTALL_DIR"
    local _activate_lock_link=""
    _unlock_activate() {
        if [[ -n "$_activate_lock_link" ]]; then
            local owner
            owner="$(readlink "$_activate_lock_link" 2>/dev/null || true)"
            [[ "$owner" != "$$" ]] || rm -f "$_activate_lock_link"
        else
            flock -u 8 2>/dev/null || true
            exec 8>&- 2>/dev/null || true
        fi
    }
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
        exec 8>"$INSTALL_DIR/.activate.lock"
        flock 8
    else
        _activate_lock_link="$INSTALL_DIR/.activate.lock.pid"
        local owner
        until ln -s "$$" "$_activate_lock_link" 2>/dev/null; do
            owner="$(readlink "$_activate_lock_link" 2>/dev/null || true)"
            if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
                sleep 1
            elif [[ "$(readlink "$_activate_lock_link" 2>/dev/null || true)" == "$owner" ]]; then
                # Re-verify the link is still the SAME stale value observed
                # above before removing it (TOCTOU-safe reap): another
                # process could have reaped and replaced it between the two
                # readlink calls, and a blind rm -f would then delete that
                # new, live lock instead.
                rm -f "$_activate_lock_link"
            fi
        done
    fi
    local current_active
    current_active="$(_versioned_current)"
    if [[ -n "$current_active" ]] && _version_lt "$SRC_VERSION" "$current_active" && [[ "$FORCE" -ne 1 ]]; then
        _skip "Not activating: source $SRC_VERSION is older than already-active $current_active (a newer build activated first; --force to override)"
        ACTIVATION_SUPERSEDED=1
        _unlock_activate
        return 0
    fi
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink --no-link; then
        _fail "Failed to activate versioned runtime slot (versions/$SRC_VERSION; marker-only, no .venv link)"
        _unlock_activate
        return 1
    fi
    _unlock_activate
    _ok "Runtime version $SRC_VERSION active (marker-only; versions/$SRC_VERSION)"
}
VERSIONED_RUNTIME=1
SRC_VERSION="0.2.0-dev1"
FORCE=0
INSTALL_DIR="C:/Users/tmichon/AppData/Local/Temp/pytest-of-tmichon/pytest-445/test_activate_proceeds_normall0/install"
VENV_DIR="C:/Users/tmichon/AppData/Local/Temp/pytest-of-tmichon/pytest-445/test_activate_proceeds_normall0/install/versions/0.2.0-dev1"
LINK_DIR="C:/Users/tmichon/AppData/Local/Temp/pytest-of-tmichon/pytest-445/test_activate_proceeds_normall0/install/.venv"
SCRIPT_DIR="C:/Users/tmichon/AppData/Local/Temp/pytest-of-tmichon/pytest-445/test_activate_proceeds_normall0"
_versioned_activate
echo "RETURNED:$?"
echo "SUPERSEDED:$ACTIVATION_SUPERSEDED"
