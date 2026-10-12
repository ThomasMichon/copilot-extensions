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
