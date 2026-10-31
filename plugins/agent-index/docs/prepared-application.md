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
compares the parent pending row's exact source, account scope and acquired
payload once, then binds a compact cache incarnation in `prepared_crawl_parents`.
Each cache access checks that incarnation, not the full acquired content.
Parent retirement or replacement invalidates the incarnation and cached rows
in the same SQLite transaction, including when a journal-capable older caller
clears the manifest. Returning to identical manifest bytes cannot revive an old
binding. Neither table has a cursor, source lease, completion marker or repair
authority.

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
`tests/test_prepared_journal.py` and
`tests/test_work_counts.py` exercise native layouts, retained preparation,
publication failures, vector validation, scope/checkpoint changes and
one-generation multi-batch application.
The explicit T3 `tests/test_prepared_restart.py` lane exits a preparing child
process without cleanup, then proves a fresh process reuses its durably completed
pairs before acquiring a newer delta. It is not part of the ordinary smoke lane.

See [architecture](architecture.md), [work counts](work-counts.md) and
[consumer migration](consumer-migration.md).
