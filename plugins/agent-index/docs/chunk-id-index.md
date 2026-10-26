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
without claiming that a stale FTS index became current. Existing physical-version
cluster coverage remains conservatively invalidated.

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
