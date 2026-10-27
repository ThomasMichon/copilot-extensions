# Prepared bucket/model item reads

The store exposes an **opt-in Python prerequisite** for slice-local clustering:
read one canonical source bucket and only the requested model tables. It does
not yet change ordinary clustering invalidation or artifact replacement.
The [covered maintenance owner](architecture.md#covered-derived-maintenance)
still uses its conservative full pass after a real projection change.

```python
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.source_bucket_index import prepare_source_index

# An already initialized participating source store, not legacy writer adoption.
prepare_source_index(store.content_store, max_rows=canonical_preparation_budget)
prepare_chunk_id_index(store.get_vector_table("prose"), max_rows=vector_preparation_budget)

items = list(store.iter_bucket_item_representations(
    "forge:issues", model_ids=("prose",), row_budget=canonical_slice_budget,
))
```

Preparation reuses the same budgeted scalar-index mechanism as
[chunk-ID point reads](chunk-id-index.md). The canonical `source_idx` is a
B-tree on `source`; vector reads require the selected tables' prepared
`chunk_id_idx`. Initial builds and unindexed tails require explicit physical-row
budgets. Complete current fragment coverage and at most 32 segments are required.
Reads never build or extend an index, compact storage, merge old index segments,
prune versions, rebuild FTS or silently fall back to a corpus scan.
Pure preparation retains already-current FTS coverage and witnessed logical
generations through the existing source-domain maintenance transition.
The old chunk-ID state/exception imports remain compatible aliases of the shared
scalar state/exception; no new receipt or writer authority is introduced.

A bucket is the existing first-two-colon-segments definition. `forge:issues`
includes that exact source and its colon-delimited descendants, not
`forge:issues-extra`. A one-segment bucket selects only that exact source.
Names, including quotes, backslashes, percent and underscore, are literal;
the indexed prefix range does not treat them as wildcards. Empty, overlong,
control-containing or noncanonical bucket names refuse.

The mandatory `row_budget` is a nonnegative canonical **chunk-row** limit, not
an item count. An indexed count refuses an oversized slice before materializing
canonical rows. Complete input is read in batches of 200, grouped by source/path,
and pooled in the same line order with the same weights and exact-content hash
as full representations. All canonical chunks contribute to the item hash,
even when the selected model covers only some chunks. Only selected registered
model IDs are queried; missing vectors skip that model's centroid, not the
canonical input. Duplicate model selections are coalesced.

Missing/incompatible preparation, unindexed tails, invalid selections, incomplete
or malformed canonical input, duplicate chunk IDs and backend failures propagate
without a success-shaped empty result. A genuinely empty indexed bucket performs
no vector reads. Lazy iteration holds the existing source-domain read exclusion;
callers must exhaust it successfully before publishing any derived artifact.
Memory and clustering work can grow with the selected bucket, and native
manifest/fragment metadata and index I/O are not constant or zero. This API does
not certify whole-index write I/O, retention, atomic source-delta results,
per-bucket generations or partial artifact coverage.

## Reproduction

Run `tests/test_source_bucket_index.py` and `tests/test_chunk_id_index.py` through
the repository's bounded plugin test runner. Native fixtures retain 100, 10,000
and 100,000 unrelated canonical/vector rows. Cold/warm executed plans must use
`ScalarIndexQuery`, scan exactly the two selected data rows and stay below 64
reported I/O operations per query. Actual representation calls reject global
identity enumeration, per-file canonical scans and unselected model reads.
Strict-input, budget, restart, tail, update/delete and compatibility controls
remain in this opt-in integration lane, not the required unit-only CI packet.
