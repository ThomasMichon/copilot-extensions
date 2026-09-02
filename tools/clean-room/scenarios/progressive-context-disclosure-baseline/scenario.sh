#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=progressive-context-disclosure-baseline}"
export CR_SCENARIO_NAME
SOURCE="${CR_HARNESS_MOUNT:-/harness}"

cr_init
cr_meta "role" "progressive-context-tier-p"

phase 0 "validate frozen progressive-disclosure fixture"
if [ ! -d "$SOURCE/plugins" ]; then
    jam "repo-config" "the source checkout is not mounted at /harness" \
        "pass -HarnessMount <copilot-extensions-checkout> or --harness-mount <checkout>"
elif capture "verify-fixture" -- bash -c \
    'python3 "$1" verify --source "$2" &&
     python3 "$1" verify-phase2' \
    progressive-context-verify "$_SELF_DIR/fixture.py" "$SOURCE"; then
    pass "frozen inputs and all deterministic Phase 2 render cells are coherent"
else
    jam "scenario-fixture" "progressive-disclosure fixture validation failed" \
        "repair the frozen fixture before running any behavioral comparison"
fi

cr_finalize
