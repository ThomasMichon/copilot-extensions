#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=progressive-context-disclosure-eval}"
export CR_SCENARIO_NAME
SOURCE="${CR_HARNESS_MOUNT:-/harness}"
FIXTURE="$SOURCE/tools/clean-room/scenarios/progressive-context-disclosure-baseline/fixture.py"
ROOT="/home/operator/progressive-context-disclosure-eval"
RESULTS="/home/operator/out"
EVIDENCE="$RESULTS/eval/progressive-context-evidence.json"

cr_init 2>/dev/null || true
phase 9 "record counts-only transcript observation"

if [ ! -f "$ROOT/run-metadata.json" ]; then
    fail "the generated experiment cell is unavailable"
    jam "scenario-fixture" "run metadata is missing after setup" \
        "rerun the cell after its setup fixture is repaired"
elif capture "observe-cell" -- \
    python3 "$FIXTURE" observe --root "$ROOT" --results "$RESULTS"; then
    observation_values="$(
        python3 - "$RESULTS/eval/progressive-context-observation.json" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
count = value["transcriptCount"]
timed_out = value["timedOut"]
fallbacks = value["modelFallbackMarkerCount"]
exit_code = value["driverExitCode"]
session_resolution = value["sessionResolution"]
requested = value["requestedModel"]
actual = value["actualModel"]
if (
    not isinstance(count, int)
    or not isinstance(timed_out, bool)
    or not isinstance(fallbacks, int)
    or not isinstance(exit_code, int)
    or not isinstance(session_resolution, str)
    or not isinstance(requested, str)
    or not isinstance(actual, str)
):
    raise SystemExit(1)
print(
    f"{count}\t{str(timed_out).lower()}\t{fallbacks}\t"
    f"{exit_code}\t{session_resolution}\t{requested}\t{actual}"
)
PY
    )"
    extraction_rc=$?
    if [ "$extraction_rc" -ne 0 ]; then
        transcript_count=0
        timed_out=true
        model_fallbacks=0
        driver_exit=127
        session_resolution="resolver-failed"
        requested_model=""
        actual_model=""
    else
        IFS=$'\t' read -r \
            transcript_count timed_out model_fallbacks driver_exit \
            session_resolution requested_model actual_model \
            <<<"$observation_values"
    fi
    if [ "$transcript_count" -eq 0 ] || [ "$timed_out" = true ] ||
        [ "$model_fallbacks" -gt 0 ] || [ "$driver_exit" -ne 0 ] ||
        [ "$session_resolution" != resolved ] ||
        [ -z "$actual_model" ] ||
        { [ "$requested_model" != auto ] &&
          [ "$actual_model" != "$requested_model" ]; }; then
        python3 "$FIXTURE" write-evidence \
            --root "$ROOT" \
            --results "$RESULTS" \
            --output "$EVIDENCE" \
            --verdict INVALID \
            --jam scenario-transport-gap >/dev/null
        fail "the driven session did not produce a valid requested-model turn"
        jam "scenario-transport-gap" "the transcript is missing, timed out, or records a model fallback" \
            "repair the ACP transport/model selection and rerun this cell; INVALID does not eliminate the variant"
    else
        pass "counts-only guide observations are ready for the independent literal-mode judge"
        cr_meta "tier_e_verdict" "PENDING-JUDGE"
    fi
else
    python3 "$FIXTURE" write-evidence \
        --root "$ROOT" \
        --results "$RESULTS" \
        --output "$EVIDENCE" \
        --verdict INVALID \
        --jam scenario-transport-gap >/dev/null 2>&1 || true
    fail "counts-only transcript observation failed"
    jam "scenario-transport-gap" "the transcript could not be reduced to observation evidence" \
        "repair the scenario transport or evidence writer and rerun the cell"
fi

cr_finalize
