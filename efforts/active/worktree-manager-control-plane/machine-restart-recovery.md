# Machine-Restart Session Recovery

[Parent effort](README.md) - [Tracker #4564](https://github.com/ThomasMichon/copilot-extensions/issues/4564)

## Guiding Intent

Restore eligible, previously live native CLI conversations after their execution
environment restarts, without reviving deliberately closed sessions, duplicating
survivors, or granting the resumed agent new operational authority.

This is a focused extension of the existing Worktree Manager effort, not a
second session registry or a new daemon. It is vision-closing against
[session-hosting](../../../visions/session-hosting/README.md):
`Durable liveness snapshot`, `recoverable-across-full-restart`,
`snapshot-outlives-the-process`, `persist-before-notify`, and
`launch-receipts-are-provisional`. Existing agency and hosting boundaries remain
binding. Any newly necessary standing intent must be reconciled before runtime
implementation, not silently inferred from this plan.

## Request

Operator request, verbatim:

> We should make a little effort to see if it's possible to add automatic agent restoration post-restart of the machine. Basically, worktree-manager can add a handler for a restart notification, as well as have a "black-box" to track live sessions periodically, and then on successful re-launch of the daemon, it can carefully restore the previously-live sessions. Of course, it will need to detect when sessions exit gracefully. When resuming, it can pass a resume seed that clearly indicates a restart occurred, and the agent should check status before proceededing.

Execution direction, verbatim:

> Great. Write out or extend an effort for this, and then kick a CLI headed agent via agent-bridge to work on it

The operator authorized planning and delegated implementation. Production
reboots, daemon restarts, host boot/logon configuration changes and live
automatic-recovery enrollment are not implied by that direction.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Planning coordinator | Land this reviewed proposal and verify the CLI worker's pickup | Existing public tracker and planning PR |
| Restart recovery implementer | Own the scoped implementation, validation, review and completion record | Native CLI/mux session through agent-bridge; source handle `steady-resistor` |

## Coordination

- **Topology:** one isolated implementation worktree; normal PRs to `dev`.
  The planning coordinator stops editing that worktree before worker pickup.
- **PR ownership:** the coordinator owns the plan PR through merge; the
  implementer owns subsequent PRs through their real review and merge gates.
- **Scope:** Manager-owned recovery mechanics and the smallest necessary
  owner-protocol extensions. Preserve process boundaries: agent-worktrees owns
  worktree/head/claims and native session acquisition; agent-bridge owns message
  delivery; agent-dispatch owns task policy.
- **Existing owners:** #4564 retains reachability/history and manual restoration.
  Preserve #1478 and #6677: deliberately ended manual agents remain dormant.
  Do not take unrelated control-plane, fleet, release or workflow work.
- **Milestones:** report the reconciled contract, the first failing/passing
  recovery tests, first integrated implementation, and final reviewed result
  through the same bridge conversation. Record material outcomes here.
- **Continuity:** use native context continuation where supported. Do not
  replace the implementing conversation merely to refresh context.

## Context

The Manager already has a singleton mux daemon, a disk-backed mapping registry,
incarnation/revision fencing, bounded attachment observation and a native
seeded cold-Resume path. #5573 delivered attached-client observation; it did not
deliver machine-restart restoration.

The mapping is not a sufficient recovery ledger today: it does not bind every
entry to its original native conversation and boot identity, and a failed mux
probe may tombstone an entry without recording why execution disappeared.
Pane-exit observations and session-end hooks are useful evidence, but neither a
generic tombstone nor an exit code alone distinguishes intentional closure
from shutdown teardown.

## Plan

The following implementation shape and safeguards are **agent-recommended**
means of fulfilling the operator's request. They require normal source review.

### 1. Reconcile the contract

- [ ] Trace current launch, head/session acquisition, teardown and daemon-start
  paths against current source. Record the minimum owner-protocol additions.
- [ ] Define recovery eligibility, intentional closure, shutdown interruption
  and unknown evidence separately. Include detached but genuinely live agents;
  attachment count is presentation evidence, not eligibility.
- [ ] Define supported native environment boundaries, including Windows versus
  WSL and native Linux. Same-boot daemon updates must not masquerade as reboot.
- [ ] Specify capability-gated, explicit local enrollment and report-only
  inspection. Unsupported or unprovable environments report a blocker rather
  than silently becoming a different launch mode.

### 2. Preserve the black box and exit evidence

- [ ] Persist last-positive hosting evidence at lifecycle transitions and on a
  bounded periodic cadence, under the existing attributable installation.
  Bind the exact native conversation, project/worktree, host environment,
  boot epoch, execution incarnation and observation sequence/freshness.
- [ ] Preserve recovery evidence independently of current reachability and
  display tombstones. Store only necessary metadata in private local state,
  not transcripts, secrets, environment dumps or untrusted executable commands.
- [ ] Record reasoned intentional-exit evidence durably; delayed observations
  from an older incarnation must not retire a replacement.
- [ ] Handle native restart/shutdown notification by checkpointing the eligible
  generation before teardown. Reconcile cancelled shutdowns. Do not rely on
  this notification being delivered during a crash or sudden power loss.
- [ ] Surface failed, corrupt, stale or unavailable evidence explicitly.
  Ambiguous closure stays unconfirmed; absence of a clean-exit hook is not proof
  that automatic resurrection is authorized.

### 3. Reconcile and restore on startup

- [ ] Admit recovery before ordinary dead-mux reconciliation can erase the
  previous boot's useful evidence. Freeze candidate generations and serialize
  recovery through existing ownership mechanisms.
- [ ] Revalidate current worktree existence, native transcript/session identity,
  current head/succession, task policy and holds through their actual owners.
  Terminal, superseded, intentionally closed or uncertain candidates must not
  launch automatically.
- [ ] Reuse a proven surviving session first. Otherwise request supported
  native cold Resume of the exact original conversation in its proper mux.
  Never substitute a new conversation, raw process spawn or a global repair
  command that mutates unrelated sessions.
- [ ] Persist recovery attempt and outcome; reconcile a crash between launch
  and receipt, concurrent startup, seed timeout and delayed registration before
  retrying. Do not claim exactly-once delivery merely from a pre-launch flag.
- [ ] Apply bounded admission and concurrency. Authentication, dependency or
  ownership failures remain visible and resumable without busy retry loops.

### 4. Seed, startup integration and source delivery

- [ ] Deliver an explicit restart seed using the existing supported native
  prompt transport. Identify restart recovery, require current status and
  objective/claim/hold reconciliation, and prohibit interpreting the seed as
  renewed permission for physical or service operations.
- [ ] Verify expected native identity, ready CLI/prompt, fresh registration
  where that optional capability is available, and acknowledged seed pickup.
  Launch, registration and completed restoration are distinct outcomes.
- [ ] Integrate with the existing daemon's platform lifecycle rather than a
  parallel scheduler. Provide the supported boot/logon startup path needed for
  automatic operation, with explicit enrollment and least necessary authority.
  Ship platform implementations or visibly capability-gated refusals; do not
  call untested platform behavior complete.
- [ ] Update actual architecture, configuration, inspection and operator
  recovery documentation. Add required changefiles for touched release units.
- [ ] Drive coherent implementation PRs through review and merge; report
  merged, released and installed states separately. Retain explicit rollout
  authorization and deployment ownership instead of silently enabling recovery.

## Validation Plan

All items below are **agent-recommended** validation obligations.

- [ ] Establish failures against the pre-change behavior before fixing the
  ledger/reconciliation paths; execute the targeted bounded repository runner.
- [ ] Cover intentional exit before/after a periodic checkpoint, delayed old
  exit hooks, shutdown-time process termination, cancelled shutdown, abrupt
  power loss and absent final notification.
- [ ] Cover stale snapshots, torn/corrupt state, denied writes, unavailable boot
  identity and unreachable dependencies without empty/success-shaped fallbacks.
- [ ] Cover same-boot daemon restart and zero-downtime replacement: survivors
  remain untouched and no automatic duplicate launch occurs.
- [ ] Cover concurrent startup, crash after claim and after successful launch,
  late registration, seed timeout despite a ready prompt, and retry after
  uncertainty. Prove no replacement conversation or duplicate native process.
- [ ] Cover changed head, pruned/missing worktree, concluded task, explicit
  manual pause/exit, changed profile, missing transcript and authentication wait.
- [ ] Exercise actual CLI/mux plus restart-seed acknowledgement in disposable
  fixtures or the clean-room rig. Simulate boot epochs; do not reboot or kill
  production sessions to obtain evidence.
- [ ] Exercise supported platform notification/startup adapters and preserve
  Windows/WSL distinction; disclose any remaining lane-specific qualification.
- [ ] Verify ownership, private-state handling, bounded resource use, existing
  manual Resume/restore and optional standalone behavior remain intact.
- [ ] Complete documentation-impact review, required test/installation gates,
  provider review and actual merges. Resolve or transfer every remaining item
  to a named tracked objective before recording this slice complete.

## Completion Gate

The slice is not complete at a ledger prototype, registered process or opened
PR. The eligible original-conversation recovery path, graceful-exit exclusion,
restart seed and platform startup contract must be implemented and qualified,
or individual residual items explicitly transferred to named tracked work.
Any live rollout still requires separate authorization and running-state proof.
The umbrella effort remains Active independently of this slice's completion.

## Journal

### 2026-10-18 - Planning and delegation

- Reused #4564 rather than opening a duplicate tracker. Its completed #5573
  observer remains the starting point; manual task dormancy under #6677 is
  preserved. Captured both operator requests above and separated the proposed
  implementation safeguards from requested behavior.
- Assigned the dedicated native CLI implementer after this proposal's review
  gate. Implementation and platform qualification remain open; no host
  configuration, session recovery or automatic startup has been enabled.
