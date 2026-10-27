# Transactional cluster slices

`ClusterStore` has an additive Python storage seam for partial artifact
publication. Ordinary maintenance still runs the global clustering pass; this
seam does not select slices, prepare indexes or enable incremental maintenance.
The [architecture overview](architecture.md#covered-derived-maintenance)
describes that default and its source-owned coverage.

## Optional complete manifest

`store.cluster_slice_coverage.ClusterSliceManifest` describes the complete
artifact with:

- the existing `ClusterCoverage` signature and aggregate counts;
- a coarse `basis` signature binding policy, domain and table identity;
- per-`(bucket, model_id)` input signatures, eligible-item counts and cluster
  counts;
- unique eligible-item totals per bucket, counting the union across models
  rather than summing overlapping model memberships.

Signatures are 64-character lowercase hexadecimal hashes supplied by the
input-owning caller. They must describe witnessed input generations; the
manifest is not a new source authority, cursor or lock. Empty slices can retain
their input signature with zero items or zero clusters. A deleted bucket can
remain covered with zero counts. Slice omission is an explicit roster change.

The version-1 JSON manifest lives in the same `clusters.db` as the existing
artifact and global coverage. It is limited to 256 slices, 256 bucket totals
and 64 KiB of serialized UTF-8. Duplicate identities, invalid counts,
inconsistent totals and oversized manifests refuse before artifact writes.
Construction canonicalizes ordering. Missing optional metadata is compatible
with global-only artifacts; invalid or unknown versions log that full repair
is required and cannot authorize partial publication.

## Publication boundary

An explicitly chosen complete pass may seed the manifest with
`replace_all(clusters, coverage=manifest.coverage, slice_manifest=manifest)`.
The method validates cluster counts per slice and commits rows, global
coverage and the optional manifest together. Omitting `slice_manifest` retains
the full-replacement contract and removes any prior slice manifest.

`replace_slices(clusters, changed=keys, previous=old, manifest=new)` requires a
nonempty, bounded selection and the complete previously observed manifest.
It refuses a changed coarse basis, changes to unselected slice records or
unselected bucket totals, duplicate cluster IDs, and rows outside the selected
slices. Empty selected replacements are valid.

Under one SQLite `BEGIN IMMEDIATE` transaction, publication compares the current
global and slice receipts with `previous`, deletes only selected cluster/member
rows, inserts their replacements, then publishes both new receipts. Stale or
revoked coverage raises an explicit full-repair error before artifact mutation.
Unselected rows retain their IDs, member ordering and creation timestamps.
Any transaction failure rolls back rows and both receipts together.

The caller must hold the existing `SourceDomain.operation()` exclusion from
source reads through computation, final input-key validation and publication.
It must fully exhaust the [prepared bucket/model reader](bucket-item-reads.md)
before publishing. `replace_slices` participates in existing source-write
ownership, including the dirty latch on failure; artifact rollback does not
clear that source recovery requirement. It does not independently verify the
caller's source signatures.

Database triggers revoke slice coverage after inserts, updates or deletes in
clusters, members or global coverage. This includes raw artifact-row writes and
an older coverage-aware full writer's `replace_all([])`, even when no cluster
rows exist: deleting the global coverage row still revokes the slice receipt.
The partial transaction republishes the bounded receipt only after the final
artifact/global-coverage write.

## Qualification and limits

`tests/test_cluster_slice_store.py` covers the storage contract in legacy and
source-aware layouts, including old-writer invalidation, empty slices,
new/deleted buckets, restart, stale publication and transactional interruptions.
The explicit T3 file `tests/test_cluster_slice_store_scale.py` qualifies native
SQLite publication with 100, 10,000 and 100,000 unrelated clusters. Select it
through `tools/run-plugin-tests.py agent-index --allow-explicit-tiers
--test-file tests/test_cluster_slice_store_scale.py` under the invoking
harness's bounded supervisor.

That native test requires fewer than 4,096 SQLite VM steps for each of two
selected-slice updates through fresh connections, preserving unrelated rows.
It does not flush the operating-system cache or measure physical storage I/O.
Publication work includes the selected rows, indexed lookups and bounded
manifest validation/republication. Input discovery, source exclusion across a
complete maintenance round, clustering cost and consumer rollout remain
separate qualification; the clustering algorithm itself is quadratic within a
selected slice.
