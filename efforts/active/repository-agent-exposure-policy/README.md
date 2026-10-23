---
visions:
  - visions/plugins/agent-worktrees
  - visions/plugins/agent-bridge
---

# Repository Agent Exposure Policy

- **Slug:** `repository-agent-exposure-policy`
- **Repo:** copilot-extensions
- **Branch(es):** independent, source-only proposal and implementation PRs to `dev`
- **Created:** 2026-10-23
- **Status:** Draft; planning only, implementation and rollout not authorized
- **Vision:** extends [agent-worktrees](../../../visions/plugins/agent-worktrees/README.md)
  repository identity/contribution posture and
  [agent-bridge](../../../visions/plugins/agent-bridge/README.md)
  workspace allocation/session embodiment with a repository-owned hosting veto.
- **Umbrella issue:** [#8332](https://github.com/ThomasMichon/copilot-extensions/issues/8332)
- **Related work:** #5674 (machine identity/project enumeration), #7962 (native
  mux launch contract), #356 (project adoption); those owners retain their work.

## Guiding Intent

A repository may participate in the tool suite as a source repository without
being an agent-hosting harness. Reuse `expose_agent: false` as its explicit,
repository-owned negative policy. Managed callers must honor that boundary
rather than treating a registered checkout, an adopted project, or review-agent
instructions as permission to start an authoring agent there.

Keep source worktree allocation, editing, tests and PR operations usable from an
external harness. Preserve externally hosted Copilot Code Review. This is a
suite-managed hosting contract, not an OS-level prohibition on a human running a
vendor CLI.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning coordinator | Capture and land the proposal only | #8332 and the proposal PR |
| Future implementation owner | Claim one admitted slice after execution authorization | #8332; no implementation assignment yet |

## Coordination

- **Topology:** independent, source-only worktree PRs; no agent hosted in this
  repository is needed to propose or implement the policy.
- **Host (owns PRs):** planning coordinator for the initial proposal only.
- **Delegates:** none assigned; existing related-work owners are not redirected.
- **Handoff:** the reviewed effort and #8332 retain all implementation, release,
  and adoption obligations. Planning completion does not close the umbrella.

## Context

The current machine-local `projects.yaml` field `expose_agent` filters Bridge
discovery; `repos.yaml` uses `agent` when projecting local exposure into project
registration. These are not already a repository-owned, consistently enforced
hosting veto. Do not describe the proposed behavior as implemented.

