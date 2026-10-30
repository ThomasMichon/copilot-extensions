# agent-procutil

Shared **Windows-headless / detached / bounded-owned** process primitives for
Copilot CLI plugins. One vendored helper keeps launch flags and finite child
containment consistent instead of reimplementing them in each consumer.

`agent_procutil.process_retirement.PinnedProcess` is a finite external-process
primitive for application-owned incumbent retirement on Windows and Linux.
It validates the original birth against a retained process handle/pidfd,
can pause that identity while the application closes its owned descendant
scope, and confirms exit on that same kernel object. Context exit resumes
a still-live process paused by the transaction; unconfirmed resume retains
the pin on the raised error's `process` attribute for the caller's cleanup.
It never discovers targets, authorizes an update, signals a group, or replaces
the caller's lifecycle/critical-connectivity policy. Other platforms refuse
external pinning rather than falling back to numeric PID kills.
Linux pause confirmation requires every thread in a stable task-group snapshot
to be stopped; a stopped leader alone does not freeze a multithreaded parent.

For an installer that can be killed while its incumbents are paused, use
`agent_procutil.retirement_custody.RetirementCustody(root)` from the
`retirement-custody` extra, and obtain context-managed pins with
`custody.pin(pid, birth)`. One attributed finite worker owns the actual pins
outside the production caller's Windows Jobs or Linux process group. Windows
uses the already-adopted launch broker and authenticated private pipe; Linux
inherits one anonymous socketpair and the original caller's pidfd. The worker
never discovers targets or adds update authority. Caller death, a closed
lifeline, or a 30-second idle deadline resumes all remaining originals before
worker exit. Normal close confirms kernel exit and settles the instance record.
An ambiguous native resume retains the live pin without blindly repeating
Windows suspend-count changes; the worker waits for that original's exit and
the caller reports unsettled custody. No hard-kill cleanup is permitted while
live paused pins remain. Contained tests do not escape their enclosing cleanup
boundary; they are not evidence of production Job-death survival.

`agent_procutil.diagnostics` also supplies the pure, bounded subprocess
diagnostic parser/redactor originally owned by Bridge's resolver. Bridge retains
its compatibility exports and retry policy; Dispatch can use
`safe_diagnostic_text` and `structured_diagnostic_fields` without importing a
sibling plugin. Error/message/stage/code/reason strings are selected, not opaque
launch envelopes. Redaction handles known credential forms and supplied known
values after control normalization; it is best-effort, not a guarantee about
arbitrary third-party output.

## Python-free Windows bootstrap admission

### Compiler-free migration boundary

The existing compiled PowerShell entrypoints below have not yet migrated.
Python is an existing harness prerequisite owned by machine provisioning;
a cold plugin runtime does not authorize another Python download or a
helper-owned interpreter installation.

The private `_windows_parent_job.transfer_lifetime_to_parent` primitive is
a startup-only building block for a qualified, sacrificial Python helper.
It pins the original direct parent's process handle and creation identity,
creates a strict kill-on-close Job, duplicates a non-inheritable Job handle
into that same parent, assigns the helper itself and closes the helper-local
Job handle before returning. Only the original parent retains continuing
Job custody. Parent exit or closure of that handle therefore retires the
helper and its descendants in the kernel, not through a Python polling
watchdog. No breakaway permission is added, including under test containment.

The returned `ParentJob.parent_handle` belongs to the **parent's handle
table**, not the helper's. The parent must adopt it through its existing
handle owner before accepting application work. A stock .NET
`SafeFileHandle` can own that native handle without compiling interop code.
Application completion still requires the existing atomic target-Job owner,
whole-descendant settlement and observation of the original helper's exit.
A receipt is not a settlement witness. Transfer failure is terminal for the
sacrificial helper; it must never continue application work.

`offer_lifetime_to_pipe_server` is a distinct entry point for a trusted
interpreter launcher between the shell and Python. It retains the same
birth-pinned process handle and strict Job transfer, but requires the kernel's
inherited-pipe server PID to name that original caller. It does not weaken the
direct-parent API or walk/reopen numeric ancestry. It returns a `ParentJobOffer`,
not an immediate transfer that discards its process reference before delivery.
Publish `offer.receipt`, then call `accept()` only after the caller adopts the
handle and acknowledges its actual worker's birth. Only an explicit unadopted
declaration permits `abort()` to reclaim the offer through that retained parent.
On failed/partial publication or unknown adoption, keep awaiting the caller's
decision or call `abandon()` to release only the process reference and report
unknown custody. Never infer non-adoption from an I/O failure. Every decision
is one-shot, including a failed close; the direct-parent primitive is unchanged.

The private `_shell_caller_owned.serve` adapter supplies this startup receipt,
acknowledgement and correlated completion framing for synchronous shell
callbacks. Windows keeps the continuing Job handle exclusively in the original
shell; cancellation or shell death closes it. Before creating a Windows offer,
the worker requires the shell's explicit permission after complete request
delivery. The offer retains the same birth-pinned parent process handle until
adoption or an explicit nonce-bound **unadopted** abort. Only that abort permits
remote-handle reclamation; EOF or a malformed acknowledgement preserves explicit
uncertainty and never closes a numeric handle the shell might already have
disposed or reused. A failed receipt write still consumes that decision.
If the shell sees receipt EOF, it unwinds to send its abort or close input
before waiting for the worker and diagnostics; waiting first would deadlock
a worker that is still awaiting the handoff decision.
Linux/macOS retain the original shell's private stdin lifetime pipe in an
independent forked watcher member, established before releasing the callback.
The watcher's own membership pins the original group even if its leader dies;
pipe loss retires that still-occupied group. Normal callback exit uses the
existing leader-owned group retirement. The callback never inherits an application-input
pipe or a continuing lifetime writer. The shell must reject missing completion
or an unexpected owner-exit status; it closes the Job before awaiting captured
pipe EOF, and its interruptible waits permit cancellation to run that cleanup.
This is a caller-bounded helper, not a daemon, another service or a new registry.
The source-only lifecycle archive packages the same implementation for cold
shell callers; it contains Python source, not a compiled helper.

