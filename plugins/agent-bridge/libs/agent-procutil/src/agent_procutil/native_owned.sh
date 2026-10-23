#!/usr/bin/env bash
# Keep a native session child alive until its parent execs a custody-aware owner.

__native_owned_source="$(readlink -e -- "${BASH_SOURCE[0]}")"

start_native_owned_process() {
    local directory="$1" log="$2" timeout="$3" frame
    shift 3
    [[ "$(uname -s)" == Linux && "$directory" == /* && -d "$directory" &&
        "$(readlink -e -- "$directory")" == "$directory" && "$log" == "$directory/"* &&
        "$timeout" =~ ^[1-9][0-9]*$ && "$#" -gt 0 ]] || {
        printf '%s\n' 'Native child requires a private Linux directory and bounded timeout.' >&2
        return 1
    }
    for frame in 5 6 7 8; do
        [[ ! -e "/proc/self/fd/$frame" ]] || {
            printf '%s\n' 'Native child handoff descriptors are already occupied.' >&2
            return 1
        }
    done
    (umask 077; mkfifo -- "$directory/lifetime" "$directory/readiness") || return 1
    exec 8<>"$directory/lifetime" 7<>"$directory/readiness" \
        6<"$directory/lifetime" 5>"$directory/readiness" || return 1
    # Non-interactive Bash must leave job control off so setsid execs, not forks.
    if [[ "$-" == *m* ]]; then
        printf '%s\n' 'Native child handoff refuses shell job control.' >&2
        exec 5>&- 6<&- 7>&- 8>&-
        return 1
    fi
    setsid -- "$BASH" "$__native_owned_source" --owned-child "$timeout" "$@" \
        </dev/null >"$log" 2>&1 7>&- 8>&- 9>&- &
    __native_owned_pid=$!
    exec 5>&- 6<&-
    if ! IFS= read -r -t 15 -u 7 frame ||
        [[ "$frame" != "started:$__native_owned_pid" ]]; then
        printf '%s\n' 'Native child did not acknowledge its direct session identity.' >&2
        exec 7>&- 8>&-
        return 1
    fi
}

wait_native_owned_process() {
    local timeout="$1" frame
    if ! IFS= read -r -t "$timeout" -u 7 frame ||
        [[ ! "$frame" =~ ^finished:([0-9]{1,3})$ ]] ||
        [[ "${BASH_REMATCH[1]}" -gt 255 ]]; then
        printf '%s\n' 'Native child completion is unavailable; settlement remains uncertain.' >&2
        return 1
    fi
    __native_owned_code="${BASH_REMATCH[1]}"
    exec 7>&-
    # FD 8 remains open across exec. No descendant inherits its lifetime writer.
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    [[ "${1:-}" == --owned-child && "$#" -ge 3 ]] || exit 2
    timeout="$2"
    shift 2
    [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || exit 2
    IFS= read -r identity <"/proc/$$/stat"
    read -r _state _parent group session _rest <<<"${identity##*) }"
    [[ "$group" == "$$" && "$session" == "$$" ]] || exit 1
    (
        trap '' TERM HUP
        # EOF means the actual parent (including an exec successor) lost custody.
        IFS= read -r -t "$timeout" -u 6 || :
        kill -TERM -- "-$$"
        sleep 1
        kill -KILL -- "-$$"
    ) 5>&- &
    printf 'started:%s\n' "$BASHPID" >&5
    "$@" 5>&- 6<&-
    code=$?
    printf 'finished:%s\n' "$code" >&5
    # Bash must not reap this leader before the exec successor adopts it.
    IFS= read -r -u 6 || :
    wait
fi
