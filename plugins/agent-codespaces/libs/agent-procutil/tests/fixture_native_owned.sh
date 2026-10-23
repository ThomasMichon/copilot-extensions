#!/usr/bin/env bash
set -eu
if [[ "$1" == --prerequisite ]]; then
    area="$2"
    sleep 300 &
    printf '%s\n' "$!" >"$area/descendant"
    [[ "$3" != blocked ]] || wait
    exit 17
fi
helper="$1"
python="$2"
package="$3"
area="$4"
mode="$5"
source "$helper"
start_native_owned_process "$area" "$area/acquisition.log" 30 \
    "$BASH" "$0" --prerequisite "$area" "$mode"
printf '%s\n' "$__native_owned_pid" >"$area/leader"
if [[ "$mode" == blocked ]]; then
    wait_native_owned_process 25
else
    wait_native_owned_process 25
    [[ "$__native_owned_code" == 17 ]]
    exec "$python" -I -S -u "${0%/*}/fixture_native_child.py" "$package" "$__native_owned_pid" "$area"
fi