Use the existing repository configuration and resolution rules documented in
[configuration](../../../docs/configuration.md#in-repo-committed), including
canonical `.copilot-extensions/agent-worktrees/config.yaml`, supported legacy
paths and marketplace scope. Do not create a competing Bridge policy file or
infer hosting permission from the presence of agent definitions.

This is a **vision extension**, not merely a visibility fix. Before implementing
the stronger behavior, review the corresponding standing-intent amendment.
Existing as-is documentation remains accurate until runtime behavior changes.

## Request

The operator's relevant instructions, verbatim:

> Never create agent worktrees under copilot-extensions; it doesn't support an agent

> worktree-manager should have refused that, as copilot-extensions should not be registered as a project, only a worktree-class repo

> Let's consider providing a config marker that can be applied to a repo (that is aware of copilot-extensions) that specifically blocks bridging into it. Basically, a `.agent-worktrees/` or `.agent-bridge/` value that callers can check for which indicates that it doesn't bring its own agents.
> To be fair, copilot-extensions *does* have somewhat of an agent, used by Copilot Code Review, but that is its only purpose. copilot-extensions is not intended to be a harness of its own, or used to author itself.

> In the projects.yaml, we support an expose_agent field, I believe. Can we match that naming/convention for consistency?

> Great. Make an effort and add this

The operator confirmed the slug `repository-agent-exposure-policy`. The
immediate assignment is to create the reviewed plan, not execute it.
The proposed contract below records the discussed design; implementation
sequencing and additional safeguards/tests are agent-recommended, not additional
operator authorization.

## Plan

### Phase 0 - Reviewed proposal

- [x] Capture the request and accepted `expose_agent` naming; claim #8332.
- [x] Land the proposal through the repository's review gate. Stop this planning
  assignment there; leave the effort Draft and the implementation backlog open.

### Phase 1 - Intent and effective policy

_Agent-recommended implementation sequence; requires execution authorization._

- [ ] Review a vision amendment distinguishing source repositories from
  agent-hosting projects, before implementing the stronger hosting contract.
- [ ] Define one effective repository-policy resolver, consumed through existing
  plugin boundaries without introducing a second registry or mandatory runtime
  dependency between otherwise independent plugins.
- [ ] Add strict boolean `expose_agent` repository configuration using existing
  canonical/legacy path and marketplace resolution. An explicit repository
  `false` is a veto over stale local `true` and explicit named targets; a `true`
  or absent key does not independently grant adoption or launch authority.
- [ ] Preserve existing machine-local exposure controls and absent-key
  compatibility. Do not silently reinterpret every historical
  `projects.yaml expose_agent: false` as the new repository-owned veto.
- [ ] Refuse malformed explicit policy or an unreadable required policy source
  with an actionable error rather than treating it as permission to host.

### Phase 2 - Managed adoption and launch boundaries

_Agent-recommended implementation sequence; requires Phase 1 and authorization._

- [ ] Apply the effective veto before project-adoption side effects and before
  managed authoring launch effects: Manager/picker, Bridge creation/embodiment,
  resume/re-embodiment, dispatch/autopilot and managed handoff successor paths.
  Inventory actual entry points during implementation; do not assume one front
  covers the others.
- [ ] Check caller planning and the receiving launch boundary, including
  supported remote/provider routes. A stale discovery result or an explicit
  target must not bypass receiver enforcement; fail visibly when a required
  receiver cannot uphold the policy.
- [ ] Preserve source-only workspace allocation and source-control lifecycle
  operations. Separate `create worktree` from `launch agent` even when a caller
  currently composes them.
- [ ] Preserve external Copilot Code Review and review-only instructions without
  a forgeable generic `purpose=review` exemption to managed authoring launch.
- [ ] Return an actionable refusal identifying the policy and suggesting an
  eligible external harness, without silently selecting or spawning one.

### Phase 3 - Adoption and state-preserving reconciliation

_Agent-recommended follow-through; live changes require separate authorization._

- [ ] Add the source-only repository's explicit denial after generic enforcement
  and source-operation compatibility are qualified. Do not add an inert marker
  and claim that hosting is already prevented.
- [ ] Provide a supported preview/reconciliation path for conflicting existing
  project registrations. Preserve sessions, history, tracking and claims; no
  automatic termination, deletion or unregistration.
- [ ] Update authoritative configuration, adoption and launch documentation;
  publish through the normal release flow and validate authorized adoption.
  Keep release, installation and actual enforced behavior distinct.

## Validation Plan

_Agent-recommended proofs for later implementation; not executed by this plan._

- [ ] Exercise strict policy parsing and canonical/legacy/marketplace resolution:
  absent, true, false, malformed and unreadable configurations; no
  success-shaped permission fallback.
- [ ] Prove repository `false` defeats stale local `true`, explicit targets and
  cached discovery, while local exposure restrictions still apply and absent
  policy preserves compatibility without auto-adopting a project.
- [ ] Use isolated fixtures to assert denial occurs before adoption writes,
  session/process/mux creation or seed submission for each inventoried managed
  entry point. Test caller and receiver boundaries, including remote/provider
  routing and unsupported enforcing receivers.
- [ ] Prove source worktree allocation, edits, bounded tests, PR creation and
  completion remain usable under an external harness; externally hosted
  Copilot Code Review remains usable. Agent/review files confer no hosting
  permission and a generic review-purpose parameter cannot bypass denial.
- [ ] Prove a policy introduced after a session exists does not kill it,
  discard its state or erase custody. Newly attempted managed
  resume/re-embodiment is refused; read-only inspection and deliberate
  state-preserving migration remain available.
- [ ] Run targeted bounded suites and the applicable clean-room/native
  Windows/Linux entry-point proofs before release/adoption; no production
  agent launch as an unapproved test.
- [ ] Verify authorized installed policy enforcement and unchanged source/review
  flows before declaring implementation complete. Every Plan and Validation
  item must be resolved or explicitly transferred; a merged plan is not delivery.

## Proposal

Proposed repository configuration, **not implemented by this effort intake**:

```yaml
expose_agent: false
```

The new work is consistent resolution and enforcement, not another flag name.
The implementation must not special-case a repository name or confuse
machine metadata/project enumeration with hosting eligibility.

## Journal

### 2026-10-23 - Planning intake

- Confirmed the existing exposure vocabulary and captured the operator's naming
  correction. Rejected introducing a separate `agent_hosting` key.
- Opened #8332 as the planning coordination token. Prepared this proposal and
  its validation obligations; no runtime, config, registry or agent changed.
- Runtime implementation, vision amendment, deployment and live reconciliation
  remain unassigned and require execution authorization.
- Proposal #8335 merged after a current-head Copilot approval with no findings
  and passing checks. Phase 0 is complete; #8332 and the effort remain open.
  The next slice is the separately authorized Phase 1 vision/policy work, not an
  implicit runtime rollout.
