# Manual Task Session Resume

- **Slug:** `manual-task-session-resume`
- **Repo:** copilot-extensions
- **Branch(es):** planning-only PR; implementation separately authorized
- **Created:** 2026-10-14
- **Status:** Draft
- **Vision:** agent-dispatch task lifecycle; reconcile the manual-owner policy before implementation
- **Umbrella issue:** #6677

## Guiding Intent

Manually claimed work should become dormant when its Copilot process ends,
without surrendering the original task, worktree or session. It must not
automatically wake itself. A later explicit, authorized kick should be able to
resume the original local CLI in a visible mux and deliver the task's continuation.

This is a small planning capture, not authorization to implement or launch an
agent. SUSPENDED versus a new PAUSED state remains a design choice.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning coordinator | Capture intent and publish the draft | Repository planning PR |
| Future implementation owner | Design, implementation and live validation after authorization | Unassigned; claim #6677 before execution |

## Coordination

- **Topology:** one planning PR; a future isolated implementation worktree.
- **Host (owns PRs):** Planning coordinator owns this capture only.
- **Delegates:** none assigned for implementation.
- **Handoff:** the filed issue and this Draft preserve the future objective.
- Preserve existing owners of #3681, #2816, #2879, #5935 and #6007.
  Do not replace their work with a competing recovery implementation.
- The native continuity and installer-repair efforts remain separate and open.

## Context

Queue-state changes and acknowledged wake delivery are not proof that a
manually driven CLI process resumed. An implementation must trace the current
exit, liveness and wake contracts rather than assume that a missing task-worker
service means every possible recovery path is absent.

Agent-dispatch owns task policy; agent-worktrees owns worktree/session resolution
and mux launch; agent-bridge owns live message delivery. Use those boundaries,
not a second session registry or ad hoc process launcher.

Related issues cover lifecycle/conversations (#3681), dead-owner reconciliation
(#2816), automatic idle-worker nudging (#2879), prior-session recovery (#5935)
and external waits (#6007). This effort's distinctive boundary is an
operator-owned manual claim with **no automatic wake-up** and explicit local
headed reconstitution.

## Request

Operator request, verbatim:

> We should probably file an issue/feature request and write a little effort for it. If an agent claims a task for itself and then the user ends the copilot process, the task should probably wind up going to SUSPENDED or PAUSED. For a manually-claimed task, there should be no auto-wakeup, but if something else kicks it, it would be awesome if agent-dispatch could reconsitute the CLI+Mux agent session in its worktree and tell it to resume.

The operator confirmed the slug `manual-task-session-resume`. Capture only:
do not infer implementation authorization from this planning request.

## Plan

### Capture

- [x] Search related issues and file the distinct feature request.
- [x] Record the operator's premise, manual wake policy and local mux/worktree scope.
- [ ] Publish this Draft through the repository's review gate.

### Future design and implementation - requires authorization

- [ ] Trace manual versus supervised claim ownership and graceful/confirmed process exit.
- [ ] Choose SUSPENDED or PAUSED semantics while preserving task/session ownership.
- [ ] Ensure manual claims remain dormant without cooldown or idle-policy auto-wakes.
- [ ] Route an explicit kick through the existing local session/worktree/mux authorities.
- [ ] Deliver continuation and pending steering to the resumed CLI.
- [ ] _(agent-recommended)_ Reconcile the policy to the agent-dispatch vision,
  preserve other owners, and fence concurrent kicks against duplicate/live-owner launch.
- [ ] _(agent-recommended)_ Expose recorded kick, requested launch and acknowledged
  pickup as distinct outcomes; retain visible errors and safe retry.

## Validation Plan

- [x] Read back the issue title, complete body and enhancement label.
- [ ] Validate this Draft's Request against the operator's literal input and review it.
- [ ] Future: manual process exit parks work without automatic wake; explicit kick
  resumes the original local worktree/session in a headed mux.
- [ ] Future: the resumed CLI consumes pending steering and acknowledges task pickup.
- [ ] _(agent-recommended)_ Cover confirmed crash versus unknown liveness, supervised
  policy parity, concurrent kicks, terminal tasks, missing/pruned worktrees and launch failures.
- [ ] _(agent-recommended)_ Prove the actual local CLI + mux loop on supported host
  lanes, including an operator-driven process exit, rather than only mocked queue transitions.

## Proposal

Draft feature request and planning record only. No new lifecycle state, runtime
behavior, automatic launch, remote recovery or host configuration is enacted here.
Review and separate execution authorization precede implementation.

## Journal

### 2026-10-14 - Planning capture

- Confirmed the slug and compatible target effort adoption. Filed #6677 with the
  existing enhancement label and verified its complete content through provider
  read-back. Related issues were retained, not treated as duplicates or replaced.
- Created this small Draft with the exact operator Request and visibly marked
  agent-recommended safety/validation additions. Implementation remains unassigned.
