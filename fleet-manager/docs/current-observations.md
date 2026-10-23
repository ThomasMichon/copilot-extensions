# Explicit current-observation storage

`fleet_manager.observation_store.ObservationStore` is a **trusted local Python
storage API**, not a Gateway, network protocol, authenticated principal, service
installer or new domain authority. Default CLI commands never open it.
It closes one storage prerequisite of the
[central-state plan](../../efforts/active/fleet-control-v2/central-state-backing-store.md);
origin publishers, authenticated transport, complete roster synchronization and
resident subscriptions are not implemented by this slice.

## Custody and input boundary

The caller supplies an explicitly owned local database path, authority and
Gateway installation identity. The database has its own application ID,
schema/version, identity and random store ID. Foreign tables, indexes, triggers,
schema definitions, versions and installation identities refuse rather than
being migrated or repaired. It does not append tables to the operation ledger.
SQLite serializes short transactions with a one-second busy timeout and full
synchronization; errors propagate and no network work occurs inside transactions.
This is not a cross-host writer lease, replicated database or protection against
restoring an old backup over acknowledged writes. The existing installer and
Gateway lifecycle must own storage access, active-writer exclusion and recovery.

Only the future authenticated Gateway adapter may expose these operations.
It must independently validate current principal, fleet, origin, project,
entity and field grants, including revocation, **before storage access**.
Constructing a record, possessing a cursor or opening the database grants
nothing. These records contain no token or credential fields, but bounded text
is not automatically non-secret: the publisher must redact and select approved
current facts before constructing an update. Do not expose this Python API as
untrusted JSON ingress or forward its internal global positions to scoped users.
Its cursors are typed correlation values, not signed/opaque access tokens;
the eventual wire cursor must bind authorized scope without disclosing other
scopes' activity or counts.

The explicit [scoped point-reader](scoped-observation-reader.md) composes local
admission and this store under a mandatory participating owner fence. It returns
only selected state and defined entity-local metadata, never these internal
cursors. Its separate bounded snapshot method composes the trusted point-set
read below. Neither changes this raw API's trust boundary or wires a resident
consumer.

## Origin and sequence boundaries

`OriginBinding(origin, installation, generation, producer)` is an explicitly
admitted binding. All textual identities use the product's bounded identifier
contract; service-offer opaque installation IDs need an agreed adapter, not
lossy normalization. A trusted caller uses `bind_origin(binding, expected=...)`
to compare-and-set the current binding. First admission is generation 1;
replacement requires the exact previous binding and its next generation.
An identical current binding returns its original position. No update enrolls
itself, replaces a producer, or wins by supplying a newer timestamp.

The installation identity, admitted origin generation and producer incarnation
serve different purposes. A resident's local projection generation can identify
its producer but cannot authenticate the origin or substitute for admitted
generation. Connector generations remain with their transport owner.
Replacing a binding fences prior updates immediately at this storage boundary.
Existing rows remain visible as `origin-resync-required`, with original facts
and provenance, until explicitly replaced. This does not stop an old process,
retire an origin or release a claim.

`CurrentUpdate.from_dict(...)` freezes canonical schema-v1 bytes with:

- `schema: fleet-manager.current-update`, `version: 1`;
- binding fields `origin`, `installation`, `generation`, `producer`;
- exact `project` and `entity`, contiguous `sequence` starting at 1 for each
  admitted origin generation/project, and positive `source_revision`;
- finite nonnegative `observed_at`, `availability` (`current`, `stale`,
  `advisory`, `unknown`), nullable bounded reason code, and `state`.

`state` is a nonempty scalar subset of `status`, `title`, `branch`, `codename`,
`platform`, `checkout_present` and `summary`. Text is nullable and limited to
4 KiB UTF-8 per field; checkout presence is nullable boolean. Arbitrary nested
payloads, journals, ledgers, transcript bodies and claims graphs are unsupported.
The complete canonical encoded update is limited separately to 64 KiB; Unicode
escaping can exceed that bound even when each field fits.

