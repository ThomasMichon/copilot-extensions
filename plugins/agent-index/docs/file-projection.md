# Raw file-point observations

`store.file_projection.capture_file_projection` is an opt-in Python helper for
capturing immutable, complete raw content rows and registered-model vector
points under the existing source-domain operation exclusion:

```python
from agent_index.store.file_projection import capture_file_projection

observed = capture_file_projection(
    config, store, source, file_path, max_rows=per_point_row_budget,
    retained_chunk_ids=previous_chunk_ids,
)
```

`config` and `store` must select the same participating domain, content table
and complete model mapping. Model IDs, native table schemas/dimensions, embedding
profile keys and content-type routing are bound to the returned observation.
It includes the domain identity, exact source/path, content table and sorted raw
JSON bytes for every selected row, including raw metadata and write timestamps.
Metadata is not decoded or normalized. Missing vectors remain visibly absent;
present vectors retain all raw fields, finite values and checked source/language
provenance. This is an observation, not proof of required model coverage.

All canonical `file_path`/`chunk_id` and model `chunk_id` indices must already be
[prepared](chunk-id-index.md), with zero physical unindexed rows and within the
existing segment budget. Every index is checked before any point query. The
helper neither prepares indices nor writes rows, receipts, pending work or
checkpoints. Backend failures propagate instead of becoming empty observations.

The required positive `max_rows` caps **all same-path canonical candidates before
source filtering**, including other sources. It separately caps the union of
selected canonical IDs and caller-retained IDs. Each capped lookup requests one
extra row to detect overflow or duplicates; model/ID queries use the existing
200-ID batching and reject duplicate/unexpected IDs. Index and fragment metadata,
historical index entries, row byte sizes and model count remain additional work:
this is not an aggregate byte/I/O/time budget or a source-isolated path index.

`require_file_projection_capacity(store, replacements, max_rows=...)` preflights
the projected final same-path occupancy for a finite map of exact source/path
points to replacement chunk counts (zero for deletions). It reuses the capped
path lookup under source-domain exclusion, subtracts the selected points'
current rows and retains every unselected neighbor before adding all planned
replacements. The explicit native application calls it before writer entry:
individually bounded components must not overflow a shared path's capture cap
after native mutation. This helper requires an already-prepared path index and
performs no mutation or automatic preparation.

Retained IDs expose known vector remnants when their canonical rows have been
deleted. An ID that now belongs to a different canonical point refuses capture.
Unknown vector orphans are not discovered by scanning a model table. Empty raw
content alone therefore does not certify deletion, and omitting earlier IDs is
not a way to prove their vectors are absent.

The helper changes no default reader or writer and enables no component receiver
or v2 executor. The opt-in [native component ledger](component-ledger.md) can
retain ordering/tombstone observations. Its explicit native application composes
pending-parent ownership and prepare/revalidate/publish sequencing; backup
reconciliation, whole-claim completion and component read isolation remain
separate unfinished obligations.

See [the architecture overview](architecture.md) and
[prepared point-read ownership](chunk-id-index.md).
