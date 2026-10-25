#!/usr/bin/env bash
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$SELF_DIR/../../lib/clean-room-lib.sh}"
export CR_SCENARIO_NAME=manager-automatic-updates
cr_init

phase 0 "verify declared self-contained candidate"
if [ ! -f "$SELF_DIR/prepared.json" ] || [ ! -f "$SELF_DIR/payload/.install-complete" ]; then
    jam "repo-config" "Candidate payload is absent; this scenario never binds the host checkout" \
        "Run probe.py prepare with the candidate source and pass its output directory as --scenario."
    cr_finalize
fi
if [ -e "$HOME/manager-worker-qualification" ]; then
    jam "repo-config" "Qualification scratch is not fresh" "Use a new disposable container."
    cr_finalize
fi
pass "Declared candidate payload is self-contained"
cr_meta "cycle_kind" "finite-guarded-command-fixture"
cr_meta "real_installation_cycles" "false"
cr_meta "native_startup" "false"
phase 1 "qualify actual worker processes and canonical custody"
if ! capture "worker-qualification" -- python3 -I -B "$SELF_DIR/probe.py" qualify \
    --source "$SELF_DIR/payload" --workdir "$HOME/manager-worker-qualification" \
    --cycles "${CR_MANAGER_CYCLES:-1}"; then
    jam "runtime-install" "Manager worker qualification failed; see worker-qualification.log" \
        "Inspect retained fixture logs and cleanup state; do not replace the production worker with mocks."
    cr_finalize
fi
pass "Actual authenticated workers enforce leases, drain and hand over"
phase 2 "verify cleanup and state exact evidence boundaries"
if ! capture "qualification-report" -- python3 - "$HOME/manager-worker-qualification/qualification.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    report = json.load(stream)
assert report["passed"] is True
assert report["real_installation_cycles"] is False
for name in ("handover", "failed_successor"):
    assert report[name]["cleanup_verified"] is True
print(json.dumps(report, sort_keys=True))
PY
then
    jam "runtime-install" "The qualification report did not prove successful cleanup"
    cr_finalize
fi
pass "Fixture workers exited and released canonical custody"
info "Finite guarded commands do not prove actual software updates, native startup, Windows windows/focus or Job lifetime"
cr_finalize
