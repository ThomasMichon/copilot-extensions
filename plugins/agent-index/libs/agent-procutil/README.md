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
