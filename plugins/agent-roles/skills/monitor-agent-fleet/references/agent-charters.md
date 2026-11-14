# Per-agent charters and driving to resolution

Back to [Monitor agent fleet](../SKILL.md).

The monitor exists to keep already-assigned agents on task and to drive their
work to resolution. A healthy fleet churns: agents finish their charters,
finalize and drop out, and new agents are allocated for new work. Over time, a
monitor that is doing its job has fewer long-lived targets, not more.

## Drive by outcome goals, not activity

The monitor drives each agent toward its **root goal**: the observable end
state that is true when the work is done, or, for a standing service, the
steady state it maintains. A root goal is not a description of what the agent
is doing now. Its current slice, PR, experiment or review round is evidence of
progress toward the goal, never the goal itself.

| Activity-shaped (a defect) | Outcome-shaped (a root goal) |
|----------------------------|------------------------------|
| "Fixing type errors in the CLI module" | "Every Python module in the repository passes strict type checking, enforced first for new code and then for all existing code" |
| "Debugging the container SSH login" | "A container definition that can be instantiated on a supported host, reached over SSH or the messaging transport, and run the harness like any other machine" |
| "Watching the review queue" | "Every incoming PR in scope receives a review within the agreed latency" (standing) |

A good root goal names who or what reaches which state, the acceptance
evidence that proves it, and any ordering the operator required. Judge every
target against it each sweep:

- **Alignment.** Does the recent work clearly advance the root goal, or is it
  a detour, a self-added prerequisite or an unrelated tangent?
- **Projected progress.** Given the remaining acceptance and the recent rate
  of real deltas, is the goal converging, stalled or receding?

A missing root goal, or a "root" that only restates the current activity, is
itself a finding. The fix is a **goal-establishment steer** (below), not a
monitor-side rewrite.

### Goal precedence

1. **Operator-defined goal.** A goal the operator stated for the target is
   recorded verbatim with its source in the monitor's operator-goal register
   (in the supervisory record) and wins over every other source. The monitor
   never paraphrases, narrows or enlarges it.
2. **Agent-stated goal.** The goal the agent has lodged in its own charter,
   derived from its original operator source.
3. **Monitor draft.** A goal the monitor infers from the original source and
   history, always marked with confidence. A draft is a proposal for the agent
   to adopt or correct; it is never operator authority and never widens what
   the agent may do.

When an agent-stated goal conflicts with an operator-defined goal, steer the
agent to adopt the operator's wording. When two operator sources conflict,
preserve both and report once to the operator with a recommended reading.

## Standing goal-establishment authority

The fleet-monitor role, and any **equivalent supervisory agent** (one holding
a recorded charter from the same operator to supervise the target, such as
another fleet monitor or a dispatcher that adopts this contract), **holds
standing authority to send goal-establishment steers**. It needs no separate
steering grant for them. The authority covers exactly two purposes:

- **Goal nudge.** Ask an agent that has been set to work without an
  outcome-shaped root goal to establish one from its original operator
  source. Include the monitor's draft, marked with confidence.
- **Charter lodging.** Ask an agent to lodge its root goal, its acceptance
  evidence and its finalization criterion in its own charter (its canonical
  effort Goal, checkpoint or workspace record), and to re-derive its plan and
  next action from that goal. When an operator-defined goal exists, relay it
  verbatim with its source reference and ask the agent to lodge it unchanged.

The authority is standing, not unbounded:

- It applies in every mode of a recorded monitor assignment unless the charter
  or a later operator instruction excludes it. A finite one-shot read-only
  sweep reports the goal steers it would send and keeps **messages sent:
  none**.
- Every goal steer still follows the [steering protocol](steering.md): current
  head, recheck holds and exclusions, deduplicate (one outstanding goal steer
  per target), persist before sending and the peer-engagement rules. An
  all-work hold or a recorded exclusion still means no message.
- It never changes an operator-defined root, approves a scope change, sets a
  goal the original source cannot support, or adds effect authority (deploy,
  merge, spend, access, approvals). An agent that adopts a goal still needs
  its own grants for every effect.
- The agent writes its own record. The monitor reconciles the adopted wording
  into its summary and never edits a live agent's charter.
- When the agent reports that its source cannot support any outcome goal, or
  disputes the draft, record the dispute and report once to the operator with
  the draft and a recommended decision. Do not send a second goal steer on the
  same evidence.

Other corrective steers (next criterion, finish line, plan rework, detour
correction) still need the charter's steering scope.

## Keep a summarized charter for every target

