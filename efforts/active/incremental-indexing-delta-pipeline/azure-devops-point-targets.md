# Azure DevOps point-component contract proposal

Back: [acquisition proposal](azure-devops-acquisition.md#composition-decision-and-next-proof)
and [canonical effort](README.md), Phase 3. Owner:
[#13386](https://github.com/ThomasMichon/copilot-extensions/issues/13386).
This is an **agent-recommended contract**. Shared typed-target intake now retains
parked v2 claims with v1-only queue selection; component execution and read
isolation remain unsupported. Explicit
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
specifies the next implementation; it does not claim that a component reader,
configuration selector or new layout protocol is implemented.

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

Implement and qualify the domain binding/default-refusal seam first, then the
explicit component result and its existing-owner reads. Before admitting any
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
