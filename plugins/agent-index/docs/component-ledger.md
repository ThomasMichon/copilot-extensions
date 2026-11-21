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

For a protocol-2 point domain, pass its exact `point_scope=hints.scope` explicitly
to both `initialize_component_ledger(config, store, point_scope=...)` and
`prepare_ingestion_indices(store, max_rows=..., point_scope=...)`. They reuse the
existing domain operation, private preparation writer and scalar-index owner.
The ledger still requires empty configured data tables; index preparation retains
its physical-tail and segment budgets, without implicit consolidation. Reopening
verifies existing ledger identity/version/index evidence and does not recount
configured data or reset missing history. An interrupted native preparation
retains the existing dirty/uncertain latch; retry cannot replay or repair it.
Omitted, mismatched and legacy-domain point selections refuse, including nested
calls inside an explicitly selected operation. Preparation neither admits
an ordinary ledger write/consolidation nor ordinary data mutation or point
application. Explicit subordinate ledger retention has the separate
already-owned-writer requirement below.

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

For a protocol-2 point domain, `put(..., point_scope=hints.scope)` forwards that
exact explicit selection through the existing prior-record read, raw projection
capture and single-row index preparation. The caller must already own the matching
domain writer; the method never enters one. Omitted or mismatched scope and
foreign canonical families refuse before ledger effects, including inside an
already-owned writer. Reopening retains typed identities and tombstone ordering.
Ordinary point writers, ledger consolidation and queue completion remain disabled;
subordinate observations do not authorize the explicit application below.

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

For an explicitly selected protocol-2 store, pass the whole typed
`point_claim=PendingPointClaim.from_task(pinned_task)` as well. The claim must
match the exact retained v9 parent and every acquired target; a subset manifest
cannot become application input. The existing prepared owner copies and binds
that complete candidate and prepares vectors outside native exclusion.
Publication recomposes the retained target set and rechecks policy, checkpoints,
ordering and raw points before effects. Unlike protocol-1 preflight, selected
capacity and qualified task-local table handles require the already-owned point
writer. They preserve the retained complete model binding without trusting or
changing caller caches. Before path invalidation, point preflight reserves the
whole selected plan's per-index segment demand: changed canonical points,
vector-provenance upkeep, every routed batch with missing pairs, and the free
slot required for stale-ID cleanup. It does not consolidate indices. A no-effect
preflight refusal exits the writer cleanly before propagating, preserving the
receipt and checkpoints. Any unexpected preflight effect or subsequent
publication failure retains the dirty latch.
The selected path composes native content, prepared pair
publication, bounded index upkeep and stale-ID cleanup before the same sealed
ordering/checkpoint suffix. No engine call occurs inside publication.

