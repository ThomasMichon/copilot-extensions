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
Pending snapshots display UNKNOWN without replacing cached bytes or ages;
a previous bundle with a different project/worktree identity cannot be reused.
Windows standalone updater discovery starts only after its lease is held;
contenders recheck live owner/session/runtime rather than lingering behind a
current winner. Required replacements still wait for retained cleanup release.

The Windows classifier, segment/cache, monitor/producer and standalone updater
use this boundary under their admitted instance-owned custody contract. The
existing monitor owns settlement before authority release; standalone callers
retain their own unresolved cleanup. This does not resolve passive fsmonitor,
admission-lock separation, process-economy or broader platform/harness acceptance.

## Owner-bound recovery

The opt-in Windows cutover adapter requires `guarded-undrain/v1` from one
existing-token authenticated health response before transition effects. Legacy
providers refuse this qualified path rather than silently receive legacy
undrain. Existing truly absent-owner and ordinary POSIX branches are unchanged.
The new control action binds expected PID/native start/runtime generation and
exact restored route/port. Under the monitor state lock then existing routing
lock, the receiver verifies both identities before using its existing undrain
callback. Rollback may advance route election generation without changing the
retained process identity. Only the matching typed positive reply qualifies;
lost/malformed/untyped replies, publication uncertainty and unconfirmed successor
exit retain unresolved reconciliation. No new ZDD API or parallel controller is
introduced; old callers retain their separate legacy compatibility behavior.

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
