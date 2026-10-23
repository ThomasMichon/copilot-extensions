#!/usr/bin/env bash
set -euo pipefail
source "$1"
lock="$2"
mode="$3"
enter_native_operation_lock "$lock"
printf ready > "${lock%/*}/native-ready"
if [[ "$mode" == handoff ]]; then
    exec "$4" "$5" "$lock"
fi
IFS= read -r _answer
assert_native_operation_lock "$lock"
leave_native_operation_lock "$lock"