This primitive does not select or admit an interpreter, authenticate a
command, acquire an operation lock, or switch an existing bootstrap caller.
Interpreter/complete-source admission, bounded shell framing, native desktop
qualification and migration of the compiled implementations remain open in
the [compiler-free amendment](../../efforts/active/lean-runtime-dependencies/compiler-free-execution.md).
The portable controls and separate native parent-exit, explicit-handle-close
and stale-parent-birth controls test this building block, not that complete
bootstrap contract. `test_shell_caller_owned_native.py` separately exercises the
actual PowerShell SDK's callback completion, abrupt exit, original caller death
and runspace cancellation, observing pinned descendants before test cleanup.
Its native offer controls hold preallocated fixture process/I/O resources
constant across a separately gated cold SDK invocation. Raw parent-handle counts
must show exactly the correlated offered Job, if any, and return to the same
baseline after that known Job closes, before fixture disposal. This avoids
attributing subprocess setup or reader-thread resources to the SDK; it does not
permit a tolerance, warmup, garbage-collection workaround or unknown-handle close.
An explicit unadopted abort closes the worker's last kill-on-close Job handle
and may terminate it before Python can raise an error. The fixture therefore
checks the exact offered handle is invalid (`ERROR_INVALID_HANDLE`), no callback
or completion was published, and the same exact handle balance is restored;
it does not infer abort custody from a nonzero process exit. See Windows'
[last-Job-handle termination contract](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects#managing-job-objects).

### Existing compiled entrypoints

The packaged `native_daemon.ps1` and `native_owned.ps1` expose the native
counterparts needed before a Python interpreter exists. These are internal
process primitives, not an installer, a request queue, an arbitrary-command
installation CLI or a new lifecycle owner. A bootstrap must first validate
its fixed source/recipe, acquire the canonical operation lock, and journal
its own durable progress and outcome. No current plugin publisher switches
to these helpers merely because they are present.

The daemon admission helper requires stock Windows PowerShell 5.1 and refuses
PowerShell Core before compiling its helper or creating a pipe. Its
current-user ACL constructor is a .NET Framework API; no unsupported
`pwsh.exe` fallback is advertised. Select the stock host explicitly when
calling this bootstrap from a Core-based controller.

Dot-source `native_daemon.ps1` from the resolved, attributable payload and use
`Start-NativeDaemon -Script <absolute-ps1> -Arguments <trusted-string-array>
-WorkingDirectory <absolute-directory> -LogPath <absolute-diagnostic-file>`.
Every path must be fully qualified and normalized; drive-relative and
current-drive-rooted Windows paths refuse before activation or log opening.
It uses ordinary native `CREATE_NO_WINDOW` creation with explicit breakaway
and a suspended child. Before resume, the parent verifies absence from every
tracked launcher-owned cleanup Job and closes the temporary query handles.
A current-user-only, bounded named-pipe handshake checks both actual endpoint
PIDs against the retained native child identity. No WMI, COM, Scheduled Task,
UAC or automatic inherited-lifetime fallback participates.

The result is a `System.Diagnostics.Process`; keep it for observation and
dispose it afterward. Spawn/admission is not application readiness or
installation completion. `LogPath` records bootstrap failures; the entry point
owns its continuing progress/result log. A
`NativeDaemonSpawnException.CleanupConfirmed` value of false preserves
uncertainty, including failed native cleanup or a lost return after
acknowledgement; only an explicit refusal or observed cleanup clears that
uncertainty. `RetainedChild` preserves native custody after unconfirmed cleanup
for an explicit bounded `Stop()` retry followed by `Dispose()`. Callers must
consult their durable operation state rather than retrying installation.
With `COPILOT_EXTENSIONS_TEST_CONTAINED=1`, the same handshake uses an ordinary
windowless child and deliberately retains the caller's Job.

`Invoke-NativeOwnedProcess -FilePath <absolute-executable> -Arguments
<trusted-string-array> -WorkingDirectory <absolute-directory> -LogPath
<absolute-log> -TimeoutMilliseconds <positive-deadline>` captures stdout and
stderr directly into the append-only log. It atomically assigns the suspended
child to a strict kill-on-close Job using `PROC_THREAD_ATTRIBUTE_JOB_LIST`,
inherits only its two declared stdio handles, and never permits breakaway.
Return preserves the root's exit status only after every Job writer exits;
root exit alone is not completion. Timeout and failure terminate and observe
the same tree. `NativeOwnedProcessException.CleanupConfirmed` distinguishes
attested settlement from an unresolved cleanup failure. A consumer must keep
an interrupted fence on uncertainty or owner death, not infer settlement from
the operation lock becoming free.

The native fixtures exercise actual stock Windows PowerShell, canonical
Python/native lock contention, caller-Job closure, contained controls,
descendant settlement and owner death. The explicit
`PROCUTIL_NATIVE_DAEMON_LIFETIME=1` lane runs two production and two contained
cycles with a PATH containing neither Python nor uv and the canonical
console/foreground observer. This is Windows admission evidence, not a
fresh-host foundation installation or Linux/macOS qualification.
The cleanup controls also force a verified pre-acknowledgement refusal,
an untrusted PID frame, and a still-live launcher: only an authenticated,
successfully handle-pinned worker may be terminated, and both worker and
launcher exit must be observed before cleanup is confirmed.

```python
import subprocess
import sys
from agent_procutil import (
    detached_kwargs,
    no_window_kwargs,
    windowless_daemon_kwargs,
    windowless_python,
)

# Non-interactive child, output captured, no console window on Windows:
subprocess.run(cmd, capture_output=True, text=True, **no_window_kwargs())

# Fully detached background daemon (no console, survives the parent):
subprocess.Popen(
    [windowless_python(), "-m", "my_daemon"],
    **detached_kwargs(breakaway=True),
)

# Windowless host whose own console-subsystem children must stay invisible:
subprocess.Popen(
    [sys.executable, "-m", "my_host"],
    **windowless_daemon_kwargs(breakaway=True),
)
```

The helpers are no-ops off Windows (`no_window_kwargs()` -> `{}`;
`detached_kwargs()` -> `{"start_new_session": True}`), so call sites stay
platform-agnostic. `windowless_python()` selects the sibling `pythonw.exe` on
Windows because a detached venv `python.exe` launcher can re-exec a base console
interpreter that allocates a new DefTerm console. When
`COPILOT_EXTENSIONS_TEST_CONTAINED=1`, deliberate Windows Job breakaway and
POSIX session detachment are suppressed so the test runner retains descendant
ownership. Runtime code with an additional in-process survival step can use
`contained_test_mode()` to suppress that step under the same policy.
`windowless_daemon_kwargs()` preserves a `CREATE_NO_WINDOW` host on Windows
instead of using `DETACHED_PROCESS`, so console-subsystem grandchildren do not
allocate a visible console.

### Windows inner-base interpreter selection

`windowless_python()` prefers the `pyvenv.cfg` home's physical `pythonw.exe`.
It reads name-surrogate junction/symlink targets component by component with
`lstat` and `readlink`, including ancestors and final executable links, before
inspecting descendants. This avoids traversing aliases blocked by Windows RedirectionGuard;
it does not change runtime-marker selection or disable mitigation.
Pass `windowless_python_env()` into the child environment when using that base
interpreter: `__PYVENV_LAUNCHER__` retains the original selected venv's import
context. It also preserves that identity when a local GUI fallback is a link
to an executable outside the venv. `direct_python()` returns the physical
console interpreter plus that environment mapping; it selects `python.exe`
from the same configured home even
when the GUI executable is itself a link elsewhere.
Outside a venv, an available requested console sibling takes precedence; if a
GUI-file alias has none, its physical target's `python.exe` sibling is used.
Windows callers that cannot accept the ordinary console fallback can request
`windowless_python(require_windowless=True)`. Selection then refuses if neither
GUI entry exists, while accepting a GUI entry whose physical target is renamed
or relocated. Callers must not infer GUI intent from that target's basename.
The default and non-Windows behavior are unchanged.

The selected venv's configured home must also remain traversable by CPython:
`__PYVENV_LAUNCHER__` causes startup to read that same `pyvenv.cfg`. A healthy
physical executable alone does not make a blocked home usable. After the
configuration is read, an explicit home-access check refuses WinError 448 with
a runtime reconstruction diagnostic rather than returning a success-shaped selection.
Runtime construction must record a usable physical home. These helpers never
rewrite an installed configuration, substitute another venv, or override
`PYTHONHOME`/`PYTHONPATH` to disguise an unusable one.

Missing windowless interpreters retain the documented local-sibling/original
fallback. Direct venv launches refuse unavailable base interpreters. Link
cycles and inaccessible link metadata raise instead of being mistaken for
absence, and no helper substitutes an unrelated PATH Python. Non-Windows
selection is unchanged.

`tests/test_interpreter.py` covers non-traversing selection and refusal.
`tests/test_interpreter_windows.py` launches real console and GUI children twice
each through a test-owned inner-home junction, checking venv imports, prefix,
exit status and unchanged mitigation flags. Its enforced parameter requires
inherited RedirectionGuard, proves blocked-home CPython startup failure and
explicit helper refusal twice, then records a physical home only in the
disposable fixture and checks successful launches. It skips explicitly on
ordinary Windows workers rather than claiming enforcement or deployed recovery.

## Bounded owned children

`run_caller_owned(..., on_settled=callback)` optionally reports positive native
scope settlement, separately from execution success. The callback runs once
after known pre-launch preparation refusal, or after the entire strict Windows
Job / unreaped POSIX group has been retired and confirmed. It also runs when
timeout, output limits or invalid UTF-8 preserve an exception after that boundary.
Unconfirmed cleanup or an ambiguous spawn failure does not notify. Exceptions
retain their existing types and details; notification never manufactures a
completed result. Consumers must separately confirm their own authority and
borrowed obligations before using this proof to release a durable fence.
The callback is synchronous and should perform only bounded bookkeeping.

Optional `cwd` and `env` select the native child context without changing the
caller's working directory or environment. The POSIX guardian inherits that
context through its native spawn; it is not added to the execution protocol or
logged. Windows uses the same strict owned Job. Omitting both retains the
existing inherited-context behavior, stream budgets and caller-death cleanup.

`run_installation_owned()` is the explicit boundary for a trusted canonical
installer that must start durable product daemons. It retains the same output
budgets, caller-owned cleanup and settlement callback, but its Windows Job
permits explicit daemon breakaway. Ordinary descendants remain contained and
are retired before settlement; deliberately detached daemons belong to their
own service lifecycle, not that settlement proof. An external restrictive Job
still prevents escape. Contained tests suppress the exception. Ordinary tools
continue to use strict `run_caller_owned()`; this is not a fallback or permission
to detach bounded helpers, register tasks or elevate.

`agent_procutil.startup.StartupClock` separates receipt-time communication
silence from a fixed startup lifetime. `StartupTimeouts` defaults to 30 seconds
of silence, 300 seconds of startup grace and a 60-second expected startup
latency. Every target update can renew contact without understanding its
meaning; local timers and cached metadata cannot. `mark_ready()` ends startup
observation, not a later turn. Expiry returns its clock, elapsed time, silence
and last phase; this pure observer never grants cancellation or replay rights.

`agent_procutil.streaming.communicate_progress()` incrementally forwards
`(stream_name, bytes)` from an existing async process while preserving captured
stdout/stderr and stdin framing. The caller still owns process creation,
execution budgets, cancellation and descendant cleanup. This helper is not a
new launcher or a permission to terminate work on communication silence.

Minimal lifecycle bundles may copy only `agent_procutil/__init__.py` and load
it directly, with or without registering the module in `sys.modules`.
The short-lived containment helpers and their typed process-creation errors
remain self-contained; calling `spawn_daemon` requires the full package.

`agent_procutil.owned_process.run_owned()` executes a finite command with
captured UTF-8 output, optional `input_text`, and a required positive `timeout`.
It retains strict Windows Job ownership or an unreaped POSIX session/group
leader until descendant cleanup finishes. Completion, timeout and cancellation
share that cleanup boundary. Windows stdin writing has independent absolute
deadline supervision; supervision and pipe-reader threads finish before return.
Unsupported or lost custody fails visibly rather than signalling a reused ID.

An explicitly contained worker may call `require_contained_worker()` and launch
nested peers with `inherit_process_group=True`. Kernel checks require Windows
Job membership or the worker's own POSIX session/group root; an environment flag
is not evidence. Windows producers enforce strict limits using their retained
Job handle at suspended spawn, not an ambiguous NULL-Job limit query in a nested
worker. Borrowed POSIX children stay in the outer
group and are individually retired before reaping. Their outer supervisor owns
the final group sweep, including descendants, before reporting whole-scope
completion. Standalone entry points must supervise that worker with the default
owned-root mode, not assume ambient containment. This is a lifetime contract,
not application-level authorization.

## Native Linux child handoff

`agent_procutil.native_child.NativeChildCustody(pid)` adopts an actual direct,
unreaped Linux child after its original native parent deliberately execs the
selected Python interpreter. Admission requires default `SIGCHLD`, a matching
kernel pidfd and the child's own session/group leadership. It does not create
a `Popen`, reopen an arbitrary persisted PID, launch a process, or confer
installation authority. A native launcher must keep the child alive until
handoff; a shell may already have reaped a child that exited earlier.

Keep the returned custody on its adopting process and thread. Duplicate
adoption refuses, and a fork child closes its inherited pidfd without gaining
parent-child authority. `assert_owned()` rechecks the kernel relationship.
`retire()` kills and confirms the same pinned group through the existing
owned-process machinery, then reaps its original child and returns that root's
native status. An already-exited but unreaped root remains admissible.
Idempotent retirement does not repeat signalling. A root exit status alone
does not attest successful prerequisite acquisition or installation.

On `NativeChildCleanupError`, retain its `custody` and the application's
interrupted fence; the root stays unreaped until positive group settlement.
Retrying observation of that same custody is not permission to repeat the
application operation. External reaping, changed signal disposition or
unavailable kernel identity refuses rather than signalling an unproved group.
As with the existing POSIX owned-process contract, descendants must remain in
the original group; this is not a cgroup or privileged escape-containment API.
Linux native handoff tests do not qualify a complete Python-free producer,
caller-loss acquisition, macOS, fresh-host installation or rollout.

The packaged `native_owned.sh` supplies the pre-Python half:
`start_native_owned_process <private-directory> <log> <timeout-seconds> <argv...>`
starts one direct session child, and `wait_native_owned_process <seconds>`
observes its bounded completion frame. The actual parent keeps the only
lifetime writer on FD 8 across deliberate exec; acquisition descendants never
inherit it or FD 9. EOF or timeout retires the guardian's own live group.
The leader stays alive after its command exits so Bash cannot reap it before
`NativeChildCustody` adoption. Keep FD 8 until that custody positively retires
the group; the command's frame alone does not certify settlement. These
helpers reserve FDs 5-8 and require non-interactive Bash with job control off.

`native_daemon.sh` provides `start_native_daemon <script> <working-directory>
<log> <arguments...>`, the Python-free Linux counterpart of the canonical
POSIX daemon launch. It uses a new session with redirected stdio, closes
unrelated inherited descriptors and acknowledges the admitted application's
actual process identity before executing the selected script. With
`COPILOT_EXTENSIONS_TEST_CONTAINED=1`, it preserves the caller's group instead.
`__native_daemon_pid` is only a live launch observation, never persisted
application custody or a completion receipt. Neither helper supplies cgroup
escape, an installation queue or macOS admission.
`__native_daemon_wait_pid` separately identifies the launcher's direct,
waitable child. A synchronous observer waits that child for the native exit
status; the admitted application can instead be a grandchild of the disposable
lifecycle supervisor. Waiting the application PID with Bash `wait` is not valid
in that case. Installation callers still own their durable receipt/result and
must not interpret either PID as request acceptance.

## Caller-owned interactive completion

`interactive.run_interactive(argv, env=...)` runs a foreground command and
returns its exit status. Use it after an
interactive UI has returned, not on the UI's first-paint path. It requires
the main thread and settles owned descendants before returning, including on
Ctrl-C (130) or caller death. Cleanup failure raises `OwnedCleanupError` with
retained process/Job custody rather than claiming completion.
Normal exit codes are preserved; POSIX signal termination uses `128 + signal`,
and Windows' native control-C status maps to 130 rather than a wrapped CLI code.

Windows requires suspended assignment to a kill-on-close Job and confirms
Job termination. Linux and macOS use a finite guardian with a private session.
With a caller terminal, it owns a separate PTY: bytes and resize events are
relayed, the completion terminal-device identity changes, and redirected
non-TTY streams remain inherited rather than entering the relay. The caller's
absent standard streams map to the null device, and private descriptors stay
above fd 2 so those mappings cannot overwrite control pipes. The caller's
terminal attributes are restored when the caller unwinds. Default SIGHUP,
SIGTERM and SIGQUIT dispositions unwind cleanup before re-delivering the
original signal; ignored and custom dispositions remain unchanged. An
uncatchable SIGKILL cannot restore the caller's terminal modes, although the
guardian still retires its descendants. A start gate precedes command
execution and the command has its own foreground group inside that session.
Control/status pipes remain separate from terminal data.
Relay buffers are bounded. After owned-session settlement, terminal output
drains under a two-second no-progress deadline rather than a total-output
deadline; continuous progress does not discard a large command's tail.
The relay continues bounded nonblocking output progress while waiting for
settlement, so BSD terminal closure cannot wait on its paused master consumer.

Retirement covers every ordinary job group in the private session, not just
the initial group. Kernel `setpgroup` admission places owned child anchors only
in that session; unreaped anchors pin group IDs through signalling. The caller
retains the guardian unreaped until a whole-session census confirms no live
members. A permission failure or partial census is not settlement.
Stopped commands suspend the caller's foreground job group, including a shell
wrapper, for normal suspend/resume. Without a
controlling terminal, a stopped command fails explicitly and is cleaned up
instead of signalling an unrelated parent group.
Foreground ownership and terminal attributes are restored on exit;
an unrelated foreground owner is never replaced.

This is a finite post-UI relay and custody helper, not a shared terminal server
or daemon. Workloads that explicitly detach into another session through
managed lifecycle APIs remain outside its scope. The noninteractive streaming
API below is preferred for
bounded captured commands. `tests/test_interactive.py` covers strict Windows
teardown plus native descendant and POSIX controlling-terminal behavior.
`tests/test_interactive_session_native.py` additionally exercises an actual
interactive shell with separate job groups, redirected JSON stdout and the
managed-detachment exemption.
The `interactive_completion=true` selector in the manual
`installation-context-acceptance.yml` lane runs this focused packet on Linux,
macOS and Windows; it does not widen required unit-only PR CI.

## Caller-owned native execution with streaming limits

An ordinary synchronous caller that also needs caller-death cleanup uses the
explicit opt-in API:

Durable adapters may call `owned_process.require_caller_owned_support()` before
claiming effects to validate native observation. On Linux, pidfds come from
`os.pidfd_open` when provided, or the native libc entry point when a Python
build omits that optional wrapper. Both paths retain the same kernel descriptor,
identity check and group-exit observation; neither falls back to numeric-PID
liveness. Missing native support remains an explicit failure before spawning.
Admission uses the executor's same unreaped-child and default-`SIGCHLD`
prerequisites, so an incompatible signal disposition also refuses before claim.

```python
from agent_procutil.owned_process import run_caller_owned

result = run_caller_owned(
    argv,
    timeout=15,
    max_stdout_bytes=1024 * 1024,
    max_stderr_bytes=64 * 1024,
    input_text=request,
    capture=True,
)
```

Both byte budgets are required positive integers. They count raw bytes while
stdout and stderr drain concurrently, before buffering or strict UTF-8 decoding.
Arguments containing NUL characters refuse before spawning on both platforms.
The result is `subprocess.CompletedProcess`-compatible, with the original
command's return code and separate, newline-normalized text streams. With
`capture=False`, both streams are discarded and returned as `None`. No successful
partial result, silent truncation or automatic retry is supplied.

Windows creates the suspended target atomically in its strict non-breakaway Job
using `PROC_THREAD_ATTRIBUTE_JOB_LIST`; caller death before resume therefore
cannot leave an unassigned suspended target.
Input writing and bounded output draining remain deadline-supervised together;
the Job must report zero active processes and pipe workers must settle before
completion or an output-limit error.
Zero active accounting alone is not a member-handle exit witness. Retirement
pins current members before termination and waits for their native exit within
the same cleanup budget. Birth counters fence the census; an unstable census,
late unpinned birth, failed query or unconfirmed member exit remains a cleanup
error, never a successful partial completion.
Unconfirmed retirement retains its query Job and pinned member handles on the
cleanup error's Job owner. The retained Windows process's
`retire_job(error.job, timeout)` retries those same native exit witnesses;
it does not substitute a fresh census after the original Job handle closed.
An incomplete census or an unpinned birth remains a failure, not a retry that
silently discards unknown descendants.
Worker setup and startup stay inside that boundary, including interruption
before a launched thread has assigned its identity.
Normal early stdin closure preserves the target's result; unexpected input
errors remain explicit.

Native POSIX caller-death execution uses Linux `pidfd_open` and readable `/proc`
pidfd identity, or macOS `kqueue` process-exit events and `libproc` group census.
Both require unreaped-child/default-SIGCHLD custody. Unsupported native API
prerequisites refuse before target launch. macOS uses native `waitid(WNOWAIT)`
even on Python versions that do not expose it; Linux retains its Python/native
pidfd path. One finite
helper owns its session/group before starting the target in that inherited
scope. A dedicated lifetime writer belongs only to the caller; EOF, including
caller `SIGTERM`/`SIGKILL`, retires that group. Synchronized fork callbacks close
active lifetime writers in unrelated fork children as well as excluding them
from exec inheritance. The caller retains unreaped custody
of the helper through the final group sweep and native exit confirmation before
reporting completion. On macOS the guardian registers `EVFILT_PROC/NOTE_EXIT`
against its still-live direct parent, checking parent identity on both sides of
registration; reparenting or a native observation error refuses or retires the
scope. `libproc` confirms there are no live members of the pinned group before
its original leader is reaped. Individual census PIDs are never signalled.
No caller pre-wrapping, persistent daemon,
per-session subscription worker, network endpoint or sibling runtime is needed.
The helper imports only the caller's selected canonical package under isolated
Python, not an ambient CWD or `PYTHONPATH` fallback.

This is native process-group/Job lifetime ownership, not an application
authorization or hostile-code sandbox. POSIX descendants must remain in the
inherited group; deliberate session/group escape, privilege changes and
cross-kernel execution require their own supported owner rather than a claimed
native-tree guarantee.

`tests/test_darwin_caller_owned.py` covers native API construction, parent
identity changes, unavailable observations and wrong-member identities with
synthetic ABI results on every host. Its macOS-only checks exercise actual
kernel construction, bounded execution and abrupt caller death with a finite
target and descendant. Skips on other hosts are not macOS execution evidence.

The complete argument/input control request is limited to 16 MiB. POSIX
completion frames are separately bounded from the declared stream budgets,
with a 64-KiB control-stderr ceiling. Failures remain explicit:

- `OutputLimitExceeded` exposes `stream` and `limit` only after confirmed
  cleanup; its default text never includes captured content.
- `subprocess.TimeoutExpired` retains the original command and timeout;
  interruption also goes through the owned cleanup boundary.
- `OwnedCleanupError` means cleanup is unconfirmed, not that a partial command
  succeeded. It retains the exact `process` and optional `job` custody for the
  owner; retain that evidence rather than reaping an unconfirmed group or
  replaying the operation.
- Invalid input, unsupported custody, malformed completion and invalid UTF-8
  refuse visibly. Native OS errors do not assert that no target effect occurred.

Existing `run_owned()` arguments, default behavior, borrowed-scope kernel checks
and daemon launchers are unchanged.

The focused contracts are in `tests/test_caller_owned.py`; the explicit manual
native lane is `tests/test_caller_owned_native.py`. Prepare the library's `dev`
dependencies, then run these selections through the repository's bounded test
supervisor with the normal 30-second item and 300-second group limits. The native
execution cases are gated to Windows/Linux; input validation and mocked
contracts remain portable, with explicit refusal coverage for unsupported POSIX.
The native
lane builds and installs an offline wheel, removes its source fixture, and
executes actual installed interpreters. Two cycles per scenario cover caller
death (including pre-resume Windows startup and a surviving unrelated Linux
fork child), interruption, blocked input, timeout, both stream overflows and normal
return. Birth-pinned process handles/pidfds observe exit before observer cleanup;
returning callers also check exit before recording their result. Windows checks
visible windows and foreground transitions. These native cases are not added
unconditionally to required PR CI, and are not installed service/client adoption
evidence.

## Launching a resident daemon: `spawn_daemon`

### Attributable instances and explicit test lifetime

`agent_procutil.daemon_instances` is the shared filesystem lifecycle owner.
It does not require an installer record, a running sibling service, a new
singleton election, or an installed-package identity. Source paths are
first-class provenance. Import and inspection never reconcile or start a service.

```python
from pathlib import Path
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, daemon_entrypoint, daemon_test_lifecycle,
)

@daemon_entrypoint("example", source=__file__, runtime_root=runtime_home, purpose="production")
async def serve():
    await run_server()

with daemon_test_lifecycle(owner="example-test", registry_root=test_registry) as overlay:
    # Pass overlay to the actual test process; its lifecycle owner renews it.
    test_env = {**base_env, **overlay}
```

The entrypoint decorator retains admission across synchronous and asynchronous
bodies. `runtime_root` may also be a resolver receiving the decorated callable's
arguments. Nested admission in one process shares a reference-counted receipt.
Production admission starts no expiry thread or extra containment Job.
Raw admission/decorators default to **no purpose**, not protected production.
Without an inherited reservation or actual test-lifecycle declaration they
refuse before application work. A named production service entrypoint declares
`purpose="production"` explicitly; a source pathname by itself is not that
declaration. Zipapp provenance records the verified real archive plus its
existing source member rather than pretending the virtual member is a file.

Pass `instance=DaemonInstanceSpec(service, runtime_root, source, ...)` to
`spawn_daemon`. It reserves before creation, carries a locator into startup
admission, binds native birth identity, and returns `process.instance_receipt`.
`spawn_instance(argv, instance=spec, popen=None, **kwargs)` supplies the same
tracking for an existing raw/callback spawn without changing stdio or detachment.
For a manually controlled spawn, use `reserve_instance`, `instance_environment`,
and `bind_instance`; cancel only a **positively refused** creation through
`cancel_instance_reservation(..., creation_refused=True)`. Lost responses and
uncertain cleanup retain the original record.

An automatic executable provenance is never a library-file fallback. POSIX
lookup uses the actual child environment's `PATH` (including its default when
absent), resolving relative entries/paths against the child cwd. Multiple
distinct executable targets require an explicit instance rather than guessing
which execution-error fallback succeeds. Windows automatic provenance requires
an existing, fully qualified `.exe`/`.com`: `CreateProcess` search does not use
the child's replacement `PATH`/cwd, so a bare Windows command requires an
explicit instance. Normalized Python provenance names the actual executable.

