# Reproducer lab: boundary observations and monitored storage

This directory implements the first boundary and storage observations from the
[reviewed persistent reproducer plan](../../efforts/active/linux-role-fleet/persistent-isolated-reproducer.md).
There is no provisioning CLI or qualified persistent lab. The opt-in reader can
inspect explicitly selected existing resources and run fixed, non-root
probes in an already running guest. It never creates, starts, stops or
deletes resources. Source publication does not authorize those observations
against an existing installation.
The separate guest operation context is effectful only when explicitly invoked
in a separately authorized guest; the host reader never invokes it.

`boundary.py` provides:

- `parse_declaration(value)`: strict version-1 synthetic configuration, full
  image/Git object identities, separate experiment resource names and positive
  integer budgets. Malformed, unknown and out-of-scope inputs raise
  `DeclarationError` without echoing their payloads.
- `inspect_static_boundary(experiment, observed)`: pure checks on a Docker
  inspect-shaped observation. It identifies known unsafe/missing image,
  identity, namespace, privilege, exposure, CPU/memory/PID, persistent-mount
  and guest-environment fields without invoking Docker or reading host state.

The initial proposed image contract uses non-root `1000:1000`, the fixed HOME
`/home/lab`, PATH `/usr/local/bin:/usr/bin:/bin` and `PYTHONUNBUFFERED=1`.
It has four separately named volume destinations for home, state, workspace and
evidence. Config/runtime/registrar roots will live under the owned state path;
their actual service selection is not implemented or qualified here.
Supported declaration ceilings are 8 GiB memory (equal swap limit), four CPUs,
256 PIDs, 4 GiB persistent storage, 512 MiB `/tmp`, 64 MiB `/run` and 64 MiB
native output. These are tool-side input ceilings, **not enforced storage**.
Static inspection also requires exactly two `HostConfig.Tmpfs` destinations:
`/tmp` and `/run`, each with `rw,nosuid,nodev,uid=1000,gid=1000,mode=0700`
and exactly one `size=<declared bytes>` option. Missing, duplicate, conflicting,
extra or unbounded options refuse. This follows the existing restricted
provider's explicit tmpfs-flag/budget approach without changing its policy.
It is Docker configuration evidence, not a guest mount-table or runtime proof.

Version-1 declarations may explicitly set `network_mode` to `"none"` or
`"internal"`. Omission preserves the existing internal-bridge selection; unknown
values and fields refuse. The operator-selected first case uses explicit
`"none"`: actual services and fake peers communicate on that one container's
loopback. No named bridge or separate peer containers are needed for that case.
Existing declarations are not silently migrated.

Every static result retains explicit unresolved requirements, even with zero
violations: fresh usage/backing-free-space measurements, actual internal
network membership/egress, exclusive resource/driver custody, guest source and
volatile/runtime configuration, and provisioning/strict pre-effect support.
There is deliberately no `ready` or admission result. A dictionary claiming
verified quota cannot supply a backend implementation or clear these gaps.
Synthetic inputs, expected names and image identities are not physical custody,
running-source or persistence proof.

## Finite host reader

`reader.DockerReader` takes an absolute caller-selected Docker executable and a
full physical container ID, never a guessed name or automatic replacement.
`inspect(experiment, container_id)` applies the static checks and reads only the
four declared volumes. Each must have the ordinary `local` driver, local scope
and no driver options. Full-volume mount declarations are required: subpath
mounts refuse, and each effective mount source must match its inspected volume
root so that a subtree cannot hide usage elsewhere in the same volume.
The returned `storage.Resources` binds the container ID
and volume names, creation timestamps and mountpoints. These observed Docker
identifiers are not exclusive-custody proof; retain and compare them on resume.
Docker exposes no separate immutable local-volume UUID.

