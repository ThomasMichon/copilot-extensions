# CLI namespace reader admission

Bounded Continuation C slice of [Process Slot Ownership](README.md).
The operator approved the initial policy: two consecutive native execution
timeout overruns, a 60-second admission cooldown, then one bounded recovery
probe. Existing execution timeouts remain unchanged.

## Contract

- Key admission by the provider instance and its namespace enumeration role.
  Join an existing exact holder before considering circuit state.
- Only an actual native execution timeout counts as a wall overrun.
  Caller wait timeout, caller cancellation, missing executable and generic
  failure do not count as wall overruns.
- Two consecutive native overruns open admission. While open, suppress new
  native launches; preserve the in-process fallback or report namespace
  incomplete when none exists. A missing provider executable remains legitimate
  empty absence, not native recovery. Never synthesize authoritative empty data
  for an installed provider's suppressed or failed enumeration.
- After 60 seconds, admit one probe through the existing provider-local
  single-flight holder. A successful native parse closes the circuit and
  resets consecutive overruns. Fallback success is not native recovery.
- A failed recovery probe reopens the cooldown, even when failure is not a
  timeout. A non-timeout failure outside recovery resets the consecutive
  timeout streak but remains an ordinary reported enumeration failure.
- Publication invalidation and cache shutdown retain existing ownership;
  neither circuit state nor budget breach authorizes native termination.
- Keep resolve, target-repo and ensure-ready operations unchanged. Do not
  introduce a second registry or resident admission helper.

## Validation

- [x] Two native timeouts open the circuit; caller cancellation does not.
- [x] Suppressed callers never launch a subprocess and preserve fallback.
- [x] Missing fallback yields explicit namespace incompleteness for an
      installed provider; a missing executable preserves empty absence.
- [x] One probe is admitted after cooldown; concurrent callers share it.
- [x] Native success closes the circuit; failed/fallback-only probe does not.
- [x] Intervening native success or a non-timeout failure resets the timeout
      streak, so nonconsecutive overruns cannot open the circuit.
- [x] A failed or fallback-only probe restarts the full 60-second cooldown;
      callers before that new deadline cannot admit another native probe.
- [x] Existing roster/native-holder ownership contracts remain green.

Implementation and regression evidence: #6446, merged as `e28aaab8f`;
117 targeted tests passed, one skipped.

CPU measurement, cumulative role accounting, exact PID/start-time descendant
cleanup and cross-platform release/adoption remain open in the parent plan.
This wall-admission slice must not be credited as completing those gates.
