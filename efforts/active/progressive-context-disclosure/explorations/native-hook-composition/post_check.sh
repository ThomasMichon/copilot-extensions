#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=native-hook-composition-eval}"
export CR_SCENARIO_NAME
ROOT="/home/operator/native-hook-composition-eval"

cr_init 2>/dev/null || true
phase 9 "post-check model-visible canary composition"

if capture "context-evidence" -- \
    python3 "$_SELF_DIR/fixture.py" post-check \
        --root "$ROOT" --results /home/operator/out; then
    pass "every transcript contains both independent canaries with no invented token"
    cr_meta "tier_e_verdict" "PASS"
else
    fail "model-visible context composition was not established for both hooks"
    failure_class="$(
        python3 "$_SELF_DIR/fixture.py" failure-class \
            --results /home/operator/out
    )"
    jam "$failure_class" \
        "see eval/context-evidence.json and eval transcripts" \
        "classify native composition separately from literal response formatting"
    cr_meta "tier_e_verdict" "FAIL"
fi

cr_finalize
