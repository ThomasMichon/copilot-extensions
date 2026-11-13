# Graduated repair of blocked agents

Back to [Monitor agent fleet](../SKILL.md).

A worker is often blocked by its infrastructure rather than its plan: a bridge
admits messages that never reach the agent loop, a session lost its workspace
binding, a claim or handoff record contradicts the live head, or a process
stopped making progress. Steering cannot fix those blockers. Under the
charter's **repair scope**, the monitor diagnoses them and repairs them
through each owner's supported verbs, one rung at a time. Without that scope,
report the diagnosis and the rung you recommend: **repairs applied: none**.

A read-only, steering, answer or dormant-correction grant does not authorize
repair. A repair grant does not authorize steering, answering or editing a
worker's plan. Each mode needs its own grant.

## Classify the blocker first

| Symptom | Evidence that distinguishes it |
|---------|--------------------------------|
| Unresponsive transport | Receipts stay `admitted` or `sdk_accepted` while the recipient's own event history shows no matching input across a turn boundary |
| Identity/state fault | The head session, workspace binding, claim, handoff baton or registry entry disagrees with the observed live session, or the owner's doctor reports a finding |
| Stuck live agent | The process and transport are healthy, input is consumed, but there is no delta past the watchdog thresholds. Treat this as a steering problem, not a repair problem |
| Wedged process | The process shows no event, tool or log activity across repeated observations, while its owner reports it running |

A running turn, a long tool wait or an operator-owned question is not a fault.
Rule out [parked questions](pending-input.md) and productive waits before
treating silence as a blocker.

## The ladder

Climb one rung at a time. Before each rung, record the hypothesis, the
evidence that the lower rung was exhausted or produced a named finding, and
the charter's rung ceiling. Capture evidence before any mutation. Take one
action, then read it back. Wait at least one reobservation interval between
rungs, so that a slow recovery is not mistaken for a failure. Stop climbing as
soon as the target consumes input or makes a delta.

A resume or restart that times out after being accepted is not proven to have
failed, and a failed readback alone does not establish failure. Before
resuming, retrying or climbing, reconcile the original operation and the
target's current head through the owner, as in the maintainer's
[rollout and recovery](../../maintain-agent-fleet/references/rollout-and-recovery.md)
guidance. Keep an unavailable outcome unresolved rather than repeating the
operation.

| Rung | Purpose | Allowed effects |
|------|---------|-----------------|
| R0 Observe | Classify from the passive [fabric readers](fabric-readers.md) | None |
| R1 Diagnose directly | Reach the target's host through its declared SSH profile or transport. Read service and session status, logs, receipts, process lists and the owners' doctors in read-only mode. Load the owning troubleshooting skills (for example `agent-bridge-troubleshooting`, `repairing-worktrees`, `agent-ssh`) | Reads only. A command that self-heals, resumes or restarts is not a diagnostic |
| R2 Doctor records | Correct one diagnosed identity or state finding through the owner's supported repair verb: re-bind a session to its workspace, settle a stale claim, reconcile a registry entry or a projection | Reversible, owner-mediated writes scoped to the target, followed by the owner's readback |
| R3 Recover transport or session | Restart the one bridge or session-hosting service, or resume the target's own session through its supported lifecycle. Resume it; never reseed it | Only for the named target. Restarting a host-shared service requires the grant to name that service, because it affects every agent on the host |
| R4 Disruptive | Terminate a wedged process, update or reinstall a runtime, replace a session, or change credentials | Only when the grant names this effect. Otherwise prepare one operator decision packet with evidence and a recommended default |

A rung ceiling in the charter is a maximum, not a target. Most blockers end at
R1 or R2. Never skip to a higher rung because a lower one is slow. Never
remove a lock, delete a baton, clear receipts, or hand-edit an owner's private
database or store to make a symptom disappear. Those records are the
evidence.

Repair is scoped to individual blocked agents and to any host-shared service
the grant names at R3. When the diagnosis shows a host-wide tooling or runtime
fault, such as an outdated runtime, a failed update or a release defect, route
it to the assigned `maintain-agent-fleet` owner, or report it when none is
assigned. Recovering a named shared service is the one exception, even when
every agent on the host depends on it. Do not perform host maintenance,
updates or rollouts under a repair grant.

## Delegate in parallel, own one synthesis

Independent blocked targets can be diagnosed in parallel by sub-agents, using
the host's delegation mechanism (for example background task agents). Give
each delegate one bounded objective:

- Target identity, root pointer and symptom evidence.
- The original repair grant reference with its validity, holds, rung ceiling
  and the exact allowed effects. Immediately before each mutation, the
  delegate rechecks that the grant is still current, that no newer hold or
  revocation applies and that the owner's stricter gates pass; otherwise it
  stops and returns.
- The owning skills to load and the forbidden effects. A delegate never
  messages, steers or answers the target, and never edits the target's plan
  or charter.
- The required return: findings, each action with its readback, evidence
  locations, and the recommended next rung.

Group targets that share a suspected cause, such as one host's bridge service,
under a single delegate, so that two delegates never repair the same shared
service. Keep at most one repair delegate per target or shared service.
Bound concurrency to what the hosts can absorb. A delegate inherits no more
authority than the current rung. The monitor keeps the escalation record,
decides whether to climb, and owns every resulting steer.

## After a repair

Recheck the target through the passive readers. A repair grant authorizes no
message, so recovery never replays one. Keep each earlier steer or answer
outstanding and reconcile it through its own owner:

- **A steer** follows [steering](steering.md): read its receipt with the
  pending record's original key, binding and body file. Missing recipient
  history does not prove non-delivery. An `admitted` or `sdk_accepted` message
  can still arrive, and a new key creates a separate admission instead of
  deduplicating it. Any retry is the messaging owner's supported retry with the
  original key and the identical request. It is sent only while a current
  steering scope, the target's holds and any revocation still permit it.
- **An answer** follows [pending input](pending-input.md), never generic
  message delivery. After recovery the question may already be answered or
  withdrawn, and its head or the answer grant may have changed. Recheck all
  of them and reconcile an unknown outcome through that question-specific
  operation.

Never resend under a new key.

Record the result per target: symptom, rungs climbed, actions and readbacks,
current state and any remaining operator decision. A repair that does not
restore consumption or progress is reported once, with its evidence and the
next rung it would need. Map the cause to a [weak point](self-improvement.md)
so that the fleet's tooling or guidance can prevent its recurrence.