`measure(experiment, retained_resources)` checks those same identities before
and after a fixed Python probe through `docker exec --user 1000:1000`.
The synthetic image must supply `/usr/bin/python3`. It does not start a stopped
container or retry an unknown result. The reader uses the existing
`agent-procutil` caller-owned runner, at most 10 seconds per command within a
30-second observation deadline, with 256 KiB stdout and 16 KiB stderr caps.
Settlement, JSON/metadata processing and the final sample timestamp must remain
inside that deadline; merely starting the last command before expiry is insufficient.
Failures raise `ReadError` with a bounded reason rather than echoing guest or
Docker diagnostic payloads. The caller's selected Docker context is used;
this tool does not discover endpoints, log in or borrow credentials.

`guest_usage.py` traverses the four fixed roots without following symlinks,
deduplicates hardlinks per volume and counts the larger of logical and allocated
bytes (including directories and hidden files). It refuses missing/unreadable
paths, changed directory identities, nested filesystems, excessive depth,
more than 256 pending directories or more than 100,000 entries.
A guest-local five-second timer bounds the probe even
if the Docker client disconnects; host-client cleanup is not proof that a
daemon-side exec settled. Backing free space uses guest-visible `f_bavail`;
the minimum across the four roots is conservative for shared filesystems.
Evidence-root usage is the output threshold's observation, not an assertion
that native streams have been wired to that root.

## Storage-only monitoring

`storage.StorageMonitor` requires an explicit `MonitoringPolicy`: observation
cadence (1-60 seconds), active-case lifetime (at most one hour) and backing free
reserve (positive, at most 4 GiB). Its monotonic clock belongs to one host
process. Construct a new unmeasured monitor on resume; never persist/reuse its
monotonic timestamps across processes. The future case owner must independently
authorize that lifetime, cadence, resource selection and actions.

`reader.refresh(monitor)` invalidates the previous observation before reading.
`monitor.refusals(now=...)` refuses NEW work when closed, missing, stale,
expired, at/above either usage threshold, or at/below the backing reserve.
Freshness includes scan duration and expires exactly at the cadence boundary.
The active caller must refresh on that bounded cadence; no scheduler, daemon or
automatic stream of new cases is supplied. Closing the monitor, losing it or
failing a refresh never kills accepted work, fabricates a successful drain or
deletes evidence. No empty storage-refusal tuple is overall lab admission.

Ordinary volumes have **monitored thresholds, not hard quotas**. Concurrent
writes, open-unlinked files, delayed observations and accepted work can overshoot
them. A traversal is not an atomic filesystem snapshot. Free-space observation
can also change immediately after a sample. Missing/partial/error outcomes
refuse further work; no automatic deletion, truncation, growth or replacement
is supplied. CPU/memory/PID hard limits are unchanged.

## Finite network inventory

`reader.network_inventory(experiment, subject_id, peers=...)` inspects the full
selected subject ID and at most three explicitly selected, distinct full peer
IDs. It does not discover peers or accept names as physical identity.
This API requires internal mode; offline selections refuse before any Docker
call rather than inspect an unrelated bridge.
Each container must be running and attached only to the experiment's exact
physical internal, local bridge network. The network's endpoint inventory must
contain exactly those containers; endpoint IDs must agree in both directions.
Custom bridge options, attachable/ingress networks, shared network namespaces,
external attachments and missing observations refuse.

The reader repeats the container/network inspections and compares the network
ID, creation timestamp and each endpoint identity within its existing
30-second deadline (four calls, at most 10 seconds each). Pass `expected` on
resume to compare a retained observation; a missing or replaced selection
refuses, without automatic creation or repair. All calls use the same bounded
process owner as storage observations.

These are point-in-time, non-atomic observations, not exclusive-resource
custody or effective-egress proof. An attachment can change between/after
observations, and a peer ID does not establish its synthetic image, environment,
service or privilege posture. Actual peer qualification, exclusive driver and
resource ownership, and a controlled real egress-refusal experiment remain
separate prerequisites. This API never clears the static unresolved list or
admits a case.

Real egress, exclusive driver/resource custody, full guest/runtime/source
qualification, native output ownership and strict pre-effect support remain
unqualified. Provisioning needs separate exact resource approval and an owning
bounded lifecycle path; observations alone never authorize it. Case execution
remains blocked until the remaining requirements are qualified.
Restricted-provider policy and ephemeral tests remain unchanged. Real resource
creation, guest lifecycle/fault actions and deletion need separate authorization
through the normal owner. No production credentials, state or network are used.

