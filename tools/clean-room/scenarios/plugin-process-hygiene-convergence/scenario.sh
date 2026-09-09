#!/usr/bin/env bash
# plugin-process-hygiene-convergence/scenario.sh -- Tier-P F1 scenario.
#
# Runs the adversarial mock harness that validates the transient-hook-client
# contract (visions/plugin-services "process-count-scales-with-services-not-
# sessions" and "hooks-and-callbacks-are-transient" Behaviors, PR #2300)
# against a synthetic mock daemon -- not a real plugin runtime. No Copilot
# install, no plugin marketplace, no auth: this scenario is pure Python
# stdlib, so it runs identically on every OS and needs only `python3` on
# PATH.
#
# See efforts/active/plugin-process-hygiene/adversarial-convergence-mock.md
# for the full design this harness implements.
#
# Name-free / public F1. Env: CR_PPC_SCENARIOS / CR_PPC_FLOOD_N / CR_PPC_ROUNDS.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

SCENARIOS="${CR_PPC_SCENARIOS:-flood-against-live-daemon,flood-against-absent-daemon,daemon-appears-mid-flood,daemon-dies-mid-packet,concurrent-daemon-race,process-count-invariant-under-repeated-floods}"
FLOOD_N="${CR_PPC_FLOOD_N:-50}"
ROUNDS="${CR_PPC_ROUNDS:-5}"

: "${CR_SCENARIO_NAME:=plugin-process-hygiene-convergence}"
export CR_SCENARIO_NAME
cr_init
cr_meta "validates" "plugin-services (process-count-scales-with-services-not-sessions, hooks-and-callbacks-are-transient)"

_PYTHON="$(command -v python3 || command -v python || true)"

# =========================================================================
phase 0 "environment (no plugin install needed)"
envdump
if [ -z "$_PYTHON" ]; then
    jam "toolchain-venv" "no python3/python on PATH" "this scenario needs only a stdlib python3 -- install one"
else
    pass "python available: $("$_PYTHON" --version 2>&1)"
fi

# =========================================================================
phase 1 "adversarial convergence battery (six scenarios)"
if [ -z "$_PYTHON" ]; then
    jam "toolchain-venv" "cannot run the convergence probe without python3" "fix phase 0 first"
else
    capture "convergence-probe" -- "$_PYTHON" "$_SELF_DIR/fixtures/plugin_process_hygiene_convergence.py" \
        --scenarios "$SCENARIOS" --flood-n "$FLOOD_N" --rounds "$ROUNDS" || true
    _log="$CR_LOGDIR/convergence-probe.log"
    _seen=0
    while IFS= read -r line; do
        _seen=1
        _name="$(printf '%s' "$line" | awk '{print $2}')"
        _stat="$(printf '%s' "$line" | awk '{print $3}')"
        _rest="$(printf '%s' "$line" | cut -d' ' -f4-)"
        if [ "$_stat" = "PASS" ]; then
            pass "convergence/$_name: $_rest"
        else
            jam "process-hygiene-convergence" "convergence/$_name FAILED: $_rest" "a plugin's hooks/callbacks must follow the transient-hook-client contract; see cr-logs/convergence-probe.log"
        fi
    done < <(grep '^PROBE: ' "$_log" 2>/dev/null)
    if [ "$_seen" -eq 0 ]; then
        jam "process-hygiene-convergence" "convergence probe emitted no PROBE lines (crash before assertions)" "see cr-logs/convergence-probe.log"
    fi
    _summary="$(grep '^PROBE-SUMMARY:' "$_log" 2>/dev/null | tail -1)"
    [ -n "$_summary" ] && info "$_summary"
fi

# =========================================================================
cr_finalize
