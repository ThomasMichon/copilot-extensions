#!/usr/bin/env bash
# shellcheck shell=bash
codespaces_requires_install_transaction() {
    [[ "${1:-}" != status ]] && { [[ "${1:-}" != uninstall ]] || [[ "${2:-false}" != true ]]; }
}

enter_codespaces_install_transaction() {
    local timeout="${COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC:-300}" py reply fd root home unsafe native_flock=1
    [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || timeout=300
    # Keep MSYS on its existing Windows inode owner, not an unqualified flock port.
    case "${OSTYPE:-}" in msys*|cygwin*) native_flock=0 ;; esac
    if [[ "$native_flock" == 1 && "${COPILOT_EXT_NO_FLOCK:-}" != 1 ]] && command -v flock >/dev/null 2>&1; then
        # Borrow the same persistent inode used by the Python and PowerShell owners.
        case "$INSTALL_DIR" in /*) ;; *) return 1 ;; esac
        case "/$INSTALL_DIR/" in */../*) return 1 ;; esac
        root="${INSTALL_DIR%/}"
        while [[ -n "$root" && "$root" != / ]]; do
            [[ ! -L "$root" ]] || return 1
            root="${root%/*}"
        done
        mkdir -m 700 -p -- "$INSTALL_DIR" || return 1
        root="$(CDPATH= cd -P -- "$INSTALL_DIR" && pwd)" || return 1
        home="$(CDPATH= cd -P -- "$HOME" && pwd)" || return 1
        [[ "$root" != / && "$root" != "$home" && "$home" != "$root/"* && -O "$root" ]] || return 1
        unsafe="$(find "$root" -prune \( -perm -0020 -o -perm -0002 \) -print)" || return 1
        [[ -z "$unsafe" ]] || return 1
        if [[ -e "$root/.install.lock" || -L "$root/.install.lock" ]]; then
            [[ -f "$root/.install.lock" && ! -L "$root/.install.lock" && -O "$root/.install.lock" ]] || return 1
            unsafe="$(find "$root/.install.lock" -prune -links +1 -print)" || return 1
            [[ -z "$unsafe" ]] || return 1
        fi
        for ((fd=10; fd<64; fd++)); do
            if ! (eval ": >&$fd") 2>/dev/null && ! (eval ": <&$fd") 2>/dev/null; then break; fi
        done
        [[ "$fd" -lt 64 ]] || return 1
        eval "exec $fd<>\"\$root/.install.lock\"" || return 1
        if ! flock -w "$timeout" "$fd" || [[ ! "$root/.install.lock" -ef "/dev/fd/$fd" ]]; then
            eval "exec $fd>&-"
            echo 'Install transaction admission failed; refusing to proceed unlocked' >&2
            return 1
        fi
        CODESPACES_INSTALL_LOCK_FD="$fd"
        return 0
    fi
    # Native flock is not universally installed (notably on stock macOS).
    # Use only an already available interpreter with the existing inode owner.
    py="$(_bootstrap_python)" || {
        echo 'Install transaction requires native flock or existing Python; no provisioning attempted' >&2
        return 1
    }
    [[ -n "$py" && -f "$SCRIPT_DIR/install-transaction.py" ]] || return 1
    reply="$(printf '' | "$py" -I -B "$SCRIPT_DIR/install-transaction.py" "$INSTALL_DIR" "$timeout")" || return 1
    local read_fd="" write_fd="" bootstrap_fd=""
    for ((fd=10; fd<64; fd++)); do
        if ! (eval ": >&$fd") 2>/dev/null && ! (eval ": <&$fd") 2>/dev/null; then
            if [[ -z "$write_fd" ]]; then write_fd="$fd"
            elif [[ -z "$read_fd" ]]; then read_fd="$fd"
            else bootstrap_fd="$fd"; break; fi
        fi
    done
    [[ -n "$bootstrap_fd" ]] || return 1
    CODESPACES_INSTALL_LOCK_CHANNEL="$(mktemp -d "$INSTALL_DIR/.install-lock-channel.XXXXXXXX")" || return 1
    case "${OSTYPE:-}" in
        msys*|cygwin*)
            # Native Windows Python cannot inherit an MSYS FIFO as stdio.
            # Use an anonymous input pipe; a trusted shell completion record
            # settles the native owner without relying on Bash 3.2's $! for
            # process substitutions. The record grants no mutex ownership.
            chmod 700 "$CODESPACES_INSTALL_LOCK_CHANNEL" || return 1
            : > "$CODESPACES_INSTALL_LOCK_CHANNEL/replies"
            CODESPACES_INSTALL_LOCK_RECORDED=1
            eval "exec $write_fd> >(_codespaces_record_install_owner \"\$py\" -I -B \"\$SCRIPT_DIR/install-transaction.py\" \"\$INSTALL_DIR\" \"\$timeout\")" || {
                _codespaces_close_install_channel; return 1;
            }
            CODESPACES_INSTALL_LOCK_WRITE="$write_fd"
            if ! _codespaces_wait_install_record "$((timeout + 5))" locked; then
                exit_codespaces_install_transaction || return 1
                return 1
            fi
            return 0
            ;;
    esac
    chmod 700 "$CODESPACES_INSTALL_LOCK_CHANNEL" &&
        mkfifo -m 600 "$CODESPACES_INSTALL_LOCK_CHANNEL/commands" "$CODESPACES_INSTALL_LOCK_CHANNEL/replies" || {
        _codespaces_close_install_channel; return 1;
    }
    eval "exec $write_fd<>\"\$CODESPACES_INSTALL_LOCK_CHANNEL/commands\"" || {
        _codespaces_close_install_channel; return 1;
    }
    CODESPACES_INSTALL_LOCK_WRITE="$write_fd"
    eval "exec $bootstrap_fd<>\"\$CODESPACES_INSTALL_LOCK_CHANNEL/replies\"" || {
        _codespaces_close_install_channel; return 1;
    }
    CODESPACES_INSTALL_LOCK_BOOTSTRAP="$bootstrap_fd"
    eval "exec $read_fd<\"\$CODESPACES_INSTALL_LOCK_CHANNEL/replies\"" || {
        _codespaces_close_install_channel; return 1;
    }
    CODESPACES_INSTALL_LOCK_READ="$read_fd"
    # Canonical attached-pipe shape: bind stdio before dropping every inherited
    # bootstrap endpoint, and unlink only this private rendezvous.
    {
        trap - EXIT INT TERM
        exec 0<"$CODESPACES_INSTALL_LOCK_CHANNEL/commands" 1>"$CODESPACES_INSTALL_LOCK_CHANNEL/replies"
        eval "exec $write_fd>&- $read_fd<&- $bootstrap_fd>&-"
        rm -f -- "$CODESPACES_INSTALL_LOCK_CHANNEL/commands" "$CODESPACES_INSTALL_LOCK_CHANNEL/replies" &&
            rmdir -- "$CODESPACES_INSTALL_LOCK_CHANNEL" || exit 1
        exec "$py" -I -B "$SCRIPT_DIR/install-transaction.py" "$INSTALL_DIR" "$timeout"
    } &
    CODESPACES_INSTALL_LOCK_PID="$!"
    eval "exec $bootstrap_fd>&-"
    CODESPACES_INSTALL_LOCK_BOOTSTRAP=""
    if ! IFS= read -r -t "$((timeout + 5))" reply <&"$read_fd" || [[ "$reply" != locked ]]; then
        exit_codespaces_install_transaction || return 1
        return 1
    fi
    CODESPACES_INSTALL_LOCK_CHANNEL=""
}

_codespaces_record_install_owner() {
    trap - EXIT INT TERM
    local result=0
    "$@" > "$CODESPACES_INSTALL_LOCK_CHANNEL/replies" || result="$?"
    printf 'exit:%s\n' "$result" >> "$CODESPACES_INSTALL_LOCK_CHANNEL/replies"
    exit "$result"
}

_codespaces_wait_install_record() {
    local deadline=$((SECONDS + $1)) requested="$2" line admitted terminal
    while :; do
        admitted=0
        terminal=""
        while IFS= read -r line; do
            case "$line" in
                locked) admitted=1 ;;
                exit:*) terminal="${line#exit:}" ;;
                *) echo 'Unrecognized install owner reply; refusing admission' >&2; return 1 ;;
            esac
        done < "$CODESPACES_INSTALL_LOCK_CHANNEL/replies"
        if [[ -n "$terminal" ]]; then
            [[ "$requested" == settled && "$terminal" == 0 ]] && return 0
            echo 'Install transaction owner exited before admission or failed' >&2
            return 1
        fi
        [[ "$requested" != locked || "$admitted" != 1 ]] || return 0
        [[ "$SECONDS" -lt "$deadline" ]] || {
            echo 'Install owner admission/settlement not confirmed' >&2; return 1;
        }
        sleep 0.05
    done
}

_codespaces_close_install_channel() {
    local fd
    for fd in "${CODESPACES_INSTALL_LOCK_WRITE:-}" "${CODESPACES_INSTALL_LOCK_READ:-}" "${CODESPACES_INSTALL_LOCK_BOOTSTRAP:-}"; do
        [[ -z "$fd" ]] || eval "exec $fd>&-"
    done
    CODESPACES_INSTALL_LOCK_WRITE=""
    CODESPACES_INSTALL_LOCK_READ=""
    CODESPACES_INSTALL_LOCK_BOOTSTRAP=""
    if [[ -n "${CODESPACES_INSTALL_LOCK_CHANNEL:-}" ]]; then
        rm -f -- "$CODESPACES_INSTALL_LOCK_CHANNEL/commands" "$CODESPACES_INSTALL_LOCK_CHANNEL/replies"
        rmdir -- "$CODESPACES_INSTALL_LOCK_CHANNEL" 2>/dev/null ||
            [[ ! -e "$CODESPACES_INSTALL_LOCK_CHANNEL" ]] || return 1
        CODESPACES_INSTALL_LOCK_CHANNEL=""
    fi
}

exit_codespaces_install_transaction() {
    local result=0
    if [[ -n "${CODESPACES_INSTALL_LOCK_FD:-}" ]]; then
        flock -u "$CODESPACES_INSTALL_LOCK_FD" || result=1
        eval "exec $CODESPACES_INSTALL_LOCK_FD>&-"
        CODESPACES_INSTALL_LOCK_FD=""
    fi
    if [[ "${CODESPACES_INSTALL_LOCK_RECORDED:-0}" == 1 ]]; then
        if [[ -n "${CODESPACES_INSTALL_LOCK_WRITE:-}" ]]; then
            eval "exec $CODESPACES_INSTALL_LOCK_WRITE>&-"
            CODESPACES_INSTALL_LOCK_WRITE=""
        fi
        _codespaces_wait_install_record 10 settled || result=1
        CODESPACES_INSTALL_LOCK_RECORDED=0
    fi
    _codespaces_close_install_channel || result=1
    if [[ -n "${CODESPACES_INSTALL_LOCK_PID:-}" ]]; then
        wait "$CODESPACES_INSTALL_LOCK_PID" || result=1
        CODESPACES_INSTALL_LOCK_PID=""
    fi
    return "$result"
}
