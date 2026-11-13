# Per-agent charters and driving to resolution

Back to [Monitor agent fleet](../SKILL.md).

The monitor exists to keep already-assigned agents on task and to drive their
work to resolution. A healthy fleet churns: agents finish their charters,
finalize and drop out, and new agents are allocated for new work. Over time, a
monitor that is doing its job has fewer long-lived targets, not more.

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

An agent that has been set to work but has no discoverable charter (root,
acceptance, kind and finalization criterion) is a weak point: its successor,
its operator and the monitor all have to reconstruct its goal from history.
Under steering scope, send one bounded **charter backfill** steer. It carries
the monitor's draft summary, marked with confidence, and asks the agent to
adopt or correct it in its own record. Under dormant correction scope, backfill
an inactive owner's record directly, using that mode's clear-versus-ambiguous
rules. Without either scope, recommend the backfill. Reconcile the agent's
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
