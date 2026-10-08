# File-check-in process registry - architecture proposal

**Status:** Draft; no broker is implemented or deployed by this proposal.
**Parent:** [effort](README.md). **Intent:** [Process Registry vision](../../../visions/process-registry/README.md).

The operator requires one shared broker if a daemon is used, no reporting
subprocesses, file check-ins, on-demand state, and approximately two folder
snapshots per hour. The selected first-release security scope is the designated
operator account; other accounts must not start competing brokers.

All mechanisms and initial tuning values below are **agent recommendations**
for review, not additional operator mandates or measured performance results.

## 1. Ownership and the one-broker boundary

Recommend Worktree Manager own the optional host registration service and its
installation/upgrade adapter. The implementation owner would be
`worktree-manager`, with a small shared client in a canonical `libs/` source
tree. An `agent-*` installation can publish into an authorized mailbox, but
cannot install, update, replace, or start the host broker on discovery.

The host's explicit handler descriptor names the designated management source,
protocol version, root, security principal, native OS/PID domain, and generation.
It is not derived from whichever marketplace happens to invoke a command.
The installation command is the only path that creates/enables this descriptor
and allocates mailbox permissions. Missing, incompatible, or invalid
descriptors mean reporting is unavailable; producers never bootstrap a handler.
Existing owning launch/setup code resolves this context outside the reporting
hot path and passes a cached, validated mailbox context to the invoker/client.
The announcement path does not open a descriptor file or perform discovery.
Standalone callers without that context remain unconfigured and do nothing.

The lease key/root is stable across plugin versions, cells, and worktrees.
There is one serving broker and one database writer for this role. Competing
authorized manager starts lose the same OS exclusive lease and stand down.
Different caller-local roots or ports must not create separate broker roles.
Worktree Manager installations from another source may discover the handler,
but management takeover requires an explicit operator decision.

This is a **deliberate host-owned federation capability**, not an
installation-owned global registry. Producer mailboxes stay in their own
authorized runtime/state roots. Only the broker writes its host-owned database
and snapshots. No consumer writes another installation's state. Generic scripts
do not need to manufacture core plugin installation-cell internals: an owning
invoker supplies their applicable provenance; otherwise it remains unknown.

First-release inspection concerns native processes in this host's OS/PID domain.
WSL/container/remote PIDs are not interchangeable with native PIDs. Such records
are explicitly foreign/unverified until a reviewed adapter exists; do not spawn
another guest broker or a per-PID inspection helper to hide that limitation.

## 2. Data path

```text
existing invoker or self-check-in
          |
          | bounded in-process enqueue, no acknowledgement or helper launch
          v
authorized producer mailbox: immutable small start/end records
          |
          | file-watch hints + periodic bounded directory reconciliation
          v
one host broker: validate -> OS identity check -> reconcile -> cache
          |
          +-> private broker-owned SQLite live-state/index
          +-> owner-scoped on-demand query
          +-> atomic periodic snapshot files + bounded retention
```

Discovery and reporting use local state, not a telemetry sink. Queries use an
owner-only native endpoint (UDS on Linux/macOS, named pipe/DACL on Windows).
Follow the [transport ladder](../../../docs/patterns/service-transport.md);
TCP requires an explicit reviewed necessity and owner-scoped authentication.
Queries never start the broker. When it is down, a query may return the latest
snapshot clearly marked historical/stale, not claim it is current.

## 3. Producer contract: cheap, optional, and not a new spawner

Keep `agent-procutil`'s flag-returning helpers pure: they have no child PID.
Integrate at actual spawn wrappers and other existing process invokers, after
they have a process handle/PID. Self-check-in supplements launcher metadata.
Do not monkeypatch every `subprocess` call or add side effects to module import.
Canonical library changes must follow vendoring, not edit consumer copies.

Use an existing runtime's background writer or one bounded, lazily-created
writer thread per long-lived producer, **never one thread or process per spawn**.
Foreground work only attempts a bounded queue insertion. No startup connect,
acknowledgement, retry loop, file flush, or shutdown join is permitted.
A saturated or stuck writer drops new entries after its fixed queue cap.
It must not cause replacement-thread growth.

