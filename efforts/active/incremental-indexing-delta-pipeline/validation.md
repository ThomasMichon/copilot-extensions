# Delta-bounded acceptance matrix

Proposed validation for the [canonical effort](README.md). This is a plan,
not a report of passing implementation tests.

## Measurement contract

For each round record separately:

- source metadata/list/page requests and returned identities;
- fetched content/comment bodies and bytes;
- path-index/checkpoint rows read and written;
- files chunked, candidate chunks and reused chunks;
- embedding requests/chunks per model signature;
- content/vector adds, updates and deletes;
- FTS/cluster dirty generations, scheduled passes and full rebuilds;
- elapsed time, but never use timing alone to declare incremental behavior.

Run the same finite delta against 100, 10,000 and 100,000 unchanged synthetic
items. An ordinary bounded round must have the same expensive-operation counts,
apart from declared lookup costs and bounded provider pagination. Do not assume
storage calls are constant-time without measuring their implementation.

## Cases

| Input | Required ordinary-round work | Safety control |
|-------|------------------------------|----------------|
| Empty event delta / unchanged source revision | Zero body fetches, chunking, embedding, content/vector writes and maintenance passes; no full `list_paths()`/history enumeration. | A bounded metadata probe is allowed; reconcile is a separately visible operation. |
| One changed file or work item | Fetch only that identity and required bounded dependencies; chunk it; embed only changed/missing chunk-model pairs. | Preserve unchanged chunks and correct metadata; compare against a clean rebuild. |
| Same content returned twice | Second application performs zero embedding requests and zero identical content/vector rewrites. | Semantic metadata/config changes must still invalidate the appropriate projection. |
| Metadata-only edit | Update facets/status/source metadata; reuse text vectors. | FTS and cluster partition fields receive the exact required invalidation. |
| One explicit tombstone | Target only that source/item and its stored chunk IDs; batch deletes across relevant models. | Other paths, source domains and accounts remain intact. |
| Rename / move | Apply old identity tombstone and new identity/content mapping as required. | Preserve source/path-sensitive chunk identity and avoid orphaned old hits. |
| Duplicate burst of 100 deliveries | One durable union; each unique identity fetched at most once per selected round. | Crash before/after claim cannot lose a distinct change. |
| Change arrives during active work | Preserve a trailing delta or include it in a fenced round before acknowledging its watermark. | A processing full task never swallows a later change. |
| Hosted timestamp overlap | Deduplicate replayed identities/revisions; bound overlap and page work. | Equal timestamps, clock skew and concurrent edits do not create gaps. |
| Crash after store, before checkpoint | Retry safely; reuse only proven committed compatible work. | Never advance source cursor beyond failed/tentative writes. |
| Crash during source pagination | Resume from durable page/event coverage where supported. | Detect expired/truncated cursors; repair explicitly rather than infer deletions. |
| Add/change one embedding model | Backfill only invalid/missing pairs for that model, resumably. | Existing models and canonical content remain usable; partial coverage is visible. |
| Change chunker/schema | Rechunk only invalidated policy scope; invalidate affected vector identities. | Do not reuse stale chunks solely because raw file bytes match. |
| Multiple tiny committed deltas | Coalesce only actual dirty projection generations. | Incremental FTS stays available; a trailing generation survives an active pass. |
| Cluster-affecting change | Dirty only affected model/bucket slices where the algorithm permits. | Compare membership against authoritative recomputation before trusting a local optimization. |
| Legacy provider | Explicit compatibility/reconcile path with bounded work and diagnostics. | Unknown delta fields/capabilities never silently disappear. |
| Incomplete source snapshot | No authoritative deletion reconciliation from omitted items. | Continue/pause/retry the snapshot, retaining prior good coverage. |
| New/expired checkpoint | An observable, budgeted bootstrap/repair operation, not a disguised ordinary round. | Preserve fairness, cancellation, rate limits and last usable search state. |

## Validation layering

Small unit fixtures assert work counts, queue transitions and protocol
compatibility in required CI. Scale, native lifecycle, packaged-source and
consumer-composition checks use existing bounded opt-in lanes. Do not create
an exhaustive new required matrix.

Qualify both a standalone single-model engine and a generic injected two-model
consumer. A downstream adopts only a reviewed/published compatible engine
revision, records its consumed provenance, and repeats the same count assertions
against its adapters before a separately authorized live rollout.

Maintain source/account/trust-domain separation and current read-after-write
visibility throughout; existing #4222, #4220, #4194, #8733 and #5302 remain
correctness owners, not exceptions to optimize around.
