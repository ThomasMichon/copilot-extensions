# Azure DevOps point-component contract proposal

Back: [acquisition proposal](azure-devops-acquisition.md#composition-decision-and-next-proof)
and [canonical effort](README.md), Phase 3. Owner:
[#13386](https://github.com/ThomasMichon/copilot-extensions/issues/13386).
This is an **agent-recommended contract**. Shared typed-target intake now retains
parked v2 claims with v1-only queue selection; component execution and full
read-surface admission remain unsupported. An explicit
[named-component read](../../../plugins/agent-index/docs/component-ledger.md#selected-named-component-reads)
qualifies one already-applied typed point in a selected domain; it does not
enable other read surfaces. Explicit
[native application and sealed-suffix reconciliation](../../../plugins/agent-index/docs/component-ledger.md#explicit-native-application)
do not enable either boundary. The shared
[component recovery carrier](../../../plugins/agent-index/docs/architecture.md#component-recovery-carrier-application-unsupported)
retains per-mutation ordering inputs and refuses changed replacement, premature
completion and application. Pure version/content comparison supplies no durable
applied-version or deletion proof. The
[provider evidence matrix](azure-devops-acquisition.md#work-item-target-coverage-evidence)
remains authoritative: named item revisions and comment versions have positive
point-read evidence; a complete combined discussion does not.

This is vision-closing under [good-citizen ingestion](../../../visions/plugins/agent-index/README.md#good-citizen-ingestion):
bound work to qualified changed targets while keeping acquisition, application
and compatibility in the existing shared owners.

## Representation decision

Use an explicitly selected **point-components v1** representation, not a
partial replacement of the existing combined document. It records the newest
successfully applied named version of each observed component, not a complete
current item/discussion snapshot or a historical-version collection.

The existing `_work_item_entry` / `_format_work_item` in
`plugins/agent-index/src/agent_index/sources/azure_devops.py` writes item fields
and comments together at `workitems/<id>.md`. Keep that identity, rendering,
source selection and legacy acquisition unchanged. Do not parse its Markdown
back into a trusted component base, replace it with a body-only entry, or copy
its content into new components without qualified acquisition.

The proposed representation uses a separately configured canonical source
family and stable component paths within it:

| Component | Relative identity | Complete replacement covers |
|-----------|-------------------|-----------------------------|
| Work-item body | `workitems/<item>/body.md` | Fields of that exact item revision, never its discussion |
| Named comment | `workitems/<item>/comments/<comment>.md` | That comment's exact version, including its deletion state |

Bind representation/version, effective endpoint/account, query union and current
definition/context to recovery identity. A representation change is not an alias
for an existing family or permission to reuse its pending manifests/cursor.
Use the shared `FileEntry` source/path identity, source-aware application and
existing progress owners, not another connector store or indexing engine.

Legacy readers continue seeing exactly their existing dataset. New component
reads require explicit selection of the new representation and report component
identity, applied version and **named-target-only coverage**. They must not
silently join both representations, present a component as a complete item, or
advertise all current comments. Admission stays off until every exposed read
path enforces this separation; a different source suffix alone is insufficient
because an unfiltered search could still expose both. Source configuration and
read selection must be reviewed together before implementation is enabled.

This is not a migration or a default repair path. Existing combined content,
foreign-source rows and pending work remain intact. Replacement of the legacy
reader representation would require a separately qualified complete base and
an explicit migration/rollback plan; this proposal supplies neither.

### Read-isolation design

The selected **agent-recommended design** is one cold, separately owned
source-aware data domain for one exact point scope. It extends the existing
`store.source_layout.SourceDomain` and configured `IndexConfig.data_dir`, not
another indexing engine, namespace registry or recovery owner. This section
defines the complete admission target. Exact protocol-2 domain binding,
default refusal and an explicitly selected named-component read are implemented;
point application and every-route compatibility qualification remain open.

Do not put the combined and component representations in the same domain and
rely on a source suffix, caller filter or content-table name to separate them.
Ordinary search can be unfiltered, model profiles select vector tables
independently of the content-table name, and cluster reads reduce a source
filter to a bucket. A separate content table alone therefore isolates neither
candidate retrieval nor the derived artifact.

#### Configuration and retained identity

An explicit point configuration selects a fresh data directory, a distinct
canonical component family and the complete existing `PointChangeHints.scope`:
configured source, family, account and `PointBinding` (representation/version,
effective endpoint, query union and query context). Do not infer this selection
from a path suffix, connector label or chunk metadata. Preserve legacy source
configuration, source hashes and combined rendering.

Bind that exact selection in a versioned source-layout receipt before
application, and compare it with the existing pending/prepared/application
scope on reopening. Use a new receipt protocol, not an optional field on
protocol 1: the current protocol-1 parser tolerates unknown fields and would
not enforce their meaning. A changed scope requires a separately initialized
domain, not rebinding, clearing pending work or borrowing a polling cursor.
The receipt identifies the representation; it supplies no acquisition,
application, permission or completion authority.
The qualified point writer must admit only that exact component family and
bound scope; ordinary combined-source ingestion cannot populate this domain.

Keep the configured domain's content, model tables, cluster artifact, path
checkpoints and pending/prepared state separate from the legacy data directory.
Reuse their existing owners and coherent-state/restore requirements. This
storage selection neither requires nor authorizes a second daemon, a new
service, a directory allocation on a live installation or a volume migration.
Cold initialization must refuse nonempty/unmarked state and incompatible
existing bindings without altering them.

The explicit in-process
[cold schema bootstrap](../../../plugins/agent-index/docs/source-aware-store.md#point-domain-binding-foundation)
can now prepare the configured empty content/model tables for that exact scope.
The existing [ledger/index preparation owners](../../../plugins/agent-index/docs/component-ledger.md)
also accept that same explicit selection while retaining cold-ledger refusal,
index budgets and dirty/uncertain recovery. Neither preparation seam admits
ordinary data writers or implements selected point application.

#### Default refusal and explicit reads

Keep every existing reader's default selection on the legacy representation.
An ordinary `ContentStore`, `MultiModelStore`, `ClusterStore` or search-engine
opening of a point domain must refuse before returning candidates, documents,
members or measured inventory. Apply the same rule to nested and lazy reads;
an earlier successful open is not permission to ignore a changed receipt.
Retain the existing source-domain exclusion across retrieval, hydration and
qualification. Protocol-1 readers must reject the new receipt, while current
unselected readers retain their marked-domain refusal. Binaries predating the
source-layout protocol are still excluded by the existing isolated-directory
lifecycle rule; a new marker cannot fence such binaries.

Start with an explicitly selected **in-process** component-read boundary.
Its exact scope must match the configured domain. Reuse shared search,
document and cluster owners rather than copy their algorithms. Return a
component-specific result, never an ordinary legacy-shaped result with an
optional annotation that callers can silently discard. Each exposed component
identity reports its kind, parent item, optional comment ID, applied provider
version, representation/rendering semantics and `named-target-only` coverage.
An empty result or inventory also identifies that selected representation and
coverage; it is not evidence that the full item/discussion was enumerated.

Derive applied-version claims through the existing component application,
ordering and projection owners under the same read exclusion. Ledger
observations or serialized chunk metadata alone are not applied-content proof:
the selected scope/policy, canonical identity, current native point, path
checkpoint and routed-model coverage must agree. Missing or conflicting
evidence refuses the affected read. A proven ordered deletion remains absent
from searchable content, without discarding its ordering record or inventing
legacy deletion authority. Bound per-result qualification through the existing
point budgets; do not turn a query into a corpus-wide certification pass.

The read-path inventory and acceptance obligations are:

| Existing owner or surface | Required separation before point execution admission |
|---------------------------|------------------------------------------------------|
| `ContentStore` / `MultiModelStore` candidate retrieval, FTS, hydration, direct documents and enumeration | Default opens refuse the point domain; explicit reads qualify component identities without joining legacy rows |
| `SearchEngine.search`, lexical fallback, `find_similar` and authorized fleet retrieval | Candidate and reference-ID lookup stay in the selected domain through qualification; family authorization still applies independently |
| `ClusterStore` list, direct cluster/item lookup, members and statistics | Guard the domain even without a search-engine instance; qualify every exposed member or refuse, never rely on bucket filtering |
| HTTP search/similar/clusters/status, MCP formatting, local/remote CLI and TTY rendering | Existing forms remain legacy-only and refuse point-domain access; do not leak a component through a legacy result or an available/empty status fallback |
| Consumer store/search wrappers and direct Python callers | Preserve the default refusal through the actual shared constructor/decorator path; consumer presentation is not permission to strip required component semantics |

No new HTTP parameter, CLI flag, MCP tool or fleet wire form is selected by
this in-process design. Those surfaces remain unsupported for point reads
until a separate provider-first contract declares and enforces their required
semantics against the actual executing peer. An old peer ignoring a parameter
is not support; do not probe and fall back to a legacy read of the new domain.
Keep receipt protocol 1 and all legacy forms supported with unchanged meaning;
no retirement or automatic conversion is proposed.

The domain binding/default-refusal seam and explicit named-component result
reuse existing native owners. Before admitting any
v2 executor or producer, exercise the inventory above with synthetic combined
and component domains: unchanged legacy results, both cross-domain refusals,
unfiltered and lexical reads, wrong reference IDs, direct hydration, bucket-
collapsed clusters, empty/unknown inventory, receipt/scope drift, missing or
stale application evidence, deletion and bounded qualification. Reopen retained
state in the compatibility cases. Small pure/transport contract controls
belong in required unit CI; native directory/process and restore cases stay in
the existing explicitly selected bounded lane. Unsupported routes may refuse;
they may not return a partially qualified legacy-shaped success.

## Typed target claims and completion

`ChangeHints` v1 has opaque item IDs, metadata revisions and event IDs; it has
no component-kind or coverage semantics. Do not encode a comment target into
those strings or reinterpret existing queued work. Propose **change-hints v2**
through the existing `agent-index.change-hints` contract, retaining v1:

- A point-only request contains typed work-item revision targets or typed
  comment-version targets, each with explicit parent/item identity and positive
  integer revision/version. It carries the selected representation and exact
  source/account/query binding. Unknown kinds or mixed broad v1 acquisition
  hints are refused, not dropped.
- An item target covers only that revision's fields. A comment target binds
  item, comment and version to the exact request path and validated response.
  An associated `commentVersionRef` may supply a separate named target after
  validation; it does not turn the item target into a discussion manifest.
- Union only equal scopes/representations/query bindings. Preserve distinct
  versions and correlation through the existing queued-union/trailing-work
  owner. New arrivals never expand an already claimed immutable target set.
- The entire claimed set must reach durable application or an explicit
  supported already-covered result before successful task completion.
  `AcquisitionPage` v1 can still carry complete component `FileEntry` units;
  its terminal cursor certifies only the typed claim, not a polling interval.
  Retain the ordinary polling checkpoint. Partial, unsupported, cancelled or
  failed work cannot become successful acknowledgement of the claim.

  The shared queue now exposes an explicit
  [parked whole-envelope pin](../../../plugins/agent-index/docs/architecture.md#point-only-v2-intake-execution-unsupported).
  It isolates trailing arrivals and retains an immutable claim identity across
  retry, but supplies no target coverage or completion receipt. Binding this
  identity and the entire typed target set is available through the explicit v9
  pending/prepared carrier, which retains intent rather than target coverage.
  The disconnected named-point reader can qualify one exact item revision or
  comment version using the existing query/account owner and documented point
  APIs; it returns an observation, not an acquisition page or applied record.
  Retaining and qualifying every target's durable acquisition/application outcome
  and acknowledging only that exact current claim remain prerequisites; the v2
  execution and terminal-success fences stay closed.

The existing owners are `indexing/change_hints.py` and
`change_hints_contract.py` for intake/negotiation, `task_store.py` and
`task_hints_schema.py` for durable union/claim isolation, `hinted_crawl.py` and
`paged_crawl.py` for exact callable selection and bounded progress,
`pending_crawl.py` for recovery, and `worker.py` for terminal completion
(all under `plugins/agent-index/src/agent_index/`). Extend those owners.
The current boolean v1 dequeue opt-in is not v2 support: old claimants must not
claim, decode, complete or block unrelated v1 work behind a v2 record.

No automatic conversion of broad work-item events is selected here. A producer
must establish exact targets from qualified evidence before v2 intake. Successful
component processing does not retire an original v1 item/event claim, certify
delivery of other events, or discharge an uncovered discussion mutation.
Query exit, permission denial and a 404 are not component deletion authority.
Ambiguous eligibility or changed query context refuses the affected claim;
retain it for explicit repair rather than acknowledging a filtered-out target.

## Ordered application and deletion

Stable component paths require version ordering, not last-arrival-wins.
The shared application owner must compare validated provider versions for the
same scoped component before any content/vector deletion or replacement.
A lower version is already covered only when durable evidence proves a higher
qualified version of that same component was applied. Equal versions require
matching canonical content/rendering semantics; conflicting content refuses.
Do not compare revisions between different items, comments or accounts.

Retain the applied version even when a named comment version proves
`isDeleted`: remove only that point component's searchable content, preserving
an ordered deletion record so an older retry cannot resurrect it. This is not a
work-item tombstone, query-exit deletion, complete discussion reconciliation,
or permission to remove content in the legacy family. A later positively
qualified version follows the same ordering, without inferring restoration
from absence. Physical work-item deletion remains unsupported here.

Use the existing shared pending/path/application ownership and store-before-
checkpoint sequence for this evidence; do not hide progress in connector
metadata alone or invent a second queue. Crash recovery must reconcile the
applied content/vector state and version evidence before acknowledging a
retry. A missing/conflicting ordering record is not proof of no prior write.
Bound each point read and rendered payload, count actual attempts/bytes, and
pause/refuse oversized work rather than introduce an unlimited nested fetch.

## Expand, migrate, retain compatibility

Apply [contract evolution](../../../docs/patterns/service-contract-evolution.md)
before realizing the new intake, retained records, rendering or read semantics.
Expand the existing receiver and shared queue/progress/application/read owners
first; keep v1 callers and the combined representation unchanged. Advertise
intake and execution separately from the actual selected runtime/callable,
including explicit support for point-component semantics, v2 hints and paged
acquisition. Existing page-v1 or hint-v1 metadata is not that declaration.

Only then enable an explicit caller for a compatible selected source and reader.
Missing support refuses before new-mode effects, without trying legacy
acquisition, writing both representations, or silently stripping targets.
Retained v2 claims and component ordering/deletion records need a versioned
decoder and an old-worker exclusion path before the first producer is enabled.
Compatible newer executors may resume them; do not pin unresolved work forever
to its original build. Supported rollback preserves those records and refuses
unsupported point operations without preventing unrelated legacy operations.
No v1 form, legacy source or stored content is retired by this proposal.

### Configured executor ownership before caller migration

The explicit service binding and native service count controls compose the
owners. The reusable configured executor context now supplies production
connector/model ownership; the embedding application still selects it
explicitly. This is not a serialized selector or a blanket v2 execution declaration.
This is an **agent-recommended implementation refinement** of the composition
gate below, not new source activation or rollout authority.

Keep the existing `build_app(point_service_factory=...)` and `PointService`
boundaries. Supply a production context that binds the existing configured
Azure DevOps point acquisition caller to its exact `IndexConfig`, `PointScope`,
query/recovery scope and finite row budget. The embedding application supplies
the connector selection and embedding policy explicitly; do not discover a
source, credential, model or engine from a different runtime or ambient default.
Reuse `EmbeddingRuntime`'s owned-client and deferred-readiness policy rather
than introducing another lifecycle manager. Acquire fresh connector/model
resources on context entry, close partial construction on failure, and keep
them alive until the existing joined worker/service shutdown settles.

Opening this context must not fetch provider data, start an embedding engine,
activate parked work, repair state or bootstrap a point domain. Reject an
invalid binding before dispatch. Request model readiness only for actual
missing embeddings, not for covered targets or completed retries; stop only
engines this context actually started. Existing explicit callers that already
own connector/client lifetimes remain supported unchanged.

Qualify the production context through the existing service fixture instead of
reconstructing its executor in the test. Cover paused startup, exact-scope
intake, one admitted named-target execution, higher-covered completion with
eligibility-only requests and no body acquisition or model work, and entirely
local completed-task retry. Also cover lazy readiness, partial-construction
cleanup and joined shutdown. Keep small lifecycle contracts in unit CI and
real native owner execution in the existing opt-in lane; do not repeat the corpus-size
matrix merely to prove this resource-ownership change.

This step leaves health and intake receipts' execution declarations unchanged.
A subsequent caller-migration step must separately select the actual point
binding and explicit reader, declare point/v2/paged semantics truthfully, and
exercise missing support and provider rollback before enabling a new wire
caller. Neither an owned executor nor callable metadata alone establishes
that a running service's selected binding and reader satisfy those semantics.

### Selected executor and qualified-reader composition

This reviewed **agent-recommended refinement** stays inside the existing
`build_app(point_service_factory=...)` / `PointService` Python binding. Compose
the configured executor above with the existing
`indexing.component_read.read_component` owner as one explicit selection, not
two independently supplied callables whose metadata happens to agree. This
contract changes no health, intake receipts, queue eligibility, HTTP routes or
other wire form. The explicit
[paired implementation](../../../plugins/agent-index/docs/component-ledger.md#explicit-service-binding)
realizes the selection independently of later wire-caller migration.

The configured selection binds one `IndexConfig`, exact `PointScope`,
application/recovery scope and finite qualification row budget. Its resource
context supplies both the actual executor and a named-path reader closed over
that same binding. `PointService` must validate agreement with its own configured
domain and scope before installing the pair; reject missing, conflicting or
partially constructed selections before dispatch or reader exposure. Keep the
existing executor-only constructor and caller-owned context behavior unchanged,
but do not treat that alternative as qualified reader support. Select either
the existing executor-only context or the paired context, never silently prefer
one when both are supplied. No package-version check, callable attribute or
separate capability registry substitutes for composing these owners.

The reader takes only a named component path within that selected binding; it
cannot override the domain, source, application scope or budget per call.
Delegate every result to `read_component` under its existing domain operation:
canonical typed identity, current ordering/native content, path checkpoint and
routed-model evidence must agree. Return its component-specific `ComponentRead`,
including applied version, deletion and `named-target-only` coverage, unchanged.
Unknown paths, changed receipts/scopes and incomplete or conflicting evidence
remain explicit refusals, never an empty legacy-shaped success. Do not retain
an earlier qualification as permission for a later read.

Open only existing native state needed by this operation through its current
owners; do not prepare schemas/indexes, create native model registrations, repair coverage,
enumerate a discussion or invoke acquisition/embedding readiness for a read.
Configuring in-memory model-table routing must validate existing retained bindings,
not create or repair native tables.
Keep thread-affine native handles within their owning operation and close them
on success or refusal. Paused intake/recovery is not application evidence:
starting or reading must neither activate retained work nor acknowledge it.
Reads may qualify already-applied state while execution is paused, but refuse
once shutdown closes admission. Join any admitted reads as well as workers
before closing the selected context's resources, including cancellation paths.

Qualify this composition through the existing native service fixture rather
than a test-only executor/reader pair. Cover one admitted exact execution
followed by the selected qualified read, an ordered deletion, a fresh paused
reopen, stale/missing application evidence and wrong binding/receipt refusals.
Prove no provider requests, model readiness or preparation from reader entry;
retain existing legacy-domain and unselected-route refusal controls. Add focused
pure controls for mutually exclusive contexts, binding mismatch, partial setup
and joined reader shutdown. Reuse the bounded opt-in native lane, without
repeating the unrelated-corpus-size matrix or adding native effects to unit CI.

This pair is only an in-process composition prerequisite. Execution remains
explicitly activated, every other read surface remains unsupported, and
ordinary health/intake declarations remain legacy-only. Before any new wire
caller or support advertisement, separately review the actual running-service
declaration and missing-support/provider-rollback contract against the selected
pair and the read-path inventory above. Missing paired support must preserve
retained v2 state and unrelated legacy operations; it must never trigger legacy
acquisition of a point claim or reinterpret component data as combined content.

### Running-service declaration and provider rollback

This **agent-recommended refinement**, reviewed in PR #13707, expands the
receiver only. It follows the
[service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
and uses the existing `/health` response from the actual running application,
not another discovery command, registry or preflight process. The
[receiver implementation](../../../plugins/agent-index/docs/component-ledger.md#running-point-service-declaration)
adds the declaration and a pure optional requirement validator, without
opting an existing client into it. No new caller, request field, read
route, activation endpoint or automatic recovery is admitted by this step.

Keep `indexing_contract` and its v1 execution meaning unchanged. Its v2 intake
list means durable retention, not point execution. Use one independent,
optional `point_service_contract` object with stable identity
`agent-index.point-service` and integer `version: 1`. Version 1 describes only
the selected in-process paired executor and named-component reader:

| Declaration | Meaning |
|-------------|---------|
| `paired: true` | The application's selected `PointService` successfully entered and installed the qualified executor/reader composition above; factory presence, arbitrary callable pairs, executor-only selection and callable metadata do not suffice |
| `scope_fingerprint` | SHA-256 of UTF-8 bytes from the existing complete point-scope receipt serialization; binds source, family, account and every `PointBinding` field without listing endpoint/account/query values in health |
| `representation: "point-components"`, `representation_version: 1`, `coverage: "named-target-only"` | Exact component semantics, never a complete item/discussion or current collection |
| `hints_versions: [2]`, `read_boundary: "in-process-only"` | Selected typed-target execution and the existing Python reader; not general v2 engine support, HTTP reads or `AcquisitionPage`/`SnapshotPage` qualification |
| `state`, `failed`, `execution_admitted`, `read_admitted` | Current lifecycle/admission observations, separate from structural support and never activation or applied-content evidence |

Use the existing receipt scope serialization unchanged, including its JSON
encoding defaults; version any future incompatible fingerprint encoding rather
than guessing equivalence. The fingerprint is a nonsecret equality selector,
not authentication or an application certificate. Preserve the existing
service identity beside this object. Consumers must select the same running
peer through existing discovery, invalidate observations on peer/generation
change, and compare the complete expected scope before choosing this feature.
Never infer support from package-version equality or a newer installed slot.

Only a fully entered, selected paired context can publish `paired: true` and
its scope/semantics. Missing, passive, not-yet-started, partially constructed,
executor-only or stopped bindings report `paired: false`, both admission flags
false and a specific unavailable reason; they must not retain an earlier
positive selection. When health remains available without such a binding, use
`state: "unavailable"` (or `"stopped"` after stop); a startup exception still
fails startup rather than manufacturing successful legacy fallback.
A paired service starts in `state: "paused"` with execution
not admitted; its already-applied named reads may be admitted. Explicit
activation can report `"active"`. Failure reports `"failed"` and closes
execution admission without inventing read failure or clearing retained work;
read admission follows the actual reader lifecycle. Pause/drain closes both
admissions and reports `"draining"`; stop reports `"stopped"` without support.
Drain rollback restores only the existing lifecycle behavior, never activation
of reconstructed or failed work. Keep the explicit failure observation distinct
through the separate `failed` flag when drain temporarily closes admission.

Build the declaration from the runner's installed live selection and lifecycle,
not the factory or module-global intake helper. Reading health must not open
native stores, acquire a source lock, certify rows, fetch provider data, request
model readiness, prepare state or start work. A declaration says which owners
are selected, not that any target is applied, the provider is currently eligible
or a model is ready. Existing effect-side scope, domain, governance, budget,
recovery and per-read qualification remain mandatory after discovery. A stale
positive health response cannot bypass pause, shutdown or a changed binding.

Expand this receiver declaration before a separate caller-migration review.
Until that review, existing hinted intake still parks v2 under its current
rules; no caller gains execution guarantees by sending the old request form.
Future callers requiring this selection accept only version 1 with all required
semantics, matching scope and suitable admission. Missing, malformed,
out-of-range, unpaired or mismatched declarations mean unsupported point
requirements: refuse that operation before sending an effectful new request,
with the actual missing contract and supported recovery (select a capable
service, or wait for explicit retained-state inspection/activation). Unknown
additive declaration fields may be ignored; unknown required versions or
changed field meanings may not. Do not retry an uncertain accepted request
under legacy syntax or reinterpret it as a combined-document acquisition.

Support old callers on the expanded provider unchanged, and support rollback
to the current pre-declaration provider by this explicit feature-level refusal,
not by rejecting legacy indexing/search or preventing runtime replacement.
Retain v2 claims, pending parents, correlations and uncertain outcomes across
rollback; an old executor must neither dequeue them nor enrich/relabel their
evidence. Restoring a capable provider starts paused and uses the existing
retained-recovery owner. Keep older-than-source-layout binaries excluded from
the isolated point directory under the existing lifecycle rule. No old form or
retained revision is retired here; any future retirement requires a separately
reviewed upgrade/rollback window and disposition of unresolved work.

Qualify the eventual declaration through the real HTTP serialization seam:
old caller/new provider retains legacy health/intake meaning; new selection on
a paired provider sees the exact scope and named-only/in-process semantics;
new caller/prior provider and provider rollback refuse the optional requirement
without effects, legacy starvation or loss of retained v2 work. Cover missing,
malformed and unsupported declarations, passive/unselected/executor-only and
partial startup, paused/active/failure/drain/rollback/stop transitions, changed
peer/scope and stale positive observations. Pure lifecycle/reader-selection
contracts and small HTTP fixtures belong in required unit CI; reuse the existing
opt-in native service/reopen lane to prove retained-state and per-read guards.
Do not repeat the corpus-size matrix, introduce live-provider calls or move
native work into unit CI. The contract was reviewed before implementation;
wire callers, broader paged acquisition, activation and rollout remain separate.

### Request-bound required admission

The [receiver-first request refinement](point-required-admission.md) carries an
optional point requirement onto the existing strict hinted intake and confirms
its selected-peer/scope admission in the receipt. This separately reviewed
contract must precede implementation and caller migration: pre-declaration
providers already accept the old v2 form as retained-only work, so health
discovery alone cannot preserve required semantics across rollback.

## Implementation and validation gate

1. Implement the shared typed-target intake, version-aware queue selection and
   retained compatibility controls before any Azure DevOps producer. Then add
   ordered component application and explicit read isolation under those owners.
2. Qualify the adapter only for documented exact item/comment versions and the
   admitted finite query subset. Validate endpoint/account/query/parent/target
   bindings and budgets before returning successful pages. Keep the existing
   combined renderer and observation helper disconnected from this capability.
3. Compose the actual caller, queue, worker, pending recovery, native stores
   and reader before advertising point support. Small pure mixed-version and
   ordering contracts belong in required unit CI; native composition remains
   in the existing opt-in lane, not a new unconditional matrix.

| Focused control | Required assertion |
|-----------------|--------------------|
| Positive named targets | Exact item revision and comment version update only their selected component; no discussion enumeration or legacy-row mutation |
| Binding and coverage refusals | Wrong parent/account/query/identity/version, missing references and unsupported broad events preserve claims, cursor and good rows |
| Reordering and deletion | Newer-before-older, duplicate, equal-version conflict and delete-before-old-edit cannot regress content or resurrect a deleted component |
| Crash and partial acquisition | Interrupt around store/version/checkpoint/terminal publication, reopen the real owner, retain immutable claims and trailing arrivals; no false successful completion |
| Mixed peers and rollback | V1/new, v2/new and new/prior combinations preserve old semantics or refuse before effects; an old claimant neither consumes v2 nor starves legacy work |
| Read compatibility | Existing combined-reader results stay unchanged; every new read route selects the component namespace explicitly and reports incomplete collection coverage |
| Query and resource limits | Entry/union checks stay candidate-scoped; changed context, ambiguous exit, cancellation and exhausted request/payload budgets refuse without full acquisition |

Repeat the existing 100/10,000/100,000 unrelated-item count qualification through
the actual consumer path; selected target counts must remain equal. Point
application is not proof of complete event delivery, current-discussion coverage,
ordinary polling, lost-event repair, PR/thread coverage or bounded full
reconciliation. Those requirements remain open in the canonical Plan and
Validation Plan. No source activation, live provider read, subscription change,
repair, deployment or rollout is authorized or reported by this proposal.
