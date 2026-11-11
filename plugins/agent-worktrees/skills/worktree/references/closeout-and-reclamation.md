# Closeout is not reclamation

Parent: [worktree guide](../SKILL.md).
Procedures: [outbound obligations](obligations.md) and
[cleanup details](cleanup-details.md).

## Logical completion

A worktree answers for unsettled work before it finalizes. Existing `at-rest`
semantics allow a resource to persist without blocking completion merely because
its bytes remain. Preserve effort, PR, protected-pair and accepted-transfer gates;
do not clear unfinished responsibility with a generic release.

Finalization does not require deletion. Physical reclamation is a separate
explicit cleanup decision, initiated by an agent or the user under applicable
scope and retention policy. Record finalized-retained storage honestly.

**Current runtime compatibility:** installed `finalize` implementations may
also remove an eligible idle checkout as part of their existing lifecycle.
This guidance does not change those implementations or introduce a retain-only
flag. Inspect the selected command's actual behavior and preview. If logical-only
finalization is required but unavailable, keep the checkout and report that
unsupported boundary rather than hand-editing state or running deletion anyway.
An authorized removing closeout still uses the managed owner, never raw removal.

## Artifact disposition and history

Use the artifact/storage owner's supported contract. Temporary data, reusable
caches, execution history, durable knowledge and delivered user content have
different lifetimes. Publication or verified delivery can close custody without
deleting the destination; record the result in history, not another permanent
destination-artifact ledger.

Agendas and journals belong in owner-resolved local history outside disposable
checkouts. Existing checkout-local history remains protected until owner-led
rescue/disposition; an ignored path is not automatically disposable or archival.
Checkout retirement, consistent archive collection and history deletion remain
separate decisions. A backup or copy attempt is not accepted transfer or completion.

## Explicit cleanup across repositories

The parent remains responsible for actual child work, but finalizing a parent
does not require deleting safely finalized children. A cleanup pass must discover
known retained child allocations in their real repositories as well as root
checkouts. Active-obligation queries intentionally stop at settled edges and
cannot prove the absence of finalized-retained disk allocations.

Use existing owner-qualified records and supported discovery; do not invent a
new registry, scan arbitrary source roots or treat missing/partial evidence as
an empty footprint. Ask each resource owner to preview/revalidate its candidate.
Preserve live sessions, unlanded source, open/unknown PRs and unknown history.

Report these independently: objective complete; worktree finalized; checkout
retained or removed; history preserved; outstanding work resolved/transferred.
A successful command or terminal tracking label is not proof every directory
was removed. An independent blocked child need not prevent safe cleanup of a
different admitted sibling.
