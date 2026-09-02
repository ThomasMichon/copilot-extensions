#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"
: "${CR_SCENARIO_NAME:=context-handoff-eval}"
export CR_SCENARIO_NAME
ROOT="/home/operator/context-handoff-eval"

cr_init 2>/dev/null || true
phase 9 "post-check handoff efficiency, fidelity, and lifecycle"

WT_ID="$(cat "$ROOT/worktree-id" 2>/dev/null)"
capture "handoff-head" -- agent-worktrees head-session \
  --worktree "$WT_ID" --json || true
if capture "handoff-metrics" -- python3 "$_SELF_DIR/fixture.py" metrics \
  --root "$ROOT" --results /home/operator/out; then
    pass "handoff efficiency, fidelity, and lifecycle metrics passed"
    cr_meta "handoff_metrics" "context-handoff-eval-metrics.json"
else
    fail "handoff efficiency/fidelity/lifecycle metrics failed"
    jam "session-lifecycle" \
      "see context-handoff-eval-metrics.json and eval/transcript.txt" \
      "the successor must explicitly consume before takeover and preserve unverified predecessors"
fi

cr_finalize
