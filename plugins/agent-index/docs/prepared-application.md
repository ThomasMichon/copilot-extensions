# Prepared source application

The shared engine prepares each acquired source manifest before applying it to
the native multi-model store. Chunking and embedding happen outside the
source-aware reader/writer fence. The acquired manifest remains pinned in the
existing `PendingCrawls` journal until store application, source-state save and
journal completion all succeed.

## Preparation and publication

Whole files are chunked into the same streaming batches used by ordinary batch
application; a file's chunks never straddle its cleanup/checkpoint boundary.
Compatible file checkpoints skip preparation. For selected pairs, the engine
reuses existing vectors only after qualifying their embedding-coverage receipts
and actual bytes. Missing vectors invoke the configured engine lazily.

For a nonempty selected-layout manifest, the existing owner first checks the
canonical `chunk_id`/`file_path` and registered-model `chunk_id` B-trees.
It can create missing indices on empty tables with a zero-row preparation
budget. Existing nonempty unindexed data requires explicit budgeted preparation
through `store.scalar_index.prepare_index()`; an ordinary round never disguises
that corpus build as a delta. Readers do not prepare indices.

Prepared chunks, file fingerprints and vectors live in `prepared_crawl_rows`,
a subordinate table in the existing path-index SQLite database. Construction
decodes the completed acquisition through the shared pending-journal codec.
Application callers also supply their manifest and full-rebuild mode; both must
match that decoded acquisition, including component versions when present.
Canonical manifest serialization preserves numeric metadata types rather than
equating booleans, integers and floating-point values; object key order is immaterial.
The journal then compares the parent row's exact source, account scope and
payload under SQLite writer exclusion, **before** creating any prepared tables,
triggers or rows. That same transaction binds a compact cache incarnation in
`prepared_crawl_parents`.
Each cache access checks that incarnation, not the full acquired content.
Parent retirement or replacement invalidates the incarnation and cached rows
in the same SQLite transaction, including when a journal-capable older caller
clears the manifest. Returning to identical manifest bytes cannot revive an old
binding. Neither table has a cursor, source lease, completion marker or repair
authority.

Each `PreparedApplication` also binds a fresh, one-shot batch plan in the existing
prepared rows. Its `batch-plan/v2` marker and plan-specific `batch-ref/v1` namespace are
separate from legacy `batch` rows, so even an already-running old journal cannot
delete or overwrite them. Different preparations retain independent plans;
rebinding an already-bound handle and changing a retained batch both refuse.
Plans retain only compact references to shared, content-addressed
`batch-payload/v1` bytes in the same journal. Identical retries do not duplicate
full chunk/metadata payloads. Batch serialization sorts object keys recursively,
including metadata nested inside lists, without changing list or checkpoint order.
Equivalent metadata insertion orders therefore share the same payload.
Each read qualifies both the reference and the
referenced payload's digest under one parent-checked SQLite snapshot.
Batch reads/writes and the
pre-publication checkpoint check verify that plan binding in the same SQLite
transaction as cache access. Exact vector pairs remain shared under the unchanged
pending-parent incarnation. Bare journal round-trips keep their existing behavior
when no application plan is bound.
An unbound handle cannot write batches or reset a plan while an application
binding exists; those checks share the mutation's SQLite transaction.
Direct binding on a fresh journal only appends an isolated namespace: it cannot
revoke an active publisher's plan, including during native writes. All namespaces
remain subordinate to the pending parent's existing retirement triggers.

