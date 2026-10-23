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
  Default execution also checks native caller-owned process support before
  claiming a request or acknowledging startup. An unsupported runtime leaves
  a precise pending admission error, not an interrupted installation fence.
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
        with custody.execution_scope():
            execute_canonical_update()
else:
    enqueue_ordinary_request()
```

`execution_scope()` registers a cleanup obligation with the existing owner
before product execution. Only an ordinary return certifies completion and
releases it; an exception makes the participant unusable for further mutation.
If that participant exits or converts the exception into an integer status,
the owner still records interruption and refuses later queued work until the
existing positive-settlement policy permits recovery. Nothing creates a second
queue, lock or journal completer. New scope messages require an explicit owner
acknowledgement; a downlevel owner refuses before the body can mutate rather
than silently ignoring the obligation. Legacy ownership assertions, including
the native foundation builder, keep their existing wire contract.

Short Unix endpoints retain their existing location under the execution root.
If that pathname exceeds the native byte limit, the same witness instead owns
a short-lived, user-private temporary directory and removes it when the witness
closes. Clients verify that directory's owner and private permissions; the
authenticated execution root and request identity remain unchanged. No queue
is relocated, second owner created, or capability written to a file. A
temporary-folder configuration that cannot provide a short endpoint refuses
explicitly rather than weakening authentication or selecting another queue.

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

### Native foundation progress and interruption

The earlier Python-free construction boundary has a separate, narrowly scoped
progress record, **not another request queue or installation owner**:
`<execution-root>/foundation-operation.json`. It uses
`copilot-extensions.installation-foundation-operation`, version `1`, and the
same `<execution-root>/execution.lock`. SQLite remains schema 2; no request
history is deleted, migrated or moved to another execution root.

`foundation_status(root=...) -> FoundationOperation | None` reads that bounded,
plain-file record without creating anything. It records only operation/source
digest identity, native admission boot, state, timestamps, canonical exit status,
bounded failure reason codes and explicit old-boot settlement. No submitted
commands, credentials or participant capabilities belong there. A recorded
`running` state is journal evidence, not proof that its owner remains live.
Missing progress alongside existing `foundation/` artifacts is a readiness
error, never permission to fall back to a legacy installer.
Python and Linux native readers retry at most three times when an ordinary
file's path/descriptor identity changes during atomic replacement. Each attempt
reopens and validates the complete snapshot; persistent replacement, nonplain
inputs and malformed data remain explicit errors, not stale/default results.

Trusted producers use `foundation_state.begin`/`finish` only while holding the
actual canonical `OperationLock` for this execution root, or a live authenticated
`BorrowedCustody` for that same root. Acceptance is
published before mutable foundation staging; writes flush data and use
write-through replacement on Windows or directory fsync on POSIX. An integer
outcome certifies the producer's complete cleanup boundary. Unknown settlement
has no exit status and remains interrupted. A second begin cannot overwrite
its active or interrupted operation, and completion must match the exact
admitted record.

Admission also reads any existing SQLite queue without creating one. Unsettled
running, interrupted or blocked plugin work refuses foundation construction.
Only the exact currently executing transaction under the same live lock may
enclose foundation work; an abandoned running row is not that authority.
Orphaned SQLite sidecars without their database also refuse, rather than
creating an apparently empty replacement. Windows readers share deletion; write-through publication retries bounded
reader-sharing conflicts under custody. A persistent conflict fails explicitly
without reporting completion or discarding the prior progress.

Pending plugin requests may be accepted behind a **live** foundation owner,
but only the kernel custodian can execute. A later free-lock observation changes
unfinished native progress to `interrupted`, never success or automatic replay.
Enqueue, worker launch, wait, drain and pending-worker failure observation all
retain that global fence. An executor that leaves native construction uncertain
cannot commit its own request as successful.

`recover_foundation_after_restart(root=...)` explicitly settles only a
different, positively observed native boot on the same platform. It preserves
the interrupted outcome, source identity, absent exit status and original
admission identity; it never rebuilds or executes pending work. A live custodian,
same boot, malformed or lost state, platform mismatch or changed observation
refuses. Ordinary queue recovery reports `native_foundation_recovery_required`
instead of pretending the execution space is clear; settle that native record
first, then separately apply the existing queue recovery when required.
Deleting control files or rotating roots is not recovery.

On Windows, the packaged `update_driver/foundation.ps1` provides the same
progress protocol without Python. Load the canonical native operation lock
first. `Start-NativeFoundationProgress -Custody <held-object> -Root <execution-root>
-Plugin <absolute-update-driver-source>` hashes the fixed three-sibling
non-generated inventory and publishes running progress under that exact lock.
`Get-NativeFoundationProgress -Root <execution-root>` is read-only; it returns
the validated JSON record or no record. It rejects duplicate/unknown fields,
nonprimitive or invalid types, inconsistent outcomes, linked paths, invalid
UTF-8, trailing content and records over 32 KiB. Native boot evidence comes
directly from the same Windows kernel query as the Python implementation.

`Complete-NativeFoundationProgress` takes the same custody, root and plugin,
the exact admitted JSON as `-Operation`, an optional `-ExitCode` and a bounded
`-Reason`. Zero requires unchanged source bytes; a nonzero code requires a
reason; an absent exit status requires an uncertainty reason and leaves the
interrupted fence. Only a trusted producer that has settled its entire owned
child tree may certify an integer outcome. Persistence uses a flushed,
write-through temporary and bounded atomic replacement; it never edits the
queue or deletes foundation artifacts.

This native entry is deliberately **cold-only**: any existing SQLite queue or
WAL/SHM/journal sidecar requires the established queue-aware Python authority.
It does not interpret SQLite, assume a free lock settles an old request, or
rotate the execution root to evade queued/uncertain work. The Python
`foundation_state.begin` remains the upgrade/enclosing-transaction path.
Native inventory hashing binds bytes, not TOML semantics; the fixed builder
still validates package names, dependencies and local references before staging.
No acquisition or mutable construction is performed by these progress helpers.

These progress helpers are not ordinary plugin entry points. The finite producer
below owns prerequisite acquisition and construction; first-use adapters remain
separate work.

On Linux, `update_driver/foundation.sh` supplies the corresponding cold progress
and fixed-source helpers in Bash 4.4+, using GNU coreutils/findutils, `flock`,
`iconv` and the existing strict `json-query.awk`. The parser is a declared
standalone asset of `tools/sync-installation-context.py`, not another JSON
implementation or Python runtime dependency. Load the canonical
`single_instance_lease/operation.sh` and acquire the actual root's FD 9 first:

```bash
source "<single-instance-lease-source>/src/single_instance_lease/operation.sh"
source "<update-driver-source>/src/update_driver/foundation.sh"
enter_native_operation_lock "$root/execution.lock"
start_native_foundation_progress "$root" "$plugin"
operation="$__foundation_document"
freeze_native_foundation_source "$root" "$plugin" "$root/frozen" \
    "${__foundation_value[source_hash]}"
