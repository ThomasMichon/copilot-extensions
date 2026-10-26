# Indexing round work counts

`run_reindex()` adds a JSON-safe `work_counts` result with `version: 1`.
Existing result keys and their meanings remain unchanged. In particular,
`chunks_total` counts logical processing, not physical row writes.
The ordinary worker persists the nested result in its existing `result_stats`.
Consumers can ignore the additive field; no CLI/provider syntax changes.

Measurements are local integer increments at the existing synchronous owners,
with one snapshot at return. There is no exporter, daemon, new journal, coverage
authority or extra store query. Independent/nested runs have separate scopes;
cancellation and exceptions reset the scope.

## Measured work

| Fields | Meaning |
|--------|---------|
| `manifests_acquired`, `identities_scanned`, `upserts_acquired`, `tombstones_acquired` | Successful acquisition outputs. Scanned identities use the connector crawl's existing statistic, not HTTP page size or a corpus cardinality. |
| `manifests_applied` | Successfully applied manifests, including empty ones and replayed pending work. This is not an acknowledged cursor count. |
| `files_chunked`, `files_reused`, `chunks_generated` | Successfully chunked files, compatible file-checkpoint skips, and coalesced chunker outputs. |
| `checkpoint_lookup_keys`, `checkpoint_rows_found`, `checkpoint_rows_written` | Selected file lookup inputs/results and successfully submitted checkpoint rows. Not all SQLite reads/writes or invalidations. |
| `content_rows_upserted` | Actual primary rows returned by the content-upsert API, including updates; identical skips return zero. |
| `file_chunk_ids_deleted`, `stale_chunk_ids_deleted` | Canonical IDs removed by explicit file deletion and per-file stale reconciliation, respectively. Not vector rows per model or source-GC totals. |
| `models[model_id].candidate_chunks`, `.reused_chunks` | Routed candidates and pairs omitted by verified embedding coverage. File-level skips never reach this stage. |
| `models[model_id].embed_calls`, `.chunks_submitted`, `.chunks_embedded` | Calls attempted, chunks submitted and vector rows returned by the embedding client. Returned rows alone do not prove valid/stored embeddings. |
| `models[model_id].vector_rows_upserted` | Actual primary vector rows returned by that model's upsert API. |
| `fts_ensure_attempts`, `fts_ensure_completed` | Engine finalization calls to ensure FTS availability/currentness. Not full rebuild counts; the API's boolean reports availability. |
| `cluster_pass_attempts`, `cluster_pass_completed`, `cluster_pass_failures`, `cluster_cache_hits` | Actual clustering calls, successful/failed calls and coverage-cache reuse. A legacy swallowed pass failure still records its attempt/failure. |

All row counts describe the engine batch writer's primary API operations,
**not storage-engine I/O,
fragments, transaction counts or secondary provenance/metadata updates**.
No-op primary upserts can still perform bounded comparison reads.

The optional `http` object covers only the shared `GoodCitizenSession` boundary:
`attempts` counts client request calls, `retries` counts additional calls after
retryable failures, `responses` counts returned responses, `response_bytes`
counts decoded payload bytes (including unsuccessful statuses),
`not_modified` counts 304s, and `json_results` counts successfully parsed
terminal JSON results. Timeouts count as attempts without responses. These are
not wire-level redirect/transport retries, provider-semantic pages, identities,
item/comment body counts, or compressed network bytes.

`http: null` means this transport was not observed in the round, **not zero
provider traffic**. External providers and other transports remain unmeasured.
Unlisted work is likewise unavailable, not implicitly zero. Do not use this
partial instrumentation to claim the entire scale acceptance matrix passed.

## Recovery and failures

Counts describe the current invocation, not cumulative task history. Replayed
pending acquisition contributes application work without another acquisition
count. Fresh acquisition in that retry is counted separately. Successfully
completed operations before a failed legacy source remain visible alongside
`sources_failed`; they are not subtracted to simulate rollback.
Fatal exceptions/cancellation return no successful result snapshot and retain
the existing worker failure semantics.

`tests/test_work_counts.py` qualifies native single/two-model rounds in both
storage layouts, unchanged/replayed content, metadata reuse, changed text,
tombstones, interrupted embedding/recovery, HTTP retries and scope isolation.
`tests/test_hinted_crawl.py` checks real hosted identity acquisition and worker
serialization of the nested counts.

See [architecture](architecture.md) and the
[consumer migration recipe](consumer-migration.md).
