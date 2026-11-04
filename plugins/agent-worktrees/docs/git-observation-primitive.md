# Opt-in Windows Git-observation primitive

`push_timeout.run_bounded(..., observation_deadline=D)` provides an opt-in
Windows observation boundary. `D` is one finite absolute `time.monotonic()`
instant in the calling process. It is never persisted or transmitted.
General calls without this opt-in retain their existing timeout, binary-input,
inherited-group and foreground-push behavior. POSIX opt-in refuses before
launch; ordinary POSIX calls remain unchanged.

The primitive uses the existing private creation-time Windows Job adapter,
not suspend-then-assign through the generic factory. It requires supported
strict Job-list attributes before creating the child, preserves cwd/environment
without changing global CWD, and does not break away from containment.
Unsupported capability is explicit; no uncontained fallback is allowed.

The command wait reserves the existing five-second cleanup portion inside D.
An expired/insufficient budget starts no command. OS scheduling is not a
real-time guarantee. Cleanup failure is not successful observation evidence.
The private adapter's optional `cleanup_deadline` consumes the same D; its
default remains unchanged. Post-create failure transfers actual parent streams
before ExitStack unwinding, preserving exact process/Job custody and the
original operation/cancellation cause.

The local facade holds native root, binary streams and reader records before
reader startup. Captured bytes retain UTF-8 replacement and universal-newline
semantics. Native root release follows verified empty Job, completed root and
completed readers. Later retained settlement is zero-wait: no new observation,
clock renewal or PID/census lookup. It never uses a closed handle as proof of
empty containment.

A synchronous reader launch `RuntimeError` is not treated as a running reader
forever. Settlement checks the owned thread's pending-launch registration,
native identity and start event together under a nonblocking acquisition of
the standard threading registry lock. Only an absent pending launch plus
never-recorded identity/start proves no worker was created. It does not infer
this from `is_alive() == False`. A busy/unavailable registry, pending launch,
or interruption stays unconfirmed; an actually started reader still requires
completion and join. Missing bookkeeping fails closed. The checked CPython
3.11/3.12 ordering is retained with the test evidence; this introduces no
global cache or renewed deadline.

## Caller obligations

`ObservationCleanupError` carries the actual pending process/Job objects.
Retain it strongly until `try_settle()` positively completes. False/error is
still pending. Do not discard it in a broad catch, render it as fresh facts,
start another observation of the affected scope, or report successful shutdown.
Preserve cancellation during both the operation and cleanup phases. A delayed
outer catch must not attach an already-settled error to a newer active ticket.
Cleanup-pending snapshots display UNKNOWN without replacing cached bytes or
ages; a previous bundle with a different project/worktree identity cannot be
reused. Ordinary active observation does not invalidate a completed mux segment:
its existing, uninvalidated freshness window remains usable without starting
another Git operation or waiting for mutation admission. Expired, invalidated,
cleanup-pending and retiring entries cannot take that read-only path.
Windows standalone updater discovery starts only after its lease is held;
contenders recheck live owner/session/runtime rather than lingering behind a
current winner. Required replacements still wait for retained cleanup release.

The Windows classifier, segment/cache, monitor/producer and standalone updater
use this boundary under their admitted instance-owned custody contract. The
existing monitor owns settlement before authority release; standalone callers
retain their own unresolved cleanup. This does not resolve passive fsmonitor,
admission-lock separation, process-economy or broader platform/harness acceptance.

## Owner-bound recovery

