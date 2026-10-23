# Progress and timeout contract

This is the shared design contract for services, CLIs, tools and their nested
operation boundaries. It serves
[the Plugin Service Model vision](../../visions/plugin-services/README.md#progress-aware-service-and-tool-operations).
It states required behavior, not a claim that every existing entry point already
implements it. Per-plugin reality docs describe actual supported behavior.

## Two independent clocks

| Clock | Measures | Renewal | Expiry means |
|-------|----------|---------|--------------|
| Communication timeout | Time since contact with the target for the observed operation | Every received target update, whether or not its event meaning is handled | Communication is silent; execution outcome may still be unknown |
| Process timeout | Total allowed lifetime of the operation | Not renewed by progress or reconnection | The separately selected execution budget is exhausted |

Communication budgets are short so lost contact is detected promptly. Process
budgets are very long relative to ordinary execution and allow substantial grace
for slow preparation, contention and disruptions. They are independently named
and selected; an old generic `timeout` must not silently serve both purposes.
This contract does not select one universal numeric default.

Start the communication clock when waiting for the target, not after the first
update: startup silence is observable too. Measure local elapsed intervals
monotonically. Receipt of a target update renews the silence deadline from its
arrival; a remote timestamp is not the local clock.

Every target update counts as contact. A consumer may act on a small set of
domain events while treating all other updates, including additive event kinds
and target-emitted keepalives, as signs of life. Event recognition must not be a
prerequisite for renewing communication. A caller's own polling timer or locally
manufactured heartbeat is not received target activity.

## Progress crosses the entire operation chain

Emit updates while work happens, beginning at the command or service boundary,
and continue through nested phases. For session startup this includes worktree
state negotiation, file-lock acquisition and waiting, SSH negotiation,
session-host initialization, Git synchronization, mux creation, plugin
synchronization and instruction synchronization. The contract is not limited
to session startup or to Bridge and Dispatch.

Each operation owner reports its own current activity, waiting and transitions.
A long lock wait or quiet nested command remains visible through updates from
the target actually supervising it; claiming completion or fabricating child
progress is unnecessary. Intermediate callers forward updates promptly and
preserve enough operation and phase context to locate a stall. Buffering all
output until process exit, or reporting only `starting` and `started [54s]`,
does not meet the contract.

Separate progress delivery from the final result. Preserve machine-readable
result framing, error semantics and secret-redaction boundaries; adding
progress must not corrupt an existing JSON result or dump credentials.
Observing an unfamiliar event must not require implementing its business
meaning, dropping its liveness value or inventing a second launch path.

An outer wrapper must not impose a short fixed process deadline that preempts
an inner operation still streaming updates. Its communication observation and
process-lifetime policy compose with the target's rather than defeating it.
Process ownership and bounded teardown still apply when cancellation is
actually authorized; pipe draining must not turn that teardown into an
unbounded hidden wait.

## Failure, disruption and recovery

Report which clock expired, the last observed phase/contact and the operation's
elapsed lifetime. Distinguish loss of observation from acknowledged execution
failure, process exit and exhausted process budget.

A communication timeout is not an instruction to kill the target, reap a shared
service, start another worker or resend an uncertain mutation. Retain operation,
task, workspace and session identity. Reconnect or inspect through the existing
observation and receipt contracts, giving a still-running operation the long
process budget and disruption grace its policy allows. A caller may stop
waiting without claiming that the target stopped.

Process expiry likewise follows the operation owner's explicit cancellation and
cleanup contract; it grants no authority over unrelated processes. Progress,
acceptance, completion and cancellation acknowledgement remain distinct.

## Consumer qualification

For each adopting boundary, qualify the actual consumer path, not only a timer
helper. Cover a healthy operation whose total duration exceeds the short
communication budget while updates keep arriving; recognized and unrecognized
updates; target keepalives versus local timers; silence before and between
phases; an independent process deadline despite continuing updates; nested
forwarding and prompt flushing; reconnect/disruption with retained identity;
and uncertain acceptance without automatic replay. Keep result framing,
diagnostic redaction and owned cancellation/descendant cleanup in that coverage.

## See also

- [Architecture patterns](README.md)
- [Service transport](service-transport.md)
- [Graceful daemon cutover](graceful-daemon-cutover.md)
