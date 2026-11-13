# Compatible Agent Messaging

## Goal

> We don't care about installation governance, database ownership, process birth, process incarnation, or installation changes. So long as the target endpoints and DB schemas are *compatible*, that's good enough.

Make ordinary agent messaging depend on compatible target endpoints and
coordinator database schemas, not installation governance, database ownership,
process birth, process incarnation or installation changes. Resolve machine,
repository and live agent, enqueue once, return a follow-up receipt, and
optionally wait in the caller. Dormant targets require explicit resume before
raw send.

- **Slug:** `compatible-agent-messaging`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs targeting `dev`
- **Created:** 2026-11-13
- **Status:** Active
- **Owner:** Released for transfer; successor acceptance pending
- **Vision:** [compatibility-first ordinary messaging](../../../visions/plugins/agent-bridge/README.md#compatibility-first-ordinary-messaging)
- **Umbrella issue:** [#12487](https://github.com/ThomasMichon/copilot-extensions/issues/12487)

## Guiding Intent

Keep the ordinary communication path small and predictable across independently
updated installations. Compatible authenticated peers can communicate without
proving that their runtime construction, process generation or installation
history agrees.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Messaging driver | Contract reconciliation, source changes and PR stewardship | This effort and its public coordination issue |

## Coordination

- **Topology:** one writer, dependency-ordered source PRs.
- **Host (owns PRs):** Transferring; the successor must explicitly accept PR stewardship.
- **Delegates:** none assigned.
- **Handoff:** preserve the reviewed contract, original request, open delivery
  gates and live obligations. Existing session, transport and lifecycle owners
  keep their resources.
- This prerequisite does not complete or replace other active lifecycle or
  configured-consumer objectives.

## Handoff

The Goal and complete Request remain unchanged. Implementation is frozen for an
assigned ownership transfer, not declared complete. The current source is WIP:
no implementation PR, release or runtime adoption has occurred.

- Preserve the shared source checkpoint, original queue/handoff identity and all
  unfulfilled Plan and Validation Plan items. Do not treat source publication
  as merge, acceptance or permission to update a running installation.
- The latest regression packet passed ACP receiving (9 cases), native carrier
  controls (10), HTTP send retry controls (36) and recovery capture probes (2).
  Producer coverage passed 64 cases and failed 2 valid typed-startup admission
  cases; later groups did not run. Earlier receipt-wait coverage passed 29 cases,
  database migration coverage passed 13 and JavaScript receiver controls passed 38.
- First source step: reconcile the valid typed-startup path in
  `live_message_enqueue.py`: readiness validation returns an unassociated
  admission, but the subsequent insertion guard still refuses that startup
  kind against an enrolled consumer. Preserve original operation/input
  validation; do not weaken controls or hide the failing cases.
- Remaining work includes the final source revision's regressions, remote
  routing/receipt qualification, documentation reconciliation, install-contract
  checks, normal review/merge and truthful release evidence.
- The separately bounded queued-to-steer repair under #6394 may touch the same
  extension delivery files. Its owner has not accepted this broader client,
  carrier, database and ACP compatibility scope. Coordinate before transferring
  those edits; do not infer acceptance from a scope reply or transport receipt.
- Installation, service, credential, existing-session and uncertain-message
  obligations remain with their current owners. This transfer grants no new
  runtime-effect authority.

## Context

The current retained-inbox negotiation can reject a registered live receiver
because installation, epoch or process-custody observations are unavailable.
The requested ordinary messaging contract instead admits compatible endpoints
and schemas. This extends the Bridge vision's ordinary messaging contract.
The existing service-contract evolution pattern continues to own supported
operation/schema compatibility, without a new per-send preflight ceremony.

Existing [contract evolution](../agent-bridge-contract-evolution/README.md)
retains non-messaging lifecycle, event and durable-record semantics.
[Session discovery](../agent-bridge-session-discovery/README.md) retains its
broader catalog and cross-machine discovery work. This effort reuses their
existing interfaces; it neither claims their entire objectives nor makes
completion of those programs a prerequisite for the ordinary send flow.
[Bridge delivery status](../bridge-delivery-status/README.md) retains its
existing receipt meanings, finite wait and status-subscription work. Reuse
those receipts and wait interfaces rather than create a parallel delivery
ledger or subscription service.

For ordinary message admission and delivery, this request replaces installation,
epoch and process-custody prerequisites of the earlier retained-inbox path.
That path's current refusal tests are implementation history, not a reason to
reinstate those prerequisites. Independent lifecycle operations and durable
message identities keep their own semantics.

## Request

The operator's 2026-11-13 request, verbatim:

> That's way too many point of failure. We basically need "duck typing" compatibility here.
>
> > <repo> bridge send --agent <agentid> --message "_____"
> > ssh <machine> -- <repo> bridge send --agent <agentid> --message "_____"
> > <repo> bridge send --machine <machine> --agent <agentid> --message "_____"
> All we need to do is:
> 1. Resolve machine
> 2. Resolve target repo
> 3. Resolve target agent, either finding a directly-registered agent via ACP/session, or a matching worktree owned by the system (i.e. shows up in agent-worktrees list or agent-bridge list)
> 4. Put message queued into agent-bridge coordinator DB for delivery
> 5. Return "receipt" for followup
> 6. Invoke optional "wait for delivery" step in CLI caller
>
> ACP agents will be wrapped in a session-host, which should be registered with Bridge upon creation.
> Mux-hosted agents *should* have reached out to bridge directly, while alive, to register with Bridge, via the agent-bridge extension (being migrated to agent-remote-driver extension)
> Dormant agents won't be registered with the bridge as live, and should get rejected by raw send, as the caller needs to explicitly request "resume" on them first.
> We don't care about installation governance, database ownership, process birth, process incarnation, or installation changes. So long as the target endpoints and DB schemas are *compatible*, that's good enough.

## Scope Approvals

The request selects source behavior and its ordinary delivery contract.
It does not authorize shared service deployment, credential changes, live
session replacement, replay of an uncertain send or disposal of retained state.
Authentication and authorized destination selection remain independent of
compatibility. Lifecycle operations may retain their own process-identity
checks; those checks must not become prerequisites for ordinary messaging.

## Plan

The dependency ordering below is agent-recommended implementation sequencing,
not additional operator requirements or live-effect authority.

### Phase 1 - Reconcile and review the contract

- [x] Reconcile the current communication vision, active efforts and source
  owners without creating competing implementations.
- [x] Review the requested compatibility-first contract before implementation.

### Phase 2 - Deliver the complete ordinary flow

- [ ] Unify local, direct SSH and routed-machine target resolution.
- [ ] Resolve a compatible live ACP/session or system-owned worktree receiver;
  reject dormant targets without implicit resume or creation.
- [ ] Enqueue durably through the coordinator, return a stable receipt and
  keep optional delivery waiting in the caller.
- [ ] Register ACP Session Hosts on creation and mux receivers while alive;
  expose registration or delivery failures without losing truthful presence.
- [ ] Remove installation, database-ownership, birth and incarnation gates
  from ordinary message admission and delivery.
- [ ] Preserve old compatible callers and receivers through expansion and
  rollback; retain explicit unknown outcomes rather than replaying sends.

### Phase 3 - Acceptance

- [ ] Qualify the actual local and remote consumer paths, required source
  checks, review and merge.
- [ ] Record release and separately authorized adoption accurately.

## Validation Plan

- [ ] All three requested command forms resolve to the same target and receipt.
- [ ] Compatible independently updated peers communicate across installation
  and process-generation changes without provenance prerequisites.
- [ ] ACP and mux-backed receivers both register and receive queued messages.
- [ ] Dormant, ambiguous, unauthorized and incompatible targets fail clearly
  before enqueue; raw send never resumes or creates them.
- [ ] Receipt follow-up distinguishes queued, delivered and consumed evidence;
  optional wait does not change admission or duplicate the original send.
- [ ] Coordinator restart, receiver reconnect and lost acknowledgments preserve
  original queue identity and report uncertain outcomes honestly.
- [ ] Regression tests prove authentication and supported-schema checks remain
  effective without reinstating the removed messaging gates.
- [ ] Required CI, applicable native/external qualification and normal review
  pass against the actual source revision.

## Proposal

The six-step flow in Request is the reviewed target contract. Implement it
through the existing resolver, coordinator queue, receipt and receiving owners;
source acceptance and any separately authorized rollout remain distinct.

## Journal

### 2026-11-13 - Current objective captured

- Captured the compatibility-first request without replacing existing open
  lifecycle objectives. Reconciliation and the plan review gate remain open.

### 2026-11-13 - Contract accepted

- #12488 merged to `dev` as `da5b48ada8c0db681a360e9c4b17207b6fb8c674`
  after Copilot approval and required source checks. The reviewed vision and
  complete request now govern implementation.
- Phase 1 is complete. Phase 2 starts with the existing send parser and target
  resolution path, preserving queue/receipt meanings and explicit lifecycle
  operations rather than introducing another coordinator.
- No runtime behavior, installation, live session or deployment changed as
  part of the intent-only merge.

### 2026-11-13 - Ordinary-path implementation in progress

- Implementing the named send arguments and explicit machine route through the
  existing carrier, with read-only live-target selection and no implicit
  create/resume. Delivery observation is being separated from admission.
- Reusing the existing queue and one-shot handoff records for compatible inbox
  receiving, without installation, epoch or process-provenance admission.
  Original handoff identity and uncertain SDK outcomes remain retained.
- Four focused HTTP cases pass against a fresh database: absent governance,
  a closed unrelated lifecycle epoch, missing process observations and changed
  process observations. Each exercises admission, original receipt, one-shot
  delivery and consumption. A lean context-free fixture avoids irrelevant
  installation setup.
- Source qualification is incomplete. Earlier broader selections hit bounded
  execution limits, including a SQLite connection timeout during fixture setup
  before a test body; those attempts are not passing results.
- Native API wiring and JavaScript receiver controls are under regression
  testing. No implementation PR, release, shared-runtime update or live-session
  replacement has been performed. All remaining Plan and Validation Plan
  items stay open.
