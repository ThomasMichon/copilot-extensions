# Orphan Task Adoption

## Goal

Give an operator an ordinary interactive place to inspect and steer an
existing headless ACP task or bare scripted task after its original
controlling worktree is gone, including recovery from a different machine.
Resolve the existing resource, create OR reuse
the appropriate controlling worktree, establish explicit authorized
stewardship, and present an interactive session without silently replacing or
resuming the remote job.

- **Slug:** `orphan-task-adoption`
- **Repo:** copilot-extensions
- **Branch(es):** Reviewed proposal before separately authorized implementation
- **Created:** 2026-11-20
- **Status:** Draft
- **Vision:** [Bridge ownership and workspace composition](../../../visions/plugins/agent-bridge/README.md#workspace-allocation-and-session-embodiment);
  [Dispatch workspace continuity](../../../visions/plugins/agent-dispatch/README.md#execution-workspace-continuity)
- **Umbrella issue:** #3443

## Guiding Intent

Preserve the complete four-stage, two-shape request in #3443. Reconnection to
a headed mux session already gives an operator a place to stand; a surviving
headless ACP task and a process with no ACP session need an equally ordinary
interactive control experience. A reachable handle is neither execution
authority nor evidence that a replacement is needed.

## Participants

| Participant | Role in this effort | Reached via |
|---|---|---|
| Planning coordinator | Complete requirement mapping and proposal publication; no implementation claim | #7556 and this effort |
| Implementation coordinator | Unassigned; claim the bounded implementation through the existing public coordination convention | This effort and a later eligible OPEN implementation claim |
| Existing component custodians | Retain their current task, workspace, hosting, provider, credential and service responsibilities | Their existing component coordination records |

## Coordination

- **Topology:** #13718 delivered the reviewed planning contract. Its technical
  design/source and validation gates remain; no worker, provider or controller
  is allocated.
- **Host (owns proposal PR):** Planning coordinator. A later implementation
  coordinator must record its own accepted claim; this proposal appoints none.
- **Delegates:** None.
- **Handoff:** Preserve the named existing resource, current task/result,
  controlling workspace, actual authority and unresolved effects through the
  existing owners. A delivery receipt or missing controller is not transfer.
- [Remote ACP Host Ownership](../remote-acp-host-ownership/README.md#coordination)
  explicitly leaves #3443 orphan adoption separate. Its welded-piston host
  keeps the managed conversation-relationship scope and existing writer,
  lifecycle and retained-transfer gates. Do not extend or replace that plan.
- Bridge/Driver session hosting, Dispatch task/attachment authority,
  Worktrees workspace/custody and container/CodeSpace provider ownership remain
  independently authoritative. Compose their supported boundaries rather than
  writing another component's private store or introducing a second registry.
- #3442's unattended credential lifetime and the remote-job presentation work
  remain separate. Adoption conveys no new credentials, destination access,
  provider lease, process termination or service-management authority.
- **Accepted planned tracking:** Complete current source/receiving/custody
  readback after #13718 preceded the separate acknowledged NOT_PLANNED
  tracking retirement of #3443. All nine technical rows remain unchecked.
  Draft status and actual resource custodians are unchanged; the historical
  source number is requirement provenance, not a future live claim token.

## Context

The complete current #3443 original and all three discussions preserve four
stages for BOTH headless ACP and no-ACP scripted resources. Previously
reported Session Host reattachment, interactive task embodiment, exact
task/session adoption and external-conversation relationship acquisition are
useful finite primitives. They do not by themselves establish the requested
end-to-end existing-resource adoption UX. No new product audit or technical
completion is claimed here.

The existing remote-conversation effort is not a receiving home for this
request: its Coordination explicitly preserves it. This Draft is a separate
planning home, not a second implementation of that relationship owner.
It closes a planning gap under existing Bridge ownership/composition and
Dispatch continuity intent; it changes no vision or runtime contract.
The [architecture patterns](../../../docs/patterns/README.md#design-invariants-binding-contracts)
retain one execution workspace, component-owned command boundaries and honest
unknown outcomes without a mandatory new broker, service or allocator.

## Request

The public source request is [#3443](https://github.com/ThomasMichon/copilot-extensions/issues/3443).
Its four requested stages are retained verbatim:

> 1. Resolves the resource: task/session id -> owning host, container/CodeSpace, and current status (via the same primitives `agent-dispatch show`/`reservations list`/`doctor --check-live-sessions` already expose).
> 2. Creates (or reuses) a worktree for the appropriate repo.
> 3. Spawns an agent into that worktree with an explicit **charter**: take ownership of the named remote resource and task, report its current status faithfully, and stand ready to steer it under the operator's direction -- not to silently resume "normal" work in that worktree as if nothing happened.
> 4. Leaves the operator with an ordinary interactive session they can talk to, exactly as if they'd SSH'd into a mux pane -- just for a venue shape that has no pane to SSH into.

The command name and owning entrypoint remain design choices, not an already
implemented `adopt` command. The source explicitly includes BOTH a surviving
headless ACP task and a bare scripted task with no ACP session.
Private incident examples are not reproduced.

Everything below is an **agent-recommended planning decomposition**, not an
implementation, launch, resource-transfer or rollout grant.

## Plan

### Phase 1 - Reviewed owner-boundary design

- [ ] **OA3443-P1:** Review one supported entrypoint and four-stage transaction
  for BOTH shapes, with the direct examples below. Name the existing authority
  for each stage, real capability/refusal outcomes, recovery after response
  loss and the actual stewardship acceptance boundary. Keep lookup,
  workspace allocation, controller embodiment and remote-job execution
  distinct; do not make all of #7237 or #3442 a prerequisite for independent
  lookup or planning.

### Phase 2 - Existing resource to interactive stewardship

- [ ] **OA3443-P2:** Resolve a named task/session/resource to its actual owning
  host, container OR CodeSpace where applicable, resource incarnation, current
  task/session association, selected execution repository, current holder and
  live/terminal/unknown status through supported owners. Preserve recorded
  results and uncertain effects. Do not guess orphanhood from timestamps,
  missing panes, controller disappearance or a reachable endpoint; unsupported
  resource shapes and unreadable authority remain explicit.
  A controller on a different machine must resolve back to the original task
  authority and venue; its own machine-local queue is not a substitute.
- [ ] **OA3443-P3:** Create OR reuse an appropriate controlling-repository
  worktree through Worktrees, preserving its actual repository, custody and
  logical creation identity across retries. The task's producing queue and a
  code-change target do not select a different controller implicitly. Preserve
  the target repository's authoring/exposure policy, current sessions,
  standalone capability limits and existing remote allocation owners; do not
  create a new remote job merely to obtain a controller.
- [ ] **OA3443-P4:** Embody the controller agent with an explicit charter
  accepting responsibility for the named existing resource AND task, reporting
  current status faithfully and waiting for operator steering. Establish
  authority through the applicable current owner/operator relationship before
  consequential adoption, retaining existing granted scope rather than
  inventing per-message approvals. Record accepted custody and unresolved
  outcomes through existing owners. Do not silently resume ordinary work,
  seize another holder's execution, replay an unknown launch, or substitute a
  replacement remote body. Conversation control does not confer ownership of
  a peer service, host or workspace.
- [ ] **OA3443-P5:** Leave an ordinary interactive controller session that
  supports inspection and authorized steering for BOTH shapes. For headless
  ACP, reconnect to the surviving task/session through its hosting owner; for
  bare scripts, expose truthful process/task progress, retained outputs,
  results and supported control through the existing provider without
  fabricating an ACP session for the remote job. An interactive controller
  may itself use the normal agent host. Terminal jobs remain inspectable;
  unsupported steering and unknown state are visible, not false success.
  Document entry, status, steering, detach/reconnect, refusal and recovery.

### Direct four-stage examples

These are design/validation cases, not commands run or resources acquired.
Apply BOTH columns and ALL four rows with a controller on the original
machine AND with one on a different machine. Cross-machine lookup and control
route through the original task/resource authority and existing secured
transport; an unavailable authority is reported, not recreated locally.

| Stage | Existing headless ACP task | Existing bare scripted task |
|---|---|---|
| 1. Resolve | Resolve `task-alpha` or its session through the task/hosting owner to the actual host, venue, surviving ACP identity, current holder, execution repo and live/terminal/unknown result | Resolve `job-beta` through its provider/task owner to the actual host, venue and process incarnation, current holder, execution repo and recorded status/result; no ACP identity is invented |
| 2. Create OR reuse | Select an eligible controlling repo and create OR reuse its worktree without replacing the surviving ACP job or stealing a current session | Use the same Worktrees boundary for the controlling repo without starting another copy of the script or treating the remote resource's directory as the controller |
| 3. Charter and custody | A normally embodied controller accepts authorized responsibility for that existing ACP task/resource, preserves current authority and unknown effects, reports status and awaits steering | A normally embodied controller accepts authorized responsibility for that existing script task/resource, retains provider/process/credential custody, reports status and awaits steering; it is not the remote script |
| 4. Interactive place | The operator talks to the ordinary controller to inspect retained conversation/task state and issue supported authorized steering, including after controller reconnect | The operator talks to the ordinary controller to inspect progress/outputs/result and issue supported authorized control; no-ACP and unsupported controls remain honest |

## Validation Plan

- [ ] **OA3443-V1:** For BOTH examples, verify every one of the four stages
  from the original machine AND a different controller machine through the
  actual supported ordinary entrypoint, preserving original
  task/resource/result identity and the final interactive inspect/steer
  experience. Verify that cross-machine resolution reaches the original
  authority and venue rather than a new machine-local queue, and that
  unavailable authority is reported truthfully without replacement execution.
  Separate source/fixture evidence from any later explicitly
  authorized native/provider proof; record unavailable tiers rather than
  claiming them from internal reattachment alone.
- [ ] **OA3443-V2:** Verify already-adopted/repeated requests, concurrent
  controllers, changed holders, controller loss and response-loss recovery:
  no duplicate remote execution, replacement body, implicit takeover or lost
  result; the accepted controller/workspace/custody outcome remains
  recoverable. Terminal and unknown resources have distinct truthful outcomes.
- [ ] **OA3443-V3:** Verify denied repository exposure, missing or incompatible
  optional components, unsupported no-ACP controls, absent authority and
  credential/venue failures without access escalation, private-store writes,
  fake success or collateral host/workspace/service effects. Preserve current
  owner/operator limits and the independent #7237/#3442 boundaries.
- [ ] **OA3443-G:** Check this technical gate only after EVERY P1..5/V1..3
  clause is resolved by demonstrated fulfillment at its applicable evidence
  tier OR an explicit accepted transfer to a named tracked objective that
  preserves the clause and actual custody. Required design/source review and
  CI, and direct four-stage/two-shape/original-and-different-machine validation
  remain part of that resolution. Unavailable evidence stays unresolved
  unless its obligation is explicitly accepted in such a transfer; it is
  never a passing tier. Complete documentary receiving acceptance alone
  checks no technical row and is separate from this gate, source-issue
  disposition, resource release and deployment.

## Proposal

Draft, planning only. #13718 delivered complete reviewed receiving; the
subsequent separate judgment retired #3443 as redundant planned tracking,
not completed functionality. All nine Plan/Validation rows remain unchecked.
Neither accepted planning nor source-tracking retirement authorizes running
adoption, launching a controller, acquiring a provider, changing credentials
or taking custody of an actual job.

## Journal

### 2026-11-20 - Reviewed planning and separate tracking disposition

#13718 merged after current-head Copilot approval, required CI and correction
of all actionable findings. Complete current source and receiving readback
accepted the four-stage/two-shape planned contract before the separately
acknowledged #3443 NOT_PLANNED closure. The nine technical rows, Draft status,
independent #7237 relationship and all resource owners remain unchanged.
See the [subsequent campaign record](../backlog-reconciliation/journal/2026/11.20-retained-supervisor-and-existing-canonicals.md#previous-reviewed-adoption-and-separate-source-disposition).

### 2026-11-20 - Complete four-stage, two-shape planning

Created the complete #3443 planning contract after current whole-original and
discussion qualification. The existing remote-conversation effort retains
its distinct scope and custodian. No implementation claim, source disposition,
task inspection, native test, provider operation or rollout occurred.
See the [campaign crosswalk](../backlog-reconciliation/journal/2026/11.20-orphan-task-adoption-planning.md).
