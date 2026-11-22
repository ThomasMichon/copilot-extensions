#!/usr/bin/env bash
# Admission acknowledgement is not a completion receipt; observe canonical progress.
set -euo pipefail
[[ "$#" == 2 && "$(uname -s)" == Linux ]] || exit 2
root="$1"
digest="$2"
plugin="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$plugin/src/update_driver/foundation-source.sh"
source "${plugin%/*}/agent-procutil/src/agent_procutil/native_daemon.sh"
[[ "$digest" =~ ^[0-9a-f]{64}$ && "$root" == /* &&
    "$(readlink -m -- "$root")" == "$root" ]] || exit 2
_native_foundation_outside_sources "$root" "$plugin"
mkdir -p -m 700 -- "$root"
check_native_foundation_execution_root "$root" "$plugin"
unset UPDATE_DRIVER_WITNESS
start_native_daemon "$plugin/scripts/foundation-produce.sh" "$root" \
    "$root/foundation-producer-$(cat /proc/sys/kernel/random/uuid).log" "$root" "$digest"
printf 'launched:%s\n' "$__native_daemon_pid"
