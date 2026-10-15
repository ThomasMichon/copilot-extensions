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

HTTP `/status`, including `?sources=true`, holds that same read guard across
table inventory, counts and the optional source histogram. A refused domain
returns explicit unknown inventory (`available: null`, `chunks: null`, empty
tables/sources and a `SourceLayoutError` diagnostic), never measured-empty
counts or an available index. Status does not initialize tables, repair receipts
or build indexes. Legacy unselected status measurement and lazy optional-store
imports are unchanged.

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
Selected canonical-ID lookups propagate query failures instead of treating them
as absent IDs. A failed immutability preflight leaves the operation dirty before
destructive intent, with content/vector provenance unchanged; only explicit
no-effect validation can reopen it. Genuine absent IDs still permit insertion.
Unselected ID reads retain their existing availability fallback.

FTS subprocesses own a second stable kernel guard, `.source-fts.lock`, until
their process exits. Before spawning, the parent persists `fts_pending` while
holding main ownership, then releases only that guard to the child. The pending
receipt blocks recovery in the startup gap; the child-owned guard blocks it
after parent death. The parent cannot publish clean completion until the child
exits and ownership is reclaimed. Missing or unsettled handoff evidence never
becomes success. Children remain contained/headless; no daemon escape is used.

On Windows, receipt readers use non-inheritable `GENERIC_READ` handles with
`FILE_SHARE_READ | FILE_SHARE_DELETE`, and replacement of an existing receipt
uses `ReplaceFileW`. A held reader retains its complete old JSON snapshot while
the owning writer publishes a new one. Delete sharing alone is not sufficient
with `os.replace`/`MoveFileExW`, which can return WinError 5 for an open target.
First publication into an absent path still uses `os.replace`; other platforms
retain their normal atomic-replace path. Neither path unlinks the destination
first, changes permissions, or falls back after a failed publication.

There is no receipt permission-error retry. An incompatible reader or permanent
denial fails immediately; selected parent-side FTS I/O errors propagate rather
than triggering full-build escalation or availability-shaped success. Existing
dirty/effect/nonce evidence stays closed, and read failures retain their OS
exception cause. The kernel locks and child nonce remain the authority for
ownership, not receipt-handle sharing.

FTS completion is separate from availability of an older index. Internally a
rebuild reports success, an empty-table no-op, or failure, including failed
incremental/escalated attempts. Selected mode raises on failure even if the
previous index remains queryable, retaining dirty/effect uncertainty. Empty
first-touch preparation returns false availability without spawning a builder
or leaving the domain dirty; subsequent queued ingestion can write normally.
Unselected legacy callers retain the existing public availability-bool
semantics, including true availability of an older index after rebuild failure.
Selected child failures expose only a bounded exit-code/reason diagnostic, never
raw stderr or source bodies. The established retryable/commit-conflict classifier
is shared across the child and parent boundary: both first-build and incremental
conflicts retry the same build kind, and exhausted conflicts retain uncertainty
without incorrectly escalating to a full rebuild. Parent receipt-publication
`OSError`s still propagate unchanged rather than entering this retry path.

The receipt's `fts_coverage` mapping binds each prepared content table to its
exact covered Lance version. Canonical upserts/deletes invalidate coverage
before mutation. The owning writer publishes coverage only after actual FTS
success (or an empty-table no-op) and settled child ownership. Queued full and
incremental ingestion refresh FTS after all source garbage collection and
compaction, including omitted-source/all-source runs, and before publishing
task completion. Writer completion preserves this newly
recorded coverage rather than restoring an older receipt snapshot.

Selected FTS and hybrid serving, including ordinary native retrieval, require
current coverage plus a durable index for nonempty content. A durable but
stale index is not readiness: these reads raise without index/schema mutation
or silent vector-only hybrid fallback. Vector-only and document reads still
use the normal clean-domain fence. Out-of-band changes, failed builders and
parent-death uncertainty retain the existing fail-closed behavior.

Before mutation the receipt becomes dirty; success records the resulting
versions and clean state. Interruption or a write exception leaves scoped
reads closed. Out-of-band table version changes close them too.
`validate_source_store(store)` is explicit recovery for **this new domain**:
it first requires the current table versions to match the retained receipt,
then performs bounded canonical/projection checks and refuses missing/orphaned/
conflicting provenance. It can clear a no-effect interruption only **when no
destructive effect was attempted and no unexplained version drift occurred**.
Surviving canonical rows cannot prove that an external writer did not delete
required vectors; unexplained drift is refused without changing the receipt.
Before destructive content/vector
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

## Canonical ingestion families and garbage collection

