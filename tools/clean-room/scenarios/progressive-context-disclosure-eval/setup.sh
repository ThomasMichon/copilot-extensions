#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=progressive-context-disclosure-eval}"
export CR_SCENARIO_NAME
SOURCE="${CR_HARNESS_MOUNT:-/harness}"
FIXTURE="$SOURCE/tools/clean-room/scenarios/progressive-context-disclosure-baseline/fixture.py"
ROOT="/home/operator/progressive-context-disclosure-eval"
MANIFEST="/home/operator/scenario/manifest.json"
EVIDENCE="/home/operator/out/eval/progressive-context-evidence.json"
INVALID_WRITER="/home/operator/scenario/write-invalid.py"

mapfile -t CELL < <(python3 - "$MANIFEST" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
cell = manifest["experiment"]
for key in (
    "deferralLevel",
    "referenceRepresentation",
    "emphasis",
    "assembly",
    "taskId",
    "model",
    "repetition",
):
    print(cell[key])
PY
)

write_invalid() {
    local jam_name="$1"
    if [ -f "$INVALID_WRITER" ] && [ "${#CELL[@]}" -eq 7 ]; then
        mkdir -p "$(dirname "$EVIDENCE")"
        if ! python3 "$INVALID_WRITER" \
            --manifest "$MANIFEST" \
            --output "$EVIDENCE" \
            --jam "$jam_name" \
            >>"$CR_LOGDIR/invalid-evidence.log" 2>&1; then
            fail "zero-turn INVALID evidence could not be written"
        fi
    fi
}

cr_init
cr_meta "role" "progressive-context-tier-e"
if [ "${#CELL[@]}" -eq 7 ]; then
    cr_meta "variant" "${CELL[0]}/${CELL[1]}/${CELL[2]}/${CELL[3]}"
    cr_meta "task" "${CELL[4]}"
    cr_meta "model" "${CELL[5]}"
    cr_meta "repetition" "${CELL[6]}"
fi

phase 0 "materialize one isolated progressive-context cell"
if [ ! -f "$FIXTURE" ]; then
    write_invalid "repo-config"
    jam "repo-config" "the frozen source fixture is not mounted at /harness" \
        "run the eval with -HarnessMount or --harness-mount pointing at this checkout"
elif [ "${#CELL[@]}" -ne 7 ]; then
    write_invalid "scenario-fixture"
    jam "scenario-fixture" "manifest experiment coordinates are incomplete" \
        "generate or repair the Tier-E scenario manifest before running"
elif capture "materialize-cell" -- \
    python3 "$FIXTURE" materialize \
        --root "$ROOT" \
        --source "$SOURCE" \
        --deferral-level "${CELL[0]}" \
        --reference-representation "${CELL[1]}" \
        --emphasis "${CELL[2]}" \
        --assembly "${CELL[3]}" \
        --task-id "${CELL[4]}" \
        --model "${CELL[5]}" \
        --repetition "${CELL[6]}" \
        --venue acp; then
    pass "fresh context, guides, random canaries, and synthetic hook payload materialized outside the fixture mount"
else
    write_invalid "scenario-fixture"
    jam "scenario-fixture" "progressive-context cell materialization failed" \
        "repair the deterministic renderer or selected manifest coordinates before spending an eval"
fi

phase 1 "verify the generated cell and trust only its synthetic repository"
if [ -f "$ROOT/run-metadata.json" ] && capture "verify-cell" -- \
    python3 "$FIXTURE" verify-materialized --root "$ROOT"; then
    mkdir -p "$HOME/.copilot"
    cp "$ROOT/copilot-config.json" "$HOME/.copilot/config.json"
    pass "generated context metrics, guide canaries, repository, payload, and ACP cwd are coherent"
else
    write_invalid "scenario-fixture"
    jam "scenario-fixture" "generated progressive-context cell failed verification" \
        "do not drive the Tier-E session until the materialized cell passes its Tier-P gate"
fi

cr_finalize
