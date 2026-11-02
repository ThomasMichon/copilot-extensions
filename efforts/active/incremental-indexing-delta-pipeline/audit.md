# Incremental work audit

Planning evidence for the [canonical effort](README.md), inspected at
`a6d1d31e549f899026ad159d4ec31e6ed2d820bd`. These are static source findings,
not measured production latency or claims that every operation below scans
physical table data.

## Confirmed amplification and existing good paths

| Area | Evidence | Consequence and proposed owner |
|------|----------|--------------------------------|
| Deletion discovery | `indexing/engine.py:713-776` reads stored source paths, calls `list_paths()`, then diffs the complete sets before changed-content discovery. | Even an empty delta can enumerate unchanged history. Shared connector/manifest completeness and tombstone contracts belong in agent-index. |
| Normal-round reuse | `indexing/engine.py:806-858` computes content fingerprints, but skip eligibility is tied to `resume_since`. | Retry reuse exists; ordinary repeated upserts need equivalent revision-aware selection without abusing retry timestamps. |
| Queue payload | `indexing/task_store.py:27-36,87-120` and `server.py:42-57` represent source/full work, not changed identities or cursor coverage. | Coalescing reduces task rows without reducing the source acquisition scope. Extend the generic contract. |
| Active work | `indexing/task_store.py:242-325` preserves a later incremental when a full task is already processing. | This is correct: replacing it with unconditional dedup would lose changes. New delta unions need durable trailing coverage. |
| Hosted GitHub content | `sources/github.py:87-96,157-181,274-287` replays a timestamp overlap and fetches returned item comments. | Repeated IDs can refetch body/comment history. Preserve the safety overlap while coalescing identities and persisting cursor coverage. |
| Hosted work tracking | `sources/azure_devops.py:110-145` combines timestamp overlap with full work-item/PR path enumeration. | Provider-specific cursors and tombstones should avoid routine historical listings; the shared engine defines completeness. |
| Local Git | `sources/git_repo.py:417-444` has changed-path acquisition but full tracked-path and commit-SHA enumeration in `list_paths()`. | Reuse existing commit-range logic for bounded deletion deltas; reserve full enumeration for repair. |
| Retry acquisition | `indexing/worker.py:157-171` resumes using a task time window, while the task lacks durable source-page/event coverage. | Stored embeddings can be reused, but source acquisition can replay. Preserve worker adoption and add acquisition checkpoints. |
| Identical writes | `store/content_store.py:181-224`, `store/vector_table.py:88-131`, `store/multi_model_store.py:199-207` delete/add supplied rows. | Prevent unchanged writes before store mutation; compare metadata separately from embedding validity. |
| Deletion fan-out | `store/multi_model_store.py:268-281` obtains chunk IDs and deletes content plus registered model vectors per file. | Batch known affected identities rather than paying repeated per-file/model transactions. Measure transactions; do not assume every call is a table scan. |
| Stale cleanup | `store/content_store.py:347-425`, `store/vector_table.py:150-171` contain count/delete/count or stale-ID lookup paths. | Keep immediate cleanup scoped to changed files/chunks. Bare `count_rows()` may use metadata: its call count is known, its physical complexity is not established here. |
| FTS | `store/content_fts_worker.py:63-145` uses incremental `optimize()` for an available dirty index; `store/content_store.py:851-873` already coalesces a dirty bit and suppresses unchanged/retry-backoff work. | Preserve these working mechanisms. Delta-aware counters/thresholds are proposals, not evidence that current coalescing is absent. |
| Clusters | `store/clustering.py:81-154` already works on bucket/model slices and constructs each selected slice's similarity matrix. | Global/slice-wide computation is not automatically a bug. Investigate changed-slice invalidation and explicit repair budgets without inventing a missing slicing mechanism. |

Paths in this table are relative to
[`plugins/agent-index/src/agent_index`](../../../plugins/agent-index/src/agent_index).
The cited source snapshot fixes the meaning of line references.

## Consumer integration boundary

Consumers can currently adapt the generic runner while still bypassing the
shared engine's fingerprint/checkpoint behavior. A shared-store dependency alone
does not prove a consumer benefits from indexing optimizations.

The proposed engine seam must admit injected connectors, multi-model profiles,
embedding/lifecycle clients and progress hooks. Consumers should not need to copy
the crawl/embed/reconcile/commit loop. Forge event decoding and schema
normalization remain provider-owned.

The shared engine is already multi-model capable: `indexing/engine.py:301-320`
registers configured profiles, and `_embed_and_store_batch` routes across the
model clients. Integration should reuse that path, not add a parallel
multi-model subsystem.

## Correctness and interpretation limits

- Whole-set `len()` statistics are not an additional physical storage scan
  independent of the path enumeration that produced those sets.
- Per-source isolation is intentional. Cross-source batching is optional;
  it must not weaken checkpoint or source-lock guarantees.
- Store-before-checkpoint ordering can repeat safe work after a crash.
  Avoiding all duplicate work is not permission to acknowledge uncommitted data.
- Model registration is not proof of complete vector coverage. Reuse must
  include an embedding-profile signature and observable missing coverage.
- A metadata-only change can affect filtering, FTS or cluster partitioning even
  when it does not require another text embedding.
- Periodic reconciliation, GC and compaction remain necessary. The proposal
  separates them from ordinary delta rounds instead of deleting safety work.

## Reproduction and acceptance ownership

Current tests worth retaining include `tests/test_resume_checkpoint.py`,
`tests/test_task_store_full_incremental_sequencing.py` and
`tests/test_content_store_fts.py`. They establish existing safety mechanisms,
not end-to-end delta-bounded acceptance.

Independent bounded baseline execution confirmed
`test_resume_checkpoint.py::test_no_resume_reembeds_everything` passes (1 test),
and the queued-full sequencing plus incremental-FTS files pass (22 tests).
The first test positively demonstrates the current normal-round re-embedding
behavior; a future fix must deliberately replace that expectation with
no-op/reuse semantics while retaining the other safety guarantees.

The [validation matrix](validation.md) defines the missing count-based gates.
Before implementation, add fixtures that independently count provider requests,
historical enumeration, fetched documents, chunking, embeddings, writes,
checkpoint work and maintenance generations.
