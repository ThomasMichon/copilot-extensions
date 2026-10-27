# Prepared bucket/model item reads

The store exposes an **opt-in Python prerequisite** for slice-local clustering:
read one canonical source bucket and only the requested model tables. It does
not change the default clustering invalidation or artifact replacement.
The [covered maintenance owner](architecture.md#covered-derived-maintenance)
uses its conservative full pass by default; the
[opt-in slice planner](cluster-slices.md#opt-in-maintenance) composes this reader
with witnessed generations and transactional partial publication.

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
By default, items without selected centroids are omitted.
`include_unembedded=True` also yields those canonical items, with an empty
centroid map, so callers can count the complete bucket without querying
unselected models.

Missing/incompatible preparation, unindexed tails, invalid selections, incomplete
or malformed canonical input, duplicate chunk IDs and backend failures propagate
without a success-shaped empty result. A genuinely empty indexed bucket performs
no vector reads. Lazy iteration holds the existing source-domain read exclusion;
callers must exhaust it successfully before publishing any derived artifact.
Memory and clustering work can grow with the selected bucket, and native
manifest/fragment metadata and index I/O are not constant or zero. This API does
not certify whole-index write I/O, retention, atomic source-delta results,
or partial artifact coverage.

## Witnessed bucket generations

Participating content/vector upserts, canonical deletes and vector provenance
updates now describe their affected sources through
`SourceDomain.bucket_mutation`. Canonical deletes reuse their existing row
stream, selecting source alongside chunk ID; they do not add a second listing.
The owner's table transition must connect writer-entry identity, every scoped
mutation or physical-maintenance transition, and the final clean witness.
Raw writes before, after or between those operations cannot inherit that proof.
An unannotated effect conservatively invalidates changed tables as a whole.
Standalone vector ID/staleness deletion remains on this conservative path.

Optional `bucket_generations` version-1 metadata is nested inside the existing
`logical_projection`, not a separate file, lock, source cursor or authority.
Each table has a coarse baseline and sparse bucket overrides stamped with the
outer logical commit generation. Nested writes union affected buckets and publish
only once at the clean commit. Empty/deleted buckets retain their overrides so
removing the last item cannot resurrect an earlier key. Creation, replacement,
unknown writers and unsupported source identities reset coarse coverage.
Invalid/missing bucket metadata uses the table generation instead.

The map permits at most 256 sparse overrides across tables, and each writer's
per-table union is similarly bounded. Exceeding the budget logs a conservative
fallback, never a partial map that omits an affected bucket. Every receipt
publication drops bucket metadata before table-level logical metadata when
needed for the existing 64 KiB limit; mandatory state still refuses overflow.
Sources that cannot form a reader-qualified bucket remain writable, with
whole-table invalidation rather than an invented partial proof.

`bucket_generation` is an internal key helper for an already-current, physically
witnessed logical projection. It is not a standalone freshness check. Existing
global cluster coverage is unchanged and still observes every table generation.
The [opt-in slice planner](cluster-slices.md#opt-in-maintenance) validates the
same optional states only after matching their physical witnesses; it refuses
missing proof instead of silently turning an ordinary update into a full repair.

## Reproduction

Run `tests/test_source_bucket_index.py` and `tests/test_chunk_id_index.py` through
the repository's bounded plugin test runner. Native fixtures retain 100, 10,000
and 100,000 unrelated canonical/vector rows. Cold/warm executed plans must use
`ScalarIndexQuery`, scan exactly the two selected data rows and stay below 64
reported I/O operations per query. Actual representation calls reject global
identity enumeration, per-file canonical scans and unselected model reads.
Strict-input, budget, restart, tail, update/delete and compatibility controls
remain in this opt-in integration lane, not the required unit-only CI packet.
`tests/test_bucket_projection.py` adds native write/delete/model isolation,
nested unions, restart/tombstones, mixed maintenance, raw/unknown writes,
older-writer drift, failed publication and bounded metadata fallback controls.
