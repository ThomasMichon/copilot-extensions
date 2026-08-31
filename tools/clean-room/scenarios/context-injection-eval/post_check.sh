#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=context-injection-eval}"
export CR_SCENARIO_NAME
ROOT="/home/operator/context-injection-eval"

cr_init 2>/dev/null || true
phase 9 "post-check model-visible completeness and side-effect evidence"

if capture "context-evidence" -- \
    python3 /home/operator/scenario/fixture.py post-check \
        --root "$ROOT" --results /home/operator/out; then
    pass "every transcript contains the exact injected canary set and no workaround"
    cr_meta "tier_e_verdict" "PASS"
else
    fail "model-visible context completeness was not established"
    failure_class="$(
        python3 /home/operator/scenario/fixture.py failure-class \
            --results /home/operator/out
    )"
    jam "$failure_class" \
        "see eval/context-evidence.json and eval transcripts" \
        "classify context delivery separately from literal response formatting"
    cr_meta "tier_e_verdict" "FAIL"
fi

cr_finalize