# Only a trusted producer's positively settled child tree permits an integer.
complete_native_foundation_progress "$root" "$operation" "$plugin" null cleanup_unproved
leave_native_operation_lock "$root/execution.lock"
```

`read_native_foundation_progress "$root"` never acquires custody or creates
state. It sets `__foundation_present` to `0` or `1`; for a present record,
`__foundation_document` retains the bounded snapshot and
`_native_foundation_encode` prints its validated canonical representation.
Readers reject NUL, invalid UTF-8, malformed/duplicate/unknown fields and
non-finite or inconsistent values. Timestamp tokens and signed 64-bit integer
outcomes retain their exact JSON spelling rather than round-tripping through
awk's floating-point arithmetic.

All mutators reassert the same kernel-proved FD 9. Encoded writes, including
their trailing newline, must fit the reader's 32 KiB limit before any temporary
is created; refusal preserves the prior readable recovery evidence. Writes use a private
temporary, filesystem sync and atomic replacement. `reconcile_native_foundation_progress`
records a lost owner as interrupted without certifying descendant settlement;
`recover_native_foundation_after_restart` requires a different actual Linux
kernel boot and retains the original interrupted identity/outcome. Neither
operation edits or initializes a queue. The same cold-only queue/sidecar refusal
applies before native admission.

`native_foundation_source_digest` and `freeze_native_foundation_source` use the
same UTF-8, byte-sorted three-package inventory as Python and PowerShell,
including both canonical installer-engine variants. Freezing requires a new
destination inside the execution root, which cannot be inside a source package.
Both the root and destination must remain outside all three source packages,
including when the execution root is an ancestor of the selected sources.
It rechecks the original and frozen inventories under custody. These Linux
helpers do **not** launch a producer, acquire prerequisites, settle child groups,
or implement Linux genesis/upgrade; those remain separate integration work.

### Linux foundation producer

`scripts/foundation-submit.sh <execution-root> <full-source-sha256>` launches
the fixed `foundation-produce.sh` through the canonical native process helper.
Its `launched:<pid>` acknowledgement is not durable installation acceptance
or completion: observe `foundation-operation.json` for the admitted operation
and outcome. This is an internal foundation-construction front door, not a
plugin provisioning request or an arbitrary-argv installer.

The producer holds the same FD 9 flock before staging, freezes the fixed
three-package source closure, and acquires pinned uv/private managed Python
through the existing installer-engine helper. It does not require Python or
uv on PATH. The native acquisition guardian keeps its direct session child
alive through Bash's deliberate exec of that private interpreter.
`NativeChildCustody` must retire the entire pinned group before the producer
accepts its integer prerequisite result. The same process consumes the
inherited flock, issues the existing participant witness only to the fixed
builder, and completes native progress after construction and source rechecks.
Failure before private Python or positive group settlement preserves an
interrupted fence, never a success-shaped or settled integer result.
The canonical constructor still requires native `ps` process observation;
the producer refuses a missing observer before creating execution state.

An existing selector or queue/sidecar requires the selected immutable
foundation's own retained `foundation_linux` module and canonical queue
admission. Missing/legacy capability refuses; no queue is initialized, cleared
or reinterpreted by the native shell. Pending requests survive an upgrade,
and the existing immutable-generation/retention contract preserves the old
slot. A matching healthy selection returns without another acquisition.

The explicit manual `foundation` portfolio includes real Linux cold downloads,
caller process-group destruction, a contained-launch control, native
acquisition parent loss, immutable upgrade, pending-request preservation and
source removal. These are separate from fresh clean-room OS qualification,
macOS support, publication-wrapper migration and authorized host rollout.

### Fixed foundation construction participant

On Windows, `update_driver/participant.ps1` supplies the native owner side of
the existing version-1 participant protocol without Python. After loading the
canonical `single_instance_lease/operation.ps1`, call
`New-NativeParticipantWitness -Custody <held-object> -Root <execution-root>
-RequestId <operation-id>`. It requires that actual native object to hold the
root's `execution.lock`; a PID, boolean, environment flag or other lock path
is not custody. Owner-thread issuance creates a retained read-only assertion;
each authenticated exchange rechecks the original handle, path and identity.
Release or owner death invalidates joined and future participants.

`Environment()` returns the ephemeral `UPDATE_DRIVER_WITNESS` value for only
the fixed trusted construction child. Do not persist or log it, or expose it
to prerequisite downloads or arbitrary subprocesses. The unchanged Python
participant consumes the inherited capability before package children.
Close the witness with `Dispose()` before releasing the operation lock, in
`finally`. Same-user named-pipe ACLs, bounded pickle-free JSON/HMAC exchanges,
and independent joined clients preserve the Python protocol. Invalid clients
lose their connection, not the genuine owner's custody; listener failure is
reported by export/close rather than granting alternate authority. This is
cooperative same-user custody, not a sandbox against process-memory access.
The bridge does not launch construction, publish progress, acquire prerequisites
or enable a cold producer.

At most 16 clients may be joined concurrently. Excess connections are refused
without revoking already joined participants; capacity returns after disconnect
observation. Stalled handshakes expire independently. Async I/O retains its
task-owned wait handle through completion/cancellation even after a deadline.

`scripts/foundation-build.py` constructs only this library plus the canonical
`agent-procutil` and `single-instance-lease` sibling packages. The fixed
manifests, local references and complete non-generated source inventory must
match admission. This raw-source closure is identical in shape on development
and materialized payloads. Existing installer-engine/versioned-runtime sync
owners supply its byte-identical construction helpers; Manager and plugin
runtimes are not dependencies.

The internal participant requires a live borrowed execution witness and
matching running foundation progress **before staging**. It consumes the
witness before package children, freezes the admitted source, and uses canonical
`numbered_candidate` construction, non-editable dependency governance, installed
health and process retention. It reasserts custody and source identity before
link-free activation. Native admission remains the sole progress completer.
There is no arbitrary command/package recipe or unlocked construction mode.
Invoke its interpreter with `-I -B -X utf8`; the entrypoint supplies only its
fixed package/helper import paths.

The canonical runtime root is `<execution-root>/foundation`. Slots are named
`foundation-<full-input-sha256>`; that is a content-addressed foundation name,
not a Git commit or a rewritten distribution version. Ordinary canonical
completion and current/last-known-good selectors own publication. Exact input
hashes distinguish same-package-version changes, and failed/unhealthy
construction never selects an incomplete slot. Foundation discovery must bind
the expected complete slot and protocol, never choose another source by age or
lexicographic fallback. Existing retained workers and queue state remain under
the same execution root; bootstrap interpreter files must outlive any dependent
venv and are not removed by this participant.

The construction participant itself does not acquire a bootstrap interpreter,
write admission/completion records or modify a plugin publisher. The separate
Windows producer below owns those earlier construction stages.

`tests/test_native_participant.py` exercises the real stock-PowerShell owner
against unchanged Python participants: simultaneous joins, consumed environment,
kernel-lock contention, forged/mismatched and malformed/oversized messages,
owner release/death, same-path replacement, closed witnesses and absent custody.
`libs/single-instance-lease/tests/test_native_operation.py` independently proves
that an IPC thread may only invoke the issued read-only assertion, not assert
or release ownership. These are native interoperability controls, not a cold
foundation installation or non-Windows native bootstrap claim.

### Finite Windows foundation producer

`Start-NativeFoundationProducer -Root <execution-root>
-Plugin <absolute-update-driver-source> [-ExpectedSourceHash <sha256>]`
starts only `scripts/foundation-produce.ps1`, through the canonical native
outside-Job admission helper. It requires stock Windows PowerShell 5.1 and
returns the actual process handle, **not a completed installation receipt**.
Waiting callers observe that process plus the matching durable foundation
record; they never run acquisition or installers themselves. A launch refusal
does not select a foreground alternative. The direct script is the supervised
debug path and acquires the same kernel authority.

The finite worker holds the exact installation lock across source admission,
prerequisite acquisition, construction, activation and outcome persistence.
Only real kernel lock contention waits; malformed paths, absent capability
and unsupported state refuse. Execution roots inside the source closure are
rejected before control-directory creation. New control roots use the same
TokenUser-owned protected inheritable ACL as the Python queue, and existing
permissions are never migrated.

After publishing progress, native source freezing copies only the fixed three
packages into the operation's private bootstrap tree. Both the copy and the
original must match the admitted inventory before any acquisition helper runs.
The fixed owned acquisition child loads that frozen canonical
`installer-engine.ps1`; the existing sync owner supplies that native helper,
rather than another downloader. `Ensure-Uv` retains its pinned
archive/hash and existing-tool policy. `New-SignedVenv` prefers a compatible
signed base and otherwise uses its canonical managed-Python fallback.
Foundation bootstrap requests Python 3.11 and a 3.11 minimum signed base for
stdlib TOML support; other adopters retain their 3.10 defaults.

The logical UV returned by canonical acquisition is handed to the construction
SDK's existing physical-executable selector before strict path resolution.
Working WinGet links/junctions are not confused with forbidden linked source
or bootstrap-interpreter inputs; no different UV is substituted from PATH.

Each operation retains its bootstrap venv, downloaded base interpreter and
private cache under `<execution-root>/bootstrap/<operation-id>`. These files
are not removed while completed foundations can depend on them. Prerequisite
and builder trees run under strict native Jobs with bounded deadlines.
Prerequisites and package children never inherit the participant capability;
only the fixed builder receives it, consumes it and checks live custody.
The builder uses the frozen source and rechecks the separately admitted
original before staging and activation. Only verified full-tree settlement
permits an integer outcome. Uncertain cleanup retains an interrupted fence.
A later native caller that obtains free custody records a lost running owner
as interrupted without needing Python, but does not replay its construction.
`Repair-NativeFoundationAfterRestart -Custody <held-object> -Root <root>` is
explicit positive settlement only: it requires a genuinely different native
boot on the same platform and preserves the interrupted outcome. It neither
rebuilds nor executes queued work, and ordinary startup never calls it.

A second caller can reuse only a completed matching operation, its exact
source-qualified current slot and successful retained installed health.
It neither starts another bootstrap nor edits pending request history.
Without that healthy exact selection, any existing queue artifact or selected
foundation requires queue-aware admission. The native owner invokes only
`update_driver.foundation_admission` from the exact retained selected slot,
through its retention launcher, in a strict bounded owned child. This fixed
module consumes a fresh same-root witness, checks installed execution health
and calls the existing `foundation_state.begin` queue reader. It neither
executes a submitted recipe nor implements another SQLite reader. The native
owner retains the original kernel lock across admission, acquisition,
construction, selection and outcome persistence.

A live transaction therefore finishes before native upgrade admission can
acquire custody. Unsettled abandoned work still refuses; a free lock alone
does not settle it. Pending requests and already-started workers remain at the
same execution root, with their pinned old runtime retained, and execute only
after the producer releases custody. An installed foundation lacking this
admission module is an explicit readiness/repair failure, even when its queue
is empty; empty history does not prove that an unaware legacy executor is fenced.
Missing/corrupt selectors and incompatible queues refuse before bootstrap
staging or prerequisites, preserving queue bytes and the healthy selection.
No native branch reads, edits or deletes SQLite.

The producer does not enroll maintenance or enable ordinary first-use
publishers. Native Linux/macOS genesis and the remaining first-use integrations
stay explicit, separate boundaries. The single foundation progress snapshot
is not durable ordinary-request history: future publication adapters still
need canonical queued request receipts.

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

Real foundation construction is a separate opt-in lane:

```powershell
$env:UPDATE_DRIVER_FOUNDATION_BUILD='1'
python tools\run-plugin-tests.py update-driver --test-file tests/test_foundation_build_process.py --test-timeout 300 --timeout 360
```

It requires uv and an available seasoned build backend, uses a fresh isolated
cache/root, constructs twice to check unchanged completion bytes, rejects an
unhealthy changed-source candidate without selecting it, removes the input
source and exercises installed health and the retained finite worker.
It never installs the host foundation or provisions a plugin. A preinstalled
test interpreter is not Python-free genesis evidence. The manual `foundation`
portfolio of `installation-context-full.yml` runs this bounded lane on fresh
Windows and Ubuntu runners without adding native/network work to required
unit-only PR CI.

Python-free Windows genesis has its own explicit opt-in:

```powershell
$env:UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS='1'
python tools\run-plugin-tests.py update-driver --test-file tests/test_foundation_genesis_process.py --test-timeout 600 --timeout 780
```

This lane performs real network acquisition and immutable construction in a
fresh private root, with no Python/py/uv command available to the native
submitter. It forces a private managed interpreter/cache instead of borrowing
the orchestrator's registry-installed Python. The originating caller and its
Job are closed during admitted construction; a second cold caller converges
on the same operation. Assertions cover one bootstrap, exact selected source,
installed health, retained source-independent queued execution and no visible
console/focus transitions. Only this isolated opt-in removes contained-test
suppression for its native producer; all returned native handles retain bounded
cleanup. The manual Windows foundation portfolio runs it; ordinary scheduled
and required unit-only CI do not.

`test_foundation_producer_process.py` instead replaces only the inner helpers
in a copied fixture source. It qualifies native orchestration, pre-acquisition
progress, exact status and owned-builder death without downloads; it is not
evidence of actual cold construction.
