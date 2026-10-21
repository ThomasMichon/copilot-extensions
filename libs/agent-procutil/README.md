# agent-procutil

Shared **Windows-headless / detached / bounded-owned** process primitives for
Copilot CLI plugins. One vendored helper keeps launch flags and finite child
containment consistent instead of reimplementing them in each consumer.

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

## Caller-owned native execution with streaming limits

An ordinary synchronous caller that also needs caller-death cleanup uses the
explicit opt-in API:

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
Worker setup and startup stay inside that boundary, including interruption
before a launched thread has assigned its identity.
Normal early stdin closure preserves the target's result; unexpected input
errors remain explicit.

Native POSIX caller-death execution currently requires Linux `pidfd_open`,
readable `/proc` pidfd identity, and the existing unreaped-child/default-SIGCHLD
prerequisites. Unsupported prerequisites refuse before target launch. One finite
helper owns its session/group before starting the target in that inherited
scope. A dedicated lifetime writer belongs only to the caller; EOF, including
caller `SIGTERM`/`SIGKILL`, retires that group. Synchronized fork callbacks close
active lifetime writers in unrelated fork children as well as excluding them
from exec inheritance. The caller retains unreaped custody
of the helper through the final group sweep and poll-based pidfd exit confirmation
before reporting completion. No caller pre-wrapping, persistent daemon,
per-session subscription worker, network endpoint or sibling runtime is needed.
The helper imports only the caller's selected canonical package under isolated
Python, not an ambient CWD or `PYTHONPATH` fallback.

This is native process-group/Job lifetime ownership, not an application
authorization or hostile-code sandbox. POSIX descendants must remain in the
inherited group; deliberate session/group escape, privilege changes and
cross-kernel execution require their own supported owner rather than a claimed
native-tree guarantee.

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
- **Escapes the caller.** `breakaway=True` (default) adds
  `CREATE_BREAKAWAY_FROM_JOB` on Windows so a kill-on-close Job around the
  caller (an installer or updater) does not kill the new daemon; POSIX gets a
  new session. When the caller's Job forbids breakaway (`ERROR_ACCESS_DENIED`),
  the shared launcher uses a bounded, windowless WMI bootstrap outside that
  Job. Its bootstrap uses the actual base interpreter with WMI's supported
  detached-process flag, not a venv redirector or a console window. It passes
  the original argv, cwd, complete environment, creation policy
  and split log paths over an authenticated, one-use loopback channel; neither
  credentials nor shell-interpolated daemon arguments go on the broker command
  line or into temporary files. The actual daemon is staged suspended, its
  native process handle is adopted and its independent lifetime checked, then
  it is resumed. Failed uncommitted launches are reaped before a retry.
  Cleanup branches run independently and preserve `DaemonSpawnError`;
  `cleanup_confirmed=False` reports uncertain process exit and prohibits even
  an explicitly requested contained retry.
  The returned handle names the **actual daemon**, not PowerShell, WMI or a
  console wrapper, and supports `pid`, `poll()`, `wait(timeout=...)`,
  `terminate()` and `kill()`. Brokerage has a 30-second launch deadline with a
  bounded cleanup wait. If both direct breakaway and brokerage fail,
  `DaemonSpawnError` (an `OSError`) is raised and the daemon log records why.
  A caller for which the shorter lifetime still beats no daemon may explicitly
  pass `contained_fallback=True`; only after brokerage fails is that degraded
  launch attempted, with a warning and a log entry. `breakaway=False` and
  contained test mode never invoke the broker. Such contained daemons die when
  the caller's Job closes.
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
join one shielded native future. Cancelling a caller leaves
`pending_sample_future` tracked until its sampling operation actually completes;
that running future cannot be cancelled by an asyncio wrapper or loop shutdown.
The caller can boundedly await `asyncio.wrap_future(holder)` at shutdown without
claiming native completion from a cancelled coroutine. The sampling thread is
daemon-scoped to its owning process and cannot outlive that process.
psutil may inspect system process tables internally; the identity cap alone
does not prove host-independent constant sampling cost.

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
