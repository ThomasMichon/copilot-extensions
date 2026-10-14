# Cold source-aware store

`source-aware-v1` is an explicit cold-only layout for a **new independently
owned directory**. It is not a legacy migration, database transfer, hot upgrade,
or assertion that another live writer has been stopped.

## Selection and bootstrap

Standalone host YAML can select:

```yaml
store:
  layout: source-aware-v1
```

This supplies `AGENT_INDEX_STORE_LAYOUT=source-aware-v1` to the core service and
its participating workers. Only `serve`/`start` bootstrap the selected store;
configuration inspection does not create state. Indexing's writer entry point
also bootstraps cold state explicitly. Programmatic use is
`initialize_source_store(IndexConfig(...))`.

Initialization refuses an unmarked nonempty/legacy directory without creating
a lock/receipt there. Repeated initialization of a complete selected domain
does not rewrite its tables or layout receipt. Unknown layout/protocol, missing
preparation or unselected access to a marked domain fails explicitly.
Existing native behavior stays unchanged when no layout is selected.

The default durable receipt is `<IndexConfig.lance_dir>/.source-layout.json`,
alongside the stable `.source-writer.lock`. It records a domain identity,
layout/protocol 1, clean/dirty state, and exact current Lance table versions.
It is layout/readiness evidence, not a task/objective ledger or authentication
grant. The domain UUID is not a source permission.

## Participating ownership and consistency

The domain uses a process-local reentrancy guard **plus actual nonblocking
kernel file exclusion** (`msvcrt.locking` on Windows, `flock` on POSIX).
Content/vector mutation paths and scoped reads share the same exclusion.
All selected-domain serving reads share it too: ordinary search, similarity,
documents, hydration, cluster reads and statistics. The guard spans candidate
retrieval through hydration, and nested/lazy read helpers retain ownership.
Concurrent participating processes refuse busy access; they do not use PID
or an idle-status snapshot as authority. Marker replacement never replaces
the lock file. A missing lock on an initialized domain fails closed.

**This does not fence older immutable binaries that do not implement the
protocol.** Deployment must allocate an isolated new directory and allow only
participating new binaries to access it. Do not mount an incumbent volume,
restore legacy data into it, remove its marker/lock, or claim coexistence with
old writers. Isolation is a lifecycle/deployment gate, not a caller boolean.

New vector rows carry `source` and `language` copied from canonical persisted
content, with IDs and vectors preserved. Caller grant aliases are never
projection input. Source identity is immutable for an existing chunk ID;
connector source changes create new IDs. Canonical content updates maintain
the projection, and canonical deletes remove corresponding vectors under the
same participating exclusion. Detached queued workers inherit the selected
mode; default/unselected writers retain the legacy path.

FTS subprocesses own a second stable kernel guard, `.source-fts.lock`, until
their process exits. Before spawning, the parent persists `fts_pending` while
holding main ownership, then releases only that guard to the child. The pending
receipt blocks recovery in the startup gap; the child-owned guard blocks it
after parent death. The parent cannot publish clean completion until the child
exits and ownership is reclaimed. Missing or unsettled handoff evidence never
becomes success. Children remain contained/headless; no daemon escape is used.

FTS completion is separate from availability of an older index. Internally a
rebuild reports success, an empty-table no-op, or failure, including failed
incremental/escalated attempts. Selected mode raises on failure even if the
previous index remains queryable, retaining dirty/effect uncertainty. Empty
first-touch preparation returns false availability without spawning a builder
or leaving the domain dirty; subsequent queued ingestion can write normally.
Unselected legacy callers retain the existing public availability-bool
semantics, including true availability of an older index after rebuild failure.

Before mutation the receipt becomes dirty; success records the resulting
versions and clean state. Interruption or a write exception leaves scoped
reads closed. Out-of-band table version changes close them too.
`validate_source_store(store)` is explicit recovery for **this new domain**:
it performs bounded canonical/projection checks, refuses missing/orphaned/
conflicting provenance, and only then records a clean current version set
**when no destructive effect was attempted**. Before destructive content/vector
or canonical-delete steps the receipt records `uncertain_effect` and the
operation/model-table identity. Validation conservatively retains uncertainty
after such an interrupted operation: checking surviving rows cannot prove that
required vectors or canonical members were not lost. Legitimate content-only
rows are not retroactively required to have vectors. There is deliberately no
automatic membership-loss repair or API that clears this latch by inspection
alone; the owner must repair against independently retained intent or rebuild
separately isolated fresh state. FTS parent-death uncertainty is retained too.
It does not backfill labels, guess sources, erase data, or adopt a legacy store.
If validation cannot establish consistency, keep serving closed and use the
owning writer/task recovery or re-create separately isolated fresh state.

Rollback to an unselected current binary is refused. An old binary that ignores
the format is an unsupported downgrade and must not be deployed onto the
directory. No automated data rollback/old-writer restoration is provided.

## Scoped reads

`SearchEngine.search_authorized(query, families=..., mode="hybrid", ...)` and
`store.source_search.search_source_store()` accept **already authorized
canonical families**; they do not authenticate a caller. The later fleet
adapter must supply verified corpus/source grants.

Source and optional language/repository predicates are pushed into each native
vector/FTS query before candidates/top-k. Family membership is exact identity
or a colon descendant, never an arbitrary prefix. Vector rows already have
the necessary provenance, so full grants require one native query per model,
one FTS query, and bounded hydration—not an all-corpus ID list or hundreds of
partition queries. Hydration rechecks the same source/selector predicate.
Empty families never enter unrestricted retrieval. Native candidate windows,
fusion and content deduplication are reused; final equal-score results use
chunk-ID ordering within the native candidate window.
Fusion uses the native configured vector/FTS weights, including
`AGENT_INDEX_VECTOR_WEIGHT` and `AGENT_INDEX_FTS_WEIGHT`, rather than independent
backend defaults.

Ordinary scoped reads do not create tables, migrate columns or build indexes.
Unprepared/mismatched projection fails closed. Native API/engine placement and
source-ingestion credential custody are not changed by this layout.

## Local evidence and limits

Tests use real queued Git full/incremental ingestion, changed-document stale
cleanup, deletion, unchanged rerun and reopen/persistence. Only inference is a
fixture. A separate bulk-created benchmark is **not credited as ingestion**:
140,000 rows across 18 families plus forbidden lookalikes exercise native
prefiltering, empty/full/explicit grants, language/repository selectors,
bounded query counts/memory, and individual post-setup timings without
tracemalloc. Kernel exclusion is exercised against another process.

Measured fixture-only timing and test results belong in the implementation
report; they are not proof of real model/credentials, Komodo placement,
network/tunnel costs, production parity, or whole-fleet readiness. Existing-store
migration, old-binary exclusion/cutover and rollback remain separate work.

One local Windows installed-package run, after setup and without tracemalloc,
measured the following individual requests (seconds):

| Mode | Empty | Explicit family | Full 18 families | Language | Repository |
|---|---:|---:|---:|---:|---:|
| Vector | 0.052932 | 0.143852 | 0.321178 | 0.563307 | 0.769539 |
| FTS | 0.081868 | 0.304133 | 0.153057 | 0.175598 | 0.409870 |
| Hybrid | 0.029389 | 0.114190 | 0.431851 | 0.356395 | 0.686342 |

Full vector/FTS requests issued two native queries including hydration; hybrid
issued three. Empty scopes issued none. Nonempty vector/hybrid requests invoked
the warm inference fixture once; FTS invoked it zero times. These are
two-dimensional fixture-model results with local store headroom inside five
seconds, not a real-model or Gateway transport acceptance result.
