# Prepared chunk-ID point reads

The shared store has an **opt-in Python path** for strict point reads against a
prepared scalar B-tree index. It uses the existing participating source-domain
writer exclusion and receipt. It does not adopt a legacy store, introduce a
second lock/coverage authority, or change ordinary indexing/search defaults.

```python
from agent_index.store.chunk_id_index import prepare_chunk_id_index

# `store` is an already initialized participating source store.
prepare_chunk_id_index(store.content_store, max_rows=initial_rows_budget)
prepare_chunk_id_index(store.get_vector_table(model_id), max_rows=initial_rows_budget)

records = store.content_store.get_by_ids(chunk_ids, bounded=True)
vectors = store.get_vector_table(model_id).get_vectors(chunk_ids, bounded=True)
```

Preparation is explicit. Its required `max_rows` bounds the **physical rows in
unindexed fragments**, including deleted rows, not the smaller live-row count.
A missing index requires a budget for its complete initial build. An existing
index extends only the unindexed tail with the seasoned `pylance` SDK's
`optimize_indices(index_names=["chunk_id_idx"], num_indices_to_merge=0,
retrain=False)`. It never invokes combined optimization, compaction, version
cleanup or an FTS rebuild. At most 32 index segments are accepted; exhaustion
requests separately budgeted owner-coordinated consolidation, never an implicit
merge of large old segments. A covered no-op performs no index write.

