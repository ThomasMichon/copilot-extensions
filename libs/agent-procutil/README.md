# agent-procutil

Shared **Windows-headless / detached** process-spawn kwargs for Copilot CLI
plugins. One vendored helper so every plugin suppresses console-window flashes
the same way, without each reinventing the creation flags.

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
