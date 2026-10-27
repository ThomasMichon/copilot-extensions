#!/usr/bin/env bash
__install_lock_fd=""

_validate_install_root() {
    local cursor="$INSTALL_DIR"
    case "/$cursor/" in */../*) printf '%s\n' 'Legacy runtime paths must not contain raw .. components' >&2; return 1 ;; esac
    [[ "$cursor" == /* && "$cursor" != / && "${cursor%/}" != "${HOME%/}" ]] || return 1
    while [[ -n "$cursor" && "$cursor" != / ]]; do
        [[ ! -L "$cursor" ]] || return 1
        [[ ! -e "$cursor" || -d "$cursor" ]] || return 1
        cursor="${cursor%/*}"
    done
    local lock
    for lock in "$INSTALL_DIR/.install.lock" "$INSTALL_DIR/.install.lock.pid" "$INSTALL_DIR/.install.lock.pid.reap"; do
        [[ ! -L "$lock" && ( ! -e "$lock" || -f "$lock" ) ]] || return 1
    done
}

_install_lock_timeout() {
    local value="${COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC:-300}"
    case "$value" in ''|*[!0-9]*) value=300 ;; esac
    [[ "$value" -gt 0 ]] || value=300
    printf '%s' "$value"
}

_legacy_install_receipts_clear() {
    local path
    for path in "$INSTALL_DIR/.install.lock.pid" "$INSTALL_DIR/.install.lock.pid.reap"; do
        if [[ -e "$path" || -L "$path" ]]; then
            printf '%s\n' 'Legacy transaction receipt requires explicit settlement; nothing reclaimed' >&2
            return 1
        fi
    done
}

_exit_install_lock() {
    if [[ -n "$__install_lock_fd" ]]; then
        # Descendants may share this open file description.
        # Only final close releases the lock; LOCK_UN would unlock their copy too.
        eval "exec $__install_lock_fd>&-"
        __install_lock_fd=""
    fi
}

_python_install_fd_lock() {
    local timeout="$1" candidate python=""
    while IFS= read -r candidate; do
        case "$candidate" in "$INSTALL_DIR"/*|*WindowsApps*) continue ;; esac
        if "$candidate" -I -B -S -c 'import fcntl' >/dev/null 2>&1; then
            python="$candidate"
            break
        fi
    done < <(type -aP python3 python 2>/dev/null)
    [[ -n "$python" ]] || {
        printf '%s\n' 'Transaction admission requires native flock or an existing Python with fcntl.flock; no provisioning attempted' >&2
        return 1
    }
    "$python" -I -B -S - "$timeout" "$INSTALL_DIR/.install.lock" 8>&8 <<'PY'
import fcntl
import os
import stat
import sys
import time

opened = os.fstat(8)
def verify():
    named = os.stat(sys.argv[2], follow_symlinks=False)
    if not stat.S_ISREG(named.st_mode) or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise SystemExit("Transaction descriptor does not name its persistent lock")

verify()
deadline = time.monotonic() + int(sys.argv[1])
while True:
    try:
        fcntl.flock(8, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except BlockingIOError:
        if time.monotonic() >= deadline:
            raise SystemExit(1)
        time.sleep(0.05)
verify()
# No LOCK_UN: the parent retains this same open file description after exit.
PY
}

_enter_install_lock() {
    local __timeout="${1:-$(_install_lock_timeout)}"
    case "$__timeout" in ''|*[!0-9]*) return 1 ;; esac
    [[ "$__timeout" -gt 0 ]] || return 1
    _validate_install_root || { printf '%s\n' 'Unsafe legacy runtime root or transaction lock' >&2; return 1; }
    _legacy_install_receipts_clear || return 1
    mkdir -p -- "$INSTALL_DIR" || return 1
    { exec 8>>"$INSTALL_DIR/.install.lock"; } 2>/dev/null || return 1
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
        if ! flock -w "$__timeout" 8; then exec 8>&-; return 1; fi
    elif ! _python_install_fd_lock "$__timeout"; then
        exec 8>&-
        return 1
    fi
    if ! [[ "$INSTALL_DIR/.install.lock" -ef /dev/fd/8 ]] ||
        ! _validate_install_root || ! _legacy_install_receipts_clear; then
        exec 8>&-
        return 1
    fi
    __install_lock_fd=8
    return 0
}
