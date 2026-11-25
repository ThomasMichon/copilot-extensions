#!/bin/bash
# Finite pre-Python capture for Linux and macOS, including Bash 3.2.

__native_capture_source="${BASH_SOURCE[0]}"

_native_capture_settle() {
    local probe empty=0 deadline=$((SECONDS + 5))
    printf '%s\n' "${1:-cancel}" >&8
    exec 8>&-
    # The live in-group guardian signals itself, never a saved numeric PID.
    while (( SECONDS < deadline && empty < 2 )); do
        if probe="$(LC_ALL=C builtin kill -0 -- "-$__native_capture_pid" 2>&1)"; then
            empty=0
        elif [[ "$probe" == *"No such process"* ]]; then
            empty=$((empty + 1))
        else
            return 1
        fi
        (( empty == 2 )) || sleep .02 || return 1
    done
    (( empty == 2 )) || return 1
    wait "$__native_capture_pid" 2>/dev/null || :
    [[ ! -e "$__native_capture_directory" && ! -L "$__native_capture_directory" ]]
}

_native_capture_cancel() {
    local code="$1"
    trap '' HUP INT TERM
    trap - EXIT
    exec 7>&-
    if ! _native_capture_settle 2>/dev/null; then
        printf 'Native capture cleanup is unconfirmed: %s\n' "$__native_capture_directory" >&2
    fi
    exec 9<&-
    exit "$code"
}

_native_capture_restore_traps() {
    local signal
    trap - EXIT HUP INT TERM
    # Bash 3.2 nounset treats an empty array as unset.
    for signal in ${__native_capture_ignored_signals[@]+"${__native_capture_ignored_signals[@]}"}; do
        trap '' "$signal"
    done
}

capture_native_process() {
    local timeout="$1" fd frame code=1 settlement=cancel signal disposition
    local -a __native_capture_ignored_signals=()
    shift
    [[ "$timeout" =~ ^[1-9][0-9]*$ && "$#" -gt 0 &&
        "$__native_capture_source" == /* && "$-" != *m* ]] || return 1
    for signal in EXIT HUP INT TERM; do
        disposition="$(trap -p "$signal")"
        [[ -n "$disposition" ]] || continue
        [[ "$disposition" == "trap -- '' $signal" ||
            "$disposition" == "trap -- '' SIG$signal" ]] || return 1
        __native_capture_ignored_signals+=("$signal")
    done
    for fd in 5 6 7 8 9; do
        [[ ! -e "/dev/fd/$fd" ]] || return 1
    done
    __native_capture_output=''
    __native_capture_directory="$(umask 077; mktemp -d "${TMPDIR:-/tmp}/native-capture.XXXXXXXX")" ||
        return 1
    if ! mkfifo -- "$__native_capture_directory/lifetime" "$__native_capture_directory/ready"; then
        rm -f -- "$__native_capture_directory/lifetime" "$__native_capture_directory/ready"
        rmdir -- "$__native_capture_directory"
        return 1
    fi
    if ! { : > "$__native_capture_directory/output" &&
        exec 8<>"$__native_capture_directory/lifetime" 7<>"$__native_capture_directory/ready" \
            6<"$__native_capture_directory/lifetime" 5>"$__native_capture_directory/ready" \
            9<"$__native_capture_directory/output"; }; then
        exec 5>&- 6<&- 7>&- 8>&- 9<&-
        rm -f -- "$__native_capture_directory/output" "$__native_capture_directory/lifetime" \
            "$__native_capture_directory/ready"
        rmdir -- "$__native_capture_directory"
        return 1
    fi
    # Monitor mode creates a private group on both platforms without setsid.
    set -m
    "$BASH" "$__native_capture_source" --child "$timeout" "$__native_capture_directory" "$@" \
        </dev/null >"$__native_capture_directory/output" 7>&- 8>&- 9<&- &
    __native_capture_pid=$!
    set +m
    exec 5>&- 6<&-
    trap '_native_capture_cancel $?' EXIT
    trap '_native_capture_cancel 129' HUP
    trap '_native_capture_cancel 130' INT
    trap '_native_capture_cancel 143' TERM
    for signal in ${__native_capture_ignored_signals[@]+"${__native_capture_ignored_signals[@]}"}; do
        [[ "$signal" == EXIT ]] || trap '' "$signal"
    done
    if IFS= read -r -t "$timeout" -u 7 frame &&
        [[ "$frame" =~ ^finished:([0-9]{1,3})$ && "${BASH_REMATCH[1]}" -le 255 ]]; then
        code="${BASH_REMATCH[1]}"
        settlement=complete
    else
        code=124
        printf '%s\n' 'Native capture exceeded its admission deadline.' >&2
    fi
    exec 7>&-
    if ! _native_capture_settle "$settlement" 2>/dev/null; then
        printf 'Native capture cleanup is unconfirmed: %s\n' "$__native_capture_directory" >&2
        exec 9<&-
        _native_capture_restore_traps
        return 125
    fi
    __native_capture_output="$(cat <&9)"
    exec 9<&-
    _native_capture_restore_traps
    return "$code"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set +e
    [[ "${1:-}" == --child && "$#" -ge 4 ]] || exit 2
    timeout="$2"
    directory="$3"
    shift 3
    set +m
    # A live leader pins its numeric group identity against reuse.
    builtin kill -0 -- "-$$" 2>/dev/null || {
        printf '%s\n' 'Native capture did not acquire its private process group.' >&2
        exit 1
    }
    trap '' HUP INT TERM
    (
        disposition=''
        IFS= read -r -t "$timeout" -u 6 disposition || :
        # The caller's pre-opened FD 9 retains unread stdout after unlink.
        rm -f -- "$directory/output" "$directory/lifetime" "$directory/ready"
        rmdir -- "$directory" || :
        [[ "$disposition" != complete ]] || kill -s KILL 0
        kill -s TERM 0
        sleep 1
        kill -s KILL 0
    ) 5>&- &
    guardian=$!
    (
        trap - HUP INT TERM
        exec "$@"
    ) 5>&- 6<&- &
    command=$!
    wait "$command"
    code=$?
    printf 'finished:%s\n' "$code" >&5
    exec 5>&- 6<&-
    # Keep the original group leader alive until its guardian retires the group.
    wait "$guardian"
fi
