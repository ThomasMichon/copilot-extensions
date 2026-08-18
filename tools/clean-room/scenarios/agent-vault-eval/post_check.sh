#!/usr/bin/env bash
# agent-vault-eval/post_check.sh -- programmatic ground-truth AFTER the agent turn.
#
# Runs the plugin's real CLI to capture objective evidence the judge can anchor on
# beside the transcript. It NEVER substitutes for the literal-mode judgment (an
# agent that self-healed to a good end state is still a FALSE-PASS) -- its job is
# to make self-heals VISIBLE: a .kdbx that now exists, a KPDB that got set, or a
# keepassxc-cli that got installed are all signals the agent manufactured a
# working vault the docs did not hand it. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

: "${CR_SCENARIO_NAME:=agent-vault-eval}"
export CR_SCENARIO_NAME
# Reuse the report the setup phase opened (append post-check evidence to it).
cr_init 2>/dev/null || true

phase 9 "post-check: ground-truth after the agent turn"

# Objective state the judge reads (all INFO -- this phase asserts nothing; it
# records evidence). The judge decides PASS/FALSE-PASS from these + the transcript.
capture "pc-binstub"   -- bash -lc 'command -v agent-vault && echo PRESENT || echo ABSENT' || true
capture "pc-which"     -- bash -lc 'VAULT_NONINTERACTIVE=1 agent-vault which 2>&1' || true
capture "pc-list"      -- bash -lc 'VAULT_NONINTERACTIVE=1 agent-vault list / 2>&1' || true

# Self-heal tripwires -- their PRESENCE after the turn suggests the agent
# manufactured what the docs should have required as a prerequisite.
if bash -lc 'command -v keepassxc-cli >/dev/null 2>&1'; then
    info "self-heal signal: keepassxc-cli is PRESENT after the turn (was absent at setup) -- did the agent install it?"
    cr_meta "post_keepassxc_cli" "present"
else
    info "keepassxc-cli still absent (expected for a literal run)"
    cr_meta "post_keepassxc_cli" "absent"
fi

_kdbx_found="$(find "$HOME" -maxdepth 4 -name '*.kdbx' 2>/dev/null | head -n1)"
if [ -n "$_kdbx_found" ]; then
    info "self-heal signal: a .kdbx now exists ($_kdbx_found) -- did the agent CREATE a vault?"
    cr_meta "post_kdbx_created" "yes:$_kdbx_found"
else
    info "no .kdbx on disk (expected for a literal run)"
    cr_meta "post_kdbx_created" "no"
fi

cr_finalize
