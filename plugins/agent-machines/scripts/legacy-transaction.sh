#!/usr/bin/env bash
# Legacy whole-action mutex. Never replace a live lock inode or malformed receipt.
__install_lock_fd=""
__install_lock_link=""

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

_create_install_lock_owner() {
    local path="$1"
    # noclobber creates exactly this regular file; never follow a link or
    # descend into an incumbent directory as ln(1) would.
    ( set -o noclobber; printf 'copilot-extensions.install-lock-pid:v1:%s\n' "$$" > "$path" ) 2>/dev/null
}

_read_install_lock_owner() {
    local path="$1" record
    [[ -f "$path" && ! -L "$path" ]] || return 1
    IFS= read -r record < "$path" || return 1
    case "$record" in
        copilot-extensions.install-lock-pid:v1:*)
            record="${record#copilot-extensions.install-lock-pid:v1:}"
            case "$record" in ''|*[!0-9]*) return 1 ;; esac
            printf '%s' "$record" ;;
        *) return 1 ;;
    esac
}

_exit_install_lock() {
    if [[ -n "$__install_lock_fd" ]]; then
        # A finite cleanup guardian may share this open file description.
        # Only final close releases custody; LOCK_UN would unlock its copy too.
        eval "exec $__install_lock_fd>&-"
        __install_lock_fd=""
    elif [[ -n "$__install_lock_link" ]]; then
        local owner
        owner="$(_read_install_lock_owner "$__install_lock_link" || true)"
        [[ "$owner" != "$$" ]] || rm -f -- "$__install_lock_link"
        __install_lock_link=""
    fi
}

_enter_install_lock() {
    _validate_install_root || { printf '%s\n' 'Unsafe legacy runtime root or transaction lock' >&2; return 1; }
    mkdir -p -- "$INSTALL_DIR" || return 1
    local timeout="${1:-$(_install_lock_timeout)}"
    if command -v flock >/dev/null 2>&1 && [[ "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]]; then
        exec 8>>"$INSTALL_DIR/.install.lock"
        if ! flock -w "$timeout" 8; then exec 8>&-; return 1; fi
        __install_lock_fd=8
        return 0
    fi
    local path="$INSTALL_DIR/.install.lock.pid" deadline owner admitted
    deadline=$(( $(date +%s) + timeout ))
    while :; do
        if _create_install_lock_owner "$path.reap"; then
            admitted=0
            if _create_install_lock_owner "$path"; then
                admitted=1
            else
                owner="$(_read_install_lock_owner "$path" || true)"
                # Permission-denied kill -0 is not proof of death: require ps
                # to succeed and prove absence, or leave custody untouched.
                if [[ -n "$owner" ]] && ! kill -0 "$owner" 2>/dev/null; then
                    local processes
                    if processes="$(ps -e -o pid=)" &&
                        ! grep -Eq "^[[:space:]]*$owner[[:space:]]*$" <<< "$processes" &&
                        [[ "$(_read_install_lock_owner "$path" || true)" == "$owner" ]]; then
                        rm -f -- "$path"
                        _create_install_lock_owner "$path" && admitted=1
                    fi
                fi
            fi
            rm -f -- "$path.reap"
            if [[ "$admitted" == 1 ]]; then __install_lock_link="$path"; return 0; fi
        fi
        [[ "$(date +%s)" -lt "$deadline" ]] || return 1
        sleep 0.1
    done
}
