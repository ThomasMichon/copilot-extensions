# CLI namespace reader admission

Bounded Continuation C slice of [Process Slot Ownership](README.md).
The operator approved the initial policy: two consecutive native execution
timeout overruns, a 60-second admission cooldown, then one bounded recovery
probe. Existing execution timeouts remain unchanged.

## Contract

- Key admission by the provider instance and its namespace enumeration role.
  Join an existing exact holder before considering circuit state.
- Count actual native execution timeout, not an outer caller wait timeout,
  cancellation, missing executable, or a generic failure, as a wall overrun.
- Two consecutive native overruns open admission. While open, suppress new
  native launches; preserve the in-process fallback or report namespace
  incomplete when none exists. Never synthesize authoritative empty data.
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

- [ ] Two native timeouts open the circuit; caller cancellation does not.
- [ ] Suppressed callers never launch a subprocess and preserve fallback.
- [ ] Missing fallback yields explicit namespace incompleteness.
- [ ] One probe is admitted after cooldown; concurrent callers share it.
- [ ] Native success closes the circuit; failed/fallback-only probe does not.
- [ ] Intervening native success or a non-timeout failure resets the timeout
      streak, so nonconsecutive overruns cannot open the circuit.
- [ ] A failed or fallback-only probe restarts the full 60-second cooldown;
      callers before that new deadline cannot admit another native probe.
- [ ] Existing roster/native-holder ownership contracts remain green.

CPU measurement, cumulative role accounting, exact PID/start-time descendant
cleanup and cross-platform release/adoption remain open in the parent plan.
This wall-admission slice must not be credited as completing those gates.
