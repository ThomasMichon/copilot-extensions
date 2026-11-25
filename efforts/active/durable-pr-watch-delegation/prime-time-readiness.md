# Prime-Time Readiness Ladder

Back to [Durable PR-Watch Delegation](README.md#prime-time-readiness-ladder-agent-recommended).

A facility adopter asked on 2026-11-24 to plan the remaining
agent-pull-requests gaps, priorities and deliverables with intermediate
validation, so that each layer of the system is shown to work before higher
layers rely on it.

The gap assessment, priorities and layering below are **agent-recommended**.
They organize existing Plan and Validation Plan items; they do not replace,
close or narrow any of them, and they grant no new effect authority. Every
existing approval, hold, capacity limit, review and deployment gate still
applies.

## How to read a layer

Each layer names one deliverable and an intermediate validation that must pass
before work above it relies on that layer. Evidence is recorded per tier and
never promoted across tiers:

| Tier | Meaning |
|------|---------|
| **S** source | Reviewed, merged source with focused tests |
| **R** release | Packaged/promoted version that contains the source |
| **I** installed | Running on an adopter host through the normal lifecycle |
| **L** live | Actual provider/consumer behavior observed on the installed service |

A layer is **passed** only at its stated exit tier. A lower layer that later
regresses reopens; higher layers stop relying on it until it passes again.

## Ladder

| Layer | Priority | Deliverable | Intermediate validation (exit tier) |
|-------|----------|-------------|-------------------------------------|
| **L0 Intent** | - | Reviewed plan: this ladder and the [unresolved-query recovery contract](native-github-snapshot-batches.md#proposed-explicit-unresolved-query-recovery) | Both merged through ordinary review (S) |
| **L1 Observation** | P0 | Continuing provider observation: released [selected-member packing](native-github-snapshot-batches.md) plus implemented unresolved-query recovery | Every enrolled PR reaches a fresh snapshot within existing request/resource limits; fair progress with a deliberately slow target; restart or interruption retains original unknown records and charges while a qualified graceful-owner transition admits future observations (L) |
| **L2 Service lifecycle** | P0 | One installed service path: client -> gateway -> service -> consumer | Install, restart, upgrade and rollback keep one writer and the durable ledger; credential withdrawal refuses rather than falls back; installer exit does not end service lifetime (I, then L) |
| **L3 Reviewer feed** | P0 | [Reviewer next-action feed](reviewer-action-feeds.md) driving a standard dispatch reviewer recipe | Reviewer-owned enrichment and retained-task wake qualified without fabricated scheduler rounds or ownership; pinned-candidate comparison explains every disagreement; one complete installed consumer workflow runs end to end (L) |
| **L4 Write workflows** | P0 for writes | Author/reviewer flows: review-workspace load, incremental sync, exact source, protected submission and verified readback | One real eligible review covers each step, including a changed-head race and a partial/unknown write that is reconciled, never replayed (L) |
| **L5 Provider conformance** | P1 | Operation x provider matrix for GitHub, Gitea and Azure DevOps | Each cell backed by evidence from its own provider; comment origin, edit and recovery included; no cross-provider credit (L per cell) |
| **L6 Front door** | P1 | One dependable CLI | [Legacy-command parity inventory](cli-parity-inventory.md) before any deprecation; durable PR-primary association; arbitrary-working-directory operation with correct caller custody; ambiguous GitHub identity resolved or refused, not ambient (S, R, then I) |
| **L7 Callbacks and state** | P1 | [Subscriber-owned callback context](subscriber-callback-context.md) and [safe historical-state transition](subscriber-state-transition.md) | Callback context negotiated, persisted and honored across restart; older writers cannot damage newer state (S, then I) |
| **L8 Release decision** | - | Honestly scoped release statement | L1-L3 passed at their exit tiers; L4-L7 status published as supported/unsupported, not implied (S docs, R) |

**Not release blockers** (agent-recommended): optional auto-merge automation,
presentation polish, push delivery, legacy-command removal and exactly-once
effects. They remain tracked work, not gates for a narrower release.

## Ordering

Work proceeds bottom-up: L1 before L2/L3 reliance, L3 before write-enabled L4.
L5-L7 may progress in parallel where they do not depend on an unpassed lower
layer. Recovery source for L1 follows the reviewed recovery contract; it does
not reinterpret original unknown records as completed queries.
