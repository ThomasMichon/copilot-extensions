#!/usr/bin/env bash
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=native-hook-composition-eval}"
export CR_SCENARIO_NAME
ROOT="/home/operator/native-hook-composition-eval"

cr_init
cr_meta "role" "starting-state-and-tier-p"

phase 0 "prepare two independent synthetic plugins with plain sessionStart hooks"
if capture "prepare-fixture" -- \
    python3 "$_SELF_DIR/fixture.py" prepare --root "$ROOT"; then
    pass "two non-cooperating plugins and one disposable repository prepared"
else
    jam "scenario-transport-gap" "fixture preparation failed" \
        "check fixture.py output in cr-logs/prepare-fixture.log"
fi

phase 1 "install both plugins through the supported local marketplace"
MARKETPLACE="$ROOT/marketplace"
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE" || true
install_failed=0
for plugin in synthetic-hook-alpha synthetic-hook-beta; do
    if ! capture "install-$plugin" -- \
        copilot plugin install "$plugin@native-hook-composition"; then
        install_failed=1
    fi
done
if [ "$install_failed" = 0 ]; then
    python3 "$_SELF_DIR/fixture.py" activate --root "$ROOT"
    pass "both synthetic plugins installed through the Copilot CLI"
else
    jam "scenario-transport-gap" "the CLI could not install the local marketplace" \
        "the current CLI/runtime cannot exercise this fixture through its supported marketplace path"
fi

phase 2 "prove each hook independently emits its own canary (Tier-P precondition)"
if capture "tier-p-hooks" -- \
    python3 "$_SELF_DIR/fixture.py" tier-p --root "$ROOT"; then
    pass "both plugin hooks independently emit exactly their own distinct canary"
else
    jam "plugin-load" "one or both synthetic hooks did not emit their expected canary" \
        "do not spend a Tier-E run until the fixture itself is proven correct"
fi

cr_finalize