## Offline-first network observation

`reader.offline_network(experiment, retained_resources)` requires explicit
`network_mode: "none"` and checks the same physical container/volume selection
before and after one fixed non-root Linux probe. It uses five calls through the
existing process owner, at most 10 seconds each within one 30-second envelope.
It does not inspect/create a Docker bridge or discover peers.

`offline.py` observes the network namespace inode, the sole interface and its
up/loopback flags, IPv4 main routes, IPv6 routes and IPv6 addresses. Each fixed
kernel file is limited to 8 KiB and each table to 32 records. Before making any
connection attempt, it requires only `lo`, no IPv4 main routes and only supported
loopback IPv6 routes/addresses. It then makes one one-second TCP attempt each to
the reserved documentation addresses `192.0.2.1` and `2001:db8::1`, port 9.
Both must actually return `ENETUNREACH`; success, refusal, timeout, permission
errors and unsupported families do not become a passing no-route observation.
There is no DNS, proxy, production endpoint or external receiver.

The probe compares namespace/interface/address/route configuration before and
afterward, ignoring only volatile IPv6 route reference/use counters. A local
five-second timer and elapsed check bound it. The host checks its exact result
shape and final deadline without retrying or starting a missing container.
Synthetic controls never run these socket attempts or inspect host kernel state.

This is point-in-time evidence, not permanent egress enforcement, exclusive
custody or a complete guest/source/runtime qualification. It does not clear the
static unresolved list, start a case or establish native START/review. Recheck
selected resources and boundaries before further separately authorized work;
host-client cleanup still does not prove a daemon-side exec settled.

## Immutable local source input

`source.SourceReader(absolute_git_path).materialize(repository, commit,
private_results_root, fresh_name)` prepares one local build input from the
experiment's full `source_sha`. It does not build an image, transfer into a
guest, install dependencies or run a service. The results root must already
exist, be private and caller-owned, and be outside repositories and managed
session state. This initial source path supports POSIX hosts only.
The fresh artifact directory is exclusive; an existing or incomplete attempt
is never overwritten, deleted, resumed or retried automatically.

Git reads only the selected immutable objects, not the index or dirty/untracked
working-tree bytes. Preflight refuses symlinks, submodules, malformed paths,
case-folded or Unicode-normalization path aliases (including directory prefixes
and reserved Git metadata components), even on case-sensitive hosts,
more than 16,000 files, any blob over 8 MiB or logical source content over
256 MiB. Canonical caseless comparison normalizes before and after folding.
The listing is at most 4 MiB; the prefix inventory admits at most 32,000 entries
and 8 MiB of combined original/key UTF-8 bytes, checked before retaining a new
entry. Deep zero-byte trees cannot grow that inventory without bound; these
are input/retention ceilings, not a hard process-memory quota.
A shallow local-file fetch produces one detached commit and its tree
without copying the source's config, hooks, refs, reflogs, index or object store.
No remote URL is configured and no fetch-head source-path record is written.
The subprocess environment is a fixed synthetic baseline: ambient credentials,
Git overrides, proxies and service-discovery settings are not inherited.
Global/system Git config, templates, hooks, lazy fetches and automatic
maintenance are excluded. `GIT_ATTR_NOSYSTEM=1` and an explicit null
`core.attributesFile` also exclude host system/user attribute policy;
repository-tracked attributes remain part of the selected source.
Only the explicit local-file transport is permitted. Destination initialization
explicitly matches the verified commit's SHA-1 or SHA-256 object format.

Every command uses `process.py`'s existing caller-owned runner with at most
15 seconds, 4 MiB stdout and 16 KiB stderr within one 60-second preparation
deadline. Final commit/tree observations and metadata writing are inside that
deadline. `source.json` contains selected object identities and input counts,
not completion, running-source, custody or lab-admission authority. Any raised
error leaves the attempt unavailable even if partial files or metadata exist.
The tool does not interpret a lock/PID, old directory or artifact as permission
to continue an interrupted guest effect.