The journal can bind a completed component envelope for subordinate cache work.
The explicit [native component application](component-ledger.md#explicit-native-application)
can supply `component_selection`, a frozen subset of acquired upsert identities.
This narrows only preparation; the journal still binds the entire exact manifest.
Selected component files are always chunked, with ordinary qualified vector
reuse, rather than trusting a file checkpoint as applied-version proof.
Default prepared application, the ordinary engine/recovery paths and pending
completion still refuse ordered components.

Vectors are keyed by model/table, canonical chunk ID and raw embedding-input
identity, including the model revision and preprocessing policy. Stored float32
bytes have an integrity digest and must have the configured finite shape.
Completed pair preparation can survive an interrupted later batch/model.
Retry reconstructs its file plan from the retained manifest and current file
checkpoints, reusing only exact cached vector inputs. It does not publish an
indexed-file checkpoint merely because embeddings were prepared.

Before entering the writer, the engine rechecks the selected file-checkpoint
snapshot under the existing domain exclusion. Changed checkpoints refuse before
mutation. Cancellation is checked after the final preparation call and again
around that pre-writer check, so a cancellation already observed before entry
does not dirty the store. One outer `SourceDomain.write()` then contains all content/vector
writes, stale-chunk cleanup and explicit tombstones for that application.
Nested mutation methods retain their existing effect/coverage checks.
Application reads prepared vectors, never invokes an embedding client, and
requalifies current vector coverage before omitting a pair write.

Before mutation, index metadata must still be fully covered, with room for one
additional segment when chunks are planned. At the end of the same writer,
point/file upkeep covers at most the application's prepared chunk count in
physical unindexed rows per index. The existing scalar helper appends only the
named index's tail, without consolidation, data compaction or version cleanup.
It verifies real fragment/row coverage and preserves the logical generation of
physical-only transitions. The 32-segment ceiling refuses a future row-writing
application before mutation; [explicit owner-budgeted consolidation](chunk-id-index.md#ingestion-preparation-and-explicit-consolidation)
can restore capacity before retrying the retained acquisition. It never runs
automatically from ingestion. A failure during post-write upkeep retains the dirty/uncertain
latch and pending acquisition, not an acknowledged source cursor.

Each file checkpoint still follows its completed vector coverage and stale
cleanup. The domain publishes its clean receipt before source-state save;
the pending row clears only after that save. A successful source-aware
application therefore advances the logical generation at most once, according
to actual witnessed mutations. An empty or entirely checkpoint-reused
application without tombstones enters no writer. Legacy unselected stores
also use preparation, but have no source-domain generation or cross-table
reader fence.

## Retained named-target acquisition

The explicit version-9 pending envelope binds a complete `PendingPointClaim`,
including its parked task/pin, account, endpoint, query, representation and every
typed target's version, parent and event correlations. `PreparedJournal` requires
that same whole claim; ordinary loading, application and completion still refuse it.

`PreparedPoints` retains individual adapter-qualified `PointAcquisition` outcomes
in the existing immutable prepared rows. A caller supplies the observation's
whole claim when retaining one exact target, its component version and exactly
one complete `FileEntry` or explicit `DeletedFile`. The component and mutation
must agree on path and canonical component family, and the component version
must equal the named target version. This helper performs no network reads and
does not establish fresh query eligibility. The adapter owns that qualification.
Each retained row checks the parent's existing incarnation and integrity digest.
Identical retries reuse the row; conflicting content cannot replace it.

Reopening can retrieve each positively retained target without reacquiring its
body. Manifest ordering records alone do not count as acquired target coverage.
`compose()` refuses until **every exact target**, including older versions and
separate correlations, has an observation. It enforces a one-to-one mapping
between logical points and physical components, stable component semantics, and
identical content/metadata/deletion for equal versions before selecting each
component's newest acquired version. Boolean, integer and floating-point
metadata values remain distinct. The separately versioned row codec refuses
unknown/duplicate fields, nonfinite numbers and non-JSON metadata.

The composed manifest is only a **candidate**: it does not replace the exact
manifest already bound to the pending parent. It preserves that parent's poll
marker without certifying a polling interval.

`PreparedJournal.for_point_application()` explicitly binds a complete candidate
for preparation. It first qualifies the exact pending payload, account scope
and whole claim through the ordinary journal owner, then recomposes every
retained target. The supplied manifest must match that candidate exactly,
including metadata types, component versions and the unchanged poll marker.
`PreparedPoints.bind_application()` retains that canonical manifest in one
immutable, separately versioned prepared row under the same cache incarnation.
An identical retry, including after a lost return, reuses the binding; missing
targets, changed inputs or conflicting retained binding bytes refuse. The
pending payload, target rows and parent incarnation remain unchanged. The
default journal constructor still requires its supplied manifest to match the
original pending manifest, even after this explicit binding.

Binding alone does not invoke preparation or native application.
`PreparedApplication` can explicitly consume the candidate by supplying both
`point_claim` and `component_selection`, with `full=False`. It deep-copies the
manifest, including nested metadata, before validation and file selection.
It validates the selected domain against the whole claim's typed scope, then uses
`for_point_application()` to recompose and bind every exact target before
resetting batches or calling an embedding client. Selection narrows chunking,
not the complete candidate or claim. Missing observations, changed claims,
scope/family mismatches, candidate changes and dirty domains refuse before
changing existing preparation. Recomposition holds no native-domain exclusion;
short native checks precede it. Whole-claim recomposition uses one existing
journal read snapshot with batches of at most 200 keys and constant-time target
membership; it does not open a connection per target. Raw payloads stream from
that snapshot and are released after validation rather than collected for the
whole claim. Equal-version comparison retains canonical-content digests, not
another copy of every historical body; only the selected candidate content is
retained for output.
For every source-aware preparation, the journal acquires its SQLite writer
transaction before entering native exclusion for the final clean check and
fresh plan insertion. SQLite contention therefore holds no native fence.
Plans are never replaced, even through direct journal calls. Preparation inside
an already-owned native writer, reader or operation refuses before journal
binding, so callbacks cannot hold native exclusion across recomposition or
embedding. Native exclusion does not cover ordinary target recomposition,
chunking or embedding.

Point preparation uses the same subordinate journal, batch plan, checkpoint
snapshot and exact vector cache. Its existing coverage/vector readers receive
the explicit scope; reusable native vectors still need matching receipts and
actual indexed bytes. Preparation uses the existing WAL-aware path-index
connection for coverage reads; committed journal work does not require the WAL
to be empty. Side-effect-free qualification still uses immutable reads and
refuses nonempty WALs. Neither mode writes coverage or repairs indices.
Chunking and embedding remain outside native writer exclusion.
Restart can reuse completed model pairs without changing canonical rows, file
checkpoints, source state, pending work or the parked queue claim.
Default preparation still refuses point envelopes, and
the separate explicit
[typed-claim application and completion](component-ledger.md#explicit-native-application)
compose point-domain publication and whole-claim qualification through their
existing owners. No native application, task acknowledgement, pending retirement,
cursor advance, executor admission or capability advertisement follows from
preparation alone. Ordinary recovery is not either explicit transition.
`tests/test_prepared_points.py` covers restart, exact-target completeness,
conflicts and stale parents; `tests/test_azure_devops_points.py` connects the
named-version HTTP mock reader to this retention path and proves body reuse.
`tests/test_point_prepared_application.py` qualifies actual native point-domain
preparation, interrupted-pair reuse, input refusals, coverage-qualified native
vector reuse and unchanged publication/completion boundaries.

## Failure and compatibility boundaries

Preparation failure/cancellation leaves canonical content, vectors, file
checkpoints and source state untouched. Search can continue against the clean
committed store during model/network work. Chunker failures propagate in both
layouts, including a later file failing after an earlier file's vectors were
prepared. Retry reuses those exact cached pairs without treating preparation
as a file checkpoint or successful source publication.

A full acquisition still forces compatible existing files through chunking.
If a later chunker fails, the old committed data/checkpoints remain usable and
the retained manifest keeps its full-rebuild flag. Even an ordinary retry
finishes that pinned full acquisition before discovering a newer delta.
Direct batch application can reuse an earlier file only when its store and
cleanup checkpoint completed; a later chunker failure cannot advance the
whole-source marker.

Pending-acquisition scope checks and mandatory replay prevent a newer delta
from replacing retained work. These are not a cross-generation source lock or
proof that different aliases' completed cursors describe the same stored family.

Store writes are **not rollback transactions** across Lance and SQLite.
An interrupted participating writer retains the existing dirty/effect latch;
prepared vectors do not authorize reopening it, clearing uncertainty or
repairing lost membership. See [source-aware storage](source-aware-store.md).
Legacy retry can reuse earlier completed file checkpoints and cached exact
vectors; native uncertain effects still require the existing explicit owner
recovery. A failed clean-receipt publication cannot advance source state.

Failure saving source state or clearing the pending row may leave an already
committed store application. Retry retains the old acquired manifest, reuses
completed file checkpoints and finishes the existing publication sequence
before fresh acquisition. This is not exactly-once lifetime accounting.

Completed manifest envelope versions remain unchanged. A journal-capable older
engine can replay the same manifest without using prepared rows; it may redo
embedding work and publish multiple generations. Partial acquisition versions
retain their existing rollback restrictions. No CLI flag, provider wire version,
store-layout protocol or automatic migration/repair is added.

Preparation performs work proportional to the acquired delta, with embedding
calls and cache lookups split by the configured batch size. The complete
manifest and a whole file's chunk output are still materialized, and prepared
vectors require additional durable disk space. This is not a bound on
whole-source memory, physical I/O, writer duration or full-reconciliation time.
The existing final GC, FTS and cluster owners remain outside this source writer.

`tests/test_delta_recovery.py`, `tests/test_prepared_application.py`,
`tests/test_prepared_journal.py`,
`tests/test_prepared_parent_binding.py` and
`tests/test_work_counts.py` exercise native layouts, retained preparation,
publication failures, vector validation, scope/checkpoint changes and
one-generation multi-batch application.
The explicit T3 `tests/test_prepared_restart.py` lane exits a preparing child
process without cleanup, then proves a fresh process reuses its durably completed
pairs before acquiring a newer delta. It is not part of the ordinary smoke lane.

See [architecture](architecture.md), [work counts](work-counts.md) and
[consumer migration](consumer-migration.md).
