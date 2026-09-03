#!/usr/bin/env bash
set -euo pipefail

: "${CR_SCENARIO_NAME:=agent-index-installation-cells}"
export CR_SCENARIO_NAME
_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

cr_init
phase 0 "source and clean fixture boundary"
envdump
CR_PARTNER_PATH="${CR_PARTNER_PATH:-${CR_HARNESS_MOUNT:-}}"
export CR_PARTNER_PATH
if [[ -z "${CR_PARTNER_PATH:-}" || ! -d "$CR_PARTNER_PATH" ]]; then
    jam "repo-config" "no mounted source tree is available" "pass -HarnessMount on Linux or -PartnerPath on Windows"
    cr_finalize
fi
driver="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/scenario.py"
if [[ ! -f "$driver" ]]; then
    jam "drop-structural" "scenario driver is absent" "restore scenario.py"
    cr_finalize
fi
pass "mounted source and portable scenario driver are present"

if ! selected_build_mode="$(python3 -X utf8 "$driver" build-mode)"; then
    jam "repo-config" "Agent Index build mode is invalid" "set CR_AGENT_INDEX_BUILD_MODE to full or smoke"
    cr_finalize
fi
export CR_AGENT_INDEX_BUILD_MODE="$selected_build_mode"
cr_meta "build_mode" "$selected_build_mode"
if [[ "$selected_build_mode" == "smoke" ]]; then
    cr_meta "acceptance_mode" "diagnostic-only"
else
    cr_meta "acceptance_mode" "full"
fi

cleanup_done=0
cleanup_scenario() {
    if [[ "$cleanup_done" -eq 1 ]]; then
        return 0
    fi
    python3 -X utf8 "$driver" cleanup
    cleanup_done=1
}
on_exit() {
    rc=$?
    trap - EXIT
    if ! cleanup_scenario; then
        printf '%s\n' 'agent-index installation-cell cleanup failed' >&2
        exit 1
    fi
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

for stage in 1 2 3 4 5; do
    case "$stage" in
        1) title="default and false policy remain legacy and non-activating" ;;
        2) title="two active Agent Index cells provision and serve independently" ;;
        3) title="cell A updates and cuts over while cell B is unchanged" ;;
        4) title="current management rolls back and recovers cutover crashes with one owned PID" ;;
        5) title="public deploy plus foreign and cross-cell control fail closed; shutdown is isolated" ;;
    esac
    phase "$stage" "$title"
    if capture "stage-$stage" -- python3 -X utf8 "$driver" "$stage"; then
        pass "$title"
    else
        if grep -Eqi 'HandshakeFailure|Failed to fetch|No solution found|No matching distribution|certificate|TLS|SSL|package index' "$CR_LOGDIR/stage-$stage.log"; then
            jam "toolchain-uv" "stage $stage could not resolve the lightweight Python dependencies; see cr-logs/stage-$stage.log" "pass -UvIndex with an available package index or use a base interpreter that supports CR_AGENT_INDEX_BUILD_MODE=smoke"
        else
            jam "install-contract" "stage $stage failed; see cr-logs/stage-$stage.log" "inspect the deterministic dual-cell lifecycle evidence"
        fi
        break
    fi
done

if capture "cleanup" -- python3 -X utf8 "$driver" cleanup; then
    cleanup_done=1
    pass "all recorded Agent Index services stopped gracefully"
else
    jam "install-contract" "ownership-checked Agent Index cleanup failed; see cr-logs/cleanup.log" "inspect the recorded PID and endpoint evidence"
fi
trap - EXIT
if [[ "$selected_build_mode" == "smoke" ]]; then
    jam "repo-config" "smoke mode completed as a diagnostic and is not an acceptance result" "rerun with CR_AGENT_INDEX_BUILD_MODE=full"
fi
cr_finalize