The input-size ceilings are **not a hard disk quota** on Git metadata or
materialization. Partial writes and filesystem overhead remain possible and
are retained on failure. Source staging, image build and guest copy still need
their own bounded storage/approval and installed-source qualification; an
observed Git commit is not a proof of an executing guest's code.

## Effective volatile-mount observations

`reader.volatile_mounts(experiment, retained_resources)` checks the same physical
container/volume selection before and after one fixed non-root guest probe.
It uses the existing five-call, 30-second observation envelope and 10-second
per-command bound. `guest_mounts.py` reads at most 32 KiB of
`/proc/self/mountinfo`, opens only `/tmp` and `/run` with no-follow directory
descriptors, and reads their owner, mode, filesystem device, read-only flag and
total capacity.
It requires unchanged mount-table bytes around the observations and has a
guest-local five-second timer plus an elapsed-time check.

`mounts.inspect_volatile_mounts` checks the bounded wire shape, effective
`1000:1000` identity, one read-only `/` mount, and separate `/tmp`/`/run`
filesystems with exact top-level mounts. Each must be tmpfs with
`rw,nosuid,nodev`, a writable filesystem flag, owner `1000:1000`, mode `0700`
and the exact declared capacity.
An observed capacity rounded above or below the declaration refuses; select
representable byte sizes rather than silently rounding the budget.
Duplicate/shadowed mounts, nested volatile mounts, malformed paths/options,
more than 256 records and stale physical selections refuse.

These are non-atomic, point-in-time observations of the selected volatile
mounts, not a complete guest mount policy, service/config/DB/registrar or
executing-source qualification. They do not establish egress, resource custody,
persistence, native-output retention or strict pre-effect support, clear the
static unresolved list, or admit a case. Client cleanup is not proof that a
daemon-side exec settled; missing/error observations remain unavailable.

## Fixed guest operation exclusion

