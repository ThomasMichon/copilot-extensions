# Reusing the shared Python indexing engine

This recipe replaces consumer-owned crawl/chunk/embed/reconcile orchestration
with `agent_index.indexing.engine.run_reindex`. The consumer keeps source
adapters, configuration, model and engine policy, presentation and deployment.
The shared engine keeps acquisition recovery, chunk/model reuse, stores,
reconciliation, checkpoints and derived maintenance.

## Provider-first compatibility

Publish the shared API before updating consumers. Select a released or otherwise
explicitly supported engine source, record its consumed provenance, and
mechanically refresh the consumer's existing package/vendor path. Do not edit a
deployed copy or copy an indexing loop into the consumer.

Existing `run_reindex` and `run_worker` invocations retain their defaults.
The new in-process keywords are optional; they do not introduce CLI flags or
provider wire capabilities. A consumer requiring these keywords must not fall
back to its fork when an older engine rejects them. Keep the previous compatible
consumer/engine pair available for an intentional rollback. Pending acquisition
must finish under a journal-capable generation before rollback to older code.

## Configuration and source ownership

Use `IndexConfig` or a subclass and shared `ModelProfile` values. Subclasses may
override defaults without dropping current engine fields such as `revision`.
A model whose weights or preprocessing change without a new name must advance
its revision.

Pass the complete consumer source selection as `list[SourceSpec]`. Register
custom factories with the existing `agent_index.sources.register_connector`
API; exact and longest-prefix resolution also inform generic source GC.
An explicit source request resolves against this injected selection, not an
unrelated ambient corpus. Registration is process-local; perform it in the
actual worker as well as the retrieval host when they are separate processes.

Each connector still owns its canonical `source_name`, nonsecret
`recovery_identity`, complete delta/snapshot contract and stable account/target
binding. Injected configuration cannot bypass recovery scope checks. Complete
pending acquisition before changing source identities; an unbound old cursor
requests explicit repair, never a silently broad incremental crawl.

Retirement of a legacy consumer naming scheme is a separate migration under
the existing source-ownership guards, not an unrestricted pre-index callback.
Custom sources must remain declared/registered during the compatibility window.

### Source retirement and background maintenance

The default configured crawl selection is authoritative for source retirement.
Consumers also accepting pushed content must not treat a partial crawl selection
as the complete inventory. Pass `SourceRetirementPolicy` from `indexing.gc`
with `configured_snapshot=False` to retain sources outside that selection.
`stale_generations=False` disables full-run naming-generation collection, while
the existing `AGENT_INDEX_REINDEX_GC=0` safety valve disables both collections.
Exact names in `AGENT_INDEX_GC_KEEP_SOURCES` remain protected before either
classifier runs; a caller's naming predicate cannot override operator retention.

A consumer with a legacy naming scheme can supply the policy's pure
`is_live_source(source)` predicate and `live_crawl_keys` set. The shared GC loop
still owns exact-source deletion, pending-acquisition exclusion and selected
store guards. The predicate classifies stored names, not crawl aliases; retained
crawl keys preserve cursors even when a stale stored generation shares a name.
This is not a pre-index mutation callback or a replacement pipeline.
Failed full runs do not advance `last_full_reindex`, collect stale generations
or compact as though reconciliation had completed.

Consumers with their own coalescing cluster maintainer should pass a config
copy with `cluster_enabled=False` to indexing, leaving the retrieval/background
configuration unchanged. Preserve pending-maintenance accounting in the worker;
disabling inline clustering is not evidence that the background artifact is
current. Source-retirement policy does not disable required selected-store FTS.

## Client and lifecycle ownership

`EmbeddingRuntime` contains `create_client(profile)`,
`ensure_engine(profile, client)`, `stop_engine(profile)` and `manage_engines`.
Return a fresh shared `EngineClient` or compatible subclass for each profile.
The run owns and closes every returned client, including when a later factory
or registration fails. Do not return a borrowed long-lived retrieval client.

Readiness remains lazy: the engine invokes it only for a nonempty embedding
plan. `ensure_engine` returns whether this run started the engine; only those
engines are stopped. Set `manage_engines=False` for externally managed
inference. Client close still occurs, and lifecycle cleanup still runs if a
client close raises. Search availability remains independent of this lifecycle.

Bind those values in a small adapter, not another pipeline:

```python
from agent_index.indexing.engine import run_reindex
from agent_index.indexing.worker import run_worker


def index_consumer(*, full=False, source=None, progress_cb=None, resume_since=None):
    return run_reindex(
        full=full,
        source=source,
        progress_cb=progress_cb,
        resume_since=resume_since,
        config=consumer_config,
        source_specs=consumer_sources,
        embedding_runtime=consumer_embedding_runtime,
    )


exit_code = run_worker(task_id, config=consumer_config, indexer=index_consumer)
```

The configuration, source list and runtime above are values owned by the
consumer. `run_worker` uses the supplied config for task storage and priority.
Its default engine receives that config automatically; a custom callable like
this adapter must bind the same config itself.

The worker preserves progress/cancellation, retry windows, result statistics
and `sources_failed` → `PARTIAL`. A consumer may wrap the result to persist its
own maintenance accounting, but must preserve failures rather than report
success after partial writes.

Preserve the additive nested [`work_counts`](work-counts.md) result when
adapting statistics. It distinguishes logical processing from measured primary
row writes; unavailable provider measurements must not be rendered as zeros.

Consumers planning strict current-ID validation can use the opt-in
[prepared chunk-ID read path](chunk-id-index.md) within an already participating
source store. Preparation has explicit row/segment budgets; it is not automatic
legacy adoption or a substitute for existing embedding-coverage ownership.

Hint capability belongs to the exact selected callable. A wrapper or partial
does not inherit it just because the underlying function supports it. Advertise
only versions the wrapper actually accepts, validates and forwards unchanged;
the worker otherwise parks hinted tasks before invoking an unsupported adapter.

## Verify the actual consumer path

### Repo filtering in a consumer retrieval facade

The default SQL repo filter matches generic source identities such as
`github:owner/repo:issues`. A consumer retaining another canonical naming scheme
may subclass `ContentStore` and override its pure `repo_filter_sql(repo)` method.
Escape user-supplied values with the existing SQL literal/pattern helpers and
keep the consumer's vector post-filter semantics consistent.

The shared `fts_search` still owns currentness guards, query sanitization,
source/language filters and execution before the candidate limit. Do not copy
the FTS loop, replace a process-global helper or fetch an unbounded candidate
set to filter in Python. `tests/test_consumer_repo_filter.py` exercises the real
native indexed query with wildcard, quote and backslash controls.

`tests/test_consumer_runtime.py` qualifies single- and dual-model injected
consumers through the actual shared engine, native metadata-only reuse,
registered-source full GC, client/lifecycle failure cleanup and injected worker
capability/terminal behavior. Existing default-call and recovery tests remain
part of the bounded integration selection.

Consumers must additionally verify their own registered adapters, worker entry
point, exact consumed source, retrieval correctness and operation counts. A
shared fixture, merged PR or published release is not proof that a consumer's
running system uses this path. Deployment and live indexing retain the
consumer's separate authorization and safety gates.

See [architecture](architecture.md) for the engine's current guarantees.
