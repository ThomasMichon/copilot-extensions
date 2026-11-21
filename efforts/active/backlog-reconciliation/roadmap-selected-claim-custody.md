*The following contribution was assisted using Copilot.*

# Selected Current Claim-Custody Contribution

Parent: [Backlog Reconciliation](README.md), Phase 2b.
Selection: [original nominations](roadmap-next-slices.md).
Integration: [scoped sequencing](roadmap-sequencing.md).
Evidence and delivery: [dated journal](journal/2026/11.21-current-claim-custody.md).

## Current residual and credited protections

This is an agent-recommended bounded contribution for OPEN #5842, not a
source assignment, claim operation or whole-outcome receiving decision.
The selected source is `df851c316cb5103b290c7da74bf6a68c1e7c8c25`.
Nine actual file-changing publications after the admitted `86a0145` baseline
were qualified before the changed claim-handoff/finalization source was read.
Complete current issue/discussion metadata was stable and qualified.

The ordinary [local and remote acceptance branches](https://github.com/ThomasMichon/copilot-extensions/blob/df851c316cb5103b290c7da74bf6a68c1e7c8c25/plugins/agent-worktrees/src/agent_worktrees/claim_handoffs.py#L618-L681)
still call source settlement before consumer completion.
[Source settlement](https://github.com/ThomasMichon/copilot-extensions/blob/df851c316cb5103b290c7da74bf6a68c1e7c8c25/plugins/agent-worktrees/src/agent_worktrees/claim_handoffs.py#L234-L317)
removes the source claims, saves that ledger and persists terminal `accepted`.
[Consumer completion](https://github.com/ThomasMichon/copilot-extensions/blob/df851c316cb5103b290c7da74bf6a68c1e7c8c25/plugins/agent-worktrees/src/agent_worktrees/claim_handoffs.py#L320-L432)
can subsequently reject unavailable/conflicting consumer state, changed child
ownership or missing/foreign leases, or fail during later lease/record writes.
The source path does not restore responsibility on those failures.

Credit the existing actual-consumer-project/execution-space guard, unsupported
cross-space owned-worktree refusal, source-owner proofs, paired claims,
consumer-conflict and lease checks, cross-machine fence and accepted-state
completion route. The consumer-authority helper is unchanged from the
previously admitted version. Producer-backed acceptance remains a separately
delegated path; neither its progress nor this ordinary-path study establishes
the other path's complete transaction behavior.

The newer [finalization checks](https://github.com/ThomasMichon/copilot-extensions/blob/df851c316cb5103b290c7da74bf6a68c1e7c8c25/plugins/agent-worktrees/src/agent_worktrees/finalize.py#L1468-L1549)
recheck nonterminal bundles near cleanup, including under the source-record
lock, and preserve unsettled/reserved obligations. Those protections are real.
They do not close this interval: the handoff registry's
[source lookup](https://github.com/ThomasMichon/copilot-extensions/blob/df851c316cb5103b290c7da74bf6a68c1e7c8c25/plugins/agent-worktrees/src/agent_worktrees/claim_handoffs.py#L686-L710)
excludes terminal bundles after source settlement has removed the forward
claims. This is a static responsibility/finalization exposure, not a claim
that a live source actually finalized or a resource became unowned.

## Intent, priority and existing owners

The current relevant vision clauses were read after their actual current
publications were qualified. Agent Fabric's
[resource-claims](../../../visions/agent-fabric/README.md#resource-claims)
requires source responsibility and finalization blocking through atomic
source/consumer/resource acceptance, including synchronous remote ownership
reservation. Worktrees retains its
[accountable ledger](../../../visions/plugins/agent-worktrees/README.md#accountable-resource-ledger)
and [durable-obligation finalization](../../../visions/plugins/agent-worktrees/README.md#finalization-joins-durable-obligations)
contracts. This closes existing intent; it introduces no new ownership system.

Retain P1 source-correctness priority because failure can remove accountable
source responsibility before the receiving side is durable. Confidence is
high in that narrow current ordering, not in unmeasured runtime incidence.
This is no P0 escalation or assertion that it outranks every other P1.

The original claim-handoff/source-consumer-resource custodians retain ordinary
and producer-backed C3/C4. The actual consumer-coordination owner retains C2,
including the complete inherited #1602 contract. The authorization/release/
abandon owners retain C1 inherited from #4411. Related daemon work is not
newly accepted whole-issue intake; the whole-effort-home gap stays explicit.
No owner, claim, reservation or execution venue is reassigned.

## Finite contribution and independent exits

Through those owners and applicable implementation authority, design a
recoverable nonterminal acceptance protocol before changing the ordinary
transition. A successful receiving/preflight case must precede the selected
fault: begin with a valid consumer record and successful preflight, then
inject loss/conflict or a later write/lease failure before completion.
An already-unready-at-entry rejection is a different control, not the
atomicity regression. Merely reversing two calls is insufficient.

| Gate | Finite contribution and acceptance boundary |
|---|---|
| C3/C4: custody and commit | Retain authoritative source responsibility and its finalize block until consumer forward claims **and** resource-side ownership/fenced leases commit. Inject contained failures/crashes at each ledger, child-owner and lease transition, including mixed claims, partial later-transition failure and synchronous remote reservation. Outcomes must remain recoverable, visibly nonterminal and truthful under retry/idempotence; the consumer inherits finalization responsibility on commitment. |
| C2: actual consumer readiness | Resolve the actual consumer-owner project's versioned state-root/binding readiness across cross-project, cross-machine, internal/remote and producer-backed entries. Incompatible readiness must refuse before consumer claims, reservation/transfer, source mutation or provider effects. Prove unchanged source/reservation/finalize authority and later legitimate-binding success while show/decline/cancel/teardown stay available. Keep this battery separate from commit-failure coverage. |
| C1: operator direction | Retain the complete independent release/abandon/settle authorization obligation from #4411. A reason, confirmation flag, observed session or generated authorization prose is not the requested operator-origin distinction. This source-delta study does not claim that whole lane absent or complete, prescribe a new trust provider or satisfy it with atomicity. |

Preserve actual rightful-owner, stale-actor, execution-space, paired-claim,
producer, provider, lease and reservation restrictions. No timeout/age expiry,
force release, procedural bypass or second ownership ledger is introduced.
Authentication, readiness and all-or-nothing commitment do not substitute for
one another.

The useful order is original-owner admission and focused synthetic regression/
protocol design, reviewed correction, actual containing release, then the
original authorized consumer acceptance. A fixture design is not an executed
test; a source merge is not installed adoption. No live claim/lease experiment,
native venue or new every-host qualification requirement follows.

This contribution can progress independently of framing, authored-destination
protection and endpoint cleanup. It does not require entire related daemon,
installer or singleton programs to finish first. All original source/copy,
resource, review and native limits remain.

## Parent boundary

All 43 parent check states, 24 open items and six roadmap gates remain
unchanged. This adds one current priority/owner/finite-exit contribution, not
whole-roadmap integration or technical completion. The last complete census
remains 898 open, 169 above fixed 729; no new census or closure credit is
claimed. No product test, runtime, claim, resource, issue disposition or
deployment is performed.