Positive creation refusals cancel only an unconsumed no-PID reservation.
Broker failures distinguish no target creation, staged-but-unadmitted children,
and possibly started work. Only a confirmed pre-admission exit may settle its
tracking record without an application-file cleanup. Unknown exit, identity,
custody or started effects preserve the original receipt and never trigger an
automatic replay. Commit failure carries `instance_receipt` for recovery.

`spawn_finite_worker(argv, instance=spec, **spawn_daemon_kwargs)` declares
`spec.lifecycle="finite-worker"` and retains the same headless/broker lifetime
policy. Production finite workers have **no TTL**. Their Python shim records
`finished` work closure even after the creator exits; this is not a claim that a
process observed its own kernel exit. The same explicit cleanup/backstop settles
only the control record after exact process-birth exit is observed. It never
stops live production finite work or deletes its production artifacts, and
uncertain launch/protected records continue to hold.

Production purpose has no lease. Explicit `unattended-test` purpose defaults to
`DaemonLeasePolicy(ttl_seconds=600, max_lifetime_seconds=14400)`. Interactive
tests must supply their own finite pause-tolerant policy; temporary pathnames,
pytest metadata, and agent-turn activity never select a test policy.
`daemon_test_lifecycle` yields an explicit environment declaration, not a shared
single-use instance. Each child reserves separately. The actual owner process
renews only its run, and its context teardown is the primary cleanup path.
A daemon does not renew itself. Renewal cannot revive expiry or extend the
immutable maximum lifetime.