`guest_operation.operation()` reuses the source-shipped
[`single-instance-lease` operation primitive](../../libs/single-instance-lease/README.md#operation----custody-for-one-operation)
at the fixed `/lab/state/reproducer-driver.lock` key. It does not accept a host
results directory, create the state directory, initialize resources or install
dependencies. The lock file may be created on first authorized acquisition and
is retained after release; no unlink, PID/age reclaim or automatic replay occurs.

Before acquisition, before yielding custody, on each returned
`GuestOperation.assert_owned()` and at context exit, it checks existing Linux
`1000:1000` identity, the unlinked state directory's device/inode and private
`0700` posture. Bounded mount-table reads reuse `guest_mounts.py` and the existing
`mounts.py` parser. `/lab/state` must be an exact writable mount on a different
device from the read-only root, without nested/shadowed mounts. The initial
parser also rejects any `/lab` ancestor mount: a same-device overmount can hide
an older state record still present in mountinfo. Device equality alone does
not prove that record is the opened directory's effective mount. The initial
nonvolatile filesystem selection supports ext4, XFS, Btrfs and ZFS; unfamiliar
filesystems refuse rather than being presumed durable. The open directory device
must match that mount. Each observation has a five-second elapsed ceiling,
32 KiB table ceiling and 256-record ceiling; the operation lifetime itself
belongs to the future bounded case owner.

Existing lock files must be regular, single-linked, on the observed state
device, owned by `1000:1000` with mode `0600`. After acquisition, the shared
primitive verifies lock-file and execution ownership. The caller must invoke
`custody.assert_owned()` before its consequential stages. Failure propagates
and releases only this context's descriptor; partial work/evidence is retained.

This serializes cooperative guest drivers mounting the same physical state
volume. It is not a Docker volume-association certificate, persistence
qualification, overall READY signal or fencing of accepted descendants.
Directory/mount observations are non-atomic; equal-privilege replacement is not
prevented. After a driver crash, kernel lock release does not prove its task,
Bridge delivery or service work finished. The future case owner must join the
existing task/Bridge journals and refuse unknown accepted work before new work
or replay. Actual shared-volume contention, crash retention and case recovery
remain separate qualification obligations. No read-only observation acquires
this lock or authorizes a resource/case action.

## Tests

`tools/test_reproducer_lab_boundary.py` is an in-process, synthetic control set:
declaration/budget rejection, static unsafe or incomplete Docker observations,
mount/environment isolation and retained unavailable prerequisites.
`tools/test_reproducer_lab_monitoring.py` covers mocked finite-reader seams,
physical-resource mismatch, failure invalidation, exact threshold/cadence/
lifetime edges, monitor loss/resume and local synthetic traversal fixtures.
`tools/test_reproducer_lab_network.py` covers exact subject/peer membership,
foreign attachments, physical endpoint replacement, retained selections,
malformed/missing observations and final-settlement deadline refusal.
`tools/test_reproducer_lab_source.py` covers pure/synthetic immutable-source
inputs, exact file/byte ceilings, environment/command selection, retained
partial failures and the last command's deadline. It invokes no Git.
`tools/test_reproducer_lab_mounts.py` covers bounded mount-table parsing,
read-only root, exact tmpfs flags/capacities/owner/mode/device checks, duplicate/
nested mounts, fixed probe/physical selection, failed commands and final expiry.
Local file-descriptor fixtures exercise the probe without querying host mount
state or invoking Docker; they are not real container qualification.
`tools/test_reproducer_lab_offline.py` covers compatible/explicit mode selection,
kernel observation bounds, loopback-only refusal, actual-error classification,
before/after drift, fixed probe/resource selection and host/guest deadlines.
All sockets, namespace and kernel inputs are synthetic.
`tools/test_reproducer_lab_operation.py` covers fixed-key shared-primitive
selection, bounded parser reuse, state/lock posture and pre/post failure
propagation through synthetic lock seams and local directory-descriptor
fixtures. It never reads host mount tables or invokes the guest lock against
real resources. These controls do not qualify real inter-process/shared-volume
exclusion or crash settlement.
`tools/test_reproducer_lab_initial_case.py` exercises the initial-task gate with
synthetic service seams and local SQLite rows: all retained states/attempts,
orphaned history, missing tables, strict-write failure, changed journals,
lost-create replies and exact observation deadlines. It does not execute the
actual service queue, start a guest or qualify persistence.
Run it with the repository's selected Python/pytest environment through the
caller's bounded test supervisor:

```text
<bounded-test-supervisor> -- <python> -m pytest -q tools/test_reproducer_lab_boundary.py tools/test_reproducer_lab_monitoring.py tools/test_reproducer_lab_network.py tools/test_reproducer_lab_source.py tools/test_reproducer_lab_mounts.py tools/test_reproducer_lab_offline.py tools/test_reproducer_lab_operation.py tools/test_reproducer_lab_initial_case.py
```

These tests do not run Docker, open a socket, install dependencies, exercise native
service lifetimes or join the unconditional promotion matrix. See
[`TESTING.md`](../../TESTING.md) for the separate real integration portfolio.

The explicit local-Git lane is separate from those synthetic selectors:

```text
<bounded-test-supervisor> -- <python> -m pytest -q tools/reproducer-lab/source_native_tests.py
```

Its fresh SHA-1 and SHA-256 synthetic repositories prove the selected committed bytes survive
dirty/skip-worktree changes, excludes untracked content, source configuration,
hooks and older history, and leaves the source checkout untouched. It does not
use Docker, a remote forge, production credentials or service processes.
The filename is intentionally outside default pytest discovery; no required
PR/promotion matrix is expanded.

## Initial task pre-effect gate

`initial_case.submit_initial_task(custody, queue, journal, diagnostics)` is an
explicit synchronous guest-only source seam for the first offline synthetic
task. It accepts already prepared runtime-owned `TaskQueue` and read-only
`AcquisitionJournal` objects; it never constructs, initializes or migrates
either database. The fixed selections are `/lab/state/dispatch/queue.db` and
`/lab/state/bridge/bridge.db`. Their existing directories must be private
`1000:1000`, mode `0700`, on the observed state device; database files must be
regular, single-linked, mode `0600`, with the same identity. The Bridge reader
must still address the file it opened. The existing `GuestOperation` checks
mount/lock custody before observations, again immediately before submission,
and after the queue call.

The gate reads the original services' existing history, without an active-state,
latest-attempt or repository filter. Any retained task, reservation, wake,
attachment, acquisition, session, turn, message or supported creation/recovery
history refuses the initial case. This includes independent acquisition
markers/receipts, accepted producer requests, fallback attempts, launch gates
and retired conversation bindings, not just rows joinable to current tasks or
sessions. Dispatch's lazily prepared `cleanup_recoveries` table is checked
when present, without creating it; an empty fresh queue need not already have
that table. A same-named view or unreadable existing table refuses.
Even terminal or refused rows are not a
descendant-settlement certificate. Orphaned operation history is checked
independently of task/session joins; missing tables and read failures propagate.
Dispatch uses its existing connection owner with a five-second SQLite progress
deadline. Each combined Dispatch/Bridge observation checks its five-second
elapsed boundary before and after the Bridge read; these are refusal deadlines,
not a hard kernel-I/O or SQLite-connection cancellation guarantee.

Only after that observation does the existing
`DiagnosticLog.write_event_strict` synchronously persist an
`initial_task_create_entry` in `/lab/evidence/diagnostics/reproducer.jsonl`.
The record names the synthetic case, not a prompt, credential, process ID,
native identity or readiness claim. Malformed records, unavailable strict
support, append/sync errors and lost custody prevent the queue call. The gate
rechecks the histories and file identities after sync, then invokes the existing
`TaskQueue.create_outcome` with a fixed synthetic lane, original dedup key, seed
and `agent:reproducer-lab` requirement. A deduplicated/replayed result refuses;
the actual queue remains the only task ledger. A lost create reply propagates
and leaves any committed task in that ledger, preventing an automatic second
initial task. No row is retired, rewritten, cleared or inferred complete.

This first seam deliberately supports **initial task creation only**, not replay
or admission of another case after prior work. Resume inspects existing records
through their original owners; extending safe case/replay support requires
actual joined execution and settlement evidence, not a new task/operation
ledger, PID test or a diagnostic entry used as authority. Empty tables after
history loss, replacement or external deletion are not proof that an old effect
never occurred. Resource selection/history retention, absence of other
producers, actual service configuration, source/image identity, fresh host
storage monitoring and all boundary qualifications remain caller prerequisites.
The reads are not an atomic transaction across both services and do not fence
their autonomous writers. This seam cannot authorize an unqualified lab.

Run the entire operation context and submission on the same synchronous
execution owner, outside an event loop. An async owner must await that whole
worker operation; enqueuing a callback or moving an already held operation
context between threads is not supported. Actual runtime fixture wiring,
native output, downstream backend calls, mounted-volume/crash behavior and
separate exact resource authorization remain open. There is no provisioning
CLI, new scheduler/controller, production default or required-CI expansion.

The explicitly selected local-owner lane is:

```text
<bounded-test-supervisor> -- <python-with-Bridge/Dispatch-dependencies> -m pytest -q tools/reproducer-lab/initial_case_local_tests.py
```

It uses the actual source `TaskQueue`, read-only Bridge `Database` and
`AcquisitionJournal`, `DiagnosticLog` and shared `OperationLock` on temporary
local SQLite/JSONL files. Before opening the read-only reader, the actual
writable `AcquisitionStore` prepares its additional launch-gate/binding schema;
the gate itself does not initialize or repair those tables.
It checks both successful file/directory syncs precede
the real queue create, injected sync failure leaves the real queue empty, and
a lost create reply retains one task and refuses a second initial create.
Orphan-only fixtures use the actual queue/store schemas independently of the
production history lists; schema-key coverage checks catch omitted task,
reservation, assignment, delivery, worktree or session tables.
The cleanup-recovery fixture prepares its receipt through the actual existing
owner's preview/apply flow before simulating orphaned rows in the temporary DB.
Only guest path/mount admission is synthetic in this lane. The filename is
outside default test discovery; no real container, mounted volume, backend
spawn, crash/power-loss or running-service qualification is claimed.
