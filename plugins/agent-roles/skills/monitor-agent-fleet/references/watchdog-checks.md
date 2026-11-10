# Watchdog checks

Back to [Monitor agent fleet](../SKILL.md).

These checks turn the [misdirection patterns](misdirection-patterns.md) into a
per-agent routine for an assigned recurring watch. They come from an audit of
long-running agent sessions in which the largest single cost was not failing
work but **questions left open with no default, no deadline and no other work
in progress**. Other recurring costs were self-added prerequisite chains,
"merged" reported as done before any deployment or live test, identical review
rounds, handoffs that rewrote the operator's request, timeout-increase requests
made before asking whether the work itself was excessive, heavy validation of
trivial changes and a superseded agent that was never told. Record the chosen
values in [the charter](../assets/CHARTER.md).

## The watchdog never blocks work

The monitor detects and steers; it never does the observed work and never
holds anyone up. **The watchdog never asks a blocking question.** When unsure,
it takes the least intrusive reversible option within its charter, usually a
read-only finding or one steer with a stated default, and records the judgment.
It never edits another agent's files, plan or agenda, never raises another
agent's budget, never restarts, stops, deploys or merges, and never treats
silence as authority. A monitor that waits on its own question has become the
problem it watches for.

Hold work to the original acceptance gate, not a stricter one. An installed
payload version does not prove which runtime is serving, but a monitor that
requires more than the original request did, such as payload and running
service versions being equal, has added its own prerequisite. Withdraw that
hold and record the withdrawal.

## Inspect per agent, from persisted state

Read each agent's persisted records, not its live context window: its status
or disposition line, open follow-ups, agenda or plan position, effort
plan/validation, the original operator request, its latest handoff or
checkpoint, and the repository/PR/CI and deployment delta since the last
observation. For each agent record:

| Check | Inspect | Flags when |
|-------|---------|-----------|
| Disposition drift | Status line and agenda position against recent work | The status contradicts recent work, or the current action is unchanged for two observations while the agent is active |
| Open questions | Each pending question's age, recommended default, revisit time and concurrent activity | A question has no default or no revisit time, outlives its revisit time, or the agent has no tool activity while it is open |
| Open obligations | Approved-but-unmerged PRs, unanswered change requests, unconsumed handoffs, unpushed commits | An obligation has no recorded owner action for longer than the charter allows |
| Repeated state | Diagnosis, command, verdict and head across observations | The same diagnosis, command or verdict recurs on unchanged input |
| Turn end after failure | The last result before the agent went idle | The turn ended on a reported failure with no diagnosis, next step, recorded default or handoff; a reported failure does not finish the turn |
| Prerequisite growth | New prerequisites, child PRs and detours against the parent criterion | A new prerequisite names no failed parent boundary, or depth exceeds the charter's limit |
| Budget changes | Timeouts, caps, retry and visit limits | The agent raised its own limit, or asks to raise one without a measurement |
| Validation weight | Validation time and selection against change size and risk | Validation time exceeds the charter's proportionality budget for a small, low-risk change |
| Delivery state | Merged, released, deployed and live-verified states against the root's acceptance | The agent reports done at merge while the root needs live behavior |
| Handoff fidelity | Newest handoff's objective against the verbatim original request | The handoff drops, narrows or contradicts an operator instruction, or adopts a peer-relayed hold over it |
| Supersession | Roles reassigned during the period and the predecessor's live state | A replaced agent is still running, watching or holding claims without being told |

Healthy is one compact log line, not a report or a message. Unreadable state is
`unknown`, never healthy; report the gap once after the charter's count of
consecutive unknown observations.

A liveness label such as running or stalled does not prove a wedged process or
a visible unanswered prompt, and the monitor usually cannot see an interactive
question or permission form. When no available reader settles it, batch those
agents into one request for operator terminal attention, each with its last
observed delta, and handle any reported question through
[parked questions](pending-input.md). Do not rerun an unavailable question
reader or its fallback every cycle.

When the charter records a model requirement, verify the selected model from
the runtime's context or usage metadata before reporting a discrepancy or
withholding a steer. With a synthetic or fusion model, per-turn model-change
notices and per-call usage can name a constituent model while the selected
model is unchanged.