For every observed target, keep one private summary in the monitor's own
state (for example `notes/agents/<workspace-id>/charter.md`, referenced from
the supervisory record), filled in from [the agent charter
template](../assets/AGENT-CHARTER.md). Key it by stable workspace identity, not
session or title. Update it when a sweep observes a material delta: a new
operator source, a changed acceptance criterion, a completed slice or a
changed blocker. Keep it short, because it is the index the monitor reads
first every cycle, before deeper history.

**The agent's own charter is authoritative; the monitor's copy is a summary.**
The agent's charter lives where the agent keeps it: its canonical effort,
checkpoint or workspace record. When the two diverge, record the divergence
and the source of each side. Then steer the agent to reconcile its own
charter, or correct an inactive owner's record under
[dormant correction](dormant-correction.md). Never overwrite a live agent's
record to match the monitor's inference.

### Encourage each agent to keep its own charter

An agent that has been set to work but has no discoverable charter (an
outcome-shaped root goal, acceptance, kind and finalization criterion) is a
weak point: its successor, its operator and the monitor all have to
reconstruct its goal from history. Send one bounded **charter backfill** steer
under the standing goal-establishment authority above. It carries the
operator-defined goal verbatim when one exists, otherwise the monitor's draft
summary marked with confidence, and asks the agent to adopt or correct it in
its own record. Under dormant correction scope, backfill an inactive owner's
record directly, using that mode's clear-versus-ambiguous rules. When the
charter excludes goal steers, recommend the backfill. Reconcile the agent's
adopted wording into the monitor's copy, not the reverse.

## Classify each target's charter kind

| Kind | Meaning | What the monitor drives |
|------|---------|-------------------------|
| **Completion** | A finite outcome with acceptance criteria | Progress to the next acceptance criterion, then verification and finalization through the agent's own flow |
| **Standing** | An explicitly indefinite service, such as a reviewer or watcher | Staying on course: intake coverage, latency and loop discipline. Never push it toward finalization |
| **Exploratory** | No goal set yet: design discussion, questions and answers, or a back-and-forth with the operator | Light touch only (below) |

Record the kind, the evidence and a confidence. Charter text, an effort's
plan, an operator's assignment message or a claimed task establishes the kind.
A worktree title or a guess does not.

### Light touch for exploratory agents

Do not force an agent to move when it is engaged in design discussion or a
back-and-forth with the operator. For an exploratory target:

- Waiting on the operator is a required wait, not waste. Send no idle,
  completion or charter-rework nudge.
- Record observations and report parked questions only.
- When uncertain whether a goal was set, treat the agent as exploratory and
  note the uncertainty.
- An exploratory agent that has gone idle with no goal and no open operator
  exchange is reported to the operator, who decides whether to set a goal or
  finalize it. It may receive at most one non-blocking goal nudge asking it to
  record whether a goal was set.

An agent becomes a completion or standing target once it is **set to work
with a goal**. Signals include an operator assignment with a goal or go-ahead
(for example "implement", "go", or acceptance criteria), a created effort,
plan or claimed task, or autonomous execution against a stated objective.
From that point, the monitor stays on its case.

## Drive completion charters to resolution

Each sweep, for each completion target, record the remaining acceptance
criteria, the last delta, and the time since the agent was set to work. Then
pick the smallest steer that moves it toward resolution:

1. **Next criterion.** When the agent is between slices, name the next unmet
   acceptance criterion and the one action that advances it.
2. **Finish line.** When the criteria appear met but the agent has not
   finalized, ask it to verify the criteria against current evidence and
   finalize through its own finalization flow. The monitor never finalizes,
   abandons or retires a worker itself.
3. **Rework the charter or plan.** When the plan has drifted, the charter has
   grown beyond its original source, or acceptance is stale, ask the agent to
   rework its own charter and internal plan. It should restate the acceptance
   from the original source, move nonblocking discoveries to the appropriate
   backlog, and set an explicit return. Changing the root objective still
   requires its operator.
4. **Blocked on infrastructure.** Hand the target to
   [graduated repair](repair-escalation.md) under its grant, or report it.

A standing target gets only the on-course check: is it covering its intake,
honoring its loop rules and recording its results? Never steer it toward
completion.

Never push a premature finalization, lower an acceptance bar, or count a
merged child as the root's completion, just to reduce the roster.

## Report churn

Each recurring sweep reports the fleet's churn: completions and finalizations
observed, new targets allocated, and each completion target's age since it
was set to work against its last delta. A long-lived completion target with
no recent delta is the first candidate for a resolution steer or repair. A
shrinking or steadily churning roster is evidence that the monitor is working.
A growing roster of aging targets is a weak point to report.
