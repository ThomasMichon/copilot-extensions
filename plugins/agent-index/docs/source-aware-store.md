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
The native storage format and read guards stay unchanged when no layout is selected.

The default durable receipt is `<IndexConfig.lance_dir>/.source-layout.json`,
alongside the stable `.source-writer.lock`. It records a domain identity,
layout/protocol 1, clean/dirty state, and exact current Lance table versions.
It is layout/readiness evidence, not a task/objective ledger or authentication
grant. The domain UUID is not a source permission.

### Point-domain binding foundation

The in-process `initialize_source_layout(path, point_scope=hints.scope)` seam
can bind a **cold, separately owned** directory to one complete
`PointChangeHints.scope`. Its protocol-2 receipt retains the exact configured
source, component family, account and `PointBinding`: representation/version,
effective endpoint, query union and query context. Reopening requires that
same typed selection through `domain_for(path, point_scope=hints.scope)`.
A different scope, protocol or nonempty unmarked directory refuses without
rebinding or adopting its data. Receipt publication cannot downgrade or
replace a different binding.

`initialize_source_store(config, point_scope=hints.scope)` additionally
bootstraps the configured empty content/model schemas through the existing
writer and dirty/effect/clean protocol. It requires distinct configured tables
and matching model IDs. The same receipt retains a versioned `point_models` map:
the content table and each configured model ID's distinct vector table and
positive integer dimension. This map is first saved only under the existing
writer while both retained and native table inventories are empty. Receipt saves
hold the same existing exclusion across the current-ownership read and atomic
publication and cannot drop or replace a retained map. A bare
`initialize_source_layout` has no model configuration and retains no map.
Prepared point tables missing that map are not adopted.

Receipt decisions, native schema validation and creation
share one existing source-domain operation, so a concurrent initializer cannot
lend it a stale empty-state decision. Reopening retains the same receipt and
tables and requires the complete same model/table/dimension mapping, including
same-dimension model swaps. Registration through any `MultiModelStore` facade
checks the retained content/model binding before returning even a cached model.
Unknown IDs, aliases, changed dimensions and content-table collisions refuse.
Model name/revision, query policy and engine placement do not define this table
ownership; embedding coverage and profile-policy owners still qualify their
changes. No model addition, table rebinding or legacy conversion is implied.
Missing configured tables in an already prepared domain, changed vector
schemas or a content table with the wrong schema/role refuse rather than being
added or converted. An interrupted bootstrap remains
dirty and requires its existing recovery owner; initialization does not replay
it. This is an in-process cold schema seam, not a component ledger/index
bootstrap, point application or a configuration/wire selector.

Existing default `ContentStore`, `MultiModelStore`,
`ClusterStore`, search and presentation paths remain legacy-only and reject
protocol 2 before opening their native stores. Default and explicitly selected
domain handles do not share cached selection; existing nested/lazy guards
recheck the receipt instead of trusting an earlier open. Legacy handles opened
before marking also check for later selection before native reads or writes,
including direct vector access and sibling cluster state. Guards use the native
connection's stable URI or an absolute retained sibling path, not a relative
caller path that can retarget after a working-directory change. The cluster
database and its sibling guard derive from the same constructor-resolved
absolute database path. Every receipt
reader rejects duplicate fields. Protocol 1 otherwise retains its existing
meaning and forward-compatible metadata handling.
The content store also retains its native connection URI for FTS lock paths
and child arguments, so maintenance cannot retarget after a caller-directory
change while its read guard stays on the original connection.

