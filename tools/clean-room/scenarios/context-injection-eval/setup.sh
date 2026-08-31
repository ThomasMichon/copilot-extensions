#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=context-injection-eval}"
export CR_SCENARIO_NAME
ROOT="/home/operator/context-injection-eval"
VARIANT="${CR_CONTEXT_VARIANT:-A}"
SOURCE="${CR_HARNESS_MOUNT:-/harness}"

cr_init
cr_meta "role" "starting-state-and-tier-p"
cr_meta "variant" "$VARIANT"

phase 0 "prepare synthetic local marketplace"
if capture "prepare-fixture" -- \
    python3 /home/operator/scenario/fixture.py prepare \
        --root "$ROOT" --source "$SOURCE" --variant "$VARIANT"; then
    pass "synthetic marketplace and two disposable repositories prepared"
else
    jam "scenario-transport-gap" "fixture preparation failed" \
        "mount the unpublished source checkout with -HarnessMount"
fi

phase 1 "install unpublished authority through the supported local marketplace"
MARKETPLACE="$ROOT/marketplace"
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE" || true
install_failed=0
for plugin in \
    context-injection \
    synthetic-a-alpha synthetic-a-beta \
    synthetic-b-alpha synthetic-b-beta \
    synthetic-side-effect; do
    if ! capture "install-$plugin" -- \
        copilot plugin install "$plugin@copilot-extensions"; then
        install_failed=1
    fi
done
if [ "$install_failed" = 0 ]; then
    python3 /home/operator/scenario/fixture.py activate --root "$ROOT"
    pass "authority and synthetic plugins installed through Copilot CLI"
else
    jam "scenario-transport-gap" "the CLI could not install the local unpublished marketplace" \
        "the current CLI/runtime cannot exercise this prototype through its supported marketplace path"
fi

phase 2 "run deterministic broker permutations"
if capture "tier-p-matrix" -- \
    python3 /home/operator/scenario/fixture.py tier-p --root "$ROOT"; then
    pass "authority-first, producer-first, concurrent, session, and CWD permutations passed"
else
    jam "plugin-load" "deterministic context aggregation matrix failed" \
        "do not spend a Tier-E run until the programmatic broker contract is green"
fi

cr_finalize
