# Azure DevOps point-component contract proposal

Back: [acquisition proposal](azure-devops-acquisition.md#composition-decision-and-next-proof)
and [canonical effort](README.md), Phase 3. Owner:
[#13386](https://github.com/ThomasMichon/copilot-extensions/issues/13386).
This is an **agent-recommended contract**. Shared typed-target intake now retains
parked v2 claims with v1-only queue selection; component execution, application
and read isolation remain unimplemented. The shared
[component recovery carrier](../../../plugins/agent-index/docs/architecture.md#component-recovery-carrier-application-unsupported)
retains per-mutation ordering inputs and refuses legacy downgrade, premature
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
