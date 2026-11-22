#!/usr/bin/env bash
set -euo pipefail
source "$1"
lock="$2"
mode="$3"
enter_native_operation_lock "$lock"
printf ready > "${lock%/*}/native-ready"
if [[ "$mode" == cleanup ]]; then
    mv -- "$lock" "$lock.old"
    : > "$lock"
    if leave_native_operation_lock "$lock"; then exit 2; fi
    if [[ -L /proc/self/fd/9 ]]; then exit 3; fi
    enter_native_operation_lock "$lock"
    leave_native_operation_lock "$lock"
    exit 0
fi
if [[ "$mode" == foreign ]]; then
    exec 8>&9
    exec 9>&-
    exec 9<>"$lock.foreign"
    if leave_native_operation_lock "$lock"; then exit 2; fi
    if [[ ! -L /proc/self/fd/9 ]]; then exit 3; fi
    exec 9>&-
    exec 9>&8
    exec 8>&-
    leave_native_operation_lock "$lock"
    exit 0
fi
if [[ "$mode" == handoff ]]; then
    exec "$4" "$5" "$lock"
fi
IFS= read -r _answer
assert_native_operation_lock "$lock"
leave_native_operation_lock "$lock"
