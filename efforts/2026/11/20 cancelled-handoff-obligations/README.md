# Cancelled Handoff Obligations

## Goal

Qualify and, only where needed, correct #4619 OS3-OS4/OSV2, received from
#10372: cancelling an eligible unconsumed task-backed handoff reconciles
that same task's originating creator obligation after its actual Dispatch
owner confirms the terminal outcome. Preserve rightful ongoing ownership
and ordinary finalization gates.

- **Slug:** `cancelled-handoff-obligations`
- **Repo:** copilot-extensions
- **Branch(es):** serial plan, source and acceptance PRs
- **Created:** 2026-11-20
- **Status:** Done
- **Vision:** [Worktrees claims and obligations](../../../../visions/plugins/agent-worktrees/README.md#claims-leases-and-obligations), [Handoff cancellation](../../../../visions/plugins/context-handoff/README.md#extension-independent-recovery)
- **Umbrella issue:** #4619, finite OS3-OS4/OSV2 only

## Guiding Intent

An acknowledged cancellation must not strand its exact creator obligation.
Task terminalization, pending-handoff retirement and resource-ledger
settlement remain distinct outcomes. Reuse their existing owners, expose
partial failure and support targeted recovery without pretending that an
absent pending entry or successful subprocess settled a resource.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Finite source qualification, correction if needed, PRs and acceptance | Issue #4619 reservation; source codename `patient-knob` |

## Coordination

- **Topology:** serial source-worktree PRs; no new worker.
- **Host (owns PRs):** Source driver.
- **Delegates:** none.
- **Handoff:** ordinary current-owner continuation; no resource transfer.
- Keep the existing BOTH-store abort/arm atomicity owner, all CodeSpace
  terminal-settlement, retained-job/lease and live-use custodians independent.
  The accepted OA observation lane is not reopened. #6109, #5675 and #3442
  remain separate. The whole parent stays Open.
- Credit existing Dispatch asynchronous handoff release, post-creation
  terminal reconciliation and Worktrees' exact settlement/mutation fences.
  No new general task release rule, state store, coordinator or service.

## Context

#10372 records an unconsumed manual-only handoff, explicit cancellation,
authoritative abandoned/zero-attempt/no-owner/no-started-execution state,
no armed candidate and a still-active creator obligation blocking finalize.
Its subordinate tracker is administratively subsumed, not fixed.

Current `abortHandoffTask` uses a pre-abandon ledger/candidate check,
expected-status abandonment and later pending-ledger cancellation.
Dispatch already schedules a best-effort terminal release in a background
thread. Its source explicitly documents a multi-project targeting
limitation. The handoff creator also has a post-creation terminal-race
reconciler. These existing mechanisms require measured qualification;
neither their existence nor the historic report proves today's full
cancellation-to-finalization result.

## Request

Original #10372 expected behavior:

> Successful cancellation should reconcile the same exact task-backed handoff's creator obligation once the owning dispatch service confirms its terminal state. Cancellation and claim settlement should remain safe and idempotent across partial failure. Unknown, claimed, running or contradictory owner state must retain protection rather than force-release a resource.
>
> Add a focused lifecycle regression covering save/trigger, unconsumed abort, owner-terminal confirmation, creator-claim reconciliation and normal finalization. Preserve PR merge checks, genuine outstanding task obligations and existing takeover/ownership gates; do not make finalize ignore task claims.

The original requires no successor, shared daemon restart, task takeover or
worktree abandonment. This effort covers source and disposable consumers
only, not an operation on the historically reported live task.

## Scope Approvals

Source diagnosis, bounded corrections, contained fixture validation and normal
PR publication only. The public reservation is
[7029605985](https://github.com/ThomasMichon/copilot-extensions/issues/4619#issuecomment-7029605985).
No live task abort, claim settlement, finalization, provider/resource operation,
process/service repair, installation, cleanup or rollout is authorized.
The reviewed plan does not grant those effects.
After the causal adapter witness, the operator selected
`recorded-creator-binding`: record the exact creator binding in existing
handoff metadata; older tasks without sufficient proof remain protected until
an explicitly owner-qualified retry. The unique-current-owner-discovery
alternative is not selected. This selects the finite source/legacy contract,
not authority for live task or runtime changes.

## Plan

### P1 - Establish the actual cancellation outcome

- [x] Exercise the real save/trigger and manual-only unconsumed abort
  consumer against disposable task and Worktrees owners. Observe the exact
  originating creator claim and ordinary finalization, not merely mocked
  release argv. Credit the existing asynchronous and creation-race paths.
- [x] Qualify same and different project contexts, including a coincidentally
  matching worktree ID. Establish any causal counterexample before production
  correction; do not infer an owner from the caller/coordinator cwd alone.

### P2 - Complete exact closeout without weakening custody

- [x] If a counterexample remains, correct only its owning cancellation/
  settlement seam using existing owner-qualified operations. Require actual
  task-owner terminal confirmation and preserve exact claim identity,
  candidate and record-mutation/handoff/producer fences. Do not change
  BOTH-store abort/arm ordering or general task-responsibility admission.
- [x] Make partial cancellation/settlement outcomes explicit and a safe
  targeted retry idempotent. Unknown, mismatched, claimed/running,
  contradictory or replaced state retains the obligation; no blind replay,
  forced release or new source of authority.
- [x] Clear focused real-consumer regressions, affected suites, whole-file
  strict checking where Python changes, contract/documentation guards,
  exact-source qualification and normal review/required CI before merge.
- [x] Read the complete current parent and discussion before accepting only
  proved OS3-OS4/OSV2 clauses at their actual evidence tier. Complete only
  the original finite reservation; preserve all other rows/custodians.

## Validation Plan

- [x] V1: Save/trigger -> eligible unconsumed manual-only abort -> actual
  owner-confirmed terminal state -> exact originating creator claim settled
  -> ordinary finalization succeeds in a disposable fixture, with no successor.
  Preserve completed-PR checks and separately genuine unsettled obligations.
- [x] V2: Same/different project and wrong-owner/ref controls preserve every
  unrelated record. Include matching leaf IDs, target mismatch, absent or
  uninspectable owner and changed candidate/claim generation at the boundary.
- [x] V3: Failed/unknown abandon, claimed/running/contradictory state,
  partial settlement failure and supported retry retain truthful separate
  outcomes. Retry must not re-abandon or settle another resource.
- [x] V4: Keep existing async release, creation-race reconciliation,
  file-backed/manual/off behavior, exact candidate checks and ordinary task
  responsibility semantics intact. Retain all applicable no-live-effect and
  unchanged-state controls.
- [x] V5: Focused fail-first proof for any correction, affected source suites,
  lint and complete-file strict analysis where applicable; qualify the actual
  published source on supported disposable platforms, then genuine current-head
  review and required CI. Distinguish source evidence from productive
  provider/native/installed acceptance.

After these finite source gates resolve, mark this effort Done, archive it by
completion date through its own normal PR and release only its completed focus.
Archive publication is a closeout action, not a pre-Done checkbox.

## Proposal

This closes the existing Worktrees creator-obligation and extension-independent
handoff cancellation intent, not a new lifecycle architecture. The
[patterns](../../../../docs/patterns/README.md) require independent plugin operation,
shared owner authority, truthful uncertainty, economical evidence and parity.
Use the smallest demonstrated correction after P1. A callback retry is not
proof of settlement; a new receipt protocol, registry, polling worker or
general terminal-task release policy is not assumed necessary.
The [creator-routing decision](creator-routing-decision.md) records the
measured cross-project gap, available evidence and the legacy compatibility
choice before a production correction.

## Journal

- [2026-11-20 - Source proposal, creator-binding decision, qualification and finite acceptance](journal/2026/11.20-source-qualification.md)
