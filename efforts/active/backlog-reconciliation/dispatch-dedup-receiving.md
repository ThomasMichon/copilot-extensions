# Dispatch exact-dedup receiving

Back to [Backlog Reconciliation](README.md).

## Proposed route and effect boundary

Add #11912's complete exact-dedup/detail regression contract as independent
section 9 in existing OPEN #11674. This is a review-first tracking proposal,
not an actual issue amendment, accepted source retirement or technical pass.

The receiver already retains independently fixable Dispatch query/retry,
admission/dedup and lifecycle-accountability fixtures. This one-row response
projection belongs to that bounded fixture-contract qualification, without
claiming a common defect or replacing any incumbent lane.

Preserve all eight incumbent sections, 31 unchecked rows, 35 reported cases,
discussion/history, owners, literal alternatives, platform requirements and
global Plan/Validation. Add one unchecked row and one reported case:
**nine lanes, 32 unchecked rows and 36 reported cases**.

Besides the complete insertion below, replace only these four numeric tokens
in the existing aggregate summaries:

| Existing context | Before | After |
|---|---|---|
| Global validation row/case total | 31 rows / 35 cases | 32 rows / 36 cases |
| Explicit OPEN/unchecked summary | 31 rows / 35 cases | 32 rows / 36 cases |

These are eight replaced ASCII bytes. No other incumbent text is rewritten.
The historical source enumeration stays historical; the direct new section
identifies #11912 and supplies its own Plan/Validation.

## Complete direct receiving addition

Insert this section immediately before the single
`## Planned work and validation exit` heading:

```markdown
## 9. Exact dedup/detail read-only projection regression — #11912

**Scope and ownership.** Retain this independent Dispatch API/fixture lane alongside section 6's pending-conclusion stand-in and section 7's admission/dedup availability contracts. It concerns one exact dedup lookup's response projection, not the installer helper closure, a shared root cause, or a request to change all task-list projections. Existing Dispatch task/API and fixture owners retain implementation responsibility; this receiving addition appoints no implementer and releases no claim or resource.

The original Windows/Python 3.12 report at `58e5547d01` identifies `tests/test_pending_verification_dedup.py::test_pending_verification_dedup_keeps_actual_submission_and_read_only_detail`. Its exact-equality assertion, `response.json() == [expected]` at the reported line 75, differs only in `task_obligation`. `GET /tasks/{id}` calls `enrich_task_detail(...)` from `task_obligations.py`; `GET /tasks?dedup_key=...` returns `_task_with_spawn_dict(...)` from `coordinator_tasks.py` without that enrichment. The detail enrichment arrived after the exact-equality test contract. The report's suggestion that impact-selected CI omits this test is a coverage hypothesis to qualify, not proof of cause or permission to widen required CI indiscriminately.

**Original alternatives.** At the owning revision, choose either:

1. Make the exact dedup lookup return the same enriched detail, retaining “lookup equals detail” and limiting the additional read-only obligation snapshot to at most one row; **or**
2. If the exact lookup is intentionally a lighter projection, narrow the fixture comparison to exclude the detail-only `task_obligation` field while comparing everything else.

Do not silently choose an alternative through consolidation. Neither route may remove the `PRAGMA query_only` read-only assertion or turn this lookup into a queue mutation.

- [ ] `tests/test_pending_verification_dedup.py::test_pending_verification_dedup_keeps_actual_submission_and_read_only_detail` passes against the explicitly selected enriched-one-row **or** deliberate lighter-projection contract, retaining all other response/submission comparisons and the `PRAGMA query_only` assertion. Qualify the original Windows/Python 3.12 failure boundary and retain the exact owning revision, selector, chosen alternative and result; a neighboring API pass is not acceptance of this case.

**Plan and validation.** First qualify the response/fixture drift and the reported impact-selection gap at the existing owner's current revision. Decide the intended exact-lookup contract without prescribing a larger product refactor. Make the smallest appropriate source or fixture correction under the alternatives above, then validate the named case using the repository's bounded, appropriately contained path on the reported Windows/Python 3.12 boundary. Retain the read-only database guard and submission/detail semantics; record unavailable platform evidence as a gap, not a pass. Correct any demonstrated selection omission through the existing change-scoped portfolio owner without restoring an exhaustive required-CI lane.

**Non-goals and receiving acceptance.** No task/queue operation, send replay, installed repair, runtime update, native execution or cleanup is authorized by this tracking amendment. Implementation and execution need their existing authority. Planned receiving does not wait for this test to pass: after actual acceptance of this complete lane, the original source tracker can be separately assessed for redundant-tracking retirement, not technical completion. This receiver remains OPEN while its row is unresolved.
```

## Complete source crosswalk

| #11912 obligation | Direct receiving clause |
|---|---|
| Windows/Python 3.12 failure at `58e5547d01`, exact selector and line-75 assertion | Evidence paragraph and acceptance row retain the original boundary and exact failing comparison. |
| Only `task_obligation` differs; detail enrichment versus exact lookup; enrichment postdates the fixture | Evidence paragraph names both paths/functions, the single differing field and chronology; Plan first qualifies the drift. |
| Possible impact-selected CI omission | Evidence and Plan preserve a hypothesis to qualify through the existing portfolio owner, not a proven cause or blanket CI expansion. |
| Enrich lookup to equal detail, with at most one additional read-only snapshot | Original-alternatives item 1 and the acceptance row directly preserve that branch. |
| OR deliberately compare every field except detail-only `task_obligation` for a lighter lookup | Item 2 and the acceptance row preserve the conditional alternative rather than choosing an API design. |
| Keep `PRAGMA query_only` | Guard paragraph, acceptance row and Plan make the read-only assertion invariant. |
| Preserve response/submission semantics and focused correction | Scope limits the change to one exact lookup; Plan requires the selected source-or-fixture correction, exact revision, selector and result. |

## Distinct Windows budget contract

#11995 remains on its original token. Its Worktrees prepared-membership/native
write budget-intent question is not the Dispatch projection contract. The
study considered a separate cross-plugin lane, but this proposal deliberately
does not extend the Dispatch receiver on the basis of shared Windows/timeout
vocabulary or fixture machinery. Its two families, original alternatives,
phase evidence and existing Windows owners remain independent.

This leaves section 8's Linux #11745 wake/history lane and section 4's
different native-Windows helper/materialization cases untouched. The earlier
[accountability receiving](dispatch-accountability-receiving.md) remains
historically accurate; no #11745 effect is replayed or declared technical
completion.

## Publication and acceptance

1. Land this complete proposal through normal review.
2. Before any issue effect, refresh complete source/receiver qualification,
   current bodies/edit identities and owner conditions. Reconcile a changed
   incumbent instead of overwriting it from this capture.
3. If still qualified and compatible, amend #11674 with the reviewed section
   and four count updates. Read back the entire actual body, verify all eight
   incumbent sections/31 check states and the new unchecked row. Read-then-write
   checks are not an atomic provider precondition.
4. Only after actual direct receiving acceptance, separately adjudicate
   #11912's redundant-tracking retirement with a source-specific explanation
   and verified `CLOSED` / `NOT_PLANNED` state, not `COMPLETED`.
5. Journal the actual effect through review. Keep #11674 OPEN with every
   unresolved row and original implementation/resource custodian.

Complete admitted originals/discussions establish no explicit source claim,
mandatory release token or conflicting source-OPEN condition for #11912.
The receiver's OPEN-until-supported-results condition remains binding.
This does not invent an extra personal sign-off, appoint an implementer,
authorize execution or release a resource.
