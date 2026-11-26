# Compatible Agent Messaging

## Goal

> We need to make it so that as long as a call to `agent-bridge` specifies a valid agent target, the bridge will take the message. The message should wind up in a durable queue targeting the agent, and then it is up to the ACP session-host or the CLI-side extension to deliver it.

Make ordinary agent messaging depend on compatible target endpoints and
coordinator database schemas, not installation governance, database ownership,
process birth, process incarnation or installation changes. Resolve machine,
repository and existing agent, enqueue once, return a follow-up receipt, and
optionally wait in the caller. The 2026-11-25 requirement supersedes the earlier
dormant-send refusal: receiver availability affects delivery, not durable
acceptance. Sending still does not authorize implicit resume or creation.

- **Slug:** `compatible-agent-messaging`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs targeting `dev`
- **Created:** 2026-11-13
- **Status:** Draft
- **Vision:** [compatibility-first ordinary messaging](../../../visions/plugins/agent-bridge/README.md#compatibility-first-ordinary-messaging)
- **Umbrella issue:** [#12487](https://github.com/ThomasMichon/copilot-extensions/issues/12487)
- **Current acceptance repair:** [#14606](https://github.com/ThomasMichon/copilot-extensions/issues/14606)

## Guiding Intent

Keep the ordinary communication path small and predictable across independently
updated installations. Compatible authenticated peers can communicate without
proving that their runtime construction, process generation or installation
history agrees.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Messaging driver | Contract reconciliation, source changes and PR stewardship | This effort and its public coordination issue |
| Durable acceptance fixer | Finite #14606 producer acceptance and native-answer repair; review and merge stewardship | #14606 and #12487 |

## Coordination

- **Topology:** one writer, dependency-ordered source PRs.
- **Host (owns PRs):** Messaging driver.
- **Delegates:** none assigned.
- **Handoff:** preserve the reviewed contract, original request, open delivery
  gates and live obligations. Existing session, transport and lifecycle owners
  keep their resources.
- This prerequisite does not complete or replace other active lifecycle or
  configured-consumer objectives.
- The durable acceptance fixer owns the bounded amendment below, not another
  inbox or the existing #13734 idle-consumer work. Coordinate actual overlapping
  source edits on the linked issues. Keep original message IDs, receipts,
  unknown-effect fences and independently owned consumer retry limits.

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

### 2026-11-25 - Durable acceptance amendment

The maintainer's current request, verbatim:

> We need to make it so that as long as a call to `agent-bridge` specifies a valid agent target, the bridge will take the message. The message should wind up in a durable queue targeting the agent, and then it is up to the ACP session-host or the CLI-side extension to deliver it.

The required contract is recorded on #14606. Authenticated, well-formed sends
to valid existing agents are durably accepted independently of current
delivery, admission, native-generation or carrier state. Form answers follow
the same model for a valid pending request. Queue/delivery/terminal outcomes
remain observable and original receipts idempotent. Invalid targets and failed
durable writes remain admission failures. This explicitly supersedes only the
older dormant-send rejection; it does not grant implicit resume, creation,
replacement, live replay or deployment.

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
  accept known dormant agents without implicit resume or creation.
- [ ] Enqueue durably through the coordinator, return a stable receipt and
  keep optional delivery waiting in the caller.
- [ ] Register ACP Session Hosts on creation and mux receivers while alive;
  expose registration or delivery failures without losing truthful presence.
- [ ] Remove installation, database-ownership, birth and incarnation gates
  from ordinary message admission and delivery.
- [ ] Preserve old compatible callers and receivers through expansion and
  rollback; retain explicit unknown outcomes rather than replaying sends.

### Finite #14606 - Durable producer acceptance

- [x] Review this amendment before the broader acceptance implementation.
- [ ] Establish controlled failures before changing each affected producer:
  valid represented targets with unavailable/currently inadmissible consumers,
  known ACP targets with unavailable carriers, and exact pending native forms.
- [ ] Separate durable queue writes from receiver selection and effect admission.
  Reuse existing coordinator queues and original receipt identities; do not add
  a competing inbox, daemon or sender-side delivery loop.
- [ ] Resolve known existing agents through current Bridge/Worktrees owners,
  including dormant targets. Preserve exact selectors and unambiguous routing;
  never fabricate a conversation for an empty placement or retarget an original
  uncertain effect to an unrelated native session.
- [ ] Keep input durable through Bridge, daemon and receiver restarts,
  deregistration and registration cleanup. Bind or adopt delivery only through
  the owning ACP Session Host or CLI receiver; unavailable delivery remains
  queued with an observable reason.
- [x] Keep pending form answers in their independent queue, not the prompt
  consumer. Preserve exact request validation, expiration, one-shot effects and
  original audit outcomes through timeout and registration churn.
- [ ] Qualify the producer-first compatibility window, current/prior supported
  callers and receivers, failed durable writes, original-key recovery and
  consumer progress, then land incremental reviewed source PRs.

### Phase 3 - Acceptance

- [ ] Qualify the actual local and remote consumer paths, required source
  checks, review and merge.
- [ ] Record release and separately authorized adoption accurately.

## Validation Plan

- [ ] All three requested command forms resolve to the same target and receipt.
- [ ] Compatible independently updated peers communicate across installation
  and process-generation changes without provenance prerequisites.
- [ ] ACP and mux-backed receivers both register and receive queued messages.
- [ ] Known dormant agents accept durable input without implicit resume or
  creation. Invalid, ambiguous and unauthorized targets fail before enqueue;
  incompatible durable-write support is reported without guessed success.
- [ ] Receipt follow-up distinguishes queued, delivered and consumed evidence;
  optional wait does not change admission or duplicate the original send.
- [ ] Coordinator restart, receiver reconnect and lost acknowledgments preserve
  original queue identity and report uncertain outcomes honestly.
- [ ] Regression tests prove authentication and supported-schema checks remain
  effective without reinstating the removed messaging gates.
- [ ] Required CI, applicable native/external qualification and normal review
  pass against the actual source revision.
- [ ] #14606: unavailable carriers, native-generation changes, unenrolled
  receivers, outstanding retained handoffs and caller wait timeouts do not
  reject or erase an accepted input. Failed durable writes never report
  acceptance. Queued and terminal states retain the original sender/target,
  message ID and idempotency key.
- [ ] #14606: ACP and CLI consumers deliver accepted work at their admissible
  point using their existing effect owner. Restart/reconnect, lost replies and
  registration churn preserve original receipts without sender replay or
  weakening unrelated lifecycle, consent or unknown-effect safeguards.

## Proposal

The six-step flow in Request is the target contract. Detailed source sequencing
will follow reconciliation with the existing owners; no implementation or live
rollout is claimed by this draft.

## Journal

### 2026-11-25 - Represented producer expansion

- The prerequisite amendment and independent native-answer repair merged in
  #14633 and #14627 respectively. The broader completion gate remains open.
- Eight controlled failures across both database layouts established that
  current frontend, epoch, enrollment and heartbeat state blocked a valid
  represented target's producer. The provider expansion now durably accepts
  ordinary input before consumer selection, using the existing inbox and
  original-key receipt owner.
- Schema 50 retains immutable producer-target observations through registration
  cleanup; supported receivers bind delivery on poll, retaining original
  handoffs and unknown effects. Unsupported legacy message semantics remain
  queued, never delivered or acknowledged through the legacy reader.
- The initial source packet passes 232 tests; a further review-fix packet
  passes 213 tests, including mixed unsupported/supported input, consistent
  historical schema, exact target assertions and real two-owner ordering.
  Whole-file strict checks pass for all 24 touched Python files on Linux and
  Windows. Dormant-agent resolution, ACP queue adoption and their
  end-to-end controls remain outstanding. This records source validation, not
  release, installed adoption or live-queue repair.

### 2026-11-25 - Durable acceptance requirement received

- Captured the newer acceptance contract from #14606 and marked the earlier
  dormant-send refusal superseded without deleting its original Request.
- Kept this canonical effort, existing messaging/receipt/lifecycle owners and
  #13734 consumer work; no second inbox or competing effort is proposed.
- The native-form failure was reproduced under both enrolled receiving
  backends and database layouts. The broader producer amendment is proposed
  before implementation; source, release and separately authorized rollout
  remain distinct.

### 2026-11-13 - Current objective captured

- Captured the compatibility-first request without replacing existing open
  lifecycle objectives. Reconciliation and the plan review gate remain open.
