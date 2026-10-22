# Agent-Bridge Delegation Convergence

- **Slug:** `agent-bridge-delegation-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** issue-bound PR worktrees to `dev`; parallel readers and
  serial shared-state writers
- **Created:** 2026-08-31
- **Status:** Active
- **Vision:** closes
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §Features/`task-shaped-delegation-control`,
  `attention-boundary-subscriptions`, `bounded-delegated-results`, and
  `single-stream-message-admission`, plus
  §Behaviors/`attention-requires-a-live-relationship` and
  `prompt-injection-requires-single-stream-proof`
- **Original umbrella:** [#1448](https://github.com/ThomasMichon/copilot-extensions/issues/1448);
  complete original/inherited acceptance is retained directly below
- **Historical phase references:**
  [#1449](https://github.com/ThomasMichon/copilot-extensions/issues/1449)
  (delegated-agent lifecycle and attention contract) ·
  [#1450](https://github.com/ThomasMichon/copilot-extensions/issues/1450)
  (attention-oriented wait) ·
  [#1451](https://github.com/ThomasMichon/copilot-extensions/issues/1451)
  (queue-first, handoff-safe steering) ·
  [#1452](https://github.com/ThomasMichon/copilot-extensions/issues/1452)
  (bounded delegated-result snapshots) ·
  [#1453](https://github.com/ThomasMichon/copilot-extensions/issues/1453)
  (idempotent single-stream live-session admission) ·
  [#1506](https://github.com/ThomasMichon/copilot-extensions/issues/1506)
  (task-shaped control facade) ·
  [#1454](https://github.com/ThomasMichon/copilot-extensions/issues/1454)
  (AHP projection)
- **Related issues/dependencies:** [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460)
  (version-skew-safe contract foundation) ·
  [#1468](https://github.com/ThomasMichon/copilot-extensions/issues/1468)
  (contract registry and baseline fixtures) ·
  [#22](https://github.com/ThomasMichon/copilot-extensions/issues/22)
  (terminal reconciliation after transport loss) ·
  [#48](https://github.com/ThomasMichon/copilot-extensions/issues/48)
  (worktree ownership reservation) ·
  [#60](https://github.com/ThomasMichon/copilot-extensions/issues/60)
  (live-session lease reconciliation) ·
  [#1045](https://github.com/ThomasMichon/copilot-extensions/issues/1045)
  (low-noise progress inspection) ·
  [#112](https://github.com/ThomasMichon/copilot-extensions/issues/112)
  (in-place handoff) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (immutable event identity) ·
  [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266)
  (AHP host convergence) ·
  [#1267](https://github.com/ThomasMichon/copilot-extensions/issues/1267)
  (coordinator-first delegation guidance) ·
  [#954](https://github.com/ThomasMichon/copilot-extensions/issues/954)
  (venue parity)

## Guiding Intent

Make delegation through agent-bridge feel as compact and steerable as native
sub-agent control while remaining truthful about the bridge's broader execution
model.

A caller should be able to create a delegated agent, retain one identity, read a
bounded account of its accumulated work, steer it with another message, wait
until attention is required, interrupt or cancel its current work, and retire it
deliberately. Those semantics should remain the same whether the target is local,
remote, container-hosted, CodeSpace-hosted, ACP-driven, or presented through an
AHP client.

The bridge must not imitate a guarantee it cannot provide. An attached caller or
retained subscriber can be released at an attention boundary; a caller that
submits and disappears has detached work, not an implied future wake-up channel.
Durable fire-and-forget task ownership remains agent-dispatch's concern.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Delegation contract driver | Lifecycle vocabulary, CLI/API surface, attention semantics, and phase sequencing | Isolated planning and implementation worktrees |
| Contract foundation lane | Session-pinned capabilities, durable cursors, writer gates, and mixed-version fixtures | #1460 |
| AHP consumer lane | Maps the same delegation semantics onto an AHP host/client boundary without redefining AHP | #1266 |
| Venue validation lane | Proves the same control behavior across local and remote venues | #954 parity harness |

## Coordination

- **Topology:** #1449 freezes the semantic contract first. #1450 and #1452 may
  then proceed independently. #1451 and #1453 land serially because both touch
  message admission and queue ordering, and wait for #1460/#1468 gates before
  adding durable writers. #1506 composes the stable slices into the compact
  facade. #1454 lands through the #1266 AHP PR lane.
- **Host (owns PRs):** delegation contract driver.
- **Delegates:** compatibility machinery lands through #1460, AHP mapping through
  #1266/#1454, and cross-venue runner changes through #954. Each claimant owns
  only its numbered issue and focused tests.
- **Handoff:** each phase lands one independently usable control increment with
  CLI/API documentation, compatibility fixtures, and Journal evidence before
  the next phase changes defaults. Any phase that changes the bridge-owned
  durable ledger waits for #1460's registry and reader/fencing review.
- **Single-writer seams:** delegated lifecycle vocabulary, message admission,
  queue ordering, shared event identity, and bridge-owned durable schemas land
  serially even when result and attention readers proceed in parallel.
- **Continuing tracking:** this reviewed Active Plan, Validation and Journal
  retain the original umbrella and all adopted phase acceptance. Numbered phase
  references above are historical requirements, not closed live claim tokens.
  Preserve existing independently claimed source slices, assigned #1266 and all
  resource/activation holds; future implementation uses an eligible OPEN
  bounded-source claim and a Journal entry under the repository contribution
  policy. Consolidation moves no claimant, source, resource or phase gate.
  Actual reviewed merge and complete current readback precede supersession of
  #1448; retain it if concrete custody still requires the live umbrella.

## Context

The current bridge already exposes most required mechanisms: create, send, read,
wait, interrupt, end, persistent session identity, event cursors, Session Host
survival, and venue resolution. Their ergonomics and completion meanings are not
yet one compact delegation contract. Callers can be forced to interpret raw
events, distinguish several wait modes, or infer whether a finished invocation
means completed work, a request for input, a permission boundary, transport
loss, or merely a detached caller.

Native sub-agent control establishes a useful interaction model: retain an agent
handle, read accumulated work, continue it with another turn, wait for completion
or attention, and cancel deliberately. Agent-bridge should converge on that
model where the semantics are portable, while preserving the capabilities that
make it broader: durable sessions, reconnect by cursor, independent process
lifetime, remote venues, peer ownership, and explicit attached versus detached
relationships.

The sibling
[`agent-bridge-contract-evolution`](../agent-bridge-contract-evolution/README.md)
effort owns version-skew safety and session pinning. This effort owns the
delegation vocabulary and observable outcomes carried over that foundation. The
AHP effort may expose the same underlying behavior to standard host clients, but
does not redefine this lifecycle or turn bridge extensions into AHP core.

## Request

> Reconcile agent-bridge's convergence toward Copilot sub-agent ergonomics with
> its AHP host convergence and shared contract-evolution foundation, preserving
> clear ownership and truthful attached versus detached behavior.

## Plan

### Phase 0 — Freeze the delegation contract

Tracked by #1449.

- [x] Inventory current create, send, read, wait, interrupt, stop, resume, end,
      event, and result surfaces and map each to the intended compact lifecycle.
- [x] Define one stable delegated-agent identity and the relationship among
      session instance, session lineage, target, caller cursor, and successor.
- [x] Define attention-boundary reasons for completion, failure, input,
      permission, policy decision, and unrecoverable reachability loss.
- [x] Define attached, subscribed, and detached caller relationships without
      promising an asynchronous wake-up channel after the caller disappears.
- [x] Define the scope, lifetime, and succession behavior of a logical-message
      idempotency key so steering and represented-session admission consume one
      meaning.
- [x] Publish a compact delegation baseline that maps current behavior to the
      target vocabulary and names the owner of every remaining delta.
- [x] Keep #1468 authoritative for current wire/durable shape provenance while
      this phase owns the lifecycle-vocabulary mapping layered over those
      fixtures.
- [x] Pin the selected semantics in
      [`plugins/agent-bridge/docs/delegation-contract.md`](../../../plugins/agent-bridge/docs/delegation-contract.md)
      and transfer registry/fixture integration to #1460/#1468 before any new
      delegation-state writer or default is enabled.

### Phase 1A — Deliver bounded accumulated results (#1452)

- [x] Return bounded current state, latest result, and incremental work since a
      caller-held position.
- [x] Keep raw events and full detail available through explicit fidelity and
      recovery access rather than injecting them into the ordinary caller path.
- [x] Use only stable event or projection identities supplied by #1138.
- [x] Represent truncation, unavailable detail, and reduced-fidelity targets
      explicitly.
- [x] Consume #1045's liveness/progress inspection plane for "is it advancing?"
      while owning the result projection for "what did it produce?"; do not add
      a second competing status payload.

### Phase 1B — Make waits attention-oriented (#1450)

- [x] Let a retained caller or subscriber wait for selected attention boundaries
      rather than only transport completion or one successful turn.
- [x] Return a bounded structured reason, durable position, and current target
      identity at settlement.
- [x] Resume ordinary transport interruptions by cursor inside the retained
      relationship instead of settling the wait prematurely.
- [x] Carry an attention subscription across a deliberate successor handoff
      only when the successor negotiates semantics compatible with the caller's
      retained contract. Otherwise settle with an explicit contract-changed
      attention reason and the successor identity.
- [x] Consume #22's terminal-event reconciliation for transport failure; #1450
      owns wait settlement semantics, not the underlying terminal emission.

### Phase 2 — Make steering queue-first and handoff-safe (#1451)

- [ ] Admit each accepted prompt exactly once to the authoritative controller for
      the session lineage.
- [ ] Durably queue steering sent to a busy target and preserve sender
      attribution and order.
- [ ] Distinguish interrupting the current turn, stopping future work, ending the
      session, and retiring its durable representation.
- [ ] Return an acknowledgement identifying immediate versus queued admission
      and the durable logical-message/queue identity.
- [ ] Define succession behavior now and keep its handoff conformance case
      explicit; the live test may remain gated until #112's handoff primitive is
      available.

### Phase 3 — Fence represented-session prompt admission (#1453)

- [ ] Deny prompt injection when a represented interactive target cannot prove
      authoritative identity, serialized admission, and one-turn creation.
- [ ] Compose #48's ownership reservation and terminal `taken-over` state plus
      #60's lease/liveness reconciliation; this slice adds logical-message
      idempotency and submission-to-turn/result correlation rather than another
      ownership store.
- [ ] Cover duplicate extension-handler delivery, ambiguous acknowledgement
      retry, overlapping resume, stale subscribers, and duplicate rendering
      versus duplicate model execution.
- [ ] Keep lower-fidelity represented sessions useful for presence,
      observation, and attributed notification when prompt admission cannot be
      proven safe.

### Phase 4 — Present the task-shaped control facade (#1506)

- [ ] Provide one compact create/identify/read/steer/wait/interrupt/stop/resume/
      end model across CLI and authenticated API surfaces.
- [ ] Return a stable delegated-agent handle and durable position rather than
      requiring a caller to reconstruct identity from process or transport
      data.
- [ ] Keep advanced bridge operations available, but make ordinary delegation
      independent of venue-specific commands or raw protocol details.
- [ ] Preserve existing command compatibility while the new surface is
      capability-gated and canaried through #1460/#1468.

### Phase 5A — Prove venue parity (#954)

- [ ] Run the same lifecycle and attention fixtures against local, peer-owned,
      trusted-container, and CodeSpace targets through #954.
- [ ] Verify that a provider or frontend update does not reinterpret the selected
      delegation contract of an existing session.

### Phase 5B — Map the contract through AHP (#1454, #1266)

- [ ] Expose the model through AHP only where #1266 can map it to standard
      lifecycle, state, actions, or explicitly negotiated extensions.
- [ ] Preserve reduced-fidelity representations for interactive or native-owned
      sessions instead of advertising unsupported control.
- [ ] Consume #1449's lifecycle/identity contract and #1450/#1452's
      attention/result outcomes in the AHP resource, action, and subscription
      model without redefining them inside the adapter.

### Phase 6 — Migrate callers and retire ambiguity

- [ ] Publish migration guidance from current bridge verbs and wait modes to the
      compact lifecycle.
- [ ] Measure use of legacy result, wait, and injection paths before changing
      defaults.
- [ ] Stop legacy writers before removing readers or adapters.
- [ ] Retire an older semantic path only through #1460's live/durable reference
      census and rollback gate.

### Direct delegation acceptance consolidated from #1448

<!-- backlog-reconciliation:7556:transfer:1448:to:efforts/active/agent-bridge-delegation-convergence/README.md -->

This direct Plan and Validation supplement retains complete unresolved acceptance within
this existing Active effort. Preserve its complete current Plan, completed phases,
Coordination and existing claimants. This is tracking consolidation of the semantic
umbrella and all its already-adopted #1451/#1453/#1454/#1506/#1361/#2341/#2339 clauses,
not implementation completion, a new controller, reassignment or rollout. Existing
phase/record/provider owners and assigned #1266 remain unchanged. Reconcile the
umbrella/intermediary pointers to these exact still-open subsections only after normal
reviewed delivery and complete public readback; otherwise #1448 stays open.

#### Plan: lifecycle, ordering and ordinary steering

- [ ] Preserve one create/identify/read/steer/wait/interrupt/stop/resume/end-or-retire
  vocabulary across CLI and authenticated API, one stable logical delegate and durable
  caller position, bounded current/latest/incremental human/JSON output, explicit raw
  expansion/truncation/unavailability and structured failures. Advanced operations
  remain available; current consumers receive documented aliases or capability-gated
  adoption.
- [ ] Preserve completion/failure/input/permission/policy/unrecoverable-reachability
  attention reasons, attached/subscribed/detached distinctions, cursor recovery and
  compatible successor following. Detached work survives without promising an ended
  caller a future wake channel; contract-incompatible succession settles explicitly with
  successor identity.
- [ ] Retain the frozen lifecycle baseline and completed result/wait slices. Ordinary
  Phase2 steering precedes represented Phase3 admission serially; facade Phase4 composes
  the consumed stable slices. Result/wait readers may proceed independently. AHP
  projection remains a separate lane through assigned #1266 and does not gate AHP
  ordering Phase3 or completion of the core delegation surface.
- [ ] Keep autonomous task ownership/scheduling with Dispatch,
  compatibility/pinning/reader-fencing with #1460, immutable event/reference semantics
  with #1138, exact AHP with #1266, and existing handoff/claim/lease/provider primitives
  with their owners. Do not create a second lifecycle/controller/ownership ledger or
  reopen their completed finite slices.
- [ ] Ordinary steering non-destructively wakes idle/resumable delegates and durably
  queues behind running/busy work. Retain the actual busy-before-queue CLI end-to-end
  regression so queue intent reaches admission, without cancelling the active turn or
  silently changing legacy consumer semantics.
- [ ] Durable acknowledgements distinguish immediate versus queued admission and
  correlate logical message, queue identity and sender attribution. Apply contract-key
  dedup before admission, including ambiguous-acknowledgement retry; preserve
  FIFO/exactly-once dequeue through caller reconnect and daemon restart.
- [ ] Route through the logical delegate and retain queued steering across deliberate
  successor handoff. Keep an explicit succession fixture gated only while its
  prerequisite is genuinely unavailable; existing handoff primitives alone are not
  queue-transfer proof. Explicit interrupt/replacement remains a separate intentional
  operation.

#### Plan: represented admission and compact launch facade

- [ ] Bind represented submissions server-side to canonical owning session and the
  frozen logical-message idempotency scope. Fence the authoritative controller's
  generation/lease across overlap, resume/reconnect, stale subscribers and taken-over
  registrations; one logical message creates at most one turn on one stream.
- [ ] Distinguish passive notification/attachment, current-turn steering, future enqueue
  and fresh-turn start. Unsupported adapters retain truthful
  presence/observation/attributed notification and explicitly refuse unsafe injection
  rather than adding a hidden controller.
- [ ] Correlate submission, queue, turn/model-call, user-message and response identities
  so duplicate rendering/history is distinguishable from duplicate inference. Exercise
  duplicate extension delivery, ambiguous acknowledgements, overlapping resumes,
  stale/taken-over controllers and subsequent user messages after reconnect.
- [ ] Consume current reservation/taken-over and lease/liveness primitives. Keep
  ordinary-startup deterministic-peer/source lanes separately owned; newly merged keyed
  native startup/receipt source is positive bounded credit, not represented-session or
  qualified native-product completion.
- [ ] Keep facade handles/positions stable through stop/resume/succession; compose
  attention/results/admission without another authority. Preserve advanced verbs,
  existing CLI/REST consumers, semantic compatibility aliases and
  reader/writer/default/retirement gates.
- [ ] Carry the already-accepted structured per-session arguments and/or MCP toolsets
  from supervisor/caller through compact CLI parsing, client serialization, request
  validation, session construction and actual child/ACP execution. Preserve venue versus
  charter; never replace typed data with shell interpolation or mutate global agent
  settings.
- [ ] Preserve provider launch/remote-ACP/ownership/protocol restrictions, supported
  resume/reattach tool configuration and worker isolation. Validate
  malformed/quoted/special inputs, unsupported options, exact forwarding and actual
  required-tool availability; a parser flag or mocked serializer is not the whole worker
  capability.

#### Plan: AHP and venue/migration lanes

- [ ] Map logical identity/succession, attention and bounded results/expansion
  references through the selected released AHP version's actual standard
  resources/state/actions/capabilities or negotiated extensions. Do not embed private
  REST/SSE assumptions, invent another lifecycle/controller/replay authority or turn
  Bridge federation into mandatory AHP core.
- [ ] Reconnect, multiple AHP clients and handoff preserve one admission authority and
  ordered stream. Equivalent delegated scenarios through Bridge controls and the AHP
  face retain lifecycle/attention/results. Distinguish upstream AHP hosting from
  downstream ACP; unsupported capabilities degrade explicitly without dropping state or
  advertising unsupported control.
- [ ] Feature-detect compatible actual released native hosts. Protocol fixtures,
  mock/model-free witnesses and remote ACP connectivity remain their actual evidence
  tiers, not native-product adoption. Preserve assigned #1266 codecs/conformance
  custody; AHP projection does not delay independent AHP ordering work.
- [ ] Preserve equivalent supported local, peer, trusted-container and CodeSpace
  lifecycle/attention behavior and honest restricted/reduced fidelity. Exercise the
  complete compact vocabulary on at least one remote venue. Preserve existing sessions'
  negotiated semantics through frontend/provider/default changes; measure legacy use,
  stop old writers before retiring readers/adapters, and consume existing
  reference-census/rollback gates.

#### Plan: represented-inbox provenance, target binding and durable audit

- [ ] Every actual inbox delivery path visibly distinguishes admitted sender, real
  channel, exact intended/live session target, stable message identity and canonical
  reply route from operator input. Labels, quoted envelopes and payload assertions are
  not authentication or enlarged permission.
- [ ] Prime receivable sessions before interpretation through their existing owned
  startup/continuity guidance. Use admitted host/transport relationship and operator
  binding for authority; retain legitimate delegated in-scope peer coordination without
  blanket rejection, operator impersonation or a competing trust authority.
- [ ] Validate exact authorized session/worktree/project/controller incarnation across
  aliases, stale/reused handles, reconnect and handoff. Test/experiment contexts cannot
  silently reach unrelated production conversations without configured
  ingress/delegation authority; a syntactically routable handle or copied tool/nudge
  label is insufficient.
- [ ] If an actual path uses a mux, retain its owning mux-session plus pane identity and
  revalidate intended session before injection or retirement. Do not invent pane
  transport for direct SDK delivery or conflate the separately corrected pane primitive
  with inbox routing.
- [ ] Maintain a bounded authorized durable sender -> intended target -> logical
  message/payload identity -> admission/delivery/consumption audit across
  restart/cutover, with explicit privacy, access, retention and redaction. Report
  unavailable evidence honestly; neither transient rows nor method/path/status logs
  alone suffice. Every delivery path and cleanup/rotation must retain sufficient
  controlled incident evidence without logging secrets or retaining all bodies
  indefinitely.
- [ ] Keep admission, SDK acceptance, user-message consumption and semantic application
  distinct. Delivery/receipt is not operator approval, successor ownership or retirement
  permission.
- [ ] Retain the corrected causal boundary: the withdrawn status-monitor/postToolUse
  attribution and separate pane-collision primitive are not this inbox incident's cause
  or proof of completion. No private incident identifier or imperative payload is needed
  in public tracking.

#### Validation: direct acceptance, not title/link equivalence

- [ ] Verify lifecycle/attention reasons, stable handles/positions, bounded output and
  explicit raw detail across supported surfaces; detachment never fabricates a retained
  wake relationship. Keep the already-completed finite result/wait evidence credited
  rather than resetting it.
- [ ] Verify running/idle/stopped-resumable/busy steering, the busy-before-queue CLI
  path, attributed durable acknowledgements, logical-key ambiguous retries, FIFO
  exactly-once drain through reconnect/restart, and deliberate succession with only
  genuinely unavailable prerequisites explicitly gated.
- [ ] Verify single-controller represented admission under two concurrent callers,
  duplicate extension deliveries, stale/taken-over generations, overlapping resumes and
  subsequent user turns after reconnect; correlate actual
  logical-message/admission/turn/model-call/user-message/response outcomes, not only
  rendered text.
- [ ] Verify exact launch arguments/toolsets across the entire caller-to-worker path,
  supported resume/reattach, malformed/quoted/unsupported cases, provider restrictions,
  isolated workers and actual required-tool availability without global changes.
- [ ] Verify actual receiving-model source distinction and startup grounding,
  intended-target/canonical-reply success, wrong/stale/foreign/reused-target refusal
  across unrelated sessions, duplicate/restart/retry and durable audit retention. Do not
  inject into an unconsenting live conversation; unavailable native lanes remain
  explicit rather than credited from envelope-only units.
- [ ] Verify equivalent supported local/peer/container/CodeSpace outcomes, the full
  vocabulary in at least one remote venue, exact supported old/new negotiation before
  effects, existing-session semantic pinning, restricted-authority denial and the
  separately owned focused AHP mapping/native acceptance where originally required.
- [ ] Preserve source/release/installed/native evidence distinctions and every current
  claimant/hold, serial writer boundary and accepted alternative. No new ACK ceremony,
  all-host/future-program completion gate or resource release is created by
  consolidation. Ordinary accepted source contributions retain their earned credit.

Public sources: https://github.com/ThomasMichon/copilot-extensions/issues/1448 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1509 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1566 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1588 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1899 ;
https://github.com/ThomasMichon/copilot-extensions/pull/7593 .

## Validation Plan

- [ ] Complete every directly retained #1448 Plan/Validation row above at its
  original evidence tier and bounds, preserving existing owners and holds.
  Tracker consolidation is not implementation or native acceptance.

- [ ] A caller can create, read, steer, wait, interrupt, and end one delegated
      target without learning its venue or transport.
- [ ] Bounded reads never require ingesting the complete transcript or raw tool
      stream, and explicit detail access remains available.
- [ ] Completion, failure, input, permission, policy, and reachability attention
      reasons are distinguishable and carry a durable resume position.
- [ ] Detaching preserves the target but creates no false promise that the
      caller's model loop will wake later.
- [ ] Detached status/result reads explicitly report that no retained
      attention relationship exists.
- [ ] Two concurrent callers produce one serialized prompt stream and at most one
      model turn per accepted prompt across retries. Successor-handoff coverage
      may use the explicit gated conformance fixture until #112 lands.
- [ ] Busy-target steering queues durably in order and survives caller,
      frontend, and transport loss.
- [ ] Unsupported interactive-session injection exposes only the fidelity it can
      prove and never forks a hidden second controller.
- [ ] Current and previous supported bridge generations negotiate, serve, or
      reject the delegation surface before side effects through #1460.
- [ ] Local, peer, trusted-container, and CodeSpace scenarios produce the same
      lifecycle outcomes; restricted targets cannot gain authority through a
      richer capability.
- [ ] The full compact control vocabulary is exercised against at least one
      remote venue, not only checked for regression.
- [ ] #1454's focused AHP mapping fixtures pass without turning bridge-only
      attention or federation behavior into false AHP core requirements. Full
      AHP conformance remains owned by #1266 and is not a completion gate for
      the core #1448 delegation surface.

## Proposal

Converge on the interaction shape, not on an assumed implementation:

| Native-style expectation | Bridge contract |
|--------------------------|-----------------|
| Create an agent and retain its identity | Create or resolve a bridge-owned target and return one durable delegated-agent handle |
| Read accumulated work | Return bounded state, latest result, and incremental work from a durable position |
| Continue or steer | Admit an attributed prompt once through the authoritative serialized controller |
| Wait | Hold an attached invocation or subscription until a selected attention boundary |
| Cancel | Interrupt the active turn without silently destroying the session |
| End | Deliberately stop or retire the owned target according to its lifecycle |
| Caller disappears | Preserve the target and record detached state; promise no future wake-up without a retained relationship |

agent-dispatch remains the durable task queue and scheduler. It may embody a
worker through this bridge contract or hibernate a long external wait, but the
bridge does not absorb task claiming, recurring scheduling, or unattended goal
ownership.

## Journal

### 2026-10-22 - Complete retained acceptance from #1448

- Materialized the complete #1448 residual directly in the Plan and
  Validation, preserving existing source/resource custody and partial delivery.
  Normal reviewed merge and current full readback precede administrative
  source supersession; no behavior, native run or rollout is declared complete.

### 2026-08-31 — Kickoff and sibling-effort reconciliation

- Promoted #1448 from a vision follow-up into the explicit sibling effort for
  native-sub-agent-like delegation control.
- Kept #1460 responsible for compatibility mechanics and #1266 responsible for
  AHP host semantics.
- Defined the central honesty boundary: attention wake-up requires an attached
  invocation or retained subscription; detached work survives without claiming
  a future caller wake-up.
- Carved #1449–#1454 and #1506 into independently claimable semantic-contract,
  wait, steering, bounded-result, represented-session safety, compact-facade,
  and AHP-projection slices.

### 2026-08-31 — Plan review and activation

- Reconciled the effort with the newer #1460/#1468 contract foundation rather
  than duplicating compatibility machinery.
- Gave #1449–#1454 full independent scope and acceptance criteria, attached
  them as native sub-issues of #1448, and added #1506 for the previously
  unowned compact control facade.
- Drew explicit ownership boundaries with #22, #48, #60, #1045, #112, #1138,
  #1266, and #954.
- Marked the effort Active after review; implementation may proceed by the
  dependency and single-writer rules in Coordination.

### 2026-08-31 — Phase 0 contract baseline

- Carved #1449 into the focused
  [`agent-bridge-delegation-contract`](../../2026/09/26%20agent-bridge-delegation-contract/README.md)
  stretch.
- Defined the current-to-target lifecycle, authoritative lineage-derived
  delegate identity, caller relationships, attention reasons, and
  logical-message idempotency scope.
- Transferred wire/durable fixture registration to #1460/#1468 before any new
  writer or default is enabled.

### 2026-09-01 — Phase 1A bounded-result proposal

- Claimed #1452 and carved the focused
  [`agent-bridge-delegated-result-snapshots`](../../2026/09/01%20agent-bridge-delegated-result-snapshots/README.md)
  stretch.
- Selected a reader-only projection over existing status, persisted turns, and
  event logs, with no new durable writer or competing progress payload.
- Kept event-log continuity private behind opaque rebuild-safe positions and
  left richer progress proof plus the general immutable-reference contract with
  #1045/#1138.

### 2026-09-01 — Phase 1A bounded results completed

- Merged the bridge-owned bounded result reader in
  [#1566](https://github.com/ThomasMichon/copilot-extensions/pull/1566).
- Merged reduced-fidelity represented-session parity in
  [#1588](https://github.com/ThomasMichon/copilot-extensions/pull/1588).
- Archived the completed focused effort and transferred the delegation program's
  next independent reader slice to #1450.

### 2026-09-01 — Phase 1B attention-wait proposal

- Claimed #1450 and carved the focused
  [`agent-bridge-attention-waits`](../../2026/09/03%20agent-bridge-attention-waits/README.md)
  stretch.
- Selected a deterministic earliest-boundary attention projection over the
  existing event, succession, and bounded-result authorities, retaining the
  HTTP or CLI invocation itself as the wake channel and adding only the narrow
  permission correlation needed for an answerable live request.
- Kept terminal-event emission with #22, handoff creation with #112, and durable
  contract registration and writer fencing with #1460/#1468.

### 2026-09-03 — Phase 1B attention waits completed

- Merged cursor-neutral selected-reason waits, attached streaming settlement,
  permission correlation, failed-state publication, and compatible successor
  following through #1899.
- Archived the focused `agent-bridge-attention-waits` effort. Reachability
  terminal-event production remains with #22, policy-event production with its
  future owner, and replayable deliberate-end tombstones with a future
  terminal-retention owner; the wait surface no longer overclaims those gates.
- Phase 2 / #1451 is the next delegation-control slice once #1460/#1468 permit
  its durable queue and admission writers.
