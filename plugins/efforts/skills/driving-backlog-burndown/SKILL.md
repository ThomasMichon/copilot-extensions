---
name: driving-backlog-burndown
description: >
  Drives a scoped backlog or aged pull-request queue toward a measurable
  completion gate using a durable charter, current item/owner ledger,
  oldest-first supervision, verified outcomes and resumable checkpoints.
  Not a stale-item bulk-close, session-log backlog, or authorization to
  start a new worker fleet.
  Trigger phrases include:
  - 'burn down this backlog'
  - 'recover the old PR queue'
  - 'drive these owners through completion'
  - 'resume the backlog burndown'
---

# Driving Backlog Burndown

Turn an authorized recovery objective into a continuing, evidence-backed
execution loop. Reduce unfinished work by completing it, not by hiding inflow,
abandoning old items, weakening gates or replacing their owners.

## Compose the existing owners

Use `planning-efforts` for effort placement, adoption, the canonical README,
reviewed plan and lifecycle. This skill adds a campaign charter and operating
loop, not a second effort format or completion authority.

Use optional capabilities only when available: `agent-worktrees:worktree` for
workspace/PR lifecycle, `agent-worktrees:tracing-claimant-graphs` for ownership,
`agent-bridge:agent-bridge` for existing-session observation/steering, and
`context-handoff:context-handoff` for an actual responsibility transfer.
Use the host's native context tools for same-owner rollover when available.
No runtime, shared coordinator, fleet or recurring schedule is required.
Resolve tool commands from their owning catalogs; keep repository/provider
mutation, authentication and publication policy with their existing owners.

## 1. Establish or resume the charter

Read the founding request and canonical effort before acting. On resumption,
use the recorded next action; do not rebuild the campaign from transcript
history or treat the last completed phase as its objective.

Fill [the charter](assets/CHARTER.md) into the effort's Request/Coordination
sections or one linked charter, without duplicating them. Record:

- Exact providers, repositories, authors/owners, item types and exclusions;
  current snapshot, new-inflow rule and explicit unknowns.
- Measurable outcome, such as maximum open count **and** maximum age, plus
  every remaining Plan/Validation Plan obligation.
- Allowed repairs, publication/merge authority, existing owner assignments,
  human-directed lanes, protected decisions and operations requiring approval.
- Priority, observation cadence, report format, pause/transfer conditions and
  the canonical state/checkpoint locations.

Reuse decisions already given. Ask only for a genuinely missing scope,
authority or design choice; the charter never grants bypass, rollout, resource
creation, abandonment or worker-spawning authority by implication.

## 2. Build the provider-backed ledger

Follow [tracking and recovery](references/tracking-and-recovery.md).
Start [the dashboard](assets/DASHBOARD.md) in the resolved effort home.

Query the full scoped provider set with explicit pagination/limits and UTC
creation times. If enumeration fails or is incomplete, report an unknown or
lower-bound count, never zero. Include new inflow unless the charter explicitly
defines a fixed cohort; show cohort and whole-queue metrics separately.

Map each item through authoritative source/workspace, controller, claim and
session records. Keep an owner roster independent of the open-item list:
one owner can hold several items and merged items can retain parent obligations.
An unmapped item, unavailable route, absent pane or stale message is not proof
of an absent owner. Preserve the original claim and source.

Keep provider state, native activity, review/CI/acceptance gates, blockers and
next actions separate, each with observation time and a bounded evidence
reference. Optional session SQL is a query cache, not another task queue or
replacement for the versioned effort/dashboard.

## 3. Sweep, unblock and execute

Repeat this loop until the parent completion gate or a genuine pause:

1. Refresh the scoped queue and verified outcomes since the previous snapshot.
   Work oldest first, adjusted for actual prerequisites or charter priorities.
2. Inspect **every active implementation owner's actual native activity and
   current gates**, including owners of recently merged slices. Read bounded
   native session output/events or the hosting owner's equivalent; a sent
   status request is not an observation. Record uncovered owners and overdue
   observations explicitly, then continue independent authorized work.
3. Reconcile the owner's current human instructions, source revision and real
   blocker. Distinguish active implementation, review wait, native/test failure,
   dependency, parked human form, transport failure and idle completed slice.
4. Steer the existing owner with one concrete next action, required proof and
   stop boundary. Resolve already-covered questions without another permission
   ceremony, but never answer a new human-only choice or supersede direct human
   steering. A prompt pending behind a form has not unblocked its recipient.
5. Follow through on consumption and execution. Admission, consumption,
   implementation and provider outcome are different facts. Do not resend an
   uncertain operation or start a duplicate worker; use the owning transport's
   receipt/retry rules. Failed connectivity leaves remote activity unknown.
6. Inspect the **full current review**, unresolved findings, required CI and
   actual acceptance evidence before each mutation. A green summary, stale
   approval, diagnostic probe or "no open issues" footer does not clear another
   stated gate. Preserve coverage, platforms, deadlines and real consumer tests.
7. Use the repository's normal managed completion path within its resolved
   authority. Stop on refusal rather than improvising an administrative merge,
   runtime update or policy repair. Independently confirm the provider outcome.
8. Reconcile the completed slice's claims and resources through their owners.
   Journal it, re-read the parent Plan/Validation Plan and select the next
   authorized slice. A merged PR is not parent completion or blanket rollout
   permission; do not finalize a still-responsible parent worktree.

If a dependency remains vague, obtain its exact owning item/hunk/artifact,
permitted integration boundary and next gate. Do not wait indefinitely for an
unspecified future merge or absorb another owner's private diagnostic changes.
Close/supersede items only under applicable authority with concrete evidence,
never solely because they are old or their owners are hard to reach.

## 4. Persist and report at meaningful boundaries

Update the dashboard and effort after source/gate/owner changes, verified
merges, decisions and material blockers. Publish coherent milestones through
the state repository's own flow; do not open a documentation PR for every poll.
Resolve that publication obligation rather than leaving it silently open.

If periodic supervision is authorized and supported, own **one** schedule with
the charter's scope/cadence, parent stop condition and report requirement.
Inventory existing schedules before creating one. A schedule is not a daemon
or a guarantee of supervision during an operator wait, outage or session exit.
Record missed intervals honestly. Do not start a replacement scheduler.

End each sweep with the charter's bounded report (100 words is a useful
default): verified new outcomes, fresh count/oldest with timestamp, concrete
implementation advances, remaining blockers and named next owner actions.
Distinguish the campaign's own closures from unrelated merges and new inflow.
Report coverage gaps; do not claim continuous supervision from a fresh census.

## 5. Continue, transfer or finish

Use [the checkpoint](assets/CHECKPOINT.md), [the transfer brief](assets/HANDOFF.md)
and the exact procedures in [tracking and recovery](references/tracking-and-recovery.md).
Same-owner native rollover preserves schedules and workers; an actual owner
transfer explicitly settles their supervision and custody.

Before declaring Done, re-query the provider and satisfy **all** charter metrics,
resolve or explicitly transfer every Plan/Validation item through
`planning-efforts`, and settle external obligations. Record the final outcome,
stop the owned schedule and release/finalize only the workspaces that are
actually complete. A blocked campaign remains Active/Blocked with an owner
and next action; an honest blocker report is not a completed objective.
