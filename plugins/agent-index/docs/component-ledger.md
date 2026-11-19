# Native component ordering evidence

`store.component_ledger` is an opt-in Python dependency for ordered component
application. It changes no default reader or writer and enables no receiver,
executor, checkpoint acknowledgement or live adoption.

`initialize_component_ledger(config, store)` explicitly bootstraps the native
`component_ordering_evidence` table only when the configured participating
content/model tables are empty. Existing bindings are verified, never reset;
a missing table, orphan table, unknown binding, incompatible schema, changed
table identity/version or unprepared index refuses access. Existing populated
stores require a separately designed adoption/recovery path, not this bootstrap.

The table lives in `config.lance_dir`, alongside the source-layout receipt and
data included by the existing native-directory backup. Arrow schema metadata
retains a random table identity across native writes, index preparation and
coherent directory copies. A constant-size protocol-1 `component_ordering`
receipt binding records that identity and the ledger owner's witnessed native
version. Generic participating writers update the domain's version map but
**not** this binding: dropping the table or losing tombstone rows cannot become
valid merely because another writer subsequently commits a clean receipt.

## Point reads and subordinate writes

`ComponentLedger(config, store).get(source, path)` reads at most two candidates
through a prepared B-tree on the exact point's digest. It rejects duplicate or
unexpected records. Each immutable `ComponentEvidence` retains `ComponentContent`
(provider version, rendering semantics, fingerprint and deletion flag), opaque
nonempty acquisition `scope` and `policy` identities, and the complete
[raw file projection](file-projection.md). These scope/policy identities must
cover the caller's actual account, endpoint, query and selected policy; this
helper neither derives them nor permits rebinding existing history.

`ComponentVersion.identity` optionally retains a typed `ComponentIdentity`:
`work_item_revision` with its parent item ID, or `comment_version` with both
parent item and comment IDs. The identity excludes provider version and event
correlations. Qualified named-version acquisition supplies it directly; prepared
observations validate it against their typed target and retain it through the
complete candidate, application binding, native evidence and snapshot. Neither
paths nor chunk metadata supply identity. The pending manifest rejects two
physical mutations carrying the same logical identity within one source.

Legacy component records omit this optional field in their canonical encoding.
Reopening or composing existing observations does not enrich them or alter an
immutable application binding. They remain untyped, not qualified for an
identity-bearing read result. Typed/untyped rebinding and changed kind, parent
or comment identity refuse at every version, before application embedding or
native writes. There is no automatic migration or adoption path. Older strict
readers reject identity-bearing records; no deployed producer or execution
capability is advertised for this opt-in Python representation.

`ledger.put(evidence, expected=prior_record, max_rows=point_budget)` requires an
already-owned source-domain writer. It re-reads the exact prior record, compares
versions with the shared component comparator, and recaptures the current raw
point before retaining evidence. New versions retain the union of current and
previously known chunk IDs; omitting old IDs cannot hide vector remnants.
Equal-version conflicts refuse; matching duplicates and older versions return
`False` without changing the ledger only while the retained raw point still
matches native data. A newer record returns `True`. A deletion record requires
empty canonical rows and empty registered-model points for all retained IDs.
Unknown vector orphans remain outside this evidence, not silently certified.

The ledger uses the existing scalar-index owner and its physical-tail/segment
budgets. Point capture and index preflight must succeed before the ledger
mutation. Its single-row native transition and index update then advance only
its own binding. A failed native effect or binding publication remains subject
to the source domain's dirty/uncertain recovery rules. Read/preparation failures
before entering a writer do not dirty the domain; exceptions escaping an owned
writer retain that owner's existing failure semantics.

`max_rows` bounds point candidates/retained IDs as described by raw projection,
not aggregate payload bytes, model count or index metadata I/O. Ledger history
is not pruned automatically. `ledger.consolidate(max_rows=physical_row_budget)`
explicitly preflights the whole current physical table before entering its
writer, then reuses scalar consolidation and rebinds the resulting native
version under that same writer. Use this operation at the segment ceiling;
generic scalar maintenance cannot advance the ledger's independent binding.

## Application and restore boundaries

These are ordering observations, **not an applied-content certificate**. An
application must still validate its exact durable pending parent and prior
native point **before derived writes**, bind acquired input/rendering and model
coverage to the intended fingerprint, prepare embeddings outside exclusion,
revalidate inside it, and compose content/vector publication, claims,
checkpoints, crash recovery and component read isolation. This helper cannot
retroactively prove those steps or make default readers component-isolated.

