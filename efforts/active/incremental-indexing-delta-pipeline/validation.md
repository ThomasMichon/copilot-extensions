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

The opt-in native policy and model-backfill controls use separate bounded
invocations:

```bash
python tools/run-plugin-tests.py agent-index --allow-explicit-tiers \
  --test-file tests/test_native_policy_matrix.py
python tools/run-plugin-tests.py agent-index --allow-explicit-tiers \
  --test-file tests/test_native_backfill_matrix.py
```

The policy control presents one item to the actual shared engine at 100,
10,000 and 100,000 unchanged chunks in both layouts. It adds a second model,
changes that model's revision, changes chunk output while preserving raw file
bytes, rechecks an unchanged output under a new policy revision, then replays
the same input. It requires equal stage counts, clean-reference results,
bounded native query rows/I/O and bounded growth in process read/write bytes.
An added model's unreturned corpus rows deliberately remain uncovered; this
is not a complete backfill claim.

The explicit source-aware backfill control proves the original model's
coverage first, adds another model, interrupts offset publication after proven
vector storage, reopens store/progress and verifies a covered replay followed
by the next finite slice. It reads three logical offsets per call amid the
three corpus sizes, checks source filtering, zero old-model embedding, only
missing new-model pairs, and unchanged canonical/source-checkpoint bytes.
The test covers the first two slices, not completion of a whole-corpus sweep.
These controls measure Linux process I/O and native query plans, not physical
disk traffic, whole-operation CPU/memory, Windows behavior or consumer rollout.

The opt-in native queue control composes the actual durable queue, worker and
shared engine at the same three corpus sizes, in both layouts with one or two
injected models:

```bash
python tools/run-plugin-tests.py agent-index --allow-explicit-tiers \
  --test-file tests/test_native_queue_matrix.py
```

It unions 100 deliveries, reopens the queue before and after claim, applies two
upserts and an explicit tombstone, and replays the same union. A later round
enqueues another 100 deliveries from inside embedding: the processing payload
must remain immutable and one durable trailing union must survive reopening,
execute once per unique identity and replay without embedding or projection
maintenance. The synthetic connector observes exact IDs, event IDs, ref pairs,
metadata revisions and the unchanged polling cursor. It is not an HTTP or
hosted-pagination measurement; existing connector controls own those boundaries.
Every changed projection is compared with a clean rebuild, including complete
vector ID sets, removed hits and unchanged sentinels. Stage counts must match
across sizes, native scans and read I/Os stay below 64, process read growth stays
below 2 MiB per acquired identity and write growth below 512 KiB. Setup and
reference rebuilds are outside the measurement. The process-byte allowance
includes metadata and queue traffic, not a physical-disk or whole-CPU claim.
Focused queue controls separately require a processing exact-source or
all-source full task to preserve the same 100-delivery trailing hint union.

The native cluster-enabled engine control uses a separately bounded invocation:

```bash
python tools/run-plugin-tests.py agent-index --allow-explicit-tiers \
  --test-file tests/test_native_cluster_matrix.py
```

Its participating two-model store contains two selected items and
100/10,000/100,000 unrelated items. A known identical-vector large component is
constructed directly outside measurement; this deliberately excludes the
global quadratic full-seed algorithm. Ordinary engine no-op, text edit,
metadata edit, replay and deletion run with the existing two-row slice budget.
The test forbids global representation reads and whole-artifact replacement,
requires only the changed bucket's complete one/two-item input, bounds SQLite
steps and native rows/read I/Os, and compares selected membership/scores against
a clean small full recomputation. Every unrelated cluster/member row and
canonical/vector sentinel remains unchanged. Stage counts match across sizes;
process read/write growth stays below 2 MiB/512 KiB. No-op/replay uses current
coverage with zero embedding, row writes or cluster/FTS passes.

The built-in GitHub polling/native-engine control is also opt-in:

```bash
python tools/run-plugin-tests.py agent-index --allow-explicit-tiers \
  --test-file tests/test_native_github_poll_matrix.py
```

It uses the actual connector over a synthetic HTTP transport, normal Markdown
chunking, the shared engine and native stores in both layouts with one or two
models. The same ten rounds run beside 100/10,000/100,000 unrelated items:
empty polling, overlap replay, equal-timestamp editing, an item response newer
than its listing, watermark catch-up, a 30-second skew inside the overlap,
interrupted comments, foreign-account refusal, resumed acquisition and a
truncated identity window. Exact HTTP sequences include paginated comments;
resumption reopens the pending journal and connector without relisting or
refetching the completed item. A newer response never advances beyond the
listing's pinned target, and failure preserves the committed checkpoint,
projection versions, logical receipt and current FTS coverage.

Stage counts and native query/row/read-I/O counts must match across corpus
sizes. Native rows and read I/Os stay below 32 per generated chunk; process read
growth stays below 1 MiB per generated chunk and write growth below 512 KiB,
using a one-chunk floor for empty/reused/refused rounds. The issue produces five
real chunks and the pull request two, so these are input-chunk budgets rather
than one-row-per-item assumptions. Reference rebuilds, explicit corpus/index
preparation and sentinel checks are outside measurement. Full path enumeration
is forbidden in measured rounds. Complete changed chunk/vector ID sets and
metadata match a clean small shared-engine rebuild, and unrelated sentinels
remain intact. Every model's total row count must equal the complete fixture
inventory, and every observed superseded chunk ID must be absent from vectors;
checking only current content IDs would miss orphaned vector rows.
Replay/catch-up/refusal has zero embedding, projection writes,
FTS/cluster passes and native queries. This is not live-provider, arbitrary
clock-skew, immutable full-snapshot, whole-memory/CPU or rollout qualification.

The same native control separately compares saved HTTP request-purpose
counters with the transport's exact attempts, completed responses and decoded
payload bytes. Metadata probes, identity-page requests, item retrieval and
comment pages must sum to the unchanged aggregate counters at every corpus
size, including interrupted requests and journal resumption. No request in
this built-in connector control may remain unclassified. These request-purpose
measurements do not infer exclusive body contents from listing payloads or
qualify transports outside the shared HTTP session.

Qualify both a standalone single-model engine and a generic injected two-model
consumer. A downstream adopts only a reviewed/published compatible engine
revision, records its consumed provenance, and repeats the same count assertions
against its adapters before a separately authorized live rollout.

Maintain source/account/trust-domain separation and current read-after-write
visibility throughout; existing #4222, #4220, #4194, #8733 and #5302 remain
correctness owners, not exceptions to optimize around.
