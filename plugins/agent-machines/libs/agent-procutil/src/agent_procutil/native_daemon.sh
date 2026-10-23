#!/usr/bin/env bash
# Native equivalent of daemon.py's POSIX new-session/contained launch contract.

__native_daemon_source="$(readlink -e -- "${BASH_SOURCE[0]}")"

start_native_daemon() {
    local script="$1" working="$2" log="$3" channel frame code=0
    shift 3
    [[ "$(uname -s)" == Linux && "$script" == /* && -f "$script" &&
        "$working" == /* && -d "$working" && "$(readlink -e -- "$working")" == "$working" &&
        "$log" == "$working/"* && "$-" != *m* && ! -e /proc/self/fd/7 ]] || {
        printf '%s\n' 'Native daemon requires normalized Linux paths and a free admission descriptor.' >&2
        return 1
    }
    channel="$(umask 077; mktemp -d -- "$working/.native-daemon-XXXXXXXX")" || return 1
    (umask 077; mkfifo -- "$channel/ready") || { rmdir -- "$channel"; return 1; }
    exec 7<>"$channel/ready" || return 1
    if [[ "${COPILOT_EXTENSIONS_TEST_CONTAINED:-}" == 1 ]]; then
        "$BASH" "$__native_daemon_source" --child "$script" "$working" "$@" \
            </dev/null >>"$log" 2>&1 &
    else
        setsid -- "$BASH" "$__native_daemon_source" --child "$script" "$working" "$@" \
            </dev/null >>"$log" 2>&1 &
    fi
    __native_daemon_pid=$!
    if ! IFS= read -r -t 15 -u 7 frame || [[ "$frame" != "ready:$__native_daemon_pid" ]]; then
        printf '%s\n' 'Native daemon admission failed; consult its diagnostic log.' >&2
        code=1
    fi
    exec 7>&-
    rm -- "$channel/ready" || code=1
    rmdir -- "$channel" || code=1
    return "$code"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    [[ "${1:-}" == --child && "$#" -ge 3 ]] || exit 2
    script="$2"
    working="$3"
    shift 3
    # Match close_fds=True without passing a caller's lock or lifetime writer.
    for entry in /proc/self/fd/*; do
        fd="${entry##*/}"
        if [[ "$fd" -gt 2 && "$fd" != 7 && "$fd" != 255 ]]; then
            exec {fd}>&-
        fi
    done
    if [[ "${COPILOT_EXTENSIONS_TEST_CONTAINED:-}" != 1 ]]; then
        IFS= read -r identity <"/proc/$$/stat"
        read -r _state _parent group session _rest <<<"${identity##*) }"
        [[ "$group" == "$$" && "$session" == "$$" ]] || exit 1
    fi
    cd -- "$working" || exit 1
    trap '' HUP
    printf 'ready:%s\n' "$$" >&7
    exec 7>&-
    exec "$BASH" "$script" "$@"
fi
