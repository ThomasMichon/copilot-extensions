# Reclamation Cohort Contracts

This is the detailed Plan and Validation companion to
[Continuous Worktree Reclamation](README.md), under OPEN #6620. It records
proposed finite receiving work, not runtime completion, accepted ownership,
permission to remove resources or authorization to activate collection.
The effort remains Draft. Planning can proceed without completing Phases 2-5;
tracker retirement still requires the source-specific gates below.

## Ownership and retirement gates

| Original | Retained owner boundary | Required before retirement |
|----------|-------------------------|----------------------------|
| #1488 | Existing task-terminal reclamation custodian; separate from per-attempt #1918 | Reviewed complete Plan/Validation receipt. The existing owner's recorded product/task-terminal disposition remains unchecked; it is not an issue-status veto. |
| #1666 | Ground-layer claimant-liveness, not allocator cleanup or reviewer lineage | Reviewed separate explicit-conclusion Plan/Validation. No separate implementer/transfer is currently recorded; that does not create an owner-ACK or native-completion gate to tracker retirement. |
| #1918 | Same current public assignee, ThomasMichon, for the exact per-attempt lane | Re-record that same assignment, scope and claim in #6620's body and this reviewed plan; preserve the child assignment and source/resource custody. No reassignment or implementation completion is required. |
| #3114 | Existing terminal-reclamation custodian plus non-Dispatch ground-layer owner | Explicit custodian scope acceptance and an actual named open owner for the general ground-layer cases, carrying all nine clauses below. |
| #4101 | Existing claimant is documentation/report-only | Reviewed mapping of all six inherited requirements; name the runtime implementation owner before implementation work, not before tracker retirement. |
| #6060 | Separate semantic conclusion tidier | Excluded from this receiving proposal; its design, authorized pilot and schedule decisions remain in its own effort. |

Keep #3114 open until its literal custodian-acceptance and named general-ground
mapping conditions are satisfied; this amendment does not satisfy them. The
other four receiving lanes may consolidate after their complete reviewed
Plan/Validation is recorded in the real OPEN #6620 parent and read back.
#6060 remains separate. Review is not implementation custody or a release of
any claim, assignee, allocation or resource. Missing native/installed/live
proof and #1488's product disposition remain unchecked independent Validation
work, not prerequisites to administrative tracker retirement.

## Workstream Plan

### Task-terminal allocations - #1488

- Persist the exact created worktree/session identity. The supervisor waits
  for legitimate terminal task resolution, confirmed body exit and explicit
  landed/abandoned disposition before delegating canonical cleanup to Worktrees.
- Preserve exact provenance, inbound/shared-worktree claims, historical
  adoption/status and restart-idempotent, visibly blocked/retryable cleanup.
  Domain artifacts and reservation settlement remain with their producer.
- Do not substitute per-attempt #1918, continuous collection or the narrow
  #9307 conclusion/interface slice for complete task-terminal acceptance.

### Explicit-conclusion claimant liveness - #1666

- An explicitly concluded/stopped headless owner with no live bound session
  and no open activation becomes non-live immediately, without whole-worktree
  finalization or waiting for the aged-dead threshold.
- Crash, cross-machine/remote uncertainty, newer ownership, incomplete
  activation and mismatched identity remain UNKNOWN/live-held.
- Non-live claimant classification is not finality, obligation settlement,
  task takeover, claim release or deletion authority. Keep #1733 reviewer
  lineage, #7202 cleanup identity, #1488 and #1918 separate.

### Per-attempt allocations - #1918

- Retain the same current public assignee, ThomasMichon, and existing exact
  per-attempt source claim in both this plan and #6620's receiving body.
  Tracker retirement preserves the child assignment and every source/resource
  responsibility; it neither transfers nor broadens that implementation lane.
- Persist exact worktree/session/task/reservation/attempt/driver and creating
  host/environment provenance before body execution and reservation settlement.
- Separate proactive cleanup of a still-present exact pristine allocation from
  reactive confirmed-absence reconciliation. Preserve targeted/reused,
  dirty/unmerged, live, foreign and legacy-unknown allocations.
- Retain durable session identity, explicit held-allocation recovery,
  newer-generation fences and visible retryable cleanup. A missing bridge
  handle, age or zero-looking count does not establish absence.
- Never reset/expire/delete held rows automatically, lose allocation identity,
  retry completed business work or absorb #1488/#1666 into this assigned lane.

### Inbound claims and historical adoption - #3114

Carry the complete nine-clause acceptance, independently of outward
session-claim release:

1. Release only the terminal allocation's exactly attributable inbound claim,
   through the existing lifecycle, exactly once across both relation sides,
   retries and restarts; never blanket-release a worktree's claims.
2. Preserve every multi-claim obligation. All other nonterminal allocations
   must be settled or the complete inbound set must participate in atomic
   ground admission. Concurrent add/resume wins; age/unknown data is not release.
3. Require legitimate producer/domain resolution and current goal/effort
   disposition, not an enum or merged PR. Preserve unfinished objectives,
   active efforts, follow-ups, handoffs, PRs, dirty/unmerged/unique work,
   live/uncertain bodies, unknown origin, foreign host, targeted/reused spaces
   and reciprocal knowledge-pair/sibling-head obligations as a supported unit.
4. Preserve exact allocation/session/worktree/task/reservation/attempt/driver
   and creating machine/environment provenance. A supervisor PID replacement,
   transient process or timer does not erase the durable resource owner.
5. Integrate release, eligibility and removal through the existing lifecycle,
   with atomic fresh canonical liveness/claims/follow-up/upstream admission;
   preserve remaining obligations on failed settlement. No parallel prune ledger.
