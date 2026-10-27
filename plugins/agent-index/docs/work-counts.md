# Indexing round work counts

`run_reindex()` adds a JSON-safe `work_counts` result with `version: 1`.
Existing result keys and their meanings remain unchanged. In particular,
`chunks_total` counts logical processing, not physical row writes.
The ordinary worker persists the nested result in its existing `result_stats`.
Consumers can ignore the additive field; no CLI/provider syntax changes.

Measurements are local increments at the existing synchronous owners,
with one snapshot at return. There is no exporter, daemon, new journal, coverage
authority or extra native row query. Mutation summaries read the existing small
receipt after successful clean publication. Independent/nested runs have separate scopes;
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
| `source_mutations[source]` | Ordered successful source-application summaries, after store work, cursor save and pending-journal completion. Retained replay and fresh acquisition are separate entries. See below for scope and unavailable values. |

All row counts describe the engine batch writer's primary API operations,
**not storage-engine I/O,
fragments, transaction counts or secondary provenance/metadata updates**.
No-op primary upserts can still perform selected-ID comparison reads; these
counts do not establish their physical I/O cost.

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

## Source mutation summaries

Each entry has `available`, `write_commits`, `logical_commits`, `tables`, `epoch`
and `generation`. In a participating source-aware store, `write_commits` counts
successful **outer** writer transactions in that application, including
identical no-ops and physical-only maintenance. Nested content/vector writes
share the outer commit. `logical_commits` counts those transactions whose witnessed table transitions
are treated as logical changes by the existing maintenance-exemption policy.
It is not a semantic comparison of every row. `tables` is their sorted union, not the manifest's
claimed inputs. Metadata-only changes report only tables actually changed;
canonical tombstones include affected model tables. Empty applications report
zero observed writes and logical changes.

`epoch`/`generation` describe the last observed writer's successfully persisted,
witness-qualified optional logical projection. They are null when no writer
was observed or the optional coverage was unavailable/dropped. Missing physical
witnesses make `logical_commits` and `tables` null, never measured zero.
Unselected legacy stores report `available: false` and all other fields null.
An empty list/missing source means no successful source publication was observed,
not proof that no partial store work happened.

Observation is execution-context-local and restricted to the same domain.
It does not itself hold exclusion or change generation policy. The shared
engine's [prepared application](prepared-application.md) finishes model/network
calls before entering one outer source-aware writer, so normal multi-batch
source application reports at most one logical commit. Other callers can still
perform several independent transactions within an observation. Interleaved writers in
other contexts do not enter its counts; its generation is not a source-exclusive
watermark. These results must never authorize cache reuse, source advancement
or recovery. Existing witnessed projection/FTS coverage remains authoritative.
Final all-source GC, FTS and clustering occur outside these source summaries.

## Recovery and failures

Counts describe the current invocation, not cumulative task history. Replayed
pending acquisition contributes application work without another acquisition
count. Fresh acquisition in that retry is counted separately. Successfully
completed operations before a failed legacy source remain visible alongside
`sources_failed`; they are not subtracted to simulate rollback.
Fatal exceptions/cancellation return no successful result snapshot and retain
the existing worker failure semantics.

An application that fails during data writes, source-state save or pending-journal
completion emits no successful mutation summary. Earlier completed source
applications remain in a returned partial round. A retry reports only the writes
it performs; compatible writes from an earlier failed attempt may be reused.
This is not exactly-once lifetime accounting across crashes or transactional
rollback of a whole source delta. Preparation counts actual engine calls only
in the attempt that makes them; replaying cached vectors does not invent new
embedding calls. Pair candidate/reuse counts still describe application-time
coverage selection, which preparation failure may never reach. File lookups
include both preparation's selected checkpoint snapshot and the pre-writer
freshness check. Whole-store physical I/O qualification remains separate work.

`tests/test_work_counts.py` qualifies native single/two-model rounds in both
storage layouts, unchanged/replayed content, metadata reuse, changed text,
tombstones, interrupted embedding/recovery, HTTP retries and scope isolation.
`tests/test_hinted_crawl.py` checks real hosted identity acquisition and worker
serialization of the nested counts.

## Prepared native round qualification

The opt-in Linux T3 `tests/test_native_delta_matrix.py` composes the actual
`run_reindex()` entry point, synthetic delta connector, injected one/two-model
clients, path-index SQLite and native stores in both layouts. It runs no-op,
one-item text edit, replay, metadata-only edit, rename, model-profile revision
gap and tombstone against 100, 10,000 and 100,000 unchanged items. Setup explicitly
prepares canonical `chunk_id`/`file_path` and model `chunk_id` B-trees; this is
**not qualification of an unprepared default store or automatic bootstrap**.
No real provider, engine service or consumer deployment is involved.

Each round compares stage counts across sizes. Native empty-query plans execute
the actual query objects, and Linux `/proc/self/io` records process read/write
characters, including cached I/O and waited-for children. Diagnostic plan replay
is included conservatively in that I/O, not subtracted. These counters are not
physical disk traffic or CPU bounds. The measured region includes final FTS
maintenance; setup, reference rebuilds and correctness reads are outside it.
Changed projections match clean engine reference builds; counts and unchanged
sentinels retain the base corpus. Native query totals must remain below 64
scanned rows and 64 read I/O operations per finite round. Process reads may grow
by at most 2 MiB and writes by at most 512 KiB above the 100-item baseline to
allow native manifest/index metadata, rather than a corpus-proportional scan.

The initial unprepared-store experiment failed these physical-work budgets even
though stage counts were equal and projection correctness passed. At 100,000
unchanged items, a one-item edit read about 40/57 MB in legacy single/two-model
stores and 84/116 MB in source-aware stores. This remains an open acquisition/
storage preparation item under the active incremental-indexing effort (#9023).
An explicit prepared fixture must not silently waive that remaining gate.
Prepared source-aware FTS refresh also exposed unrelated scalar-index rewrites
from combined `optimize()`; the incremental child now appends only the FTS delta.
The native helper regression separately forbids compaction, version cleanup and
full FTS rebuilding, retains scalar segment/fragment identities and old versions,
and verifies fresh replacement text and absence of deleted old hits. Actual FTS
unindexed-row metadata must fall from one to zero; searchability alone could
come from fallback scanning. The helper also refuses unknown or incomplete
post-update coverage before the existing owner can publish it as current.

The broader acceptance matrix remains open: unprepared-store adoption, bounded
upkeep/consolidation over many rounds, hosted pagination/body accounting,
cluster-enabled composition, source/account isolation at scale, interrupted
native composition, consumer provenance and authorized rollout are separate
qualification work. The native FTS-only path does not prepare missing scalar
indices or make an ever-growing unindexed tail bounded.

See [architecture](architecture.md) and the
[consumer migration recipe](consumer-migration.md).