`publish()` returns applied/covered point counts. It **does not** save a polling
cursor, retire the pending manifest or acknowledge a queued claim. An already
committed native application can reopen and qualify as covered while that exact
parent remains retained. A dirty/incoherent native state refuses ordinary
reopening; prepared vectors never clear uncertainty. The explicit sealed
publication suffix below is narrower than arbitrary partial-effect
reconciliation. Mixed checkpoint/native restore recovery and broader consumer/read composition
remain unfinished under
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
unchanged. `PathIndex(path, read_only=True)` opens only an existing file,
skips schema initialization and forces every connection through that read-only
policy; a missing parent directory is not created.

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
routes remain unsupported for point domains. The separate
[cold schema bootstrap](source-aware-store.md#point-domain-binding-foundation)
creates only configured content/model tables, not this ledger or its indices.
Protocol-2 ledger/index bootstrap and typed-claim application are separate
explicit prerequisites above. V2 execution/producer and ordinary current-claim completion
remain disabled; there is
no migration or conversion of existing protocol-1 state.

### Explicit whole-claim completion

`indexing.point_completion.complete_point_claim(claim, scope=..., paths=...,
tasks=..., ledger=..., max_rows=...)` completes only an explicitly selected,
already applied whole typed claim. The explicit retained worker below composes
it with application; ordinary workers, receivers, recovery and producers do not
call it. It advertises no v2 execution capability.
Queue and path databases must be the configured owners alongside the ledger.
The exact current task must retain the whole immutable claim and pin, remain
parked, and have no registered worker. Cancelled, failed, interrupted, partial,
unbound and changed claims refuse; cancellation never certifies application.

The existing prepared owner reopens its retained incarnation without creating
missing cache state, recomposes **every** named version and correlation, and
requires the existing immutable complete application binding. Completion binds
each selected component's typed logical identity, rendering semantics, provider
version and canonical fingerprint. Every component must have matching applied
or higher-covered native ordering evidence and pass the existing applied
snapshot's raw-point, canonical, routed-vector, coverage and checkpoint checks.
Missing or untyped history is not absence or deletion proof.

The operation takes the existing queue/path SQLite writer transactions before
native exclusion, then holds all three owners through qualification. It performs
no embedding, native mutation, polling-cursor save or repair. Queue success and
a compact versioned outcome in its existing `result_stats` commit **before**
pending retirement. The databases use separate WALs; this is deliberately not
described as a crash-atomic cross-database commit. The outcome retains claim and
parent digests/incarnation, scope, policy, native domain and per-component
version/fingerprint/deletion summaries, not copies of acquired bodies.
Completed point-claim outcomes reject later updates, including from older status writers.

The additive `complete_higher_point_claim(claim, components=..., scope=...,
paths=..., tasks=..., ledger=..., max_rows=...)` selects **only an entirely
superseded claim with no pending acquisition**. The caller supplies the adapter's
exact typed component mapping after fresh eligibility checks, under its source
writer. Every applied component must be strictly newer than the greatest
claimed version of that logical identity, with matching scope and rendering
semantics, and pass the same native/checkpoint/routed-coverage qualification.
Missing, equal or newer requested versions return `None` for ordinary
acquisition; inconsistent or uncertain evidence refuses. It neither creates
nor retires pending/prepared work and never advances polling.

This no-acquisition path retains `point_completion` outcome version 2 with
`mode: higher-covered`. Its components describe the **actual applied higher
versions and their fingerprints**, not invented fingerprints for unread older
bodies. Claim, scope, policy and native domain remain bound; pending digest and
incarnation fields are absent because no acquisition occurred. The existing
`complete_point_claim` requalifies this summary on completed retries, including
exact canonical equality with the recorded higher version or still-higher
coverage. Version-1 acquired outcomes retain their encoding and behavior.
Older completion readers reject the new outcome rather than reinterpret it;
rollback cannot requalify that completed retry until a compatible reader is
selected. This adds no execution capability or independently deployed wire form.

Only the owning transaction temporarily removes its exact legacy status or
pending-delete fence, performs the qualified transition, then reinstalls the
same trigger before commit. Both schema and data roll back on interruption.
Ordinary completion, processing, worker registration, v9 clear/discard,
replacement and legacy decoding retain their refusal behavior. Pending deletion
uses the existing same-transaction prepared-row/incarnation retirement triggers.

A failure between queue commit and pending retirement leaves the exact parent
retained. A fresh explicit call requalifies all current evidence and finishes
retirement without replaying application. After retirement, a lost-return retry
uses the queue's retained summary and again qualifies every point; it cannot
clear a newer pending claim. Mixed stale checkpoints, native-directory restores,
policy changes or missing coverage refuse rather than repairing or recertifying
them. This does not undo a historical queue success or qualify arbitrary backup
restoration; it blocks that retry/retirement until the existing owner resolves
the discrepancy. A coherent obsolete copy of every owner still needs an external
freshness oracle, not inference from internal agreement.

Work scales with the claimed target set, not unrelated source history.
`max_rows` retains its existing **per-point** meaning, not an aggregate claim,
payload-memory, lock-duration or physical-I/O budget. The focused native
`tests/test_point_completion.py` packet covers real completion/reopen, higher
coverage, partial/stale refusal, trailing work, cancellation, separate-WAL failure
boundaries, mixed restores and transactional-fence rollback after abrupt exit.
Small actor-free composition controls live in required unit CI. Native Windows
and ordinary producer/worker composition remain unqualified.

### Explicit retained point worker

`indexing.point_worker.run_retained_point_worker(claim, scope=..., paths=...,
tasks=..., ledger=..., clients=..., max_rows=..., progress_cb=...,
qualify_claim=...)` is a
synchronous in-process boundary for an **already fully acquired** whole claim.
The caller supplies the configured owners and owns client lifetime and provider
eligibility qualification. It does not acquire targets, initialize a native
domain, open clients or declare a v2-capable ordinary indexing callable.

The existing source-writer lock spans retained acquisition decoding, prepared
application, native publication and whole-claim completion. Missing, incomplete,
stale or foreign retained work refuses rather than fetching it or invoking
ordinary recovery. Preparation reuses the same immutable acquired observations
and prepared vectors after interruption. Current queue eligibility and callback
cancellation are checked before preparation, before publication and before
completion; cancellation or failure never becomes generic successful task status.
Native/checkpoint ordering, pending retirement and retry qualification remain
with their existing owners above.

An optional caller-owned `qualify_claim()` runs before preparation, before
publication and before completion, outside native operations. A refusal retains
the claim and recovery material. A failure after publication does not undo that
publication or acknowledge the claim; a qualified retry reuses covered outcomes.
Already-completed retries skip this provider callback.

A completed lost-return retry goes directly to requalifying completion, without
re-preparing, embedding or replaying native application. Uncertain native effects
and mixed restores refuse; this worker never automatically invokes the separate
sealed-publication reconciliation operation. Retained work stays available to
its existing recovery owner. No polling state is read or advanced.

This boundary registers no detached worker and changes no runner, CLI, HTTP,
MCP, producer, queue-selection or execution-capability declaration. Ordinary v2
execution remains disabled. Its row cap is still per point; it does not impose
an aggregate target, memory, elapsed-time or physical-I/O budget.

### Explicit Azure DevOps acquisition caller

`sources.azure_devops_point_worker.run_azure_devops_point_worker(connector,
claim, scope=..., paths=..., tasks=..., ledger=..., clients=..., max_rows=...,
progress_cb=...)` composes the named-version adapter with this worker. The caller
must already select a compatible, bootstrapped point domain and own client
lifetime. It is not a new selected-callable capability or ordinary runner path.
The existing domain operation verifies the actual native point selection before
provider acquisition or pending writes; its exclusion is released before HTTP.

The existing source writer spans fresh qualification of every distinct claimed
item, exact named-version acquisition, immutable `PreparedPoints` retention and
worker completion. A new v9 pending parent starts with the first positively
qualified observation, never an empty component manifest. Resume reuses only
positively retained observations and fetches missing named versions; retained
bytes alone do not prove current eligibility. Before preparation, publication
and acknowledgement, fresh principal/query-union/definition checks must still
match the entire claim binding. Query exit, changed principal/context,
ambiguous responses, unsupported identities/versions and cancellation refuse,
without inferring deletion or invoking broad/legacy acquisition.

For a fresh claim with no pending parent, the caller first tries the all-target
higher-covered completion above using the same physical/semantic mapping as the
named reader. A qualifying superseded claim needs only the initial per-item
eligibility checks and local native qualification: no body acquisition,
chunking, embedding or native application. Equal versions still acquire and
compare canonical content; mixed older/equal/missing claims retain whole-claim
acquisition. Existing pending work always takes its retained recovery path.

For U distinct items and M missing targets the upper bound is 132U + 67M logical
provider requests, each under the existing maximum of five attempts. This uses
the existing 1,024-target envelope and per-read decoded/rendered payload caps;
it is not a transport-memory, aggregate-payload, writer-lock-time or elapsed
budget. Provider counters remain with the shared HTTP/acquisition owners.
After acknowledged success, retry takes the existing local completion path
without provider reads, preparation or native replay.

Failures preserve unresolved work under the existing queue/pending/native
owners. No polling cursor, detached-worker registration, producer, service
contract, execution declaration, automatic repair, source activation or rollout
is added. Ordinary v2 execution remains disabled.

### Configured in-process Azure DevOps composition

`sources.azure_devops_point_runtime.run_configured_azure_devops_points(connector,
task_id, expected_hints=..., config=..., legacy_data_dir=..., point_scope=...,
scope=..., clients=..., max_rows=..., progress_cb=...)` selects the owners above
from an explicit `IndexConfig`. Its complete configured `point_scope` must match
the expected whole queue snapshot, the prepared protocol-2 domain and the
connector's endpoint/family/query union. Fresh principal and resolved-definition
qualification remains with the acquisition caller, not this static comparison.

The selected data directory must be disjoint from the explicitly supplied
legacy directory, including ancestor/descendant and resolved symlink aliases.
Derived content/model, cluster, polling-state, queue and path locations must
remain inside the selected directory. Queue/path databases and the native
domain, exact model mapping, indices and component ledger must already be
prepared by their existing owners. This entrypoint never initializes a domain,
converts legacy state or opens embedding clients; callers own connector/client
lifetime. Legacy `SourceSpec`, source hashes and environment defaults do not
change.

Under the existing source writer it qualifies native readiness, opens configured
queue/path owners, then uses `pin_point_claim` to compare and freeze the exact
parked snapshot before acquisition. A changed snapshot refuses rather than
pinning a subset or widening the request. Failures retain the parked pin and
recovery material; fresh composition reuses retained observations. A matching
completed pin takes the existing local completion retry without clients or
provider work. Native exclusion is not held during provider or embedding calls.

This is an explicit synchronous Python operation, not a `run_reindex`-shaped
callable. `TaskRunner` still delegates ordinary tasks to its versioned detached
worker; registering its in-process callback does not select this entrypoint.
No ordinary v2 dequeue, execution-version advertisement, producer, service
configuration/wire form, automatic repair or runtime activation is added.
The acquisition and per-point row budgets above remain unchanged.

### Explicit in-process worker dispatch

`indexing.worker.run_worker(task_id, config=..., point_executor=...)` selects a
point executor only for that explicit Python invocation. Supply the actual
`IndexConfig` and a synchronous callable accepting the selected `TaskRecord`;
the callable binds the connector, complete point scope, legacy directory,
application scope, row budget and caller-owned clients required above. It can
call `run_configured_azure_devops_points` with the selected task's ID and exact
typed hints. It cannot be combined with the legacy `indexer` injection.

Dispatch holds the configured source writer from snapshot recheck through the
callable's return and durable queue verification. The configured runtime still
qualifies and pins the whole snapshot before effects. The dispatcher ignores
the callable's return value: success requires the existing queue owner to
confirm completion against the original task ID, exact immutable hints and pin,
captured before the callback can mutate its `TaskRecord`. Exceptions leave
the claim and recovery material as the execution owners retained them; dispatch
does not write generic failed, cancelled or successful status. Return codes are
0 for verified completion, 1 for an execution exception and 2 for an unsupported
selection or a callback returning without durable completion.

The caller owns the synchronous invocation's lifetime and must keep its clients
alive until it returns, including during cancellation or service shutdown. An
async host must offload and join this blocking operation; abandoning a thread
does not stop its effects. This path neither changes process priority/signal
handlers nor registers a worker PID. It is not a detached-worker or cutover
adoption contract. A process interruption retains the pin; a fresh explicit
invocation can resume through the existing recovery owners.

`index-worker --task` supplies no executor, reconstructs its legacy config and
continues to refuse parked v2 work. Existing v1 callable negotiation, ordinary
dequeue, worker-generation selection and execution advertisement are unchanged.
No new CLI syntax, serialized configuration, service selector, producer or
automatic v2 scheduling is enabled.

### Explicit async lifetime owner

`indexing.point_worker_host.PointWorkerHost` adapts the explicit dispatch above
to one event loop. Enter its single-use async context inside the caller-owned
connector/client lifetime, then `await host.run(task_id)`. It offloads the
complete synchronous dispatch, including durable verification, and joins it
before propagating cancellation. Context exit and `stop()` close admission and
join the same operation even if repeatedly cancelled; they do not terminate a
thread or synthesize cancelled queue status.

Only one explicit dispatch is admitted at a time. `drain(timeout=...)` pauses
admission and waits without cancelling the worker; it returns false on timeout.
Cancelling the drain observer also leaves the active operation owned.
`resume()` reopens paused admission, but never a stopped host. Clients must
remain alive until context exit or stop settles, not merely until drain times
out. There is no bounded thread-termination guarantee.

The supported `TaskStore.cancel()` API acquires the same data-directory source
writer before cancelling point work. It returns false without changing the row
when another thread/process owns that writer, or when called reentrantly by
the current owner. This matters because a running point claim still has parked
queue status. After the owner settles, cancellation and explicit retry retain
the immutable pin and pending recovery material. Legacy queued cancellation
does not acquire this lock. The lock does not fence raw SQL or older,
nonparticipating cancellation implementations; it is not a new running state,
cross-version adoption contract or cooperative interruption of native work.

This standalone Python owner does not select tasks or configure a service.
The explicit service binding below composes it without changing detached-worker
survival/adoption. Ordinary v2 capability, cross-consumer coordination and
runtime activation remain separate.

### Explicit point queue consumer

`indexing.point_queue_consumer.PointQueueConsumer(config=..., point_scope=...,
point_executor=...)` composes the joined host with caller-scheduled bounded
queue consumption. Enter its single-use async context inside the configured
connector/client lifetime. Every instance starts paused, including after
reconstruction. Explicitly `await consumer.resume()`, then
`await consumer.run_pending(max_tasks=...)` to complete at most that many claims
(default one). Empty batches return zero; successful batches return their
completed-task count. It creates no timer, task loop or process.

The read-only `TaskStore.next_point_task(point_scope=...)` selects parked v2
work FIFO by creation time and ID, matching source, family, account and the
entire endpoint/query binding. It neither pins nor changes status. Queue reads
run off the event loop; admission is rechecked afterward so drain or stop cannot
be followed by a late dispatch. The dispatcher checks the current whole scope
again before invoking the bound executor. Its existing snapshot comparison,
runtime pinning, source writer and completion verification remain authoritative;
later arrivals never mutate an existing pin. Legacy/v1 tasks, other scopes and
terminal rows are not selected. An optional in-process `admit` callback runs
after selection, immediately before dispatch; false leaves the task parked
without treating the policy refusal as a failed claim.

A nonzero dispatch result raises with its task ID and exit code and pauses
consumption. Any exception or cancellation also pauses it; repeated scheduling
cannot retry that parked claim without explicit resume. Retained bodies and
pins remain with their current recovery owners. Reconstruction and resume do
not repair dirty native state, reconcile partial effects, or infer restore
currentness. Supported retained retry still requires the existing qualification.

Drain pauses this consumer and observes the host's active native operation;
stop permanently closes admission and joins effects. Neither cancels the thread
or revokes its queue pin. Resume refuses an active batch. Keep clients alive
until context exit or stop settles, even after a drain timeout.

The task limit bounds dispatch count, not candidate-queue reads, provider
requests, total elapsed time or native I/O. Selection may inspect parked rows
for the selected source to find an exact binding; it does not scan indexed
content or provider history. This is not a transactional dequeue lease or
cross-consumer scheduling coordinator. The explicit binding below supplies
service construction and notifications; cross-process cutover/adoption and
ordinary v2 execution advertisement remain separate. The CLI and default
ingestion behavior are unchanged.

### Explicit service binding

`server.build_app(point_service_factory=...)` optionally constructs a
`indexing.point_service.PointService` when the service's existing task runner
starts. A passive application does not invoke that factory until promotion.
The binding takes an explicit `IndexConfig`, complete `PointScope`, and
`executor_context`: an async-context-manager factory yielding the synchronous
point executor. That context owns the configured connector and all model
clients, and remains entered until native dispatch and runner shutdown settle.
No callback or new configuration form is serialized.

`sources.azure_devops_point_executor.configured_azure_devops_point_executor(...)`
supplies that context for the configured Azure DevOps caller. Pass the explicit
configuration, legacy directory, point scope, query/recovery scope, finite
`max_rows`, fresh `create_connector` factory and `EmbeddingRuntime`. Entry
opens owned connector/model clients off-loop, closes partial construction on
failure, and joins opening/cleanup even if the caller is cancelled. Borrowed
connector HTTP clients are refused without closing them. Existing direct
`run_configured_azure_devops_points(...)` callers keep owning their resources.
Opening does not fetch provider data, register models in a native store or
request engine readiness. Readiness runs once per needed model in each execution,
immediately before actual missing embeddings, so an engine that expires between
tasks is prepared again when needed. Exit stops only engines reported as started
by this context, once per model, then closes its clients. Higher-covered execution
still qualifies provider eligibility but acquires no bodies and requests no model
work; a completed-task retry stays entirely local.

Alternatively, pass `runtime_context` yielding
`indexing.point_service.PointRuntime` instead of `executor_context`.
`sources.azure_devops_point_executor.configured_azure_devops_point_runtime(...)`
composes the production owned executor with a qualified named-path reader over
the same configuration object, exact point scope, application scope and row
budget. The service validates its configuration/scope and both callables before
installing the pair. Missing, partial or conflicting selections refuse and close
their opened context; supplying both context alternatives refuses construction.
Executor-only callers retain their existing API and do not gain qualified reads.

`await service.read_component(path)` returns the existing
[`ComponentRead`](#selected-named-component-reads) unchanged. Every call opens
and closes its own native connection on the executing thread, configures only
in-memory routing against retained model-table bindings, and uses the existing
non-initializing path owner and current qualification. It never creates native
model bindings, fetches provider data, invokes model readiness or prepares state.
Missing, busy, changed or incomplete evidence refuses rather than reusing an old
observation. Reads can qualify applied state while execution is paused, without
activating or acknowledging queued work. Pause/drain/stop close read admission;
drain shares its existing timeout across reads and workers without cancellation.
Stop joins admitted reader Futures and workers before closing clients, including
repeated caller or loop-wide cancellation. Its completion Future coordinates
concurrent stops through a cancellation-deferred cleanup Task. That task ignores
direct cancellation, including before its first step, while each caller still
receives cancellation after the shared cleanup settles. This protects both
startup rollback and arbitrary caller-owned asynchronous exits, without recording
successful completion while an exit is interrupted.

The point state must already exist in its selected directory, wholly disjoint
from the legacy runner's directory. Invalid explicit configuration fails startup
or promotion instead of silently falling back to legacy execution. There is no
implicit store bootstrap, discovery or engine selection.

Every new binding starts paused, including reconstruction after failure or
interruption. The configuring in-process owner must inspect retained state,
then explicitly `await app.state.task_runner.point_service.activate()` and call
`app.state.task_runner.notify()`. Activation does not repair partial native
effects or certify restore currentness. The existing qualified retry and
recovery owners still govern that decision.

The existing `/reindex/hints` route sends only exact-scope v2 envelopes to the
binding's isolated queue and wakes the existing runner. Other bindings and all
legacy/v1/source-only requests retain their legacy queue behavior. Matching
intake may park work while point execution is paused; notification cannot
activate it. The HTTP contract remains intake v1/v2 with no ordinary v2
execution declaration, selector or activation endpoint.

### Running point-service declaration

`GET /health` separately reports `point_service_contract` with identity
`agent-index.point-service` and integer `version: 1`. Only the installed
production executor/reader composition reports `paired: true`; an unentered
factory, custom `PointRuntime` callable pair or executor-only context does not.
The internal configured-runtime type distinguishes this owned composition; it
is not an attestation boundary against trusted in-process code.

A positive declaration includes `representation: "point-components"`,
`representation_version: 1`, `coverage: "named-target-only"`,
`hints_versions: [2]` and `read_boundary: "in-process-only"`. Its
`scope_fingerprint` is SHA-256 of UTF-8 bytes from the existing complete
point-scope receipt JSON, with that serializer's defaults unchanged. This
nonsecret equality selector covers source, family, account and every binding
field without listing them in health; it is not authentication.

The `state`, `failed`, `execution_admitted` and `read_admitted` fields observe
current lifecycle admission, not applied content, provider eligibility,
governance or model readiness. Paused reconstruction can admit named reads;
explicit activation admits execution. Failure closes execution but does not
invent reader failure. Drain closes both admissions and preserves the failure
flag; rollback never activates reconstructed or failed work. Stopped, passive,
unselected, incomplete and executor-only selections report `paired: false`,
both admissions false and a specific `reason`, without stale scope semantics.
Health opens no stores, locks, provider requests or engines.

`indexing.point_service_contract.require_point_service(...)` is a pure optional
requirement validator, not wired into the existing client. It accepts only the
complete v1 semantics, exact expected scope and suitable lifecycle admission;
missing, malformed, incompatible or rolled-back support refuses with recovery
guidance. Existing source-only and hinted callers remain unchanged. A future
caller must rediscover on peer/generation change using existing service identity,
never replay an uncertain acceptance as legacy work, and retain every
effect-side qualification. Cached positive observations cannot reopen drained
or stopped readers/executors. Caller migration, HTTP reads, general v2 execution,
activation and rollout remain separate.

#### Required hinted-intake admission

The same health object adds `required_admission_versions: [1]` for the
receiver's opt-in request/receipt extension. A declaration-only v1 provider
does not imply this capability. Existing clients remain unchanged.

`POST /reindex/hints` accepts optional `required_point_service_version: 1`.
Presence requires exact integer 1, typed v2 hints, incremental mode and the
complete selected scope; null and coerced values refuse. Only this path
requires the existing matching installation/instance headers, including a
nonempty instance token even for an empty installation ID. The actual
production pair must currently admit execution. Unselected, custom,
executor-only, paused, failed, draining, stopped and changed-scope bindings
refuse before enqueue/notification, never park under legacy semantics.

The point service registers accepted queue work before its first asynchronous
handoff and joins it through drain/stop and request cancellation before closing
resources. Success adds `point_service_admission` with the contract id, version
and complete scope fingerprint, plus existing peer identity and the accepted
typed task union. It confirms admission, not applied content or continued
activation. Lost/malformed confirmation remains an unknown acceptance; never
replay it under an old form. Absent requirements preserve existing paused
v1/v2 retention. See the [reviewed request contract](../../../efforts/active/incremental-indexing-delta-pipeline/point-required-admission.md)
for compatibility and rollback requirements. Caller migration remains separate.

There is no second scheduler, timer or child process. The existing governed
`TaskRunner` iteration executes at most one point claim before its legacy queue
turn, and never starts point work alongside an adopted detached worker.
Governance is checked again after off-loop selection, immediately before
dispatch, so a newly active maintenance hold cannot admit selected work. A
failure pauses point dispatch, records `indexing.point.failed` in `/status`,
and leaves legacy scheduling available. Repeated wakes and `/undrain` do not
clear that failure. `indexing.running` includes an active point batch; the
additional `indexing.point` object reports its running/admitted/paused/failed
state only for explicitly configured bindings.

Drain synchronously pauses admission and reports false if native point effects
have not settled within its budget. `/undrain` can roll back that pause without
activating reconstructed or failed work. Stop closes point admission before its
asynchronous join, including while read-only selection is off the event loop,
and joins the existing iteration before closing the executor/client context.
Cancellation cannot turn a native thread into an adoptable detached worker or
release its clients early. Forced process interruption, cross-generation
adoption, cross-consumer arbitration, native platform qualification and runtime
rollout remain separate; this binding does not promise bounded thread shutdown.

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
For a protocol-2 suffix, supply the same whole typed `point_claim`. The existing
prepared owner requalifies its complete target candidate; the source owner
requires the exact explicit scope and unchanged physical seal. Omitted or
mismatched claims/scopes cannot reopen the writer or repair checkpoints.
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
