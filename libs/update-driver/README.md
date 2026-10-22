# Durable installation execution core

`agent-update-driver` (`import update_driver`) is a shared, product-independent
SQLite queue. It realizes the reviewed single-installation-execution-owner
contract in ThomasMichon/copilot-extensions#7758. It does not itself select,
acquire, install, or migrate a product.

## Adapter API

```python
from pathlib import Path
from update_driver import (
    RequestSpec, TrustedRunner, enqueue, ensure_worker, runtime_command, wait,
)

# Construct these inside the trusted adapter, never from an arbitrary --command.
runner_argv, runner_env = runtime_command("product.cli", ["update", "--internal-run"])
worker_argv, worker_env = runtime_command("update_driver", ["worker"])
request = enqueue(
    RequestSpec(
        "product", argv=("--ref", "main"), project="sample",
        cwd=str(Path.cwd()), source="selected-repository:main",
        context=(("installation", "default"), ("source-provenance", "configured")),
    ),
    runner=TrustedRunner(runner_argv, str(Path.cwd()), env=tuple(runner_env.items())),
)
worker = ensure_worker(worker_argv, env=worker_env)
result = wait(request.id, output=output_stream, worker=worker)
if result.state != "completed":
    raise RuntimeError(result.error)
exit_code = result.exit_code
```

Every API accepts an explicit absolute `root` for isolated tests or explicit
execution spaces. The default is `Path.home()/".copilot-extensions"/"update-driver"`;
no environment variable silently replaces that authority. A product worker may
call `drain(root=...)`, or use the finite `python -m update_driver worker`
entrypoint. No command/runner submission CLI exists.

New Windows queue directories are created atomically with the actual process
TokenUser as owner and protected, inheritable FullControl for that account,
SYSTEM and Administrators. Elevated execution therefore does not leave a
same-account ordinary caller dependent on administrator or Owner Rights access.
Existing directories and their permissions are not rewritten; this is
creation, not an implicit permission migration or a read-only status side effect.

- `RequestSpec(backend, argv=(), project="", cwd="", source="", context=())`
  carries credential-free identity. Context is sorted unique string pairs;
  cwd is normalized, while argument ordering and spelling remain significant.
  Adapters own semantic option normalization and safe-field projection. Never
  put credentials in argv, source, context, error strings, or runner metadata.
- `TrustedRunner(argv, cwd, timeout=7200, env=(), context_keys=())` stores a complete internally
  constructed pinned invocation. Only Python import mapping (`PYTHONPATH`,
  `__PYVENV_LAUNCHER__`) can be persisted as environment metadata. Authentication
  remains inherited at execution time, never serialized.
  `context_keys` declares the adapter-approved, credential-free environment
  projection from `RequestSpec.context`: before product startup, the runner
  clears every declared key and restores only the request's saved values.
  This happens before module import and project routing, not just inside an
  eventual command handler. Absent keys cannot leak from the caller that
  happened to start the shared worker. The declaration participates in pending
  equivalence; driver capability and Python import keys cannot be projected.
  Adapters must retain the original invocation directory as the runner cwd
  when their front door changes directory during project resolution.
- `runtime_command(module, args=()) -> (argv, env)` uses `direct_python` and
  the current runtime's `.runtime-bootstrap/runtime_launcher.py` with
  `-I -S -B -X utf8` when present. It never resolves a newer selected slot.
  Genuine source/dev environments without installed-slot evidence use `-m`.
  A completion marker, managed `versions` slot, or bootstrap directory makes
  a missing retention launcher an explicit failure. Linked/reparse-point
  bootstraps refuse rather than degrading to an unretained launch.
- `enqueue(spec, *, runner, root=None) -> Request` commits acceptance using
  SQLite `synchronous=FULL`. Only byte-equivalent normalized **pending**
  requests coalesce; runner and source/provenance participate in identity.
  Requests arriving after acquisition began always receive a later execution.
  Existing unsettled global fences refuse admission with `InterruptedError`;
  a detached caller cannot receive an apparently healthy receipt behind one.
- `status(request_id=None, *, root=None)` reads one Request or a tuple.
  Missing state is empty; status does not mkdir, initialize, repair, or recover
  a database. Unknown schema and malformed records fail explicitly.
- `ensure_worker(worker_argv, *, root=None, cwd=None, env=None, startup_timeout=15)` invokes
  `agent_procutil.spawn_daemon(console_children=True, breakaway=True,
  contained_fallback=False)`. Spawn failure is recorded on pending requests
  and raises `SpawnError`; it never falls back to foreground execution.
  A bounded startup receipt proves that the entrypoint reached the shared
  queue and validated native boot identity (possibly waiting on its lock),
  not that an update has completed. That immutable boot identity is captured
  once per worker and recorded on every transaction it admits.
  The receipt is published only after its file is flushed and closed, so a
  Windows observer cannot race an open writer when consuming it.
  Import failure, early worker exit, and startup timeout remain explicit.
  Failed boot admission records a precise pending error before worker exit
  and never acknowledges startup; the accepted request remains durable.
  Existing global fences also refuse worker launch with `InterruptedError`.