The opt-in Windows cutover adapter observes the exact outgoing route and existing
token independently of `guarded-undrain/v1` negotiation. An incompatible or
timed-out predecessor does not veto incoming startup. Failed drain or retirement
uses bounded native termination of only the birth-verified outgoing process.
Changed routes, reused PIDs and unavailable identities never authorize a kill.
After publication, only the matching outgoing fallback route is released; no OS
port held by a foreign process is reclaimed. Unconfirmed cleanup is reported
without rolling back or indefinitely waiting behind a healthy incoming monitor.
Existing truly absent-owner and ordinary POSIX branches are unchanged.
An unnegotiated predecessor receives no drain mutation before the successor
serves. If incoming promotion fails, its native PID/birth and authenticated
already-serving health permit route restoration without inventing a guarded
undrain receipt. A negotiated peer retains graceful prepublication drain and
typed rollback; failed drain only schedules native cleanup after the serving gate.
Restoration independently captures a native PID/birth-bound exit reference inside
the routing publication fence; even a readable birth token on an exited process
cannot qualify. The candidate excludes the outgoing port even when its old
listener is closed, with at most eight port selections before a safe refusal.
The new control action binds expected PID/native start/runtime generation and
exact restored route/port. Under the monitor state lock then existing routing
lock, the receiver verifies both identities before using its existing undrain
callback. Rollback may advance route election generation without changing the
retained process identity. Only the matching typed positive reply qualifies;
lost/malformed/untyped replies, publication uncertainty and unconfirmed successor
exit retain unresolved reconciliation. The shared ZDD best-effort retirement
hook preserves other callers' separate legacy compatibility behavior; no parallel
controller is introduced.

The native packet allows 60 seconds for each disposable provider's actual
control-module imports and readiness publication, and 150 seconds per case
including two provider starts and owned Job cleanup. This fixture allowance
does not change monitor startup, drain or production installer deadlines.
The same bounded fixture is used by the ordinary async cases; its maximum
lifetime is documented in [TESTING.md](../../../TESTING.md).
Unconfirmed predecessor cleanup is retained in the existing lifecycle history
with the exact old endpoint and a failed retirement outcome, independently of
the successful incoming cutover result.
Windows forced retirement uses a verified process handle; Linux uses the
canonical pidfd adapter. macOS has birth-verified, non-signaling kqueue exit
references for guarded rollback, but no canonical identity-bound signal handle
in this suite. Forced retirement there explicitly returns `pidfd-unavailable`,
`killed: false` and `exit_confirmed: false`: the matching old fallback route may
be released while the new monitor serves, but an old OS listener is not proven
free and its process/runtime custody remains with the existing retirement owner.
This is a deliberate macOS termination exemption, not general POSIX kill support;
no PID-only signal fallback is used. Native macOS venue validation is unavailable.

## Bounded qualification

Pure/injected tests cover actual creation attributes/storage, exact cwd/env,
capability refusal, deadline cleanup, post-create interruption with open
retained streams, partial reader startup/join, decoding, zero-wait settlement
and legacy defaults. The real observation helper → atomic adapter → facade
chain is exercised with injected Windows APIs; this is not a native pass.

The minimum separate native qualification is an isolated supported-Windows
fixture for (1) caller death immediately after creation/before resume, with
the supervisor's outer Job kept alive and no duplicate inner Job handle;
(2) a descendant-held pipe/timeout and cleanup/cancellation case using the same
unchanged D, proving exact owned root/descendant/reader cleanup or truthful
retained failure; and (3) cwd/env/output and ordinary non-opt-in smoke.
Only fixture-owned exact resources may be cleaned up. No process-name kill,
installed update, live monitor/mux/UI fixture or full-harness benchmark is
needed to qualify this primitive. Broader nominal performance, POSIX parity
and integrated-consumer acceptance remain separately tracked followups.


The separate opt-in native packet now exercises real Git/descendant-held pipes,
caller death during execution and before first resume, native Job/root/reader
settlement, real installer/control recovery and cwd/env/text/binary behavior.
Run `test_git_observation_native.py`,
`test_status_monitor_guarded_recovery_native.py` and
`test_status_monitor_guarded_cutover_native.py` through the contained plugin
runner with `AGENT_WORKTREES_NATIVE_RECOVERY=1`. They use disposable Jobs and
fresh isolated routes, never a production monitor. Source/native qualification
is not installed rollout or full-harness performance acceptance.
