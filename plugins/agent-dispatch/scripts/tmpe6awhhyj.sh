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
    # ACTIVATION_SUPERSEDED is a POINT-IN-TIME signal set here, at the
    # moment this call either skips or succeeds -- it is NOT, by itself, a
    # sufficient guarantee for a caller that acts LATER (manifest write,
    # coordinator cutover): this invocation can genuinely WIN its own
    # activation (nothing newer was current yet), release this lock, and
    # only THEN have a separate, slower-to-build newer version activate
    # before this invocation reaches its own downstream action -- the lock
    # here only serializes the compare-then-publish step itself, it cannot
    # serialize everything a caller does afterward. Callers must re-validate
    # LIVE immediately before acting via _activation_superseded_now (below)
    # rather than trusting this variable alone.
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
            { exec 8>&-; } 2>/dev/null || true
        fi
    }
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
        exec 8>"$INSTALL_DIR/.activate.lock"
        flock 8
    else
        _activate_lock_link="$INSTALL_DIR/.activate.lock.pid"
        local owner owner_now
        until ln -s "$$" "$_activate_lock_link" 2>/dev/null; do
            owner="$(readlink "$_activate_lock_link" 2>/dev/null || true)"
            if [[ -z "$owner" ]]; then
                # The link vanished after the failed `ln -s` (or is not a
                # symlink): never unlink then -- lock creators are not
                # serialized by the reap mutex, so an unlink could delete a
                # live lock taken meanwhile. Just retry.
                sleep 0.1
                continue
            fi
            if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
                sleep 1
            elif ln -s "$$" "$_activate_lock_link.reap" 2>/dev/null; then
                # A plain readlink-then-rm is a TOCTOU race: another process
                # could reap the same stale link and create its own live one
                # between the two readlink calls, and this rm -f would then
                # delete THAT live lock. A PID-bearing symlink (NOT a bare
                # `mkdir`) as a SEPARATE reap mutex is atomic AND
                # self-healing (mirrors plugins/agent-machines/scripts/
                # init.sh's stamp lock): only the process that wins this
                # `ln -s .reap` may remove the main lock, and only after
                # re-checking its current value (below).
                # Unlink only if the link still holds the owner judged stale above
                # and that owner is still not live: a dead owner cannot release its
                # own link and only serialized reapers remove a stale one, so that
                # value is stable under this mutex. An absent link, or one naming a
                # different (possibly just-released) holder, is never removed.
                owner_now="$(readlink "$_activate_lock_link" 2>/dev/null || true)"
                if [[ -n "$owner_now" && "$owner_now" == "$owner" ]]; then
                    case "$owner_now" in
                        *[!0-9]*) rm -f "$_activate_lock_link" ;;
                        *) kill -0 "$owner_now" 2>/dev/null || rm -f "$_activate_lock_link" ;;
                    esac
                fi
                rm -f "$_activate_lock_link.reap"
            elif owner="$(readlink "$_activate_lock_link.reap" 2>/dev/null || true)" &&
                 [[ "$owner" =~ ^[0-9]+$ ]] &&
                 ! kill -0 "$owner" 2>/dev/null; then
                # The reap mutex itself is stale (its owner died mid-reap) --
                # reclaim it so the main lock can never wedge permanently.
                # Same TOCTOU hazard (and same re-verify fix) as the main
                # lock's own reap above: re-check the symlink still points
                # at the SAME stale PID immediately before removing --
                # another process could already have reaped and replaced it
                # with a live reaper between the staleness check and here.
                if [[ "$(readlink "$_activate_lock_link.reap" 2>/dev/null || true)" == "$owner" ]]; then
                    rm -f "$_activate_lock_link.reap" 2>/dev/null || true
                fi
            else
                sleep 0.1
            fi
        done
    fi
    local current_active
    current_active="$(_versioned_current)"
    # Dual-authority comparison baseline, mirroring do_stamp's own guard:
    # stamped-version is a SEPARATE authority a concurrent `stamp` action
    # can publish WITHOUT ever activating (current-version stays untouched)
    # -- comparing against current-version alone would let a delayed OLDER
    # install still activate and overwrite current-version even though a
    # NEWER version is already the intended/stamped one, stranding that
    # newer snapshot. Read stamped-version directly (no interpreter needed)
    # and activate against whichever of the two authorities is newer.
    local activate_stamped=""
    if [[ -f "$INSTALL_DIR/stamped-version" ]]; then
        activate_stamped="$(cat "$INSTALL_DIR/stamped-version" 2>/dev/null || true)"
    fi
    local newest_published="$current_active"
    if [[ -n "$activate_stamped" ]] && { [[ -z "$newest_published" ]] || _version_lt "$newest_published" "$activate_stamped"; }; then
        newest_published="$activate_stamped"
    fi
    if [[ -n "$newest_published" ]] && _version_lt "$SRC_VERSION" "$newest_published" && [[ "$FORCE" -ne 1 ]]; then
        _skip "Not activating: source $SRC_VERSION is older than already-published $newest_published (a newer build activated or stamped first; --force to override)"
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
INSTALL_DIR="C:/Users/nakanaki/AppData/Local/Temp/ce-agen-ez78v9hh/t/g1/test_activate_proceeds_normall1/install"
VENV_DIR="C:/Users/nakanaki/AppData/Local/Temp/ce-agen-ez78v9hh/t/g1/test_activate_proceeds_normall1/install/versions/0.2.0-dev1"
LINK_DIR="C:/Users/nakanaki/AppData/Local/Temp/ce-agen-ez78v9hh/t/g1/test_activate_proceeds_normall1/install/.venv"
SCRIPT_DIR="C:/Users/nakanaki/AppData/Local/Temp/ce-agen-ez78v9hh/t/g1/test_activate_proceeds_normall1"
_versioned_activate
echo "RETURNED:$?"
echo "SUPERSEDED:$ACTIVATION_SUPERSEDED"
