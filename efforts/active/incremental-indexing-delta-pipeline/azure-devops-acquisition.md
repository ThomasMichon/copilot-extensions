# Azure DevOps bounded acquisition proposal

Back: [canonical effort](README.md), Phase 3. Coordination:
[#13363](https://github.com/ThomasMichon/copilot-extensions/issues/13363).
This is an **agent-recommended proposal**, not implemented capability, provider
qualification, permission to change subscriptions, or rollout evidence.

## Evidence and decision

The legacy `indexing/crawl.py` path calls `list_paths()` before
`discover_changed()`. `sources/azure_devops.py` returns no complete project
checkpoint; its work-item timestamp path still enumerates configured queries.
PR creation/closure dates do not cover edits and comments. Correct response
validation and complete offset traversal preserve compatibility, but do not
make ordinary acquisition finite or resumable.

The provider documents three different mechanisms:

| Mechanism | Documented support | Limit on the conclusion |
|-----------|--------------------|-------------------------|
| [WIQL by text](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/wiql/query-by-wiql?view=azure-devops-rest-7.1) / [saved query](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/wiql/query-by-id?view=azure-devops-rest-7.1) | `$top` limits returned results; `asOf` identifies the query's execution context. | Neither alone supplies a complete change feed, resumable query cursor or immutable membership. A limited prefix cannot authorize deletion. |
| [Reporting revisions](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/reporting-work-item-revisions/read-reporting-revisions-get?view=azure-devops-rest-7.1) | `$maxPageSize`, watermark-style continuation, `isLastBatch`, optional deleted items and discussion-only revisions. | Its documented filters are not arbitrary WIQL membership; work-item coverage is not PR/thread coverage. A project feed would broaden acquisition beyond the configured queries. |
| [Service-hook events](https://learn.microsoft.com/en-us/azure/devops/service-hooks/events?view=azure-devops) | Work-item create/update/delete/restore/comment events and PR create/update/comment events identify changed subjects. | Event names do not prove coverage of every edit/delete variant. [Probation loses new events](https://learn.microsoft.com/en-us/azure/devops/service-hooks/troubleshoot?view=azure-devops#probation); hooks alone cannot certify complete polling coverage. |

[WIQL semantics](https://learn.microsoft.com/en-us/azure/devops/boards/queries/wiql-syntax?view=azure-devops)
also prevent a blanket changed-ID intersection: time macros can change
membership without an item revision, group/context changes can affect selection,
and link queries depend on other items. `ASOF` is not compatible with tree
queries. A saved-query ID does not freeze its definition or execution context.

**Choose bounded exact-identity event acquisition first**, preserving the
polling checkpoint. Admit only query semantics whose eligibility can be proved
with finite candidate-scoped work. Do not enable a project-wide reporting feed,
rewrite every query into a timestamp predicate, or present event delivery as a
complete ordinary polling implementation. Broader query membership, lost-event
repair and PR change coverage remain explicit Phase 3 requirements.

This closes the [agent-index vision's](../../../visions/plugins/agent-index/README.md#good-citizen-ingestion)
event-led, query-scoped ingestion gap. It follows the patterns'
[right-sized surface and explicit failure](../../../docs/patterns/README.md#design-principles):
extend the existing connector/intake owners, not another service or scheduler.

## Work-item target coverage evidence

Qualification owner:
[#13386](https://github.com/ThomasMichon/copilot-extensions/issues/13386).
These conclusions distinguish documented point reads from proposed composition;
they do not advertise a new adapter capability. A bounded event delta needs
coverage of its **declared claimed target**, not an atomic snapshot of every
source object. The target must nevertheless cover everything that a successful
write replaces or that acknowledgement would retire.

| Target class | Positive provider evidence | Remaining coverage condition / current disposition |
|--------------|----------------------------|---------------------------------------------------|
| One work-item revision | [Revisions Get](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/revisions/get?view=azure-devops-rest-7.1) returns a fully hydrated item at the requested `revisionNumber`. Its `commentVersionRef` is a reference to the specific comment version added/edited/deleted in that revision. | This supports a named item-revision point read and an associated comment-version reference when present. It does not by itself enumerate the discussion or establish that every discussion mutation advances the item's current revision. |
| One named comment version | [Comments Versions Get](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments-versions/get?view=azure-devops-rest-7.1), API `7.1-preview.1`, addresses parent item, comment ID and version explicitly. `CommentVersion` includes text, version and `isDeleted`. | This supports a named comment-version point read, including that version's deletion state. It is not a manifest of all comments; the documented response model has no parent `workItemId`, so the request path supplies that binding. A comment's deletion state is not a work-item tombstone. |
| Current comment / paginated discussion | [Get Comment](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments/get-comment?view=azure-devops-rest-7.1) and [Get Comments](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments/get-comments?view=azure-devops-rest-7.1), API `7.1-preview.4`, expose current comment versions and optional deleted comments; the list supplies continuation and counts. | These operations document no item-revision or `asOf` input that fences the list. A terminal page, stable count, matching item-body reads or per-comment versions alone does not establish a complete discussion at one target. Keep the existing combined item-plus-discussion replacement unqualified. |
| Discussion edit/delete linked to an item revision | The item revision model explicitly associates `commentVersionRef` with comment add/edit/delete. [Update Work Item Comment](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments/update-work-item-comment?view=azure-devops-rest-7.1) returns a versioned comment; [Delete](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments/delete?view=azure-devops-rest-7.1) documents comment deletion. | The association is useful positive evidence, not proof of an exhaustive ordered mutation ledger. Reconstructing a whole discussion requires a complete trusted base plus proof that every admitted mutation between base and target is represented, with bounded resumable acquisition. No such reconstruction is qualified here. |
| Work-item create/update/delete/restore/comment event | [Service-hook events](https://learn.microsoft.com/en-us/azure/devops/service-hooks/events?view=azure-devops) declare `workitem.created`, `workitem.updated`, `workitem.deleted`, `workitem.restored` and `workitem.commented`. | Event names and example `id`/`rev`/history fields do not establish exhaustive edit/delete delivery or an exact comment-version binding. Validate the admitted event form and positive provider evidence before acknowledgement; opaque hint revisions/event IDs are not event-kind or tombstone authority. Physical deletion and restoration remain separate qualification gates. |
| Complete event/polling coverage | The hooks identify subjects, while the existing proposal records probation loss and query/feed limits. | A successful point-target acquisition does not certify complete event delivery, query membership across time or ordinary polling coverage. Lost events, query-definition/context changes, non-item membership and PR/thread coverage retain their separate repair/qualification requirements below. |

The revision schema marks `WorkItemCommentVersionRef.createdInRevision`,
`isDeleted` and `text` as **internal**. Do not promote those fields into an
adapter-wide fence or a supported public reconstruction protocol. The
documented `commentId`/`version` pair is still useful; neither ignoring that
evidence nor assuming a complete collection from it is justified.

### Composition decision and next proof

The existing work-item renderer writes one document containing the item and
its discussion. It cannot safely replace that document using only a qualified
item point read or one comment version while silently dropping the rest.
Two matching fresh item bodies around a finite comment traversal remain an
**observation**, not a qualified `SourceDelta` or `AcquisitionPage`. The
finite-observation helper therefore stays disconnected from discovery.

Before that boundary changes, qualify one of these paths through the existing
shared owners:

- **Combined-document target:** obtain an authoritative provider guarantee
  covering the complete discussion being replaced. A proposed revision-linked
  reconstruction must establish an exhaustive mutation-to-revision mapping,
  a complete base, exact version reads, deletion/restore semantics and a finite
  resume protocol. Samples, UI history and a stable response are not that proof.
- **Explicit point-target representation:** if only named item/comment versions
  can be qualified, first review the shared document-identity, rendering and
  acknowledgement contract needed to represent those targets without replacing
  uncovered content. Preserve existing indexed content and reader behavior,
  use negotiated old/new compatibility, and do not silently introduce a
  body-only projection or a connector-private progress store. The
  [point-component proposal](azure-devops-point-targets.md) selects an explicit
  separate representation, typed claims and version-ordered application; it
  does not enable that representation or migrate existing sources.

In either path, bind endpoint/account, query union and current
definition/context, candidate identity and the precise claimed target. Refuse
an unrelated or inconsistent revision/reference/version before a successful
page or hint acknowledgement. A newer provider response may justify later
trailing work; it cannot silently expand or retire the claimed target.
Preserve the ordinary polling checkpoint, foreign-source paths and pending
work on an unsupported or incomplete result.

Focused qualification must include the positive exact-revision/comment-version
case and negative cases for missing references, wrong parent/identity/version,
concurrent discussion edits/deletes, incomplete bases, query exit versus
physical deletion, cancellation and exhausted per-unit budgets. Synthetic
transport tests prove local controls, not undocumented provider guarantees.
No live provider reads, subscription changes, source repair or rollout are
authorized by this evidence matrix.

## Dependency-ordered implementation

1. **Qualify finite candidate selection before enabling it.** Start with flat,
   item-local scalar predicates and literal values. Define an explicit supported
   grammar; preserve the operator's original query and boolean grouping.
   Candidate restriction must be a documented, semantically equivalent derived
   query, not textual `AND` concatenation or an implicit configuration rewrite.
   Saved queries require a current definition/context binding and the same
   qualification. Reject links, time/group/custom macros and unrecognized
   constructs from this initial capability; keep their legacy full behavior
   available separately. No query specification still means no acquisition.
2. **Apply exact hints through existing contracts.** Validate source/account,
   subject identity, event kind and revision before provider acquisition.
   Evaluate the union of configured queries against the finite candidate set.
   Fetch only eligible changed bodies and their required comments. An item
   leaving one query but remaining in another is not a deletion. Query exit
   requires positive, complete eligibility evidence; a physical deletion
   requires an explicit qualified tombstone. Permission failures and 404s alone
   prove neither. Preserve the existing polling cursor; duplicate and active-
   round hints use the shared durable queue union/trailing-work owner.
   `ChangeHints` v1 carries identities and opaque revisions/event IDs, not event
   kind or tombstone authority. Obtain positive provider evidence or evolve the
   shared contract before supporting deletion semantics; never reinterpret
   opaque hint values as that evidence. Do not advertise this capability until
   the shared bounded-hint path below is qualified.
3. **Resume acquisition through the shared owner.** Use `AcquisitionPage` v1
   and `PendingCrawls` generation/CAS, with the existing 128-unit invocation
   ceiling and 65,536-byte cursor ceiling. Currently `collect_source_changes`
   selects pages only without hints; hinted acquisition returns one
   `SourceDelta`. Extend that same selection/progress owner for bounded hints
   before wiring the adapter, retaining nonpaged hinted callers and the
   immutable claimed hint union. Pin effective endpoint, account,
   query definitions/context, candidate IDs/revisions and target coverage.
   Count actual identity, metadata, item-body and comment-page requests
   separately. Bound candidate count, HTTP pages/attempts and comment work
   within each unit: one item with unbounded comments is not a finite unit.
   Persist only complete units before continuing; retries reopen progress
   rather than relist completed work. If the existing envelope cannot express
   necessary partial-comment progress, evolve that owner before advertising
   support; do not hide another journal inside the connector.
4. **Qualify broader coverage independently.** Before adding ordinary polling,
   document a provider-supported base-to-target coverage proof for every
   admitted query/event class, including discussion edits/deletes, restoration,
   query-definition changes and non-item membership changes. Reporting
   revisions may be useful only if source selection is preserved without a
   broad project crawl; it is not the selected default. Qualify PR edits and
   thread mutations separately from work items. Unsupported classes report the
   actual gap and request explicit repair; never fall back to unlimited
   enumeration inside a supposedly bounded round.
5. **Keep authoritative reconciliation distinct.** Bootstrap, missing/expired
   cursors, lost-event recovery and unsupported membership require visible,
   separately budgeted repair. Use the existing snapshot/reconciliation owner
   only when the adapter can prove its completeness requirements. Mutable PR
   offsets and a terminal HTTP page are not immutable family-wide coverage.
   Partial scans retain prior paths/checkpoints and authorize no unseen-item
   deletions. Until provider-correct resumable reconciliation is qualified,
   report it as unsupported rather than declaring this parent gate complete.

The first implementation PR owns step 1's query-classification/selection
controls, not a new polling cursor or subscription activation. Subsequent
source PRs claim bounded slices through this same effort. Keep code cohesive
in provider helpers rather than growing the connector past its module cap.

## Safety and compatibility acceptance

Add focused synthetic transport/collector tests to the existing Azure DevOps
files; add only genuinely new cases, retaining the already-qualified malformed-
response, pagination and body-validation controls.

| Case | Required result |
|------|-----------------|
| Local scalar selection and overlapping queries | Match independent full-query expectations for candidate entry, exit and union membership; zero acquisition outside candidates. Exercise precedence, quoted literals, saved definitions and empty configuration. |
| Unsupported or changed membership semantics | Links, time/group/custom macros, changed saved definitions/context and unknown syntax cannot silently become the supported subset. Preserve original configuration, good paths and cursor; identify explicit repair needs. |
| Equal timestamp/revision overlap and concurrent mutation | Deduplicate exact identities without losing distinct revisions; newer responses cannot acknowledge uncovered changes. Inconsistent selection/body evidence refuses destructive conclusions and retains retry/trailing work. |
| Delete, restore, query exit, 403 and 404 | Qualified tombstones affect only the intended indexed source/item. Restore can re-enter; a union member survives. Denial, missing details or a limited query prefix never become absence evidence. |
| Item/PR comments and oversized work | Exercise edits/deletes and missing coverage explicitly; one subject with more comment pages than budget pauses/refuses with durable progress, not an unlimited nested loop or successful truncated body. |
| Crash, cancellation and repeated/expired continuation | Reopen the actual shared pending state before and after each complete unit. Keep store-before-checkpoint, pinned target, generation/CAS and scope checks; no repeated completed-unit acquisition or automatic cursor discard/reset. |
| Hook loss or unavailable change coverage | No completeness claim from an empty inbox; missing coverage remains visible and selects explicit repair rather than a full ordinary crawl. |
| Legacy and bounded capability selection | Existing explicit full acquisition retains query semantics. A caller requesting bounded work gets the declared supported form or a truthful unsupported outcome, never a successful legacy downgrade. |

Compose the admitted path with the shared worker/engine and private native
stores in the existing opt-in lane at 100/10,000/100,000 unrelated items.
Require equal finite request/page/identity/body counts, clean-reference selected
content/vector results and unchanged foreign-source sentinels. Event replay
performs no embedding, identical writes or derived maintenance. Measure
checkpoint/path lookups and cursor/envelope growth separately; the shared
128-unit ceiling is not proof of bounded total manifest memory or network
payload bytes. Required CI retains small pure contract cases, not this whole
native matrix.

This planning PR changes no external protocol. Realization must use the
existing negotiated delta/hint/page capabilities where sufficient and follow
[contract evolution](../../../docs/patterns/service-contract-evolution.md)
before changing retained state or cross-process forms: provider first,
conditional caller migration, supported old/new and rollback controls.
Legacy compatibility is not proof of bounded enforcement.

The exit gate is qualified bounded acquisition **and** separately qualified
coverage/repair for the supported source modes, not a parser test, a merged
proposal, or the absence of a hook notification. Existing #4222/#4220/#8733/
#5302 owners, consumer qualification and deployment gates remain unchanged.