### Explicit native application

`indexing.component_application.PreparedComponentApplication` composes those
owners for an explicitly selected in-process component family. It accepts a
completed component manifest, opaque recovery scope, configured store/path/model
owners and positive `max_rows` point budget. The native ledger must already be
bootstrapped and all point indices prepared; application never adopts a populated
store, rebuilds an index or repairs an uncertain writer.

The application fingerprints each acquired replacement with the shared file,
chunker and model-policy fingerprint. Its versioned policy identity binds the
configured chunker, content table and model/profile/routing mapping. It refuses
prior observations from a different scope/policy or rendering contract, native
state without qualified application evidence, conflicting equal versions, and
missing/mismatched path fingerprints or chunk counts. Lower or equal covered versions additionally
require an unchanged complete raw point, valid canonical chunk identities, exact
routed model membership and current embedding receipts qualified against actual
vectors. A cached embedding or ledger observation alone is insufficient.

Selected replacements reuse the existing prepared journal and its exact parent
binding. Component selection only narrows the cached file plan, never the acquired
parent or immutable component set. Selected files are chunked even when a path
fingerprint matches; vector preparation can still reuse qualified pairs.
Chunking/embedding occur outside source-domain exclusion. Covered points prepare
no vectors. Preparation failure leaves native rows and path checkpoints unchanged.

`publish()` rechecks the policy, configured owners, pending cache incarnation,
selected path checkpoints, exact prior ledger records, raw native points and
covered vector receipts under the existing domain operation. Native point/retained
ID caps, projected same-path occupancy across selected sources (including
replacements/deletions and unselected neighbors) and index segment capacity
refuse before writer entry. The existing
writer then encloses content/vector writes, stale-ID cleanup, bounded index
upkeep, final canonical/routed-vector qualification, native ordering evidence
and finally path checkpoints. Deletions retain ordering and known vector IDs but
remove only the named component's rows and checkpoint. Cancellation before writer
entry is clean; interruption inside it retains the existing dirty latch.

