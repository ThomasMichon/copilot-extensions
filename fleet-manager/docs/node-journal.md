# Node acceptance, effects and result spool

Back to [operation contract](operation-contract.md) and
[Fleet Control V2](../../efforts/active/fleet-control-v2/README.md).

`fleet_manager.node_journal.NodeJournal(path, authority=..., installation=...,
target=...)` is an explicit local Python primitive, not an installed node
service or an automatically activated second queue. Its database is separate
from the Gateway operation ledger. The caller must authenticate the connection,
authorize exact principal/account/command/target/generation scope, and choose
trusted local storage before using it. Names and generation correlation alone
do not establish permission or distributed resource fencing.

`accept(intent)` atomically persists exact immutable intent and its acceptance
receipt. Duplicate identity/content returns the latest original receipt; changed
content (including generation) under the same identity fails. Receipt sequence
starts at one. The original receipt and subsequent outcomes remain in an
append-only spool independent of any Gateway connection. No admission TTL
removes accepted responsibility.

## Cooperative local effects

`commit_cooperative(intent, apply)` runs a trusted, short SQLite-only callback
against the journal's transaction. The callback uses the supplied connection for
its own local effect tables and returns a bounded result reference. Its local
effect and succeeded receipt commit together. A callback or receipt failure rolls
both back. Process exit before commit leaves acceptance; exit after commit leaves
the original queryable outcome. Repeating a terminal operation returns its prior
receipt without invoking the callback.

The callback must not perform Git, SSH, filesystem, process, network or prompt
effects. Those cannot roll back with SQLite. Do not expose arbitrary callback
selection or SQL through a remote protocol. This is an internal cooperative
effect-owner seam, not a general remote-code executor. The connection authorizer
refuses callback transaction/savepoint, attach/detach and PRAGMA operations.
Callbacks remain trusted local code, not a security sandbox; they must not alter
the journal's own schema/state or invoke privileged connection methods.
The callback's effect table and result reference must contain no secrets.
No transaction lock is held across an external effect.

## Owned cooperative allocations

`accept_allocation(intent, resource=...)` binds a `workspace.allocate` intent to
one canonical local resource, committing its held ownership and acceptance/spool
together. The caller must authenticate/authorize the operation and resolve the
real resource identity; aliases must not name the same resource as independent
ones. Resource identifiers use the existing 1-128-character bounded ASCII shape.
One operation owns at most one resource, and another operation cannot accept
that resource, even with a higher generation, different account or principal.
Exact repeats return the original latest receipt. Historical unbound acceptance
cannot acquire ownership retroactively.

For allocations, `commit_cooperative(intent, apply, resource=...)` requires
exact held ownership in the **same transaction that executes the SQLite effect**.
Immutable accepted intent binds authority, target, principal/account, request
content and generation. Wrong ownership/resource/content fails before invoking
the callback. Ownership and accepted content are rechecked after the trusted
callback; a changed binding rolls back its effect. Terminal retries still require
the original binding and do not execute again. Generic cooperative calls without
a resource cannot bypass allocation ownership, and nonallocation operations
cannot supply a resource binding.

This is effect exclusion only for a correctly implemented trusted SQLite-only
adapter whose resource lives in this journal's local transaction. It does not
fence a filesystem workspace, process, provider or resource shared with another
node/database. Two node journals do not jointly exclude a shared external
resource. `begin_external` explicitly refuses allocations: the existing UNKNOWN
attempt marker is not a substitute for an actual external effect-owner fence.
Generic `accept` remains available for legacy journal records, not permission
to execute an unbound allocation.

`allocation_owner(resource=...)` returns the immutable owning intent, or null,
as local evidence only. There is no TTL, release or generation-transfer API.
Succeeded or failed outcomes do not relinquish ownership; competing generations
remain blocked after an outcome or controller disconnection. Verified retirement
and release evidence are the next contract, not inferred from elapsed time.

## Noncooperative effects and uncertainty

`begin_external(intent)` atomically persists UNKNOWN before external execution.
Only the first transition from accepted returns true; concurrent or later calls
return false and must not execute the effect. This provides at-most-one local
attempt permission, not exactly-once external execution or remote fencing.
A crash even before starting the effect conservatively leaves UNKNOWN.
There is no lease-based retry, blind reassignment, expiry cleanup or
automatic return to accepted.

`reconcile(intent, receipt)` requires an exact correlated succeeded/failed
receipt at the next sequence after UNKNOWN. An identical terminal repeat is
idempotent, while terminal replacement fails. The caller owns evidence that the
effect actually occurred or definitively failed; shape/digest checks do not
authenticate that assertion. An executing adapter can record its known result
through this same seam. A restart without that evidence remains uncertain.

## Replay and storage limits

`inspect(intent)` returns the latest receipt or null for an unaccepted identity.
`results(after=0, limit=100)` returns chronological `{cursor, receipt}` entries;
the integer page limit is 1-256. Inspection and spool reads use committed,
deferred query-only snapshots. Cursors are local to this journal installation,
not transport acknowledgments or globally comparable connector generations.
Reads never delete results or acknowledge delivery.

The [Gateway ledger](operation-ledger.md)'s explicit `result_cursor` and
`reconcile_results` APIs couple the receiver-side cursor with receipts, outbox
and audit in one transaction. A transport must use that durable position on
reconnect, or query `inspect` for an already-reconciled operation.
Replaying old history
after the Gateway already holds a later receipt encounters its intentional stale
sequence refusal; do not weaken that rule or reinterpret stale acceptance as a
new effect. Authenticated reconnect protocol, snapshot reconciliation,
installation transfer and bounded spool retention remain future gates.
Pagination bounds each read, not total disk use.

The journal uses five-second SQLite contention bounds, foreign keys, FULL
synchronous commits and local serialized writers. Every transaction verifies
schema and exact authority/installation/target identity. Only construction can
create storage; operations fail without recreating a removed database.
Unsupported/foreign databases are refused, and storage errors propagate.
Construction upgrades known schema 1 to schema 2 transactionally, preserving
existing acceptance, receipt history and cooperative effect tables while adding
empty resource ownership. It refuses incompatible core columns or a preexisting
reserved ownership-table/view name. No ownership is invented for old operations.
Old schema writers/readers fail closed after upgrade; no downgrade or stale
backup restore is supplied.
It is not a V1 authority-migration mechanism, hostile-client isolation, network-share
database, multi-host fence, power-loss durability proof or HA implementation.

Tests exercise installed code across real process boundaries: concurrent
duplicate effects, abrupt pre-commit exit, post-commit lost reply followed by
Gateway reconciliation without effect duplication, and external-attempt crash
that remains UNKNOWN. No live deployment, service enrollment or authorization
change is required or performed by these tests.
Owned allocation tests additionally exercise four installed node processes,
competing identities/generations, effect crash rollback with retained ownership,
lost postcommit replies with one effect, atomic spool rollback and legacy schema
upgrade without historical ownership. These local proofs do not establish
cross-host fencing, revocable delegated grants or live Gateway/node readiness.
