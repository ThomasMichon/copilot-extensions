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
- **Status:** Draft
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
- **Host (owns PRs):** Messaging driver.
- **Delegates:** none assigned.
- **Handoff:** preserve the reviewed contract, original request, open delivery
  gates and live obligations. Existing session, transport and lifecycle owners
  keep their resources.
- This prerequisite does not complete or replace other active lifecycle or
  configured-consumer objectives.

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
- [ ] Review the requested compatibility-first contract before implementation.

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

The six-step flow in Request is the target contract. Detailed source sequencing
will follow reconciliation with the existing owners; no implementation or live
rollout is claimed by this draft.

## Journal

### 2026-11-13 - Current objective captured

- Captured the compatibility-first request without replacing existing open
  lifecycle objectives. Reconciliation and the plan review gate remain open.
