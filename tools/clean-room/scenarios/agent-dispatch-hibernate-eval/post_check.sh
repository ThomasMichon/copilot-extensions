#!/usr/bin/env bash
# agent-dispatch-hibernate-eval/post_check.sh -- programmatic ground-truth AFTER
# the agent turn.
#
# It NEVER substitutes for the literal-mode judgment (the judge decides PASS /
# FALSE-PASS from these signals + the transcript) -- its job is to make the
# suspend OBJECTIVE and to prove the harness owns the wake edge:
#   * post_detached_waiter  -- is a live waiter for the caller-controlled signal
#                              still running? (genuine suspend => the wait was
#                              handed off, not busy-held in-session)
#   * post_waiter_detached  -- is that waiter reparented to init (ppid==1)? i.e.
#                              a TRUE OS-level detach, not a foreground child.
#   * post_agent_dispatch_run -- is the waiter owned by an `agent-dispatch run`
#                              process (the documented mechanism), vs a hand-rolled
#                              nohup/&/setsid waiter (a FALSE-PASS tripwire)?
#   * post_waiter_released_on_fire -- after the harness FIRES the signal, does the
#                              waiter exit? proves the caller controls the wake.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

SIGNAL_NAME="hibernate-signal"
WAITSH="$(cr_signal_wait_cmd "$SIGNAL_NAME")"

: "${CR_SCENARIO_NAME:=agent-dispatch-hibernate-eval}"
export CR_SCENARIO_NAME
# Reuse the report the setup phase opened (append post-check evidence to it).
cr_init 2>/dev/null || true

phase 9 "post-check: ground-truth after the agent turn"

# --- 1) did the worker genuinely SUSPEND? (a live waiter owns the wait) --------
capture "pc-ps" -- bash -lc "ps -eo pid,ppid,args 2>/dev/null | grep -F '$WAITSH' | grep -v grep || true" || true
if cr_signal_waiter_present "$SIGNAL_NAME"; then
    pass "a live waiter for '$SIGNAL_NAME' is running -- the provided wait was HANDED OFF, not busy-held"
    cr_meta "post_detached_waiter" "present"
    _had_waiter="yes"
else
    info "no live waiter for '$SIGNAL_NAME' after the turn -- the agent may have busy-waited, resolved, or skipped the wait (see transcript)"
    cr_meta "post_detached_waiter" "absent"
    _had_waiter="no"
fi

# Reparented-to-init (ppid==1) => a true OS-level detach, not a foreground child.
_ppid1="$(ps -eo ppid,args 2>/dev/null | grep -F "$WAITSH" | grep -v grep | awk '{print $1}' | grep -qx 1 && echo yes || echo no)"
cr_meta "post_waiter_detached" "$_ppid1"
info "waiter reparented to init (ppid==1, true detach): $_ppid1"

# Is the waiter owned by the DOCUMENTED mechanism (`agent-dispatch run`) rather
# than a hand-rolled waiter? Look for a live `agent_dispatch ... run ... <waitsh>`.
if ps -eo args 2>/dev/null | grep -E 'agent[_-]dispatch.* run ' | grep -F "$WAITSH" | grep -qv grep; then
    pass "the waiter is owned by an 'agent-dispatch run' process (the documented hibernate-the-wait mechanism)"
    cr_meta "post_agent_dispatch_run" "yes"
else
    info "no live 'agent-dispatch run' wrapper around the wait -- if a waiter exists it may be hand-rolled (FALSE-PASS tripwire; confirm in transcript)"
    cr_meta "post_agent_dispatch_run" "no"
fi

# Self-heal tripwire: did the agent write its OWN waiter/wrapper script anywhere?
_own="$(find "$HOME" -maxdepth 3 -type f \( -name '*wait*.sh' -o -name '*hibernat*.sh' \) 2>/dev/null | grep -vF "$WAITSH" | head -n3 | tr '\n' ' ')"
if [ -n "$_own" ]; then
    info "self-heal signal: agent-authored wait-ish script(s) present: $_own -- did it hand-roll a waiter instead of using 'agent-dispatch run'?"
    cr_meta "post_own_waiter_script" "$_own"
else
    cr_meta "post_own_waiter_script" "none"
fi

# --- 2) prove the HARNESS owns the wake edge: fire, then confirm release --------
phase 10 "post-check: fire the caller-controlled signal + confirm release"
cr_signal_fire "$SIGNAL_NAME"
_released="no"
for _i in 1 2 3 4 5 6; do
    sleep 1
    if ! cr_signal_waiter_present "$SIGNAL_NAME"; then _released="yes"; break; fi
done
if [ "$_had_waiter" = "no" ]; then
    info "no waiter was present to release (nothing was handed off) -- release check is n/a"
    cr_meta "post_waiter_released_on_fire" "n/a"
elif [ "$_released" = "yes" ]; then
    pass "waiter exited after the harness fired '$SIGNAL_NAME' -- the caller owns the wake edge (suspend->release proven)"
    cr_meta "post_waiter_released_on_fire" "yes"
else
    info "waiter did NOT exit within 6s of firing -- inspect pc-ps / transcript"
    cr_meta "post_waiter_released_on_fire" "no"
fi

cr_finalize
