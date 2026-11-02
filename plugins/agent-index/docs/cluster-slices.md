# Transactional cluster slices

`ClusterStore` has an additive Python storage seam for partial artifact
publication, connected to an opt-in bounded maintenance planner.
The default remains the global clustering pass.
The [architecture overview](architecture.md#covered-derived-maintenance)
describes that default and its source-owned coverage.

## Opt-in maintenance

Set `IndexConfig.cluster_slice_row_budget` to a nonnegative integer to select
bounded maintenance through the existing `refresh_clusters` entry point.
It defaults to `None`, with no new environment variable or CLI setting.
The budget limits canonical chunk rows per changed bucket, not the number
of resulting items or the cost of a full repair.

An already initialized participating source domain, physically witnessed logical
bucket generations and a complete artifact manifest are required. Seed or repair
that artifact with an explicitly requested `refresh_clusters(store, config,
force=True)`. That request performs the existing complete pass, without the
ordinary bucket-row budget, then publishes global and slice coverage together.
It does not initialize a legacy domain or repair invalid source receipts.
It refuses an unrepresentable manifest rather than publishing partial coverage.
The seed includes zero-count slices for witnessed sparse buckets without eligible
centroids, including unembedded-only and deleted buckets. Later unrelated
updates do not rediscover those unchanged buckets as new input.

Before an ordinary update, explicitly prepare the canonical source B-tree and
the selected vector tables' chunk-ID indices using the
[budgeted preparation API](bucket-item-reads.md). New physical tails also need
explicit preparation. The planner never prepares, compacts or scans around a
missing index.

The planner holds `SourceDomain.operation()` across input witnesses, item reads,
clustering, final witness comparison and transactional artifact publication.
Its coarse basis binds local root/domain, logical epoch, configured table/model
roster, coarse baselines and clustering policy. Slice signatures bind that basis
plus the canonical bucket and selected model's vector bucket generations.
Candidate buckets are the union of the prior bounded artifact roster and current
sparse overrides. Content changes select every model in that bucket; vector-only
changes select only their model. Empty/new/deleted buckets remain explicit.
Unchanged coverage returns cached counts without item reads or artifact writes.

Partial counts must match the full pass's union of items with **any** model
centroid. The reader can include canonical items without selected centroids while
still querying only selected models. An all-model read gives the exact union.
For a selected subset, the union is provable when selected models together or an
unchanged model cover every canonical item, or all unchanged models have zero
eligible items. Other mixed partial overlaps raise `ClusterSliceRepairRequired`;
the planner neither guesses by summing model counts nor queries unselected
vectors to hide the ambiguity. Explicit full repair remains available.

Missing artifact coverage, policy/coarse/roster drift, unavailable logical bucket
proof, ambiguous counts and roster overflow refuse partial maintenance.
Scalar preparation/budget errors and incomplete reads propagate. These opt-in
failures do not fall through to the legacy best-effort full pass. No source
cursor or checkpoint is advanced by the planner. Partial results retain the
existing global `items`, `slices`, `clusters`, `elapsed_ms` fields and add
`slices_refreshed` and `buckets_refreshed`.

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
manifest validation/republication.

`tests/test_cluster_slice_pass.py` qualifies native composition, explicit seed
and repair, restart/no-op, new/deleted buckets, model/content isolation, mixed
union refusal, source exclusion through computation/publication and interrupted
reads/transactions. `tests/test_cluster_slice_pass_scale.py` is a separate
explicit T3 lane with 100, 10,000 and 100,000 unrelated canonical/vector items.
Each of two updates through fresh handles reads only two selected canonical and
vector rows, avoids unselected model vectors, and requires fewer than 4,096
SQLite VM steps before preserving every unrelated artifact row. Executed
canonical/vector plans require scalar indices, two scanned rows and fewer than
64 reported I/O operations per query. The fixture constructs its known large
unrelated component directly: it does not qualify full-seed complexity, physical
storage I/O or a cold operating-system cache. Initial/full repair and consumer
rollout remain separate; clustering is still quadratic within a selected slice.