This extends a proven pattern: [ssh-manager's dial log](../../../libs/ssh-manager/README.md)
uses a bounded background writer and performs no file I/O on an event-loop
thread. Reuse its discipline, not its SSH connection/broker implementation.

**Filesystem caveat:** a small local write is not a hard-timeout primitive.
An antivirus filter or stalled filesystem can block it, and `O_NONBLOCK`
does not give regular-file writes a universal deadline. Therefore direct
synchronous writes are not allowed on latency-critical paths. Keep reporting
off those paths, bound queue/memory, and never await a hung writer.

For `.py`/`.ps1`, use in-process primitives only where the runtime permits that
contract. Standalone `.sh` cannot safely obtain atomic rename or asynchronous
I/O by launching `mv`, Python, or a background reporter; prefer parent-side
registration for managed shells. Where a safe self-hook is unavailable, record
the coverage gap rather than violate the no-helper/no-wedge contract.
Short-lived producers may exit before a background write completes; their
longer-lived invoker is the primary announcement point. Zero-loss reporting of
every short-lived process is not promised by a nonblocking best-effort path.

No handler descriptor means no files and no worker thread. An enabled handler
temporarily offline may leave a bounded spool for restart recovery; producers
do not wait for liveness checks. Mailbox limits apply during that outage.

## 4. File format, ordering, and cleanup

Propose schema-versioned UTF-8 records containing event/registration ID, operation
(`start` or `end`), PID domain, PID, native creation token when available,
reporter/parent identity, observation time, role, owning source/plugin/version,
and optional session/cwd/worktree. Do not include secrets, environment dumps,
or full command lines. Unknown fields are absent/explicit, not invented.

Writers publish a uniquely named temporary file in the authorized mailbox,
then atomically rename it to the committed suffix, entirely in-process.
Readers ignore temporary files. Proposed cap: 8 KiB per committed record.
Unparseable, oversized, changing, or unsupported records become bounded
diagnostics, not recurring expensive parse work. The broker never follows a
check-in's path instructions or recursively discovers arbitrary directories.

Files are **immutable tickets**, not a continually rewritten file named only
after a PID. Starts and ends have separate unique tickets. The daemon deduplicates
by event ID and merges registrations by verified process-instance identity:
host/PID domain + PID + native creation token. Arrival time is not birth time.
An unverified `(PID, observation time)` claim cannot safely identify a live
replacement; retain it as a claim, not an authenticated process instance.

Do not immediately delete start tickets on producer exit: a fast create/delete
can disappear before a watcher reads it. Write an end ticket instead. The broker
transactionally ingests a ticket before unlinking that exact file; a crash
between commit and unlink replays idempotently. Producers only attempt cleanup
of their unpublished temporary files without waiting. Tickets are not removed
merely because a watch notification arrived.

Maintain exact-instance terminal state/tombstones so end-before-start and delayed
duplicates cannot resurrect a closed registration. A new PID creation token is
a new process, not a continuation. On recovery, corroborate candidate live
instances with the OS regardless of record arrival order.

Proposed mailbox quota: 2,048 admission slots per installation, including
in-progress temporary and committed tickets, with bounded temporary-file
lifetime and broker-side cleanup of stale/unverifiable entries.
Track overflow/eviction and source coverage. Reserve room or bounded coalescing
for end records, but do not depend on receiving them: identity reconciliation
removes dead live-state entries after a missed end. Quota enforcement must use
nonblocking admission (for example, a bounded number of exclusive slot claims
in the background writer), not an unbounded search or foreground directory
walk. If admission fails, drop that ticket and record degraded reporting.
Admission slots must bound files even with the broker offline; a broker-only
quota check or an uncoordinated cached counter is insufficient. The slot
mechanism and crash cleanup are implementation acceptance gates.

## 5. Watcher, liveness, and enrichment

Watching is an optimization, not the authority. Coalesce notifications into a
bounded dirty-mailbox set. Duplicate events do not schedule duplicate scans.
Watcher overflow, directory replacement, downtime, or failed subscription
requires a periodic bounded rescan of the authorized mailbox catalog.
Polling fallback is valid; it must obey the same work budgets.

For new identities, inspect OS owner, creation token, executable, and parent
facts once, then cache stable information. Reconcile live identities in
staggered batches (initial proposal: roughly once per minute, not a burst).
At 500 instances, that averages about eight checks per second; benchmark this
instead of treating it as evidence of acceptable CPU use.

Reuse [ZDD process-identity diagnostics](../../../libs/zdd/README.md) where
the interfaces and trust semantics fit. Current creation-time inspection uses
native Windows process APIs or Linux `/proc`; macOS requires a reviewed native
adapter, not an assertion that the `/proc` implementation is portable.
Access denied, namespace mismatch, and inspection failure mean **unknown**,
not exited. Last verification time accompanies every liveness answer.

"Ping back" initially means read-only OS identity/liveness checks. Do not
signal arbitrary scripts, open callback servers in every process, or launch
an inspector for each PID. Explicit responsiveness probes for cooperating
long-lived services are later work, separate from liveness.

Represent self-reported, invoker-reported, OS-corroborated, lineage-inferred,
and unknown provenance separately. A Windows Python launcher/interpreter pair
can be one logical worker but two physical processes. A console host can be
shared or created indirectly; do not mark it ours from its name or parent PID
alone. Use verified associations when available; otherwise label inference or
unknown. Exited-before-inspection tickets can contribute bounded history,
but must not become confirmed-current inventory.

## 6. Storage, snapshots, and security

Recommend a private, broker-only SQLite database for registration state,
idempotency, terminal identities, source catalog, and observation freshness.
Batch transactions; no per-notification fsync. Limits cover queue depth,
database growth, mailbox backlog, active records, inspection workers, and
diagnostic frequency. The query returns pagination/coverage limits explicitly.
Never discard a live registration silently to enforce a cap.
Keep storage and inspection work off the query actor, using bounded worker
capacity and cached state. A stalled filesystem/inspection call must not
produce replacement-worker growth: mark freshness/coverage degraded, reject
excess work, and retain the last known snapshot. The daemon cannot guarantee
completion of a stalled kernel call, but it can avoid propagating that stall
to useful producers or its independent cached-query path.

Publish snapshot files atomically, independently of mailbox watching, about
every 30 minutes. Proposed default: seven days (336 half-hourly files), plus
explicit count/byte caps; make retention configurable at the handler owner.
Snapshot output is outside the watched inboxes to avoid feedback loops.
Reconcile cached identities before marking a snapshot current; include
last-verified timestamps and incomplete/unknown counts. On-demand queries use
cached state by default; optional refresh is bounded and never a full scan per
viewer. The journal is local on-demand history, not a telemetry exporter.

Restrict descriptor, database, mailbox catalog, endpoint, and snapshots to the
designated account with POSIX permissions/Windows DACLs. Validate mailbox roots,
ownership, file type/size, and no-follow/reparse policy before parsing.
Reject other-account records. Core runtime producer provenance comes from the
installation's existing validated context; generic callers use an explicitly
registered source identity without depending on core cell machinery.

**Files do not authenticate their writer process.** Same-account malicious code
can forge tags and typically mutate that account's files. Corroborating PID
owner/birth/executable does not cryptographically prove plugin/session claims.
This is a cooperative operator-account inventory, not a security boundary
against hostile code already running as that operator. Do not promise otherwise.
The broker never uses claims to kill or restart processes.

## 7. Upgrade and recovery: no competing brokers

All manager versions/installations use the same host-role lease, not a
port-specific or per-cell lease. Reuse the OS-exclusive
[single-instance lease](../../../libs/single-instance-lease/README.md);
root/key selection and discovery must make that host scope explicit.
The service self-registers internally without writing a ticket through its
own hooks; registration code must not recurse on broker storage operations.

Request a **reviewed strict-singleton cutover exemption** to the usual
active/passive overlap in
[graceful-daemon-cutover](../../../docs/patterns/graceful-daemon-cutover.md).
Prepare/validate a new immutable runtime offline. The existing authorized
manager quiesces the one broker, commits state, verifies it has exited and
released the lease, then starts the successor. If old ownership is ambiguous,
abort the upgrade rather than create another broker. No overlapping passive
broker is allowed; a short query gap is acceptable because writers do not
wait and bounded committed tickets survive it. Rollback also observes the
same one-broker rule. Only explicit management may restart the daemon.

Restart replays committed tickets, restores/rechecks database live state, and
rescans watched roots before claiming full freshness. Wall-clock timestamps
are for display; elapsed budgets use monotonic time, and boot/native identity
prevents old records from becoming current after a host restart.

## 8. Validation and acceptance gates

The [effort Validation Plan](README.md#validation-plan) owns the test checklist.
Measure, rather than assume:

- Zero reporting subprocesses and zero extra broker instances during bursts,
  absent-handler operation, restarts, and multi-install/version contention.
- Proposed targets: disabled-handler p95 added spawn latency under 1 ms;
  enabled foreground enqueue p95 under 1 ms; steady broker CPU under 1% of
  one core at 500 registrations. These are draft targets, not guarantees.
- Freeze the broker and its writer, deny access, stall producer file I/O,
  exhaust disk/quota, and overflow watcher notifications. Useful producer
  work/exit must not await reporting, and queues/thread counts remain bounded.
- Reorder/replay starts and ends; reuse PIDs; crash between DB commit and
  ticket deletion; restart or replace directories. No wrong attribution or
  resurrection; unknown remains distinct from dead.
- Exercise native Windows/Linux/macOS identity paths, foreign namespaces,
  physical/logical counts, shell/MCP/console-host coverage, and private
  snapshot access. Any unsupported case is an explicit release decision.

## See Also

- [Effort](README.md) and [vision](../../../visions/process-registry/README.md).
- [Process-slot ownership](../../../docs/patterns/process-slot-ownership.md),
  [endpoint rendezvous](../../../libs/endpoint-rendezvous/README.md), and
  [marketplace isolation](../../../docs/patterns/marketplace-installation-cells.md).