- `wait(id, *, root=None, timeout=None, output=None, poll_interval=.05, worker=None)`
  follows the logfile and returns the durable terminal Request. A completed
  request preserves the canonical integer exit status, including nonzero.
  Interrupted/blocked outcomes have **no** successful exit status.
  Waiting opportunistically acquires the same nonblocking kernel lock to
  reconcile a lost owner's durable `running` row. It never executes installers
  and needs no second update invocation, PID test, or age threshold.
  Pass the actual `ensure_worker` return as `worker`, retaining its canonical
  process handle for observation. Durable terminal outcomes take precedence.
  If that exact handle exits while the request is still pending, the waiter
  reports `SpawnError` only after the same nonblocking lock proves no other
  drainer owns execution. It preserves the pending request and records the
  failure; it never restarts or installs inline. A live other custodian can
  still complete the request normally. Debug/synchronous drains may leave
  `worker=None`; production asynchronous callers should pass the handle.
- `drain(*, root=None, executor=None) -> int` serializes with the existing
  `OperationLock`, holding custody across claim, executor, and durable outcome.
  Contending finite workers wait their turn so the final dequeue/exit race
  cannot lose a concurrent submission. Debug uses this same path, not a bypass.
  An optional trusted `executor(Transaction)->int` must finish all its effects
  and cleanup before returning. Returning an exit code certifies that cleanup
  boundary; exceptions are conservatively interrupted.

## Execution and borrowed custody

The default executor reads each persisted trusted runner over a subprocess
boundary; one product never imports another. The original worker runtime owns
`agent_procutil.owned_process.run_caller_owned`, a finite strict native Job on
Windows or a Linux/macOS caller-death guardian/group. The private shim changes cwd,
sets only safe import mapping and the ephemeral witness, directs stdout/stderr
straight to `<request-id>.log`, and executes the exact pinned product invocation.
On Windows it waits for the product inside the owned Job rather than using
CRT exec, which would retire the root before its replacement settles.
Python output is unbuffered; other programs must themselves flush output.
No multi-minute captured-output buffering is introduced.

`Transaction` exposes `request`, `root`, `log_path`, `assert_owned()`, and
`participant_env()`. The latter creates an ephemeral mutually authenticated
named pipe (Windows) or Unix-domain socket (POSIX), **not** a network service
or scheduler. The capability is passed only in the live subprocess environment
and is never written to SQLite/logs. A nested engine uses:

```python
from update_driver import borrowed_custody

custody = borrowed_custody()
if custody is not None:
    with custody:
        custody.assert_owned()
        execute_canonical_update()
else:
    enqueue_ordinary_request()
```

`borrowed_custody(root=None, environ=None)` returns None only for an absent
witness; missing-required, malformed, forged, wrong-space, or stale evidence
raises `ParticipantError`. `participant(root=None, request_id=None,
witness=None)` requires evidence explicitly. Participants challenge the live
authenticated owner and should reassert before each consequential stage.
Entering a verified custody consumes its matching ambient transport (and the
matching entry in an explicit mutable test-environment mapping), without
restoring it on exit. A differing ambient token is not removed. The live
capability is held in the current context-local scope, not in the installer's
ambient subprocess environment. Exiting resets that scope and closes the
connection; an unrelated child cannot gain authority from ambient inheritance.

For a deliberate, trusted nested **update** operation only, use the new export
`participant_environment() -> dict[str, str]`:

```python
import os
from update_driver import participant_environment

update_env = dict(os.environ)
update_env.update(participant_environment())
invoke_known_update_suboperation(env=update_env)
```

The helper reasserts the currently scoped custody before returning its ephemeral
witness, and returns `{}` outside custody. Nested scopes restore the outer
scope, never ambient transport. Independent thread/context scopes and inherited
foreign-process state do not export the capability. Do not inject this mapping
into ordinary installer/service/daemon launches: only explicitly trusted update
handoff seams should carry it. The private runner still supplies bootstrap
transport to its direct product invocation, which consumes it on entry.
The transaction owner remains the sole journal completer. An environment flag,
PID, `--no-manager`, or a timestamp alone never proves custody. This is a
same-user cooperative capability, not a sandbox against code with access to
the owner's process memory.

## Failure and recovery boundary

Worker death initially leaves `running` durable. A waiter, submitter, or later
worker must acquire the same kernel lock before recording that row as
`interrupted`; this becomes a global fence. Read-only `status` remains a
journal-only view. No age/TTL reclaim, silent replay, unknown-process killing,
automatic fence clearing, or success-shaped fallback exists. A custom executor
must use a caller-owned tree or retain an equivalent conservative fence.

The canonical subprocess owner cleans native descendants before returning.
Unsupported native custody fails closed. POSIX targets must remain in their
inherited native group; deliberate group escape, cross-kernel commands, and
privilege transitions require their own supported owner. An interrupted fence
is not cleared merely because cleanup probably completed or the lock is free.