6. Make crash/retry idempotent: no double release, orphaned obligation, lost
   identity, repeated destructive operation or replay of completed business
   work to clear housekeeping. Failures stay visible with owner/reason.
7. Keep attributable historical adoption/status queryable after reclamation.
   Unknown legacy allocations require report-first inventory and explicit
   per-worktree attestation/adoption, not inferred ownership or bulk removal.
   Missing checkout is not missing obligation.
8. Retain the measurable exact-once, multi-claim and queryable-history cases,
   including concurrent resume/add, pairs, unknown/foreign/dirty/live holds and
   crash/restart under the existing native/validation owners.
9. Map general terminal-worktree cases outside Dispatch-created allocations
   to an actual named open ground-layer owner before retirement. Do not narrow
   the original to Dispatch or expand assigned #1918. Preserve #6060 and
   #6471/#6542 boundaries; collection cannot supersede allocator settlement.

#3401/#3696 are documentary phase transfers; #7202's bounded identity coverage
and an outward session-claim helper are not complete acceptance of these rows.

### Objective-first prune classification - #4101

Preserve all six inherited #2649 requirements:

1. Distinguish new clean commits/conversation-only work after finalization
   from genuinely landed squash and valid zero-commit artifacts.
2. Choose an authoritative finalize-time/source-evidence rule rather than a
   stale FINAL/COMPLETED label or a clean working tree.
3. Retain DIRTY/GONE/ACTIVE handling and every owner/claim/follow-up fence.
4. Wire fresh classification into batch cleanup, `reap_one` and automatic
   finished-session sweep before any masking decision.
5. Reverse historical `cleanable=True` regressions without rejecting valid
   landed or zero-commit cases.
6. Keep landed source, objective completion, accepted transfer and deployment
   proof separate. Documentation/report-only ownership grants no runtime or
   cleanup authority.

#6145/#9175 are guidance and #8346 addresses holder teardown; none substitutes
for this classifier. Preserve allocator and semantic-pilot ownership.

## Trusted-folder lifecycle

#6620 also retains these explicit lifecycle obligations:

- Keep the trusted-folder entry when finalization retains the checkout.
- Atomically reopen finalized worktree state on a supported resume or mutating
  obligation operation while retaining/restoring trust; remove the trust entry
  only after confirmed safe removal.
- Repair stale trust only with evidence, preserving permissions, claims,
  pair custody and existing finality. Unknown/missing paths are not permission
  to release responsibility or remove a workspace.

## Validation Plan

All cases are planned and unchecked; execute only within separately authorized
implementation/native/resource bounds.

| Workstream | Required assertions |
|------------|---------------------|
| #1488 | Terminal landed/abandoned exact allocations, stopped/live body, dirty/unmerged, shared claims, foreign/unknown identity, blocked cleanup, retry/restart and historical report-first status; no domain-artifact/reservation cleanup or other claimant release. |
| #1666 | Closed activation plus no live bound body becomes non-live immediately. Live/newer owner, crash ambiguity, remote/unreachable, incomplete activation and identity mismatch stay held. Replacement task-generation/reservation behavior creates no duplicate worker or unauthorized takeover, claim release or removal. |
| #1918 | No-handle pristine-present, already-absent, live, dirty/ahead/untracked, unreadable/unknown, foreign-host, newer racing attempt, lost bridge handle and supervisor restart. No identity loss, success-shaped unknown/failure, lease/claim expiry or business-work replay. |
| #3114 | One exact inbound release across both sides/retry/restart; multiple claims and concurrent add/resume cannot orphan/double-release. Partial removal, pair, foreign/unknown owner, non-Dispatch allocation, report-first adoption and post-reclaim history stay attributable/queryable. Verify all nine clauses and actual general-ground owner mapping. |
| #4101 | Source/squash-aware rule on all three callers, valid landed-squash and zero-commit artifacts, conversation-only evidence, stale/restarted projection, post-finalize new clean commit, concurrent mutation and lock-held final action admission. A FINAL/COMPLETED label or report-only audit cannot bypass objective/claim/source revalidation. |
| #6620 trust | Finalize then supported resume restores config/marketplace/hooks/skills; finalize-cleanup, repeat finalize, failure, stale/missing path and concurrent resume/cleanup preserve permissions, exact claims/pair custody and finality. |

Each lane retains independent acceptance. Runtime source, native process/lock,
released, installed and live consumer evidence are distinct; a passing narrow
fixture or a documentation checkbox cannot claim a higher tier.

## Separate responsibilities

- #6060 and [Worktree Conclusion Tidier](../worktree-conclusion-tidier/README.md)
  retain semantic candidate selection, actual objective inspection, narrowly
  authorized bookkeeping, durable objective preservation and downstream-claim
  follow-up. Bounded candidates/budget, per-item receipts, collisions,
  pause/disable and starvation belong there before apply capability.
  Design/charter completion, an explicitly approved bounded pilot with chosen
  candidates/evaluator/report destination/budget, and any daily/default
  schedule are separate gates. Report-only audit/source progress grants none.
- #6471/#6542 retain held-housekeeping recovery and pair progress. Previously
  completed source work is provenance, not new closure credit or cleanup consent.
- [Performance](performance.md) remains a measurement proposal: Phase 2 baseline
  first; proposed 5x throughput and <=10% p95 regression targets are not current
  operator acceptance thresholds.

## See Also

- [Effort Plan and Validation Plan](README.md).
- [Standing reclamation vision](../../../visions/plugins/agent-worktrees/continuous-reclamation/README.md).