Even an exactly selected point-domain handle refuses the current legacy-shaped
`read`, `operation`, `write` and reconciliation paths, including inside the
private schema writer. Explicit scoped reads support the
[named-component result](component-ledger.md#selected-named-component-reads)
through applied-version qualification. Explicit
[ledger/index preparation](component-ledger.md) now forwards that same exact
selection through its existing owners and budgets, without admitting ordinary
data writers.

`VectorTable.preflight_point_upsert` and
`MultiModelStore.preflight_point_vectors` inspect current canonical input only,
under an already-owned matching source writer and explicit `point_scope`.
They reject another family/scope, including empty input; both connection URIs
must resolve to the domain, and distinct prepared content/vector tables must
match their expected schemas. The vector chunk-ID index needs zero uncovered
physical rows; nonempty input also reserves room below the existing segment cap.
Canonical fields are compared once through the prepared chunk-ID index.
The canonical table handle opened during binding is reused for that lookup;
an open failure propagates without the generic create-table fallback.

Preflight returns selected raw canonical fields at that read boundary, not a
durable receipt, model-to-table ownership proof or permission to write.
It performs no content/vector mutation, index preparation or writer acquisition.
The ordinary mutation APIs still refuse point domains.
Selected reads retain their separate explicit prepared-index path.

`VectorTable.upsert_point` and `MultiModelStore.upsert_point_vectors` provide a
separate, explicit native vector effect under an **already-owned** exact scoped
writer. Registered handles retain the configured model ID. Every call, including
empty input and cached handles, rechecks the current receipt's content/model
table and dimension binding. An unbound direct handle refuses. The call repeats
the current preflight, reading canonical input once, and writes through the exact
qualified vector handle without the generic open/create fallback. Missing
indices, uncovered tails and exhausted segment capacity refuse before effects.
Its nested existing writer latches post-effect failures even when the caller
catches them; no implicit retry or rollback is provided.

This effect neither publishes applied-version/embedding coverage nor maintains
indices, cleans stale chunks, writes path checkpoints or completes claims. Those
remain the composing application's existing owners. A caller must budget and
perform existing index upkeep before another nonempty selected write. The
returned row count is not an application certificate. Ordinary vector APIs,
prepared point publication and all frontdoors remain disabled.

`ContentStore.upsert_point` and `MultiModelStore.upsert_point_content` expose the
corresponding native canonical effect under that same already-owned exact
writer. The current receipt must bind the canonical role and every model table;
the complete native/retained inventories, schemas and connection paths must
agree. No cached handle or failed open can select another table or create a
replacement. The only permitted auxiliary table is the existing component
ordering ledger, qualified through its own retained identity/version,
metadata-bearing schema and prepared zero-tail point index. Canonical effects
neither change that ledger nor adopt arbitrary auxiliary or model tables.
Both canonical chunk-ID/file-path indices, any already-present
source index and every bound vector chunk-ID index must be prepared with zero
uncovered physical rows. Every table that will change needs a reserved segment.
Identical raw fields retain the canonical timestamp and table version; identical
duplicate inputs coalesce, while conflicting duplicates or an existing ID with
another source refuse. Raw comparisons use the existing bounded indexed lookup,
not decoded metadata or a corpus scan.

Before any effect, selected vector-ID reads also reject duplicate/failed reads
and qualify all bound models, not just those registered on the calling facade.
The existing provenance updater then maintains source/language labels through
those exact prepared vector handles without changing embeddings. A caught
canonical add or later model-projection failure latches the outer writer dirty
and uncertain. There is no implicit repair or retry.

Canonical and vector effects still do not compare provider versions, remove
obsolete IDs, qualify embedding coverage, update indices/FTS, publish path
checkpoints or complete work. The composing application must order versions
before replacement and retain store-before-checkpoint recovery. Raw indexed
reads and native effects are selected-ID bounded; table/fragment metadata
inspection is not constant work or proof of the large-corpus acceptance gate.

`store.file_projection.require_file_projection_capacity` accepts an optional
exact point scope only under its already-owned source writer. It qualifies the
receipt-bound canonical/model inventory and optional ordering ledger through
the same native owner, without trusting or changing caller caches. Replacements
must all belong to the selected family; their nonnegative row counts replace
that family's existing occupancy at each path. Other families at that path
remain in the final count. A deletion contributes zero replacement rows.
The prepared zero-tail `file_path` index is required even for an empty replacement
set. Selected paths bound current candidates across all families before
calculating final occupancy, including for deletions.
Each queried path and its projected final count must fit the
positive integer row budget. This read-only preflight requires no reserved
mutation segment and performs no preparation, row mutation or ledger write.
The default legacy path is unchanged. The composing selected application must
run this preflight inside its writer before effects; this helper does not
enable that application or establish provider ordering.

`indexing.embedding_batch.embed_and_store_pairs` also accepts an explicit
`point_scope` in its existing bounded mode. The caller must already own the
matching writer and supply coverage, the complete retained model-profile map
and an after-store preparation callback. Native table/ledger qualification and
zero-tail vector indices are checked before inference; current raw canonical
input and selected family are checked before routing. Task-local qualified
handles ignore caller caches without changing them. Default pair/batch calls
and canonical content remain unchanged.

Routing, missing-pair selection, prepared-vector callbacks, lazy readiness and
work counters retain their existing owners. Only missing routed pairs use the
selected native vector effect, followed by receipt invalidation, explicitly
budgeted index preparation and stored-vector verification. Selected
`EmbeddingCoverage.record` requires the existing writer, requalifies model/table
and canonical input through the native owner, and compares actual indexed
vectors before opening its SQLite receipt writer. Failed or incomplete reads
cannot publish coverage. A caught post-effect pair/preparation/publication
failure anywhere after an earlier effect, including later batch-input or
model-selection checks, latches the enclosing native writer uncertain. The
nested writer covers the complete pair invocation, not only individual writes;
there is no implicit
retry or recovery. A successful reopen can reuse qualified unchanged pairs
without inference or native row writes.

This is selected pair composition, not complete component application. It does
not acquire or order provider versions, replace canonical content, remove stale
IDs, retain applied versions, advance path/source checkpoints or complete a
claim. Preparation remains the outside-exclusion owner's responsibility when
that policy is required; passing a prepared-vector callback avoids inference
inside this already-owned writer. Ordinary application, point producers and v2
execution remain disabled.

`store.point_reconciliation.reconcile_point_file` separately provides bounded
stale-ID cleanup under an already-owned exact point writer. It reuses the native
table qualification and raw file projection above through task-local owners;
caller caches are neither trusted nor changed. Its positive integer row budget
caps both same-path candidates across families and the union of current and
explicitly retained IDs. Kept and retained IDs are unique tuples within that
budget. Known vector-only remnants can be removed, but unknown orphans are not
discovered. A retained ID belonging to another canonical point or incompatible
vector provenance refuses the operation.

All bound models, raw points and prepared zero-tail indices are checked before
effects. Every affected index needs a reserved segment; an unchanged table
does not. Only observed stale rows are deleted, from each bound vector table
before canonical content, through the existing bucket mutation owner. The
return value counts distinct IDs that had rows removed, not rows across models.
Empty or fully kept points perform no native mutation. Unselected paths,
families and models and the ordering ledger remain unchanged. A caught native
failure latches the outer writer dirty and uncertain, even after an earlier
model deletion succeeded.

This cleanup does not compare provider versions or publish applied versions,
embedding coverage, indices/FTS, path checkpoints or claim completion. Its caller
must establish provider ordering before **any** replacement or deletion, and
retain store-before-checkpoint recovery. Ordinary reconciliation remains
unsupported on a point domain.

The explicit [typed-claim application and sealed suffix](component-ledger.md#explicit-native-application)
compose these native prerequisites. Ordinary point application, whole-claim
completion and every v2 executor/producer remain unsupported.
The binding is not
acquisition, application, completion or deployment authority. There is no live
directory allocation, migration, new service, HTTP parameter, CLI flag or MCP
form in this foundation.

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

`SourceDomain.operation(db)` holds this existing exclusion across a sequence
of independently committed reads/writes. Retirement uses it from its pending-
work admission through canonical/vector/path removal and configured-identity
cleanup, so backfill intent cannot enter between the GC check and deletion.
Nested mutation methods retain their own dirty/effect/clean protocol; the
operation itself is not a transaction and does not roll back or reclassify
uncertain effects. A read/guard refusal before mutation remains clean, and a
later retry retains already completed writes. Ordinary indexing is not fenced
for a backfill job's lifetime.

A nested participating write that raises after marking a new irreversible
effect or reconciliation seal latches the outer writer as failed. Catching
that exception, or completing a later nested no-op, cannot publish the source
clean or clear its uncertainty. The existing recovery/reconciliation owner is
still required; this is not rollback or new repair authority. A caught
pre-effect refusal does not invalidate earlier successfully completed writes.

Ordinary shared-engine ingestion uses [prepared source application](prepared-application.md):
embedding finishes outside exclusion, then one outer writer contains the
manifest's content, vector and reconciliation mutations. This changes neither
the dirty/effect recovery rules nor the layout protocol.

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
By default, repository prefilters use the same first colon-delimited identity as
native `repo_matches`, before vector/FTS candidate selection and hydration. This
includes ADO/azure-devops project families and byproducts, bare project names,
and exact owner/host-qualified identities. Repository names containing `%` or
`_` remain literal; an omitted repo leaves the authorized families unchanged,
while `all` is an ordinary repository name, not an authorization wildcard.

A trusted content-store extension can retain another repository interpretation
through the [consumer policy injection recipe](consumer-migration.md#repo-filtering-in-a-consumer-retrieval-facade).
Scoped native candidates and hydration use that store's SQL policy; canonical
family authorization remains an independent restriction.

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
