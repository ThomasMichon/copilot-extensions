#!/bin/bash
# Finite pre-Python capture for Linux and macOS, including Bash 3.2.

__native_capture_source="${BASH_SOURCE[0]}"

_native_capture_settle() {
    local snapshot group state rest empty=0 deadline=$((SECONDS + 5))
    printf '%s\n' "${1:-cancel}" > "$__native_capture_directory/released"
    exec 8>&-
    # The live in-group guardian signals itself, never a saved numeric PID.
    while (( SECONDS < deadline && empty < 2 )); do
        snapshot="$(LC_ALL=C ps -ax -o pgid= -o stat=)" || return 1
        empty=$((empty + 1))
        while read -r group state rest; do
            if [[ "$group" == "$__native_capture_pid" && "$state" != Z* &&
                  "$state" != X* ]]; then
                empty=0
                break
            fi
        done <<< "$snapshot"
        (( empty == 2 )) || sleep .02 || return 1
    done
    (( empty == 2 )) || return 1
    wait "$__native_capture_pid" 2>/dev/null || :
}

_native_capture_cancel() {
    local code="$1"
    trap '' HUP INT TERM
    trap - EXIT
    exec 7>&-
    if _native_capture_settle 2>/dev/null; then
        rm -- "$__native_capture_directory/output" "$__native_capture_directory/lifetime" \
            "$__native_capture_directory/ready" "$__native_capture_directory/released"
        rmdir -- "$__native_capture_directory"
    else
        printf 'Native capture cleanup is unconfirmed: %s\n' "$__native_capture_directory" >&2
    fi
    exit "$code"
}

capture_native_process() {
    local timeout="$1" fd frame code=1 settlement=cancel
    shift
    [[ "$timeout" =~ ^[1-9][0-9]*$ && "$#" -gt 0 &&
        "$__native_capture_source" == /* && "$-" != *m* &&
        -z "$(trap -p EXIT HUP INT TERM)" ]] || return 1
    for fd in 5 6 7 8; do
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
    if ! exec 8<>"$__native_capture_directory/lifetime" 7<>"$__native_capture_directory/ready" \
        6<"$__native_capture_directory/lifetime" 5>"$__native_capture_directory/ready"; then
        exec 5>&- 6<&- 7>&- 8>&-
        rm -- "$__native_capture_directory/lifetime" "$__native_capture_directory/ready"
        rmdir -- "$__native_capture_directory"
        return 1
    fi
    # Monitor mode creates a private group on both platforms without setsid.
    set -m
    "$BASH" "$__native_capture_source" --child "$timeout" "$__native_capture_directory" "$@" \
        </dev/null >"$__native_capture_directory/output" 7>&- 8>&- &
    __native_capture_pid=$!
    set +m
    exec 5>&- 6<&-
    trap '_native_capture_cancel $?' EXIT
    trap '_native_capture_cancel 129' HUP
    trap '_native_capture_cancel 130' INT
    trap '_native_capture_cancel 143' TERM
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
        trap - EXIT HUP INT TERM
        return 125
    fi
    __native_capture_output="$(cat -- "$__native_capture_directory/output")"
    rm -- "$__native_capture_directory/output" "$__native_capture_directory/lifetime" \
        "$__native_capture_directory/ready" "$__native_capture_directory/released"
    rmdir -- "$__native_capture_directory"
    trap - EXIT HUP INT TERM
    return "$code"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    [[ "${1:-}" == --child && "$#" -ge 4 ]] || exit 2
    timeout="$2"
    directory="$3"
    shift 3
    set +m
    group="$(LC_ALL=C ps -p "$$" -o pgid=)" || exit 1
    [[ "${group//[[:space:]]/}" == "$$" ]] || {
        printf '%s\n' 'Native capture did not acquire its private process group.' >&2
        exit 1
    }
    trap '' HUP INT TERM
    (
        IFS= read -r -t "$timeout" -u 6 frame
        read_status=$?
        if [[ "$read_status" == 1 && ! -e "$directory/released" ]]; then
            rm -- "$directory/output" "$directory/lifetime" "$directory/ready"
            rmdir -- "$directory"
        fi
        if [[ -f "$directory/released" ]]; then
            IFS= read -r disposition < "$directory/released"
            [[ "$disposition" != complete ]] || kill -s KILL 0
        fi
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