Exporter sequence, source revision and central position are **not interchangeable**.
An exporter must retain its pending sequence across retries; a gap refuses
without replacing current state. Source revisions cannot regress within a
generation/project. Multiple entities can share a source revision.
At the same source revision, an entity's state and the publication's observation
timestamp cannot change, but availability can change under a new export sequence.
This preserves stale/unready notification without inventing a new observation.
Source revision jumps are allowed: these are individual point observations,
not a claim that every entity from skipped source publications was delivered.

`state: null` with reason `projection-removed` records an explicit removal
from the current view. It remains point-readable and replayable, is excluded
from ordinary pages, and can be replaced by a later update. It is not a domain
tombstone, entity destruction, verified origin retirement or obligation settlement.
No absence, heartbeat expiry or missing point row proves those outcomes.

## Commit, query and recovery

`apply(update)` commits accepted bytes, the current entity slot, per-project
export head, central position and replay retention together before returning
the accepted position. An identical latest or retained older retry returns its
original position without creating another event. Reusing its sequence with
different bytes refuses. Older retries outside retained evidence fail explicitly;
the latest stream identity remains available independently of replay pruning.
No callback executes a command or external effect.

Every query requires exact origin/project scope. `read(..., entity=...)`
selects one indexed current slot and returns its current admitted binding,
nullable row and snapshot cursor. A missing row means **unobserved**, not
confirmed absence. Each row retains its source revision, availability and
central accepted position.

`read_points(points)` requires a tuple of one to 64 distinct exact
`(origin, project, entity)` keys. Every key is validated before opening one
query-only read transaction. It reuses the point reader's indexed lookup,
binding and whole-row validation and returns a tuple of `{binding, row}` values
from that same committed snapshot, in input order. There is no cursor in the
batch result; raw rows still contain trusted internal metadata and must not be
sent to scoped clients. A failure on any key fails the entire call. This is a
point-set snapshot, not an enumeration of all entities or complete source
coverage. The authorized reader projects only its granted fields and defined
entity-local metadata after whole-request admission.

`page(origin=..., project=..., limit=...)` returns up to 64 present rows in
entity-key order, without scanning a journal or returning a total fleet count.
Continue using both `next_entity` as `after_entity` and the original `cursor`
as `at`. A continuation without its cursor refuses. Any intervening central
commit invalidates further pages rather than mixing snapshots; restart the
page query. This conservative optimistic boundary does not pin unbounded
historical snapshots or guarantee progress under continuous writes.
Pages explicitly report `point-observations-only`: absence from the store is
not completeness of a machine, project or fleet.

Attach observation recovery with `replay(snapshot_cursor, limit=...)`.
The initial query and cursor come from one read transaction; replay includes
intervening commits, including origin-binding changes. Each page returns up to
64 events in central order, a next cursor and `more`. Changes from other
origins/projects are not returned. When no more scoped events remain, the
internal cursor advances to the captured central head.

The database retains the latest **1,024 central events**, independent of
current slots. A cursor older than that retained boundary requires a fresh
snapshot explicitly, even if only other scopes consumed the window. A cursor
for another store/view or beyond the current head refuses. Reopening the same
database preserves cursor identity and positions; a new database does not.
Slow readers never block writers by retaining a Python queue or old SQLite
transaction. Durable replay polling is a storage seam, **not a delivered
resumable socket subscription**.

## Consumer alignment still required

The existing `agent-worktrees` `worktrees-current-v1` read and
`worktrees-feed-v1` held feed publish schema-bounded current rows with
generation/revision and complete `done`/`commit` barriers. The local feed has
one-slot coalescing, not durable reconnect replay. Availability may change
without a source revision. Its producer enrollment is demand-based.
The central adapter must preserve those distinctions, authorize fields before
export, retain exporter sequence and explicitly reconcile snapshot completeness,
removals, reconnect gaps and producer replacement. Do not scrape tracking
YAML/history or relabel a durable advisory fallback as live current state.

This module does not enroll origins, implement that adapter, acquire a credential,
start a listener, update a container, change Manager UX, or migrate an authority.
Native/SSH standalone consumers remain unchanged. The full reviewed
[Fleet Control V2](../../efforts/active/fleet-control-v2/README.md) integration,
subscription, lifecycle, platform and adoption gates remain open.