Known preflight failures occur before a dirty writer transaction. The writer
rechecks under the existing exclusion before changing the index. If an
intervening committed writer changed that budget/coverage, a known pre-effect
refusal exits the write context normally before being re-raised, preserving the
current clean receipt and intervening commit. Errors after the index effect
begins retain the existing dirty/uncertain recovery semantics.
Pure index-only writes carry forward previously current source FTS coverage
without claiming that a stale FTS index became current. Participating writers
preserve witnessed logical cluster coverage across physical-only preparation;
unproven or legacy coverage stays conservative. See
[covered derived maintenance](architecture.md#covered-derived-maintenance).

Bounded reads require complete index coverage and at most 32 segments. They
deduplicate input IDs, query batches of 200, reject duplicate/unexpected stored
IDs, propagate backend errors and validate vector values/dimensions. Absence is
never inferred from a caught read failure. Reads never prepare or refresh an
index. Following append/update, run explicit budgeted preparation before the
next bounded read; deletion-only changes need no index extension.

`ChunkIdIndexPreparationRequired` reports missing/incompatible indices,
oversized tails, exhausted segment budgets or nonparticipating ownership.
Existing calls without `bounded=True` retain their compatibility behavior.
They are **not** certified bounded merely because their returned row count is
small. No ordinary-round migration, legacy writer adoption, model backfill,
retention policy or automatic consolidation is supplied by this API.

### Embedding coverage

The existing `EmbeddingCoverage` owner accepts `bounded=True` on `missing`
and `record`. Both use the same prepared-index vector reads, retaining raw
input/profile hashes, actual stored-vector verification and 200-ID batches:

```python
missing = coverage.missing(vector_table, selected_chunks, profile, bounded=True)
# After embeddings are stored and explicit tail preparation succeeds:
coverage.record(vector_table, completed_chunks, profile, vectors, bounded=True)
```

Receipt publication refuses missing preparation, unindexed tails, backend
errors and mismatched stored vectors before committing any new receipts.
No method prepares indices, acknowledges a failed read as vector absence, or
changes the default content-first embedding writer. A new profile with no
candidate receipts needs no vector read to establish missing pairs.
Existing calls without the keyword remain unchanged. This opt-in is a
backfill prerequisite, not a resumable backfill operation or a new cursor,
source-lock, reader-currentness or coverage authority.

### Canonical provenance before vector writes

`VectorTable.upsert(..., bounded_provenance=True)` and
`MultiModelStore.upsert_vectors(..., bounded_provenance=True)` opt into the
prepared canonical point-read path for the existing source/language provenance
check. They require a participating source store and a prepared canonical
chunk-ID index. Defaults retain the existing writer.

The opt-in validates input vectors and unique chunk IDs, checks canonical
provenance before entering the writer, then rechecks under the existing writer
exclusion. Known missing-preparation or provenance refusals before vector
effects preserve the current clean receipt, including an intervening committed
content write. Unexpected errors and errors after the vector effect begins
retain the existing dirty/uncertain recovery semantics. Empty selected input
performs no write. Successful calls write only the vector table, never canonical
content or its FTS index.

If a caller already owns an outer writer context, that caller still owns
failure disposition. This method never marks an enclosing transaction clean
or clears its uncertainty merely because its own vector effect did not begin.

This keyword bounds **canonical provenance reads**, not the whole native
delete/add transaction. It does not certify raw-text/profile currentness,
prepare indices, or publish embedding coverage/cursors. Following a vector
write, explicit tail preparation is still required before bounded vector reads.
A backfill caller must retain its source/account/profile binding and revalidate
the full current embedding input before publishing vectors and progress.

### Current canonical input before vector writes

The additional `bounded_input=True` keyword on those same methods includes the
prepared provenance checks and compares every persisted canonical chunk field
except the physical write timestamp. The existing `content_record` owner defines
this comparison, including raw text, metadata, type, path and line boundaries.
It catches raw-text changes that retain the normalized chunk ID, both before
writer entry and after an intervening content commit under writer exclusion.
Known pre-effect mismatches preserve clean state; post-effect failures remain
uncertain. Ordinary and provenance-only calls retain their previous behavior.

This is current canonical input at vector publication, not model/profile
selection authority or a resumable backfill cursor. It prepares no index,
rewrites no content, and publishes no embedding receipt. Explicit vector-tail
preparation, verified coverage and source/account/profile binding remain the
backfill caller's obligations; whole vector-mutation I/O is not certified.

### Stored-content pair writer

`indexing.embedding_batch.embed_and_store_pairs` is the same routed pair
planner/writer used by the content-first pipeline, without canonical content
upserts. It coalesces exact duplicate inputs, rejects conflicting canonical
IDs, preserves unclaimed-type routing and counts actual vector rows written.
An empty/reused plan does not call engine readiness or embedding.

Its opt-in `bounded=True` requires explicit model profiles, an existing
`EmbeddingCoverage` and an `after_store(vector_table, selected_row_count)`
callback. The caller supplies already selected current chunks and prepared
canonical indices. Current raw canonical input is checked before missing-pair
selection/readiness, even for cached pairs, and under existing vector-writer
exclusion immediately before vector publication.

The callback runs only after a vector commit; it must perform the caller's
explicitly budgeted vector-index preparation. The row count is the selected
batch size, **not evidence of the actual unindexed physical-row budget**:
pre-existing tails and intervening writes can require more, and preparation
must refuse when the authorized budget is insufficient. The helper never
prepares an index itself. Verified bounded coverage publication follows the
callback; any failure propagates without a success result or progress update.
Old receipts are not invalidated before embedding or a known pre-effect vector
refusal. After a vector commit they are invalidated before the callback; partial
vector failures cannot make stale receipts reusable without actual input and
vector-hash verification.

This helper is not an operational backfill. Its caller still owns a pinned
source/account/config/profile binding, resumable snapshot discovery, retention,
source exclusion and coverage-before-progress ordering. Whole vector-mutation
I/O remains unqualified. Without the keyword, the content-first pipeline retains
its existing writes, receipt invalidation, batching and compatibility defaults.

### Bounded canonical snapshot reader

`ContentStore.capture_snapshot()` captures a typed `ContentSnapshot` without
creating or migrating a table, reading content rows, or changing the writer's
handle. `read_snapshot(snapshot, start=..., limit=...)` opens an independent
handle and checks out the captured immutable native version. It reads at most
`limit` logical offsets using `take_offsets(...).select(...).to_arrow()`, verifies
the captured row count and complete batch length, and strictly decodes canonical
fields and JSON-object metadata. Offsets are relative to that version; returned
row order is unspecified. The terminal offset returns an empty batch only after
the descriptor, retained version and storage checks pass.

The descriptor binds the resolved local root, table name, native version, row
count, device/inode/directory-ctime identity and existing source-domain UUID.
Identity is checked before and after each read. Remote storage, unavailable
retained versions, replaced storage, malformed/incomplete rows and identity
changes raise `ContentSnapshotUnavailable`, never fall back to latest.
Existing source-aware dirty/busy/currentness checks remain in force and retain
their `SourceLayoutError` disposition.

The directory identity is deliberately conservative: native append,
replacement, deletion and index operations preserve old-version offset reads,
but ordinary operations can change directory `ctime` while preserving its
inode. Such a change requires explicit repair rather than weakening the
incarnation key. This API does not promise uninterrupted resume through every
canonical mutation, pin retention, persist a cursor, select source/account/model
scope, or authorize progress. A resumable backfill owner must supply those
contracts and the pair writer's current-input checks before vector publication.

### Explicit resumable model backfill

`indexing.embedding_backfill.backfill_embedding_slice` processes one explicitly
budgeted canonical offset range per call, without acquisition, chunking,
canonical rewrites or source-checkpoint advancement. Its required inputs are a
resolved `SourceSpec`, the actual connector, configured participating store and
path database, all configured model clients, a positive integer `row_budget`
and the pair writer's explicitly budgeted `after_store` preparation callback.
Prepared canonical indices remain a prerequisite; the helper never hides a
corpus-wide preparation step.

Backfill intent lives in the existing path database, initialized with the
`PendingCrawls` owner. It binds the accepted source/account configuration and
canonical family from `IndexState`, effective embedding policies and routing/
table mappings, immutable canonical snapshot and model-table versions. A
missing or mismatched acquisition identity requests repair, including for an
explicit restart. Backfill requires an explicit connector `recovery_identity`;
ordinary indexing's legacy source-name fallback never grants backfill account
or target authority. Only rows in the accepted colon-delimited family are embedded;
other rows still consume the explicit global-offset budget and are reported
separately. This is budgeted snapshot discovery, not a source-indexed point
enumeration or an ordinary-round corpus scan.

Ordinary indexing remains active between slices. Each slice uses existing
source-domain writer exclusion during bounded pair work, then exits the clean
writer before advancing progress under read exclusion. It checks source,
profile, canonical and vector-version identity before engine work and progress.
Verified actual pair coverage and proven vector versions are retained before
the offset is acknowledged, so a failure after clean storage completion replays
the same covered page without embedding again. Compare-before-save refuses a
concurrent restart instead of overwriting its cursor.

Changed canonical input, profile/account scope or previously covered vector
versions fail visibly; there is **no automatic latest-snapshot restart**.
`restart=True` explicitly resets only derived backfill discovery and reuses
valid pair receipts. Uncertain store failures retain the existing source-domain
dirty/repair disposition; restart does not clear it. Completed matching jobs
return through metadata checks without another content read or embedding call.

The existing GC guard refuses retirement while a backfill is unfinished, but
ordinary crawl acquisition is not fenced for the job's lifetime. Ordinary
`run_reindex` GC reports a nonfatal deferral while retaining that guard,
so successful acquisition does not become a failed indexing round. Known
pre-effect writer-admission refusals propagate only after a clean writer exit;
effectful failures retain the normal uncertainty disposition.
Retirement holds existing source-domain operation exclusion from pending-work
admission through data and identity cleanup, preventing a backfill from entering
between the guard and deletion without turning independently committed writes
into an outer transaction.

Explicit `discard_embedding_backfill` removes only an exactly matching derived intent
under clean source-domain exclusion; it requires caller authorization and
preserves canonical data, coverage and acquisition checkpoints. This also
allows deliberate abandonment of a stale job before retirement. New guard
behavior requires participating binaries with this API; it is not a lease or
mixed-old-binary GC guarantee, and does not replace #4220/#4222/#8733/#5302.

## Qualification and limits

`tests/test_chunk_id_index.py` exercises actual content/vector stores at 100,
10,000 and 100,000 canonical rows, cold/warm native plans, append/update/delete,
explicit preparation/refusals, duplicate rows and source FTS currentness.
`tests/test_scalar_index_sdk.py` separately qualifies index-only SDK compatibility
in both layouts, retained old native versions, prohibited combined maintenance
and bounded append work at the same three corpus sizes.
These belong to the existing bounded native integration lane, not required
unit-only CI.

`tests/test_bounded_vector_provenance.py` covers actual vector writes,
pre-effect refusal, intervening content commits, retry, post-effect uncertainty
and legacy exclusion. The scale fixture also routes real vector writes through
this provenance opt-in while retaining the canonical table version. This does
not claim that native vector mutation I/O has been fully qualified.

`tests/test_content_snapshot.py` qualifies native immutable offsets across
append/upsert/delete/index creation, bounded pages and reopen in both layouts,
metadata corruption, incomplete reads, descriptor/range refusals, before/after
identity races and existing source-domain guards. It records the conservative
directory-ctime limitation rather than claiming stable incarnation metadata
across all ordinary mutations. No source cursor or operational backfill is
qualified by these reader tests.

The qualified native dependency composition is LanceDB 0.40.0, pylance 13.0.0
and lance-namespace 0.11.1 on Linux x86-64. SDK wheels also exist for Linux ARM64,
macOS ARM64 and Windows AMD64; wheel availability is not native qualification on
those platforms. The host-only `[store]` extra owns the SDK dependency; clients
remain light.

Native data-row bounds do **not** mean zero index/page/metadata work. Reading
index descriptors and fragment coverage depends on segment/fragment metadata;
index comparisons/pages and payload bytes remain real costs. These witnesses
do not prove the entire incremental-ingestion scale matrix or a consumer's live
adoption. [Round work counts](work-counts.md) do not measure this SDK I/O.

See [architecture](architecture.md) and [consumer migration](consumer-migration.md).
