#!/usr/bin/env bash
set -euo pipefail

: "${CR_SCENARIO_NAME:=agent-machines-installation-cells}"
export CR_SCENARIO_NAME
source "$CR_LIB"

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
pass "mounted source and scenario driver are present"

for stage in 1 2 3 4 5; do
    case "$stage" in
        1) title="operative eligibility and full cell-provision locking hold" ;;
        2) title="two active Agent Machines cells install and run independently" ;;
        3) title="one cell updates without changing its peer" ;;
        4) title="historical owned slot rolls back without changing its peer" ;;
        5) title="blocked governance states fail closed without legacy fallback" ;;
    esac
    phase "$stage" "$title"
    if capture "stage-$stage" -- python3 "$driver" "$stage"; then
        pass "$title"
    else
        if grep -Eq 'HandshakeFailure|Failed to fetch|No solution found.*pyyaml' "$CR_LOGDIR/stage-$stage.log"; then
            jam "toolchain-uv" "stage $stage could not resolve Python dependencies; see cr-logs/stage-$stage.log" "pass -UvIndex with an available package index"
        else
            jam "install-contract" "stage $stage failed; see cr-logs/stage-$stage.log" "inspect the deterministic lifecycle evidence"
        fi
        break
    fi
done

cr_finalize
