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

## Every blocker has an owner

Do not let a blocker wait for someone to notice it. Each blocker record names
an owner who diagnoses its root cause and drives the fix through the normal
issue and pull-request flow, rather than waiting. The owner is the blocked
worker itself, a repair delegate under this scope, the assigned
`maintain-agent-infrastructure` owner for a host-wide fault, or the operator.
File a tooling or guidance defect in the consuming harness's own issue
tracker, not an upstream tracker chosen by provenance.

Filing and fixing need the charter's recorded source-change and publication
authority, or the owner's own grants. Without them, report an issue draft with
the evidence and the recommended owner. Under relentless
[drive posture](watchdog-checks.md#drive-posture), an unowned blocker is
waste; under either posture, report it with a recommended owner.

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
target's current head through the owner, as in the
[recovery rules](repair-escalation.md#recovery-rules). Keep an unavailable outcome unresolved
rather than repeating the operation.

| Rung | Purpose | Allowed effects |
|------|---------|-----------------|
| R0 Observe | Classify from the passive [fabric readers](fabric-readers.md) | None |
| R1 Diagnose directly | Reach the target's host through its declared SSH profile or transport. Read service and session status, logs, receipts, process lists and the owners' doctors in read-only mode. Load the owning troubleshooting skills (for example `agent-bridge-troubleshooting`, `repairing-worktrees`, `agent-ssh`) | Reads only. A command that self-heals, resumes or restarts is not a diagnostic |
| R2 Doctor records | Correct one diagnosed identity or state finding through the owner's supported repair verb: re-bind a session to its workspace, settle a stale claim, reconcile a registry entry or a projection | Reversible, owner-mediated writes scoped to the target, followed by the owner's readback |
| R3 Recover transport or session | Resume the target's own session (same-session resume) or restart its own bridge or session process through the supported lifecycle. Resume it; never reseed it. To reactivate an inactive head, see [dormant correction](dormant-correction.md#reactivating-an-inactive-head) | Only for the named target. A host-shared service belongs to the host's `maintain-agent-infrastructure` owner. On a host with no assigned maintainer, restarting a host-shared service requires the grant to name that service, because it affects every agent on the host |
| R4 Disruptive | Terminate the target's wedged process and launch a seeded successor in the same worktree, or change its credentials. Runtime updates and reinstalls are maintenance, never a repair rung | Only under an active grant class that names this effect. Never create a second live head or a new worktree, and never discard work. Otherwise prepare one operator decision packet with evidence and a recommended default |

A rung ceiling in the charter is a maximum, not a target. Most blockers end at
R1 or R2. Never skip to a higher rung because a lower one is slow. Never
remove a lock, delete a baton, clear receipts, or hand-edit an owner's private
database or store to make a symptom disappear. Those records are the
evidence.

Repair is scoped to individual blocked agents. When the diagnosis shows a
host-wide tooling, runtime or shared-service fault, such as an outdated
runtime, a failed update, a release defect or a failed shared bridge service,
route it to the host's `maintain-agent-infrastructure` owner. Only on a host
with no assigned maintainer may the monitor restart a shared service, and only
the service the grant names. Do not perform host maintenance, updates or
rollouts under a repair grant.

## Recovery rules

The monitor owns agent recovery. Apply these rules to every R3 or R4 action
and to recovery after a crash or reboot:

- Confirm absence with the native readers first. An error or a timeout is not
  proof that the agent or its session is absent.
- After a host crash, report it to the host's maintainer and wait for its
  host-ready declaration. If the host has no assigned maintainer and is
  responsive, proceed.
- Prefer R3 same-session resume. Use an R4 seeded successor only under an
  active R4 grant class.
- Resolve the worktree's current native head and saved original session
  identity, check for surviving execution and pending launches, and resume with
  the owner's `--resume <original-id>` form. Preserve the original
  user/provider identity and custody. Missing or conflicting evidence is a
  named recovery gap, not permission to create a duplicate session.
- Never create a second live head or a new worktree, and never discard work.
- On a saturated host, recover one agent at a time.
- A resume receipt is not a running CLI or useful work. Verify the actual
  selected CLI and resume identity, then distinguish prompt admission,
  consumption and acceptance from criterion-linked progress.
- A resumed session can stay idle without a continuation turn. Send one scoped
  continuation turn only under a current steering scope; never broadcast a
  recovery notice or steer arbitrary work.
- Verify the agent is live and, for a successor, seeded before reporting
  recovery. Record the result in the [fleet register](fleet-register.md).

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
decides whether to climb, and is the only voice toward the target. A steer
that follows a repair still needs its own steering scope; without one, the
monitor reports the recommended steer instead of sending it.

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
