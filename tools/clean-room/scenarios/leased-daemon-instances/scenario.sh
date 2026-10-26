#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${CR_SCENARIO_NAME:=leased-daemon-instances}"
export CR_SCENARIO_NAME
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
cr_init
cr_meta "validates" "attributed-daemon-instances"

SOURCE_ROOT="${CR_DAEMON_SOURCE_ROOT:-${CR_HARNESS_MOUNT:-/harness}}"
WORK_ROOT="$HOME/leased-daemon-proof"

phase 0 "fresh source-only environment"
envdump
if [ ! -f "$SOURCE_ROOT/libs/agent-procutil/src/agent_procutil/daemon_instances.py" ]; then
    jam "runtime-install" "candidate shared instance implementation is not mounted" \
        "mount the candidate checkout read-only with -HarnessMount"
    cr_finalize
    exit 1
fi
if [ -e "$WORK_ROOT" ]; then
    jam "repo-config" "the proof workspace already exists" "use a fresh scenario container"
    cr_finalize
    exit 1
fi
pass "fresh workspace; candidate source is available without host runtime state"

phase 1 "real instance lifetime and installer cleanup"
PROBE_ARGS=()
if [ -n "${CR_DAEMON_NATIVE_HELPER_ROOT:-}" ]; then
    PROBE_ARGS+=(--native-helper-root "$CR_DAEMON_NATIVE_HELPER_ROOT")
fi
if [ "${CR_DAEMON_SOURCE_BUILD:-0}" = "1" ]; then
    PROBE_ARGS+=(--build-native-helper)
fi
capture "daemon-instance-probe" -- python3 "$_SELF_DIR/fixtures/probe.py" \
    --source "$SOURCE_ROOT" --work-root "$WORK_ROOT" "${PROBE_ARGS[@]}"
_rc=$?
while IFS= read -r line; do
    case "$line" in
        "PROBE: "*" PASS "*) pass "${line#PROBE: }" ;;
        "PROBE: "*" FAIL "*)
            jam "daemon-instance-lifecycle" "${line#PROBE: }" \
                "repair the shared lifetime or installer contract; do not bypass ownership checks"
            ;;
    esac
done < "$CR_LOGDIR/daemon-instance-probe.log"
if [ "$_rc" -ne 0 ]; then
    jam "daemon-instance-lifecycle" "the real process probe exited $_rc" \
        "see cr-logs/daemon-instance-probe.log"
fi
cr_finalize
exit "$_rc"