Leased Python commands receive same-process startup admission before application
code. Windows venv redirectors are bypassed using `direct_python`, retaining
venv imports. The child observes expiry independently of its launcher;
Windows disposable children establish a strict kill-on-close descendant Job.
One local lifecycle thread handles renewal/expiry per process.

The default discovery root comes from the OS user's profile rather than an
isolated `HOME`. Explicit test roots are supported. Schema 1 stores private,
bounded `.instance` files containing `key<TAB>base64(UTF-8 value)` lines and
per-instance `.lock` files. Windows uses the existing byte-lock offset
`1 << 30`; Linux uses whole-file `flock`. Native birth tokens retain raw Windows
creation FILETIME, Linux boot/namespace/start ticks, or Darwin start tuples.
Windows path IDs use raw 64-bit volume/128-bit file identity consistently across
Python versions. The registry stores no argv tails, credentials or environment
dumps.

`cleanup_instance(receipt)` returns `InstanceCleanupResult(instance_id, status,
reason, removed)`, with status `settled`, `held`, `live`, or `missing`. It never
signals an unproved numeric PID. Reused/unknown identities, redirected or changed
paths, protected work and unknown launch outcomes hold. A guard may declare
individual already-created lifecycle files using `add_artifact`; cleanup does
not recursively delete runtime roots, source, installed code, undeclared logs,
or retained evidence. Canonical lock files remain to prevent split custody.
An existing code/config directory may be a borrowed, read-only metadata anchor,
including a root-owned container payload. Recording its identity does not confer
ownership or deletion authority. Registry entries and individually declared
artifacts still require current-user ownership and exact file identity. Missing
explicit runtime directories may be created privately as admission metadata;
callers avoiding business-state creation should use an existing anchor.