## Anti-pattern signals and steers

Diagnose only on the distinguishing evidence in
[misdirection patterns](misdirection-patterns.md). Each steer below uses the
[bounded nudge](interventions.md#a-bounded-nudge) shape: one cited diagnosis,
one changed next action, one observable exit.

- **Unbounded question.** Steer: restate the question once with a recommended
  default and revisit time, take that default when an existing recorded grant
  already covers it, and continue other authorized work meanwhile. Never tell
  the agent that silence grants a gated effect.
- **Self-added prerequisite.** Steer: name the failed parent boundary the
  prerequisite unblocks or park it as a tracked item, then execute the saved
  return action.
- **Merged treated as done.** Steer: name the remaining deployment and live
  check the root needs, or record "merged, pending rollout" with its owner.
  Never steer an agent to deploy without separate deployment authority.
- **Identical review round.** Steer: keep the standing verdict and post nothing
  until a qualifying trigger, per the review role's
  [throughput and loop discipline](../../review-pull-requests/references/throughput-and-loops.md).
- **Handoff rewrites intent.** Steer the current head back to the verbatim
  original request; a peer-relayed hold takes effect only with the original
  scope owner's authority.
- **Timeout-increase request.** Steer: measure the per-test or per-stage
  durations and ask whether the work is excessive before asking anyone for a
  larger limit. The original acceptance deadline stays the bar.
- **Heavy validation of trivial work.** Steer: run the smallest affected
  selection once per code state; keep any genuinely required gate.
- **Reported failure ends the turn.** Steer: a reported failure does not
  finish the turn; diagnose the literal failure within scope, take the next
  discriminating step or a recorded default, or hand off with the failure
  named.
- **Superseded agent never told.** Steer the successor or scope owner to tell
  the predecessor and release its overlapping task, claims and watches through
  their owners, following the review role's
  [transfer rules](../../review-pull-requests/references/communication.md#accepted-transfer-not-conversational-inference).

## Classify every wait

Do not count all waiting as waste.

| Class | Examples | Monitor response |
|-------|----------|------------------|
| Required | A gated decision only the operator can make, required human review or CI, an operator who is genuinely away | Confirm the wait has an owner, a revisit time and a default; no steer |
| Productive | Waiting on an owned long-running command while doing other authorized work; an observed event the agent is subscribed to | None |
| Waste | A question with no default or deadline while the agent idles; a question an existing grant already answers; renewing an unchanged watch at a fixed short cadence; waiting for a reply that has already arrived | Steer with the matching signal above |

Report wait time by class with its evidence, not one blended idle figure.

## Steer directly or escalate

Steer directly, within the charter's steering scope, when the correction
preserves the root goal and its constraints: return to the parent, add a
default to a question, shrink validation, stop a repeated round, restore the
original request or tell a predecessor.

Escalate to the operator only when no steer can resolve it: an objective
change, new access or spend, a destructive or deployment effect, conflicting
operator instructions, or a handoff whose deviation from the original request
cannot be settled from the record. Escalate once, with the evidence, a
recommended decision and its default.

Use one ladder per target and issue, keeping the single-outstanding-steer
rule in [steering](steering.md):

1. One non-blocking steer with a next action, default and revisit time.
2. Only with materially new evidence, a second steer that changes the
   approach, not a repeat. Unchanged evidence never earns another send.
3. Escalate once to the operator or scope owner, with evidence and a
   recommended decision, when a steer with confirmed delivery passes its
   revisit time with no progress and no recorded disagreement, or when a
   second steer goes unheeded. Never repeat the report unchanged.

A steer whose delivery is unknown is reconciled through the messaging owner's
receipts first; it neither counts toward escalation nor justifies a resend.

## Cadence

Use the charter's active and parked cadences, for example 30 minutes for
active agents and two hours for parked ones, through the existing scheduler or
event source. Observe early when a question reaches half its revisit time, an
agent shows no new delta for two observations, validation passes its budget, a
change request stays unanswered past the charter's limit or a recorded
deadline passes. Prefer events over polling; batch unchanged agents.
