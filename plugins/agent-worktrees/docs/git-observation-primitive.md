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

## Caller obligations

`ObservationCleanupError` carries the actual pending process/Job objects.
Retain it strongly until `try_settle()` positively completes. False/error is
still pending. Do not discard it in a broad catch, render it as fresh facts,
start another observation of the affected scope, or report successful shutdown.
Preserve cancellation during both the operation and cleanup phases.

This foundation adds **no active classifier, segment, monitor, updater,
roster, cutover or recovery integration**. Landing the API is not permission
to opt those consumers in without their own custody/lifetime contract. It
does not resolve owner-bound undrain, passive fsmonitor, admission-lock
separation, process-economy or broader platform/harness acceptance.

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