Selected queued ingestion stages each constructed connector's actual
`source_name` in its crawl manifest. Only successful embedding and
reconciliation (or a successful no-change crawl) publishes that identity in
`IndexState.stored_families`, keyed by the configured crawl name. A failed source
retains its previous mapping, so an alias changing to a new repository cannot
cause configured GC to discard the previous dataset on a partial run.
An identity transition starts incremental discovery without the old family's
commit cursor. A configured alias such as `git:fixture` can therefore retain its actual
`git:repo` family and exact colon descendants such as `git:repo:commits`.
Neither aliases nor arbitrary string prefixes are guessed into canonical
identities: `git:repository` is not a descendant of `git:repo`.
Selected deletion planning uses `PathIndex.get_paths_by_family()`: exact
case-sensitive identity or colon descendants, with literal `_` and `%`.
It does not use SQLite's case-insensitive/wildcard `LIKE` prefix lookup.
The existing genuine-prefix API and unselected reconciliation remain unchanged.

All-source configured GC uses that connector-derived evidence, including
cached identities for still-configured sources that were not crawled this run.
Stored-row retention uses only resolved canonical families, not configured alias
spellings. Crawl cursors are a separate namespace: an active alias keeps its
cursor even if it happens to equal a retired stored family. Only after the
complete configured GC pass succeeds are removed aliases' cursors and mappings
pruned, including when another configured alias still owns their canonical
family. Re-adding a retired alias to an unchanged repository therefore starts
discovery without its removed cursor and restores content incrementally.
Unknown identities defer the pass without state pruning; scan/deletion/path-index
failure propagates and does not falsely retire alias state. Partial data deletion
is not rolled back or reported complete. Mappings for removed configuration
entries do not retain their old families.
If a configured source has no known canonical identity, destructive configured
GC is explicitly deferred (warning plus `source_gc_deferred` in the indexing
result), rather than risking a configured but unindexed family. Unselected
legacy GC behavior is unchanged. Full stale-generation GC and compaction still
precede the final owning FTS refresh.

Selected maintenance does not inherit legacy best-effort success semantics.
Content and vector compaction persist an uncertain effect before optimization;
content compaction also invalidates FTS coverage. An optimizer failure propagates
and leaves the domain dirty, even if part of its work already committed.
Selected reconciliation, GC and cluster replacement failures likewise propagate
instead of being logged as optional success. A final clean-domain read guard
must pass after post-index callbacks before the queued task can report complete.
No validation silently clears an attempted uncertain effect. Unselected legacy
clustering/optimization retain their optional behavior.

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
Repository prefilters use the same first colon-delimited identity as native
`repo_matches`, before vector/FTS candidate selection and hydration. This
includes ADO/azure-devops project families and byproducts, bare project names,
and exact owner/host-qualified identities. Repository names containing `%` or
`_` remain literal; an omitted repo leaves the authorized families unchanged,
while `all` is an ordinary repository name, not an authorization wildcard.

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

The small functional/source/selector/inference-count fixture remains in the
required portfolio. The hardware-sensitive full 140k timing/memory proof lives
in `test_source_aware_store_exhaustive.py`, marked explicit tier T3 with
filesystem/process effects. It skips by default with
`T3 requires --allow-explicit-tiers`; scheduled/manual callers opt in through
the existing runner (no new workflow or host-state/auth exception):

```powershell
$env:PYTEST_ADDOPTS = '-s -ra'
python tools\run-plugin-tests.py agent-index --allow-explicit-tiers -k native_140000_source_layout --test-timeout 180 --subsuite-timeout 300 --plugin-timeout 600
```

Setup, timing and memory checks remain separate in that fixture. Its measured
five-second headroom is never enforced as a hardware-sensitive fast-PR budget.

One local Windows installed-package run, after setup and without tracemalloc,
measured the following individual requests (seconds):

| Mode | Empty | Explicit family | Full 18 families | Language | Repository |
|---|---:|---:|---:|---:|---:|
| Vector | 0.013229 | 0.107194 | 0.190066 | 0.212433 | 0.397763 |
| FTS | 0.023188 | 0.304637 | 0.225175 | 0.210804 | 0.448059 |
| Hybrid | 0.012392 | 0.125227 | 0.315866 | 0.404940 | 0.699135 |

Full vector/FTS requests issued two native queries including hydration; hybrid
issued three. Empty scopes issued none. Nonempty vector/hybrid requests invoked
the warm inference fixture once; FTS invoked it zero times. These are
two-dimensional fixture-model results with local store headroom inside five
seconds, not a real-model or Gateway transport acceptance result.