### Explicit positive settlement after kernel restart

Schema **2** records `Request.boot_id` at transaction admission, under the
installation lock, before invoking the executor. `kernel_boot_identity()`
reads Linux's kernel boot UUID, macOS `sysctlbyname("kern.bootsessionuuid")`,
or Windows `NtQuerySystemInformation(SystemBootEnvironmentInformation=90)` BootIdentifier.
It never derives identity from uptime, timestamps, PIDs, or a user-set flag.
Unsupported/missing native evidence fails admission explicitly.

```python
from update_driver import recovery_status, recover_after_restart

availability = recovery_status()  # advisory, read-only; does not create state
settled_requests = recover_after_restart()  # explicit mutation, same OperationLock
# No executor ran. A separate explicit normal update may now be requested.
```

- `recovery_status(*, root=None) -> RecoveryStatus` reports
  `current_boot_id`, `eligible_ids`, `refused`, and `error`. Each
  `RecoveryRefusal` contains `request_id` and a precise `reason` code.
- `recover_after_restart(*, root=None) -> tuple[Request, ...]` acquires the same
  `OperationLock` nonblockingly. A live owner, missing/malformed identity,
  platform mismatch, or same kernel boot refuses with `RecoveryError`.
  One unprovable fence refuses the whole reconciliation, not a partial reset.
  Recovery opens an existing database with SQLite `mode=rw`, never creates or
  initializes one, and checks its file identity across reconciliation.
  Missing/disappeared/replaced state refuses explicitly, including a database
  removed between the initial observation and the mutation boundary.
- Only a different native boot UUID on the same platform provides positive
  evidence that old native descendants cannot still execute. Settlement adds
  `settled_boot_id` and `settled_at`, preserving the interrupted state, failure
  text, absent exit status, and original admission identity. A crashed
  `running` record becomes `interrupted`, never successful or replayed.
- Only those positively settled fences stop blocking later work. Existing
  pending work stays pending until an explicit normal worker/update runs.
  Repeating settlement is idempotent. There is no force/TTL/PID reset, manual
  database-edit/delete recipe, root rotation escape, or reboot command.

Schema 1 predates admission boot evidence and is explicitly unsupported rather
than silently upgraded into fabricated recovery authority. Malformed state
also refuses. This new core has not been deployed to live state; these schema
changes affect only isolated fixtures in this change.

Worker queue-startup acknowledgement is not an execution/completion claim.
A worker failing before queue startup records a visible pending launch error;
accepted requests are retained for a later supported launch, not silently
dropped. A worker killed after startup but before claiming work can leave
pending requests; callers passing the returned worker handle to `wait` receive
an explicit failure after free-custody confirmation, even with no wait timeout.
Other diagnostic observers can use a bounded timeout and inspect `worker.log`.
There is no resident monitor to invent an execution outcome.

Ordinary contained-test suppression in `agent_procutil` remains unchanged. Core
synthetic/native-process fixtures never run a real updater or touch live homes.
They demonstrate the shared queue, not a complete installation migration or
authorized live rollout.

## Validation

From the repository root, the bounded native runner selects this library:

```text
python tools/run-plugin-tests.py update-driver --test-file tests/test_core.py --test-file tests/test_process.py --test-file tests/test_recovery.py --test-file tests/test_participant_transport.py
python -m ruff check --select F,E9 libs/update-driver
```

The fixture products have no installer/network behavior. Separate processes
exercise concurrent enqueue, serialized real transaction effects, output
before completion, canonical failure status, caller exit, worker death, and
the last-empty-scan/lock-release wakeup race. Recovery tests read the actual
native boot UUID repeatedly for stability and use synthetic old/new admission
identities for settlement/refusal; **the host was not rebooted**.

The opt-in Windows production-lifetime lane is separately bounded:

```powershell
$env:UPDATE_DRIVER_NATIVE_LIFETIME='1'
python tools\run-plugin-tests.py update-driver --test-file tests\test_native_lifetime.py --test-timeout 90 --timeout 240
```

Only that lane's isolated submitter removes contained-test suppression for its
production branch. It uses a real windowless caller in a strict kill-on-close
Job, forces canonical broker escape, pins the returned actual worker handle
before Job closure, and retains bounded caller-owned cleanup. Two production
transactions survive actual submitting-Job closure and produce durable
canonical exit status 7 afterward; two contained-worker controls and ordinary
bounded children die with that Job. The canonical procutil window observer
asserts zero new visible consoles or foreground transitions across the cycles.
No scheduler, service, live installer, or home-state update is involved.
Linux guardian behavior is reused; macOS uses the canonical helper's native
kqueue parent-death observation, unreaped-child custody and libproc group census.
Synthetic ABI and dispatch tests cover its construction and refusal paths;
macOS-only native lifetime tests remain separately runnable. This change's
recorded production-lifetime execution is Windows, not a Linux/macOS native
lifetime or real-reboot qualification claim.