`publish()` returns applied/covered point counts. It **does not** save a polling
cursor, retire the pending manifest or acknowledge a queued claim. An already
committed native application can reopen and qualify as covered while that exact
parent remains retained. A dirty/incoherent native state refuses ordinary
reopening; prepared vectors never clear uncertainty. The explicit sealed
publication suffix below is narrower than arbitrary partial-effect
reconciliation. Mixed checkpoint/native restore qualification, whole-claim
completion and explicit reader isolation remain unfinished under
[#13386](https://github.com/ThomasMichon/copilot-extensions/issues/13386).
The ordinary engine, recovery and pending-completion entrypoints still refuse
components. No v2 execution declaration, connector, producer, service/CLI form,
source activation or default read behavior is enabled.

The point cap includes same-path native candidates and the union of current,
retained and newly prepared chunk IDs. It does not bound acquired payload bytes,
whole-file chunking/embedding memory, aggregate component count or metadata I/O.
History/segment exhaustion requires separately budgeted maintenance, not automatic
consolidation. `tests/test_component_application.py` exercises native ordering,
replacement/deletion, routed coverage, unrelated-row preservation, preparation
replay, publication races and interruption refusals in its explicitly selected
native lane; it is not an unconditional required unit matrix.

### Applied native snapshots

`indexing.component_application.qualify_applied_component(ledger, paths, evidence,
scope=..., max_rows=...)` qualifies one exact retained observation under the
existing source-domain operation. It derives configuration/store ownership from
the ledger, checks the configured path-index location, and requires the current
ledger record, scope, application policy and raw native projection to agree.
Canonical chunk identities, exact routed membership and current vector receipts
are qualified through the same application helpers. A present component requires
both its fingerprint and actual canonical chunk count in the path checkpoint.
A deletion requires empty content/model points and no path checkpoint.

The returned `AppliedComponentSnapshot` carries the retained content/version/
rendering evidence, qualified chunks and `named-target-only` coverage. It is an
in-process snapshot at that exclusion boundary, not a durable certificate,
permission, successful claim acknowledgement or assurance that the point remains
unchanged after the operation returns. Applications reuse this qualification
before accepting prior versions and again before publication. Missing or
conflicting evidence refuses without repairing state or treating absence as
an unobserved deletion.

Coverage and checkpoint qualification use non-creating, immutable read-only
SQLite connections under that existing source-domain exclusion. They neither
initialize empty files nor change journal mode or create WAL/SHM sidecars.
Nonempty WAL or rollback journals refuse qualification rather than being
silently ignored; their existing writer/recovery owner must settle them first.
Qualification never checkpoints, deletes journals or repairs missing, empty
or corrupt path evidence. Ordinary application/reuse connection defaults are
unchanged.

This helper supports the existing explicit native application prerequisites.
Its optional exact `point_scope` propagates through the existing ledger, raw
projection, chunk-ID and vector-coverage owners. Omitting that selection still
refuses a protocol-2 domain, even within an explicitly selected operation.

### Selected named-component reads

Open `MultiModelStore(config.lance_dir, content_table=config.content_table,
point_scope=hints.scope)` and register the configured model profiles. This
only opens already prepared handles; it neither creates missing tables nor
admits ordinary store reads or writes. Build the existing `ComponentLedger`
from those owners, then call `indexing.component_read.read_component(ledger,
paths, path, point_scope=hints.scope, application_scope=..., max_rows=...)`.

The domain must match the complete selected representation/account/query scope,
and the point must belong to its canonical family. Qualification uses the
[applied snapshot](#applied-native-snapshots) under the same exclusion. Missing
ordering, typed identity, native/index, checkpoint, policy or routed coverage
evidence refuses without repair. Paths and metadata never supply missing item
or comment identity. Unknown paths refuse rather than claiming enumeration.

The distinct `ComponentRead` returns exact point scope, canonical typed identity,
source/path, applied provider version, rendering semantics, deletion state,
qualified chunks and `named-target-only` coverage. A proven ordered deletion
has no chunks but retains those identity and coverage semantics. The result is
an in-process observation, not a durable certificate or a complete discussion.

All legacy-shaped reads, including nested/lazy reads on the selected handles,
continue to refuse. Search, clusters, HTTP, MCP, CLI and consumer presentation
routes remain unsupported for point domains. Protocol-2 writer/bootstrap,
v2 execution/producer and current-claim completion remain disabled; there is
no migration or conversion of existing protocol-1 state.

### Sealed native publication suffix

After qualifying all selected native content, routed vectors, cleanup and
indices, application retains immutable publication intent in the existing
`PreparedJournal`. The exact acquired-parent incarnation owns and retires it.
Intent binds scope, policy, point cap, every selected/covered point, prior and
target ordering observations, invalidated path checkpoints and intended final
hashes/counts. It has no lease, cursor, claim or retirement protocol of its own.

The owning source writer seals that intent's digest together with every native
table's physical identity and version in its existing dirty receipt. Any later
native effect revokes the seal **before** mutation; successful ordering/index/
binding publication renews it before writing the corresponding path checkpoint.
The uncertainty latch stays set, including for an empty native phase, so older
participating validators still refuse it. A successful outer write removes the
seal with the normal clean publication.

`indexing.component_publication.reconcile_component_publication(...)` is an
explicit in-process continuation for this sealed suffix. It requires the same
manifest, parent incarnation, scope, family, policy, native/path/model owners
and point cap. Under the existing writer/FTS exclusion, it verifies the complete
physical seal, then revalidates every retained raw point and routed vector
receipt, exact prior-or-target ledger record and prior-or-completed path state
before repairing any suffix. Completed checkpoints retain their timestamps.
It invokes no embedding engine and does not replay native content/vector data.
The recovered outer commit conservatively replaces logical coverage metadata;
it cannot reconstruct the interrupted writer's fine-grained bucket witnesses.

Missing/corrupt intent, changed native tables, incomplete coverage, contradictory
checkpoints and replaced parents refuse without clearing the latch. A crash
inside native data or ordering/index/binding mutation also refuses when no
current seal survives. Reconciliation does not infer a lost member from
surviving rows, restore a table version, bless a general dirty store or qualify
mixed backups. It returns application/coverage counts only: the pending claim
and ordinary polling state remain retained, and all v2 execution fences remain.
Native controls include abrupt process exit, repeated suffix interruption,
mixed applied/pending/covered points, deletion, and owner/evidence drift refusal.

A coherent, quiescent synthetic copy of the complete native directory preserves
the evidence at another path without an inode/path identity requirement.
Missing-ledger and dirty copies refuse in native controls. This is not a new
backup implementation or qualification of live backup concurrency, mixed
checkpoint/state restores, missing unknown vectors or an obsolete complete
backup presented as current state. Those remain application/recovery work.

See the [architecture overview](architecture.md) and
[prepared scalar-index ownership](chunk-id-index.md).