`reconcile_instances(registry_root=None, limit=128, max_seconds=5)` is an explicit
installer backstop for **expired disposable** instances and observed-exit
finite control records. Resident production and unexpired disposable records
are preserved. No import, status, lookup or session-start
routine invokes it implicitly. The module CLI offers `inspect` and `reconcile`
with `--registry-root`, `--limit`, and `--max-seconds`.
The additional finite-production control-record case requires observed native
exit and never inherits disposable-artifact reclamation authority.
Collectors resume lexicographically after the last visited canonical instance
key and wrap at EOF, so protected, unexpired and held rows cannot permanently
starve later entries. Count and whole-call time bounds still apply. The same
owned inventory contains `.reconcile.lock` and atomically published
`.reconcile.cursor`; all bindings use the ASCII header
`copilot-daemon-reconcile-v1`, newline, an empty or 32-lowercase-hex key, and a
final newline. This is progress metadata, not an instance or ownership grant.
Nonblocking collector custody serializes cursor advancement across bindings.
Missing inventories remain absent; empty inventories create no progress files.
Invalid, redirected, inaccessible or foreign controls fail visibly without
repair. Inspection never advances the cursor.

The standalone Python-free binding is `daemon_instances.ps1`, with
`New-DaemonInstanceReservation`, `Set-DaemonInstanceProcess`, `Enter-DaemonInstance`,
`Exit-DaemonInstance`, and `Invoke-DaemonInstanceReconcile`. Bash exposes
`new_daemon_instance_reservation`, `enter_daemon_instance`,
`run_daemon_instance`, and `reconcile_daemon_instances`. Both use the same record
protocol. Linux supervision and cleanup use the shared standard-library Python
implementation; the package is a pure Python wheel with no custom native
executable or compiler build hook. Production shell admission still executes
the application directly. Disposable shell supervision and reconciliation
require Python 3.10 or newer, selected through the absolute
`COPILOT_EXTENSIONS_DAEMON_PYTHON` when supplied, otherwise an owned `python3`
resolved from the caller's PATH. They invoke the deterministic package/payload-local
`daemon_instance_runtime.pyz` source archive with `-I -S -B`, so filesystem
custody covers the complete import closure rather than just one entry script.
The archive contains only canonical `.py` files, not bytecode or native code;
`tools/sync-versioned-runtime.py --only-installer-instances` publishes it beside
the source-only installer adapters with non-group-writable permissions.

