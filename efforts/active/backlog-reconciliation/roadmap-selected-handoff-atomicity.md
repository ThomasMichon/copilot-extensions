*The following contribution was assisted using Copilot.*

# Selected Current Handoff-Atomicity Contribution

Parent: [Backlog Reconciliation](README.md), Phase 2b.
Selection: [original nominations](roadmap-next-slices.md).
Integration: [scoped sequencing](roadmap-sequencing.md).
Evidence and delivery: [dated journal](journal/2026/11.21-current-handoff-atomicity.md).

## Changed contract before changed source

This is an agent-recommended bounded contribution for OPEN #4619, not an
implementation assignment or permission to operate a handoff. Its complete
current original and all five discussions were qualified and read before
selection. The parent now checks two independently accepted source lanes:

| Accepted tracking lane | Actual current evidence and limit |
|---|---|
| OA1-OA3/OAV1-OAV3: current-owner projection | The incumbent's completed reservation cites reviewed #13569 and implementation #13586. It credits the already-working supported alias positive and reports correction of four foreign/unverified historical-actor counterexamples. These are owner-reported scoped source/fixture results, not campaign execution or whole resource acceptance. |
| OS3-OS4/OSV2: cancelled task's creator obligation | The incumbent's completed reservation cites reviewed #13597 and implementation #13644. The current checked rows receive exact creator binding, owning-Dispatch terminal confirmation, separate cancellation/ledger/settlement results and targeted retry. This does not complete BOTH-store abort/arm atomicity or release a productive obligation. |

Do not schedule those completed reservations as missing source work. Their
owners did not reserve or complete the remaining atomic-arm, CodeSpace,
retained-job/lease or live-use lanes. The original source/resource custodians,
actual holds and independent #6109/#5675/#3442 boundaries remain.

## Current arm boundary and credited guards

The selected source is `6f241291a39f359cddc449f11c9f416ae76c6bc8`.
All three actual handoff-core publications after the admitted `1485994`
baseline were qualified: #10870, #11432 and #13644. Their complete scoped
delta was read. The actual note/cancel CLI and lifecycle writer publications
were qualified before their relevant code, and the current tracking export
was resolved rather than assuming a same-named implementation.

The [recovery path](https://github.com/ThomasMichon/copilot-extensions/blob/6f241291a39f359cddc449f11c9f416ae76c6bc8/plugins/context-handoff/extensions/context-handoff/handoff-core.mjs#L3465-L3600)
still performs a second successful validity read, writes session-state handoff
data, can await `beforeArmPickup`, and then separately notes/arms the ledger.
The [file abort](https://github.com/ThomasMichon/copilot-extensions/blob/6f241291a39f359cddc449f11c9f416ae76c6bc8/plugins/context-handoff/extensions/context-handoff/handoff-core.mjs#L4082-L4203)
holds the reclaim-aware `.consume.lock`; recovery does not share it across
that boundary. This conclusion follows the current calls and lock scopes,
not only the retained comment describing the race.

The [note CLI](https://github.com/ThomasMichon/copilot-extensions/blob/6f241291a39f359cddc449f11c9f416ae76c6bc8/plugins/agent-worktrees/src/agent_worktrees/session_binding_cli.py#L734-L797)
does lock the worktree record around its lifecycle write. That lock does not
span the earlier file/task validation, session-state write and callback.
The actual [lifecycle writer](https://github.com/ThomasMichon/copilot-extensions/blob/6f241291a39f359cddc449f11c9f416ae76c6bc8/plugins/agent-worktrees/src/agent_worktrees/tracking_lifecycle.py#L175-L231)
does not make arm admission conditional on the backing task/file's validity.
For an existing same token it preserves the entry and may promote its live
flag; this is **not** evidence that it changes a cancelled entry back to
pending. Do not claim a live successor was spawned or every later consumer
guard was bypassed.

Credit the second read, exact predecessor/token checks, candidate protection,
file consume/abort lock, task eligibility/status CAS and new exact
creator-claim settlement. Cancellation's record lock, no-op for a candidate
or nonpending entry and guarded predecessor restoration also survive.
None supplies the requested shared admission boundary across BOTH backing
stores and every stage above. A fresh read or eventual refusal remains
different from preventing the interleaving.

## First useful contribution and finite exit

Retain P1 by the stale-recovery/competing-custody consequence, with high
confidence in the narrow current static separation and no new live-incidence
claim. Existing Handoff, actual Dispatch task authority and Worktrees
record/lifecycle owners retain the interface. An accepted whole-issue effort
home is not newly established by thematic fit or a completed reservation.

Through those owners and applicable implementation authority, characterize
competing recovery and abort immediately **after the second successful
validation**, then separately during session-state writing, the awaited
callback and note/arm. Cover BOTH file and task stores. Keep real boundary
participation in contained synthetic fixtures; a mocked unrelated CAS or
already-invalid-at-entry control cannot establish no interleave.

The contribution exits with an owner-correct shared atomic/conditional
admission design and focused regressions proving truthful competing outcomes:
a retired token cannot rearm, while a rightful candidate/consumer is not
expired, abandoned or stolen to make recovery win. Preserve reclaim safety,
proposed/queued eligibility, exact identity, pending claims and retry/manual/
no-replay policy. The existing file lock and actual task authority are the
starting points, not a mandated new verb, global lock service or second
ownership ledger. Merely moving the final read or extending a timeout fails
the original requirement.

The current
[exclusive pickup](../../../visions/plugins/context-handoff/README.md#exclusive-pickup-with-attributable-lineage)
and [operator policy](../../../visions/plugins/context-handoff/README.md#operator-configurable-policy)
clauses retain attributable retry-safe continuation and deliberate live-cutover
opt-in. The actual current vision publication was qualified before those
clauses were read. This closes existing intent, not a new lifecycle policy.

## Independent retained lanes and evidence exits

Keep the complete current #4619 contract, not just its selected race:
CodeSpace deletion-to-originating-obligation settlement **OR** authoritative
provider-absence sweep remains; UNKNOWN provider/lease/account state is not
absence. Non-expiring substantive custody, retained logical-job identity
across helper exit/restart, legitimate separate advisory recovery, live-user/
BUSY preservation and the non-destructive read/mutation boundary remain.
They have their own custodians and are not prerequisites requiring the whole
resource program to finish before the atomic-arm slice.

Reviewed source correction, containing release and the original authorized
consumer acceptance are separate exits. Credit the two accepted lanes at
their actual reported tiers without rerunning their native fixtures.
No new every-host matrix, provider deletion, live abort/trigger, claim
settlement, installation or test follows from this proposal.

This work is independent of #5842's acceptance transaction, framing,
authored-destination protection and endpoint cleanup. All 43 parent check
states, 24 open items and six roadmap gates stay unchanged. The last complete
census remains 898 open, 169 above fixed 729; there is no new closure credit.
