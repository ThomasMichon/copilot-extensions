#!/usr/bin/env bash
# Linux native admission continues in the same process after private Python exists.
set -euo pipefail
[[ "$#" == 2 && "$(uname -s)" == Linux ]] || exit 2
root="$1"
digest="$2"
plugin="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$plugin/src/update_driver/foundation.sh"
source "${plugin%/*}/single-instance-lease/src/single_instance_lease/operation.sh"
source "${plugin%/*}/agent-procutil/src/agent_procutil/native_owned.sh"
unset UPDATE_DRIVER_WITNESS
[[ "$digest" =~ ^[0-9a-f]{64}$ && "$root" == /* &&
    "$(readlink -m -- "$root")" == "$root" ]] || exit 2
_native_foundation_outside_sources "$root" "$plugin"
command -v ps >/dev/null ||
    { _native_foundation_error 'construction requires native ps observation'; exit 1; }
mkdir -p -m 700 -- "$root"
check_native_foundation_execution_root "$root" "$plugin"
deadline=$((SECONDS + 600))
while true; do
    if enter_native_operation_lock "$root/execution.lock"; then break; else code=$?; fi
    [[ "$code" == 75 && "$SECONDS" -lt "$deadline" ]] || exit "$code"
    sleep .1
done
[[ "$(native_foundation_source_digest "$plugin")" == "$digest" ]] ||
    { _native_foundation_error 'source differs from requested inventory'; exit 1; }
reconcile_native_foundation_progress "$root"
selector="$root/foundation/current-version"
queue_aware=0
for path in "$selector" "$root/queue.sqlite3" "$root/queue.sqlite3-wal" \
    "$root/queue.sqlite3-shm" "$root/queue.sqlite3-journal"; do
    if [[ -e "$path" || -L "$path" ]]; then queue_aware=1; fi
done
if [[ "$queue_aware" == 1 ]]; then
    _native_foundation_open "$selector"
    [[ "$(stat -Lc '%s' "/proc/self/fd/$__foundation_fd")" -le 128 ]] || exit 1
    selected="$(cat <&"$__foundation_fd")"
    exec {__foundation_fd}<&-
    [[ "$selected" =~ ^foundation-[0-9a-f]{64}$ ]] ||
        { _native_foundation_error 'malformed foundation selector'; exit 1; }
    slot="$root/foundation/versions/$selected"
    _native_foundation_plain "$slot/.runtime-bootstrap/runtime_launcher.py" file
    _native_foundation_plain "$slot/.install-complete.json" file
    # The retained module owns queue-aware admission, not new source imports.
    exec "$slot/bin/python" -I -S -B -X utf8 "$slot/.runtime-bootstrap/runtime_launcher.py" \
        -m update_driver.foundation_linux --root "$root" --source "$plugin" --source-hash "$digest"
fi
start_native_foundation_progress "$root" "$plugin" "$digest"
operation="$(_native_foundation_encode)"
bootstrap="$root/bootstrap/${__foundation_value[operation_id]}"
mkdir -p -m 700 -- "$root/bootstrap"
mkdir -m 700 -- "$bootstrap"
frozen="$(freeze_native_foundation_source "$root" "$plugin" "$bootstrap/inputs" "$digest")"
interrupt() {
    local code=$?
    trap - EXIT
    # No native integer outcome certifies descendant settlement.
    complete_native_foundation_progress "$root" "$operation" "$plugin" null native_handoff_failed || :
    exit "$code"
}
trap interrupt EXIT
start_native_owned_process "$bootstrap" "$bootstrap/prerequisites.log" 300 \
    "$BASH" "$frozen/scripts/foundation-prerequisites.sh" "$bootstrap"
wait_native_owned_process 290
python="$bootstrap/venv/bin/python"
[[ -x "$python" ]] ||
    { _native_foundation_error 'private interpreter unavailable; settlement required'; exit 1; }
exec "$python" -I -B -X utf8 "$frozen/scripts/foundation-linux.py" \
    --root "$root" --source "$plugin" --source-hash "$digest" \
    --child "$__native_owned_pid" --child-code "$__native_owned_code"