The interpreter and selected source must have attributable filesystem custody.
Missing, redirected or writable helper inputs refuse rather than downloading
dependencies or substituting an ambient package. Linux requires kernel pidfds,
unreaped-child `waitid` observation and the existing libc subreaper interface.
Optional Python pidfd wrappers may be absent: the shared binding then calls
the existing libc interface through `ctypes`, without compiling a helper.
Owner loss, monotonic lease/hard deadlines and reparented-descendant settlement
retain the matched settlement witness. Linux disposable reservations use record
schema 2: `deadline_mono` and `hard_deadline_mono` are `CLOCK_MONOTONIC` timestamps,
and `clock_domain` binds them to the kernel boot and the current time namespace.
Reservation, renewal, admission, monitoring and collection use that same clock;
epoch fields are informational only. Wall-clock steps cannot extend or shorten
a committed renewal. A different boot/namespace, unavailable namespace identity,
or legacy schema-1 Linux disposable lease refuses admission/renewal and holds
live cleanup rather than guessing a conversion. Older readers reject schema 2;
finish disposable work under its original owner before upgrading or rolling
back. Production records remain schema 1 and never expire. A missing
facility refuses disposable admission or holds cleanup with diagnostics;
there is no numeric-PID signaling fallback. Windows' pre-Python binding is
unchanged by this Linux replacement and retains its separate compiler-free
migration gate. Darwin external
termination currently holds when kernel-pinned custody cannot be established.

Renewable expiry is decided from the current record while holding its mutation
lock. A contended inspection remains explicitly unknown until a current read
or the independent monotonic hard maximum; it is not evidence that a cached
lease expired. Linux shell collection budgets can exceed 300 seconds, while
each CLI process-stop attempt remains capped at 300 seconds.

Windows startup uses ordinary native creation and verifies the tracked
launcher-owned cleanup scopes described below. Legacy Task components remain
available only for explicit owned recovery/removal; normal constructors no
longer adopt them. Source qualification, Main release and authorized installed
rollout remain separate states.

A long-lived daemon (one started by an installer, a hook, a CLI or a cutover
and meant to outlive it) launches through one primitive instead of hand-built
`Popen` kwargs:

```python
from agent_procutil import spawn_daemon, windowless_python, windowless_python_env

proc = spawn_daemon(
    [windowless_python(), "-m", "my_daemon", "serve"],
    cwd=runtime_home,                      # required: runtime root or home dir
    log_path=runtime_home / "daemon.log",  # required: stdout+stderr appended
    env_overrides={"MY_TOKEN": token, **windowless_python_env()},
)
```

It composes the helpers above, so its flags never drift from them:

- **Windowless.** `DETACHED_PROCESS` by default; `console_children=True` uses
  the `windowless_daemon_kwargs()` host for daemons that keep spawning
  console-subsystem children. For a Python host in this mode, use
  `python, extra_env = direct_python(sys.executable)`, pass `python` in argv
  and layer `extra_env` into the child environment. This bypasses Windows venv
  redirectors so process control names the actual worker while retaining its
  venv imports. Opaque executable commands are not rewritten by `spawn_daemon`.
- **Escapes tracked caller cleanup.** `breakaway=True` (default) requests native
  breakaway and starts the Windows child suspended. Canonical Job producers
  assign fresh `Local\ce-owned-job-<uuid>` names and propagate the complete
  semicolon-separated chain in `COPILOT_EXTENSIONS_OWNED_JOB_SCOPES`, including
  strict outer scopes. Explicit child environment replacement cannot drop
  tracked ancestors. The daemon must be absent from every exact named Job,
  not merely its caller's immediate Job. Missing/malformed scopes or failed
  queries refuse admission. Temporary noninheritable query handles close
  before resume; only the original owners retain continuing Job handles.
  The daemon's environment drops the escaped chain. A retained restrictive
  outer Job is refused even when an inner Job permits breakaway.
  This guarantees escape from tracked launcher-owned cleanup scopes, not
  arbitrary unknown external supervisors; ambient Job membership alone is not
  a rejection. Native creation denial does not activate another mechanism.
  Unconfirmed exit retains process custody on `DaemonSpawnError.process` and
  prohibits replay. Only a settled refusal permits an explicitly requested
  `contained_fallback=True`, with a warning and log entry accepting the shorter
  lifetime. Explicit `breakaway=False` and contained tests stay contained.
  The returned `Popen` controls the actual daemon. POSIX uses a new session.
- **Explicit cwd.** `cwd` must be an existing absolute directory and must not be
  inside an `installed-plugins` payload, either as given or once symlinks and
  junctions are resolved (`<runtime>/current -> .../installed-plugins/...` is
  refused). Never pass the caller's cwd: a long-lived process pins it, and
  every relative-path child breaks once that directory is removed. Because the
  daemon does not run from the caller's directory, make every path you pass in
  `argv` or the environment absolute.
- **Logged, never `DEVNULL`.** stdin is `DEVNULL`; stdout and stderr are
  appended to `log_path` (which must be absolute; it is never resolved against
  `cwd`, and like `cwd` it must not be inside a plugin payload, since the
  daemon holds it open) after a one-line launch header, and the parent closes
  its own copy of the handle before returning the `Popen`. The header records
  the cwd and the executable (`argv[0]`) only, never the rest of argv or the
  environment, since either may carry a token, password or credential URL.
  Pass an already-redacted `display="..."` label to add context to it. A
  daemon whose operators read its stderr from a separate file passes
  `stderr_log_path=` (validated, created and tightened exactly like
  `log_path`; the same path merges); the header and any breakaway note are
  then written to both logs.

`argv` must be a sequence of arguments: a bare `str`, `bytes` or path raises
`TypeError` instead of being split into characters. `env` defaults to a copy
of `os.environ`; `env_overrides` is layered on top. `extra_creationflags` may
add only a priority class or `CREATE_DEFAULT_ERROR_MODE` to the Windows
creation flags; a flag `spawn_daemon` owns itself (console, process-group, Job
or session, e.g. `CREATE_NEW_CONSOLE` or `CREATE_BREAKAWAY_FROM_JOB`) raises
`ValueError` on every platform, so a caller cannot reopen a console or defeat
`breakaway=False` and contained test mode. Interpreter selection stays with
the caller (`windowless_python()` / `windowless_python_env()`).

### Explicit finite Windows broker

