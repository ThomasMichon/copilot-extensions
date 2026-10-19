# single-instance-lease -- one live daemon per service per host

Service-neutral primitives that make "at most one active daemon owns a given
service on a host" an *asserted, repairable* property. Extracted from
agent-bridge (which proved the design in production) so multiple consumers reuse
one implementation instead of reinventing it.

- **Distribution:** `agent-single-instance-lease` (the `agent-` prefix avoids
  PyPI dependency-confusion; the package is never published to an index --
  consumers install it from a local path).
- **Import module:** `single_instance_lease`
- **Runtime deps:** none (pure stdlib).

Realizes the `single-instance-lease` behavior of the plugin-services vision: a
service acquires a host-local lease before it becomes the active endpoint; a
process that cannot acquire it stands down rather than racing; ownership is
liveness-reconciled, not timer-guessed; and a cutover reconciles the full set,
retiring every predecessor *and* every stray a plain restart would strand.

## What's inside

### `operation` -- custody for one operation

`OperationLock(Path(lock_file))` holds an exclusive, non-blocking lock only
through one context-manager operation. It reuses the lease's OS locking
primitive without borrowing daemon-lifetime or installation ownership.
The parent directory must already exist at an absolute, resolved path.
Linked/nonregular lock files are refused, `assert_owned()` verifies the
opened file still occupies that path, and only the acquiring process and
thread may assert or release custody. Separate handles in the same process
also exclude one another on Windows.
Acquisition publication and release use the same process-local guard, so a
losing concurrent attempt on one instance cannot clear its successful owner.
Fork preparation serializes descriptor setup/close; the child closes inherited
operation descriptors without issuing an unlock against the parent's open-file
description. Inherited operation objects remain invalid for the child's execution.

The lock file is never removed and no PID or age is used to reclaim it.
Closing the descriptor releases custody; a process crash does the same through
the kernel. This does **not** attest that a dead owner's subprocesses stopped,
or settle its unfinished work. A consumer must retain its own durable state
and require the relevant positive completion witness before recovery.
Uncooperative file replacement is detected at ownership checks, not prevented
by filesystem compare-and-swap.

### `lease` -- the single-instance lease

`SingleInstance` takes an OS-level, exclusive, non-blocking lock on a lock file
and holds it for the process lifetime. Because the kernel releases the lock when
the holder dies (graceful exit, crash, kill, or power loss), ownership is
**liveness-reconciled by construction** -- a lease held by a dead process is
immediately acquirable, and there is never a stale lock to "detect" or "reclaim".

```python
from single_instance_lease import SingleInstance, AlreadyRunningError

lease = SingleInstance("~/.agent-vault", service="agent-vault", port=9820)
try:
    lease.acquire()          # raises AlreadyRunningError if one is live
except AlreadyRunningError as exc:
    ...                      # a peer already owns it -- stand down
else:
    try:
        run_server()         # keep `lease` referenced for the daemon's life
    finally:
        lease.release()
```

Keying on the optional `port` lets an active and a passive daemon coexist on one
`lock_dir` during a zero-downtime cutover (they bind different ports, so they
take different locks), while two starts on the *same* port still collide.
Cross-platform: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows.

### `supersession` -- the self-retire decision

`is_superseded(table, my_pid, my_generation)` is the pure, fail-safe decision a
*demoted* daemon uses to exit on its own once a live, strictly-newer generation
has taken over. It operates on a plain routing-table `dict` (the `active` /
`previous` shape published by `zdd.routing`) so the library has **no routing-lib
dependency** -- the consumer reads the table however it likes. Returns `True`
only when the `active` entry is a *different* pid, at a *strictly higher*
generation, that is *actually listening*; every ambiguous state returns `False`
(stay alive).

### `reaper` -- the reconcile-set backstop

`reconcile_set_reap(own_pids, active_pid=..., terminate=...)` retires every one
of a service's own processes that is not `active` (nor `self`), fail-soft. The
caller supplies pids it has **positively identified** as this service's own
(identity is the caller's responsibility -- the guard against pid reuse) plus a
`terminate` callback; the reaper applies only the policy. `superseded_pids_from_table`
is a helper that harvests candidate stray pids from a routing-table `dict`.

## Testing

```
cd libs/single-instance-lease
python -m pytest
```

## Vendoring

**In dev**, runtime consumers' `pyproject.toml` references this library through
a `uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1) --
`agent-single-instance-lease = { path = "../../libs/single-instance-lease",
editable = true }` -- so every consumer resolves to this one source tree; there
is no per-plugin dev copy to keep in sync.
The standalone `customizing-copilot` payload is the explicit exception: its
vendored `plugin-activation` library carries a real sibling copy for a
self-contained dependency closure without a consuming runtime `pyproject.toml`.

**At release**, `tools/materialize_main.py` rewrites that same pointer into a
real, promoted copy at `plugins/<plugin>/libs/single-instance-lease/` for each
consumer -- non-editable, so a published plugin installs a self-contained
source tree with no cross-plugin `path` reference. `tools/sync-vendored-libs.py
--check` verifies every materialized copy's `src/` tree and version stay
byte-identical to this canonical one and to each other.
