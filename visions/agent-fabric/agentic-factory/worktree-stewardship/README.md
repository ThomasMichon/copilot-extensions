# Worktree Stewardship - Vision

- **Subject:** Explicit conversation handover and consent-based worktree continuity
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-18

## Purpose & Intent

Accepted work remains durably pending with a visible owner until its objective is
verified complete or explicitly transferred. Interactive work can become
supervised background responsibility, and dormant worktrees can regain useful
attention, without losing founding intent, source work, or operator control.
Stewardship composes the existing registrar, supervisor, task queue, and worktree
authority; it is not another cleanup queue.

## Concepts & Components

A settled charter carries the founding objective, canonical effort where
applicable, restrictions, sufficient done criteria, and retained progress.
Operator consent admits that responsibility into registered supervision.
Evidence-backed task cards support proposals and decisions. Worktree and task
owners retain identity, claims, and execution control. Workers submit completion
claims; only the operator or evaluator designated by the task's confirmation
policy accepts completion. Stewardship does not grant workers self-confirmation.

## Features

### consent-based-admission

Admission requires explicit per-objective consent or proactive operator enrollment
whose scope covers the objective, together with a sufficiently settled charter
and done criteria. Ambiguous mandates request steering. Age, a merged branch,
or an inactive process is neither consent nor proof of completion.
Orchestrators may assess and propose candidates; their proposal is not consent.

### explicit-conversation-handover

An operator can hand over a sufficiently settled conversation in either of two
forms: continue the same worktree objective under background supervision, or
graduate accepted intent into a durable task or effort allocated to an eligible
worker. Both preserve relevant decisions, source changes, restrictions,
acceptance and traceability to the originating conversation. Graduation leaves
the planning conversation free to continue without making it a competing owner
of the delegated execution.

Handover is complete only when the rightful authority durably accepts the
responsibility and its execution or waiting state is visible. A queued task may
wait for capacity without being abandoned; a sent message or successful process
launch alone is not acceptance. Closing the originating console after handover
does not cancel the objective. Background responsibility is independent of
whether its supported execution body is CLI, ACP or another session provider;
handover does not require replacing a healthy conversation merely to hide it.

### scoped-objectives-and-sequencing

Each worktree carries a legible objective, current slice, permitted repository
and effect scope, acceptance and control owner. Related work names its task,
effort, dependency and contribution relationships rather than deriving scope
from a title or giving a worker the whole portfolio's authority.
Ordered work advances through explicit prerequisites and completion conditions;
blocked successors remain pending, while independent eligible work can proceed.
The task authority owns these relationships, not a second stewardship sequencer.

### founding-objective-continuity

Each execution leg retains the founding objective and canonical effort, including
all Plan and Validation Plan gates. A completed slice advances the objective; it
does not replace it. Every gate must be met or explicitly transferred to a named
tracked objective before the original responsibility can be considered complete.

### incremental-fair-execution

Stewardship selects bounded, meaningful progress within deliberate resource-aware
fairness across admitted work. It suspends for steering, review, capacity, and
dependency waits rather than burning sessions or repetitive polls. Changed
conditions make work eligible again through the same supervision owner.

### proposals-and-open-feedback

Cards carry evidence-backed proposals, structured decisions, and an unconditional
general freeform feedback surface. A choice labelled "other" alone is
insufficient: people can explain, correct, or redirect without fitting a preset
answer. Feedback can revise the charter through its owner, but never silently
grant destructive permissions.

### same-agent-human-takeover

An operator can inspect and directly drive the original agent, preserving its
worktree and session when safe. Explicit control ownership and revision distinguish
human-directed work from autonomous responsibility. Return and withdrawal are
deliberate actions carrying the agreed charter forward.
Forgotten takeovers remain visible and may request a return-or-park decision;
silence never authorizes resuming against human control.

## Behaviors

### fence-stale-wakes-and-reassignment

Admission, resumption, and reassignment honor current consent, charter, control
revision, and rightful claims. Stale wakes and former owners cannot write or
resume autonomously during human takeover. Withdrawal prevents new autonomous
execution without erasing retained work or disguising unresolved responsibility.

### current-guidance-safe-forward-sync

Admission and resumption use supported forward-sync and current point-of-use
guidance at a quiescent boundary, never concurrently with an execution leg or
human edit. Dirty, indexed, and untracked work, conflicts, open pull requests, and
active rebase states remain preserved and legible. No automatic force, reset,
or broad stash substitutes for reconciliation. Material new guidance that
conflicts with the mandate raises steering rather than silently changing it.

### accountable-failure-and-wait

Infrastructure retries are bounded and attributable. Exhaustion leaves the task
held and escalated, with retained progress, a visible owner, and a reason for
the block; accepted work is never silently abandoned. Suspension conserves
capacity without pretending that a wait satisfies acceptance.
Explicit operator cancellation records what was withdrawn and preserves any
remaining work or transferred responsibility; retry exhaustion is not cancellation.

### verified-retirement

Cleanup follows verified landed work, the full done criteria, and settled claims
and obligations through the lifecycle owner and applicable permissions.
Age-based deletion, raw deletion, and force are not substitutes for that proof.
Unresolved work remains recoverable, including when execution infrastructure
cannot presently reach it.

## Non-Goals / Boundaries

No consent inferred from inactivity, no automatic destructive authorization, no
parallel registrar or queue, and no completion inferred from bookkeeping.
Stewardship does not override source-control conflicts, human control, or another
objective's rightful owner.

## See Also

- Parent: [agentic-factory](../README.md)
- Agency and safe lifecycle: [agent-worktrees](../../../plugins/agent-worktrees/README.md)
- Registrar, supervisor, and queue: [agent-dispatch](../../../plugins/agent-dispatch/README.md)
- Full completion gate: [efforts](../../../plugins/efforts/README.md)
- Task conversation: [task-outputs-and-review](../../../plugins/agent-dispatch/task-outputs-and-review/README.md)
- Operator cards: [tasks-pane-ux](../../../plugins/agent-dispatch/tasks-pane-ux/README.md)
- Same-agent coordination: [agent-bridge](../../../plugins/agent-bridge/README.md)