`windows_task_broker` supplies a consumer-owned on-demand Scheduled Task
implementation. It requires the optional `windows-broker` extra, which reuses
`agent-zdd`'s stdlib-only private native transport. Ordinary process operations
retain no runtime dependencies and need no sibling plugin or service.

Only the consumer's canonical installer, under its existing execution driver,
calls `reconcile` with its validated installation identity, private durable root
and already-qualified immutable runtime. Registration has no triggers, saved
password, elevation or network listener. Installed action and cwd cannot point
into a payload, checkout or session. Missing adoption fails explicitly; normal
launches never install or repair it. Update drains finite accepted transactions
without stopping them; stop/uninstall disable admission and report that already
transferred child control remains the consumer's responsibility.

`windows_broker_installation.registration(root, slot)` is the fixed numbered
installation factory: identity derives from the normalized durable root, and
the action uses that complete immutable slot's direct base `pythonw.exe` and
installed `agent_procutil/windows_broker_entry.py`. The canonical interpreter
selector bypasses the Windows venv redirector; the fixed entrypoint supplies its
own installed import directory without a saved environment. The slot's canonical
completion validator is used; mutable dev slots, missing helpers and linked
paths refuse. `load(root)` reads that root's adoption without trusting ambient
owner/config hints or repairing it. The explicit Task client's `spawn` calls
`load_current()`, binding discovery to this module's fixed installed
`versions/<generation>/Lib/site-packages/agent_procutil` location. It does not
use `sys.prefix` (which can name the base interpreter under `-S`), the caller's
cwd or owner/config environment variables. Source, vendored and linked import
layouts refuse; a newer consumer slot may still use its installation's retained
older broker action. Discovery never registers, repairs or activates a Task.
Legacy explicit installation/recovery can use
`update_driver.windows_broker.adopt` under actual inherited execution custody.
Normal constructors and `spawn_daemon` do not invoke it or this Task client.
Ordinary process operations remain stdlib-only.

`reconcile` records credential-free publication intent before changing the Task
or adopted configuration. While that record exists, ordinary launches refuse
with `installer recovery required`; they never repair it. The next explicitly
owned installer can call `recover` (also called at the start of `reconcile`)
to restore the original action, enabled state and configuration. Recovery
accepts only the recorded old/new action and configuration, refuses foreign
state and keeps its record if restoration cannot be confirmed. Repeated
adoption failure preserves a pre-existing Task rather than uninstalling it.
The canonical execution driver must own mutation and settle its interrupted
execution fences first. This record introduces no second mutation lock or
automatic request replay. Both immutable generations must remain retained
until publication or recovery removes the record. For canonical numbered
installations, adoption publishes owner-scoped `runtime-references.json`
barriers through the action slot's installed retention helper **before** Task
mutation. Canonical GC and candidate removal honor those barriers after the
installer process dies. Recovery shrinks them only after restoring Task and
configuration and removing pending intent. A missing canonical helper or
malformed reference evidence refuses rather than falling back to a process-local pin.
`uninstall` confirms owned Task removal before removing adoption metadata and
releasing that owner's barriers; an interrupted removal can be retried without
touching another registration. Non-versioned independent installations retain
their own immutable-action lifecycle and have no canonical slot collector.
These are process-crash
recovery semantics, not a claim of power-loss atomicity across Task Scheduler
and filesystem storage.

Each activation supplies only a fresh pipe rendezvous and public caller
PID/birth metadata through Task Scheduler. The client pins the actual scheduler
instance, checks its executable and authenticates that process as the native
pipe creator before sending argv or environment. Owner-only local pipes reject
other users and remote clients. The actual child is staged suspended and must
be outside **every** Job. The client duplicates its native control handle
through the already-pinned broker process, checks PID/birth/principal and Job
membership, and adopts it before resume. A lost or ambiguous acknowledgement
is a failed transaction with explicit cleanup state, not permission to launch
another copy. Failed retirement retains retryable custody and blocks new work.

The finite instance stays alive until the client's final commit and pipe close;
the caller joins only that broker, not its resident. Independent custody before
finite exit is the exemption from a resident broker's graceful-cutover contract.
Containing tests refuse activation. The disposable native lane separately records
which installer and normal-launch adapters it actually exercised; that evidence
does not qualify full cold construction, every maintenance consumer, interactive
desktop visibility, promotion or deployment.

Scheduler failures expose only fixed phase names, a numeric native HRESULT
and bounded same-machine monotonic elapsed milliseconds when available.
JSON-cmdlet discovery and conversion have distinct phase markers; missing or
invalid timing is `unobserved`. The last observed phase is not proof that the
operation stalled there. Diagnostics never disclose launch arguments,
environment values, registration contents or arbitrary native exception text.
These measurements do not extend activation or cleanup deadlines.

A child that must not outlive the process that spawned it is spawned inside a
kill-on-close Windows Job Object:

```python
proc, job = await spawn_in_kill_on_close_job(
    *cmd, stdout=asyncio.subprocess.PIPE, **no_window_kwargs(),
)  # job: JobHandle | None
```

On Windows the child is created suspended, assigned to the job, and only then
resumed, so it can neither run nor start descendants outside the job while the
job is armed. Keep the returned `JobHandle` referenced for as long as the child
should live: when it is closed (explicitly, or when the owning process exits for
any reason) Windows terminates every process still in the job. It is
best-effort: `job` is `None` off Windows or when the job can't be armed (the
child still runs). In that degraded case ordinary cleanup still applies, but a
hard-killed owner can orphan the child. Do not use it for children meant to
outlive their launcher (detached daemons, keepers).

The synchronous helper also supports `require_job=True` to refuse Windows
execution before resume if ownership cannot be armed, and
`allow_breakaway=False` to omit the Job's explicit-breakaway permission.
Bounded captured commands use both options. Defaults retain deliberate
service-child breakaway for existing callers; strict mode prevents descendants
escaping this Job. With nested Jobs, Windows may accept a breakaway launch
while retaining the child in the strict Job; cleanup, not launch failure,
is the required witness.

Callers that must prove cleanup before deleting owned runtime files use
`job.terminate_and_wait(timeout_seconds)` rather than best-effort `close()`.
The strict operation terminates the exact owned Job, checks native API results,
waits for its active-process count to reach zero, and only then closes the handle.
Native failure, invalid/expired timeout or an already-closed handle is an
explicit error; uncertainty retains the handle for retry. This API grants no
authority over unrelated processes and never substitutes a name-based kill.

For build transactions that must own every descendant, synchronous spawn uses
`require_job=True, allow_breakaway=False`. Assignment failure kills the still
suspended child before it executes; the owned Job does not permit breakaway.
Conflicting breakaway creation flags are rejected. The default
`allow_breakaway=True` remains compatible with existing callers that deliberately
launch survivable daemons.

## Opt-in Windows commands from WSL

`agent_procutil.wsl.run_windows_owned` is a separate, text-mode bounded-command
interface. Existing Windows/POSIX spawn APIs and daemon lifetimes are unchanged.
The caller supplies an absolute WSL-invokable **base `pythonw.exe`** path; the
library never discovers or installs a Windows interpreter. WSL's normal
`wslpath` mapping must make this installed package readable by that interpreter.
The dependency-free owner starts with isolated, no-site Python flags, so an
unavailable mapped package cannot fall back to the working directory,
`PYTHONPATH`, user customization or another Windows installation.
An unavailable interpreter, mapping, import or strict Windows Job refuses
execution rather than falling back to an unowned command.

```python
from agent_procutil.wsl import run_windows_owned

result = run_windows_owned(
    windows_command,
    windows_python=selected_windows_pythonw,
    timeout=10,
    input_text=request_text,
)
```

