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

A transport must durably couple its receiver-side cursor with reconciliation,
or query `inspect` for an already-reconciled operation. Replaying old history
after the Gateway already holds a later receipt encounters its intentional stale
sequence refusal; do not weaken that rule or reinterpret stale acceptance as a
new effect. Receiver cursor persistence, reconnect protocol, authenticated
snapshot reconciliation and bounded spool retention remain future gates.
Pagination bounds each read, not total disk use.

The journal uses five-second SQLite contention bounds, foreign keys, FULL
synchronous commits and local serialized writers. Every transaction verifies
schema and exact authority/installation/target identity. Only construction can
create storage; operations fail without recreating a removed database.
Unsupported/foreign databases are refused, and storage errors propagate.
It is not a migration mechanism, hostile-client isolation, network-share
database, multi-host fence, power-loss durability proof or HA implementation.

Tests exercise installed code across real process boundaries: concurrent
duplicate effects, abrupt pre-commit exit, post-commit lost reply followed by
Gateway reconciliation without effect duplication, and external-attempt crash
that remains UNKNOWN. No live deployment, service enrollment or authorization
change is required or performed by these tests.