The per-call windowless Windows owner first places **itself** in a strict
kill-on-close Job. Target creation therefore inherits Windows ownership before
execution, including the suspended-create/assignment interval of its nested
ordinary bounded command. The caller keeps a dedicated input pipe open:
caller exit, hard death or interruption closes it, and the Windows owner exits,
releasing the self-Job and its descendants. Creation, registration and closure
of its writer share the POSIX caller owner's fork lock and lifetime registry;
unrelated fork children close inherited writers instead of extending ownership.
Setup interruption also closes the pipe and waits for owner exit before
propagating, including when the caller catches and retains the exception's
traceback before any request has been sent.
This is a bounded command helper,
not a persistent service or a POSIX-group claim over Windows processes.

Target stdin, stdout and stderr remain distinct. A framed request carries text
input independently of the lifetime signal, and the result returns separate
captured text streams and the Windows command's actual return code. Requests
and serialized responses have explicit byte bounds; target capture retains the
ordinary `run_owned` memory contract. No command text, input or raw exception is
logged by the interop wrapper. Missing or interrupted completion is explicit
and never permission to replay an uncertain command.

Portable protocol tests are separate from real WSL/Windows qualification.
Timeout, blocked-stdin, SIGINT and caller-death cases, including an unrelated
fork child remaining alive, must inspect exact owned descendants
before an outer fixture closes its own Job. Native Job/stdio success alone is
not that cross-OS proof.
The unrelated-fork control requires Linux pidfd observation and keeps the WSL
launcher alive, so launcher exit cannot mask caller-owned retirement.

The opt-in native suite uses `AGENT_PROCUTIL_WSL_TEST_DISTRO` to select an
existing WSL distribution and runs each failure case twice while observing
visible windows and foreground changes. It never provisions WSL or installs a
Windows interpreter. Set `AGENT_PROCUTIL_WSL_TEST_PACKAGE_ROOT` to the
Windows-visible `site-packages` directory of an isolated, wheel-installed
Linux test environment to exercise the installed package instead of source.
Run `python -m pytest libs/agent-procutil/tests/test_wsl_native.py` on Windows.
These native cases stay opt-in; portable protocol/refusal tests remain in the
ordinary library suite.

## Optional owned-process CPU observations

`agent-procutil[metrics]` adds psutil-backed, read-only observations. The default
installation and ordinary helper imports remain stdlib-only. Even importing
`agent_procutil.metrics` does not load psutil until a sample is requested.

```python
from agent_procutil.metrics import OwnedProcessCpuSampler, ProcessIdentity

sampler = OwnedProcessCpuSampler(
    ProcessIdentity(pid=owned_pid, created_at=creation_time_from_owned_spawn),
)
observation = await sampler.sample()
```

Supply the root PID and creation time captured at the caller's own spawn seam,
not an arbitrary PID inferred later. The sampler does not establish ownership
authority. It verifies identities before and after reads, retains observed
descendants across reparenting, and never adopts a reused PID.

`observed_cpu_seconds` is the sum of observed per-identity CPU maxima, or `None`
when no trustworthy counter is available. It is always a lower bound:
polling can miss short-lived children or their final CPU interval. `issues`
explicitly report missing/denied/reused identities, unavailable metrics and
counter regressions; `truncated` reports exhausted discovery/retention capacity.
Neither field is a cleanup verdict or permission to kill a process.

At most 128 identities are retained for the sampler's lifetime, and each sample
performs at most 128 child-discovery attempts. Completed samples are reused for
one second; concurrent callers
join one cancellation-protected native future. Cancelling a caller leaves
`pending_sample_future` tracked until its sampling operation actually completes;
that running future cannot be cancelled by an asyncio wrapper or loop shutdown.
The wait does not leave a shield wrapper to report a second, unhandled error
if the native operation fails after its caller was cancelled.
The caller can boundedly await `asyncio.wrap_future(holder)` at shutdown without
claiming native completion from a cancelled coroutine. The sampling thread is
daemon-scoped to its owning process and cannot outlive that process.
Each uncached attempt shares one psutil parent-table snapshot across retained
identities and newly discovered descendants, including reparented survivors.
Edges are only discovery candidates: fresh PID/birth and parent checks still
validate adoption. A failed snapshot remains explicitly unavailable/denied;
partial results never become a successful empty topology. The snapshot is not
reused between attempts. Its host-wide enumeration remains proportional to the
system process table; the identity cap alone does not prove host-independent
constant sampling cost.

The one-snapshot count covers discovery, not every native parent-table read.
Fresh child-parent validation still calls each candidate's ppid(); on Windows
psutil 7.2.2 this performs another global parent-table enumeration. Thus a cold
attempt discovering D children performs one discovery enumeration plus D fresh
validation enumerations for a stable tree. An unchanged warm attempt performs
one enumeration regardless of retained identity count, and cached reuse performs
none. New candidates can add validation work on later attempts too. The native
Windows regression profiles the underlying C enumeration entrypoint (including
all Python aliases), checking cold counts of one plus newly retained descendants
and exactly one warm enumeration for both one- and four-child fixture trees
(including their real interpreter/console descendants). It preserves fresh
validation rather than treating
snapshot edges as ancestry authority. These are work counts, not aggregate CPU
or latency qualification.

Each actual attempt has immutable `SampleAccounting`: a random 32-character
sampler instance, a bounded sequence, `pending`/`succeeded`/`failed` state,
monotonic start/completion, elapsed sampling `wall_seconds`, worker
`thread_cpu_seconds`, and bounded timing `issues`. `observation.sample` carries
the completed successful attempt. Cached reuse returns the same observation,
provenance and cost; deduplicate by `(instance, sequence)` rather than adding
the cost again. Recreating a sampler or exhausting its 63-bit sequence creates
a new instance.

`current_sample` exposes only the current/last attempt.
`sample_accounting_future` pins its native completion accounting, including
backend failures that still raise to ordinary sample awaiters. Cancellation,
loop shutdown and join timeout never finalize this uncancellable accounting
future. A worker-start failure is explicitly failed with unknown timing.
Timing failures preserve a valid process observation and mark unavailable,
invalid or regressed clocks as unknown, never invented zero. Both duration
clocks are read in the sampling thread; native CPU counters may be quantized
independently of wall time. Collector CPU is not the observed process-tree
CPU lower bound, and must never be added to it.

CPU enforcement, consumer role aggregation and process-tree reclamation are
not provided by this API. Run its focused and native tests with the library's
`dev` and `metrics` extras under the adopting repository's bounded test runner.

## Vendoring

**In dev**, most consumers' `pyproject.toml` reference this library through
a `uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1) --
`agent-procutil = { path = "../../libs/agent-procutil", editable = true }` --
so those consumers resolve to this one source tree with nothing to keep in
sync. At least one consumer (`agent-worktrees`) ships a real local copy in
dev too, per its own self-contained build-surface requirement for its
status-monitor cutover feature.

**At release**, `tools/materialize_main.py` rewrites every remaining
`uv`-editable pointer into a real, promoted copy at
`<consumer>/libs/agent-procutil/` for that consumer -- `plugins/<plugin>/libs/
agent-procutil/` for an ordinary plugin, or a top-level, out-of-plugin
consumer's own root (e.g. `worktree-manager/libs/agent-procutil/`) for a
standalone consumer -- non-editable, so a published consumer installs a
self-contained source tree with no cross-plugin `path` reference.
`tools/sync-vendored-libs.py --check` verifies every materialized copy's
`src/` tree and version stay byte-identical to this canonical one and to
each other.
