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

The proposed #4029/#4191 residual below retains this Host, #1460/#1468,
#1138, assigned #1266, #954 and provider/session/ACP/shell-process owners.
It assigns no implementation or resource, creates no new ACK and does not
apply #1448's source-specific supersession clause to unrelated trackers.

The proposed #4681 attention follow-up below likewise retains terminal-event
production with #22 and existing Session Host/ACP owners, represented-target
resolution with its live-registry owner, and compatibility with #1460/#1468.
This effort receives the observable wait contract, not those underlying
resources. The completed Phase 1B and archived Done attention baseline keep
their earned credit. Normal reviewed delivery, current complete source/ROOT
readback and separate source-specific judgment precede any tracker retirement;
neither this proposal nor a tracking change repairs the wait channel.

The proposed complete #6394 receiving below preserves semantic aggregation,
target-resolution, inbox, registration, bounded Driver delivery, #12487
compatibility, #11448 subscription, retained-source/publisher and release
owners. The accepted bounded queued-follow-up plan and next provider/API gate
materially address BT4's framing, not release of the earlier source/publisher
obligations. Original BT4/BTV4 and its discussion retain the actual owner's
additive-phase OR separately accepted follow-up/publication decision before
closure. Reconcile that recorded condition; do not invent a generic re-ACK.
The current queued-steering claim is bounded, not custody of this whole union.
No live reservation was inspected or cleared; zero assignees proves neither
absence of claims nor source/resource release. #6394 stays OPEN during this
proposal. Normal review, complete current receiving/readback and a separate
source-specific judgment precede any administrative disposition.

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

### Proposed finite #4681 attention-wait follow-up

This directly retains all three lanes in #4681, including its consolidated
#7977 deadline/continuation and #9387 represented-session requirements.
Existing finite command-deadline handling is bounded credit, not a freshly
reproduced indefinite hang or complete repair. The original argparse notes
were ancillary and already fixed. These reader/CLI follow-ups do not become
a prerequisite for unrelated Phase 2, AHP or whole-parent work.

- [ ] **AW4681-P1:** Across Session-Host reattach, settle the genuinely completed
  ORIGINAL turn from correlated durable terminal evidence, preserving native/
  session identity, caller position and conversation continuity. Idle or
  quiescence alone cannot prove completion. Reconcile through the existing
  terminal producer and waiter; status-based cutover proof is not wait-channel
  acceptance. Preserve bounded honest failure without starting, resuming,
  recreating or replaying a conversation to make the wait succeed.
- [ ] **AW4681-P2:** Document the configured attention-wait command deadline;
  add per-wait timeout with zero meaning indefinite while preserving finite
  configured behavior. An unsettled deadline exits distinctly nonzero, not
  successful attention settlement. Text output includes the next position and
  one-line re-arm hint; JSON settled/position semantics and the existing cursor
  remain authoritative without requiring a global timeout change.
- [ ] **AW4681-P3:** Resolve represented interactive sessions consistently
  against the same live-registry target IF attention supports them, OR return
  explicit unsupported-capability rather than false absence. Preserve original
  process, turn and session. Collect exact daemon-version attribution and a
  bounded source-independent reproducer before claiming a regression or common
  cause; the historical represented not-found and post-reattach hang are
  distinct observations, not proof of one defect.
- [ ] **AW4681-LV1:** Through a fresh separate-process wait after a completed
  reattached turn, distinguish durable/correlated completion from merely idle,
  including retained native/session/position continuity and honest bounded
  failure. Keep the existing status-based drill and completed attention baseline
  credited at their actual scope; neither substitutes for this channel.
- [ ] **AW4681-LV2:** Exercise unsettled deadline exit/result, zero and per-call
  override, finite configuration, continuation position/text hint and JSON
  re-arm without losing events. A successful process exit is not settlement,
  and attention settlement is not completion of the application objective.
- [ ] **AW4681-LV3:** Check represented supported/unsupported, genuinely absent,
  live/running and uncertain targets against consistent registry resolution
  with exact daemon attribution and the bounded independent reproducer.
  Preserve process/session identity and compatibility/refusal behavior.
  Historical timing is not a new measurement; no deploy/restart, native drill,
  live subscription or session mutation is authorized by this plan.

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

### Proposed finite #4029/#4191 named-definition and shell continuity

The existing direct #1448 Plan/Validation already carries typed per-session
MCP arguments/toolsets through actual child execution, real required-tool
availability/isolation, stable identity, lifecycle and attention/results.
Its bounded current/latest/incremental results and ordinary active steering
are bounded existing credit, not proof of #4171's streamed progress and
steering during the active turn. Reuse those rows without substituting
future-turn queueing or final snapshots for the distinct outcomes below.

- [ ] **D4029-P1:** Bind the named sub-agent definition selected through
  `send`/`request` to the intended logical delegate/target, preserving
  caller, target, session and venue identities. Crosswalk structured
  create/stream/stop ACK/NACK to existing lifecycle/failure outcomes;
  retain any unmatched exact outcome as a direct gap, not another protocol.
- [ ] **D4029-P2:** Preserve #4191's alternative: keep attached shell work
  across a root-turn boundary OR safely retire it through its actual owner
  where the selected lifecycle permits. Expose ongoing/completed/unknown
  and owner-supported wait for attributed completion, output and outcome;
  preserve supported reattachment/cancellation and truthful refusal.
- [ ] **D4029-P3:** Retain #4171's reliable ongoing streamed progress AND
  durable, queryable, attributed result metadata AND real steering while
  the turn remains active. Cover hosted/represented and local/remote paths,
  quiet periods and reconnect/cutover continuity while preserving
  conversation/custody and unknown delivery/replay state. A future-turn
  queue receipt or final snapshot is not active-turn steering/progress;
  keep queue-first compatibility as its own supported semantic path.
- [ ] **D4029-LV1:** Through both caller operations, verify named-definition
  selection and identity, structured lifecycle outcomes and unsupported/
  malformed/refused cases. Reuse existing real tool-availability/isolation
  and #4171 result/steer gates rather than substituting parser-only proof.
- [ ] **D4029-LV2:** Exercise the selected shell OR alternative across the
  root-turn boundary, attributed wait/completion/output/outcome, reattach/
  cancel and unknown/refusal without misreporting transport exit as work
  completion.
- [ ] **D4029-LV3:** At the actual active-turn boundary, observe progressive
  streamed output during work and accepted steering affecting that active
  work, plus durable queryable attributed results. Cross hosted/represented,
  local/remote, quiet periods, reconnect/cutover, refusal and delivery/
  terminal unknowns without replacement, lost custody or blind replay.
  Queue-for-later and final-snapshot-only controls must not pass this lane.

### Proposed complete #6394 delivery and inbox residuals

Reuse incumbent direct identity/admission/provenance/audit gates, the bounded
[queued-steering](../bridge-queued-steering/README.md) exact-input/fallback
contract and existing review/CI/release/adoption obligations. Preserve credited
generation-39/#6420 delivery and bounded notification source without resetting
their unfinished qualification or equating partial delivery with this union.

- [ ] **TD6394-P1 - BT1 residual:** Unknown/non-live zero-match handles fail
  nonzero with one clear diagnostic before generic start or its "Starting
  session" line. Preserve supported registered-agent start OR clean rejection;
  require no invented mandatory flag. The reported observation did not
  establish an actual spawn.
- [ ] **TD6394-P2 - BT2 residual:** Preserve credited immediate non-aborting
  missing/ordinary steering, explicit queue/interrupt and unknown-urgency
  diagnosis alongside the existing queue-first compatibility path. Directly
  retain admitted/SDK-accepted/exact-root-consumed receipts, optional exact
  receipt, cursor-neutral represented activity/pending/output reads, separately
  requested finite reply waits, exclusion of nested/pre-consumption/empty-tool
  false answers, honest post-consumption correlation limits and unavailable/
  retired/readback races. Reuse incumbent identity/order/replay protections;
  do not reimplement or reset credited baseline work.
- [ ] **TD6394-P3 - BT3 residual:** In the existing bounded queued-steering
  lane, retain count/age/readiness-only notice content, meaningful-change
  coalescing, no abort/operator impersonation/fabricated idle, and bounded
  attention for BOTH stale admitted work AND SDK-accepted-without-consumption,
  with known reason or explicit unknown. Preserve directly received
  mute/cadence, matching-ID/race and single-ledger protections. Retain the
  requested real busy-session nudge plus distinct mechanical receipts without
  an assistant reply.
- [ ] **TD6394-P4 - BT5:** Distinguish actual receiver exact-native capability
  from daemon capability or fresh registration. Preserve explicit incompatible
  refusal with truthful retryability OR truthful incompatible/stale diagnostic
  treatment, including capability visibility or bounded-age skipped-poll
  evidence. No unexplained permanent admission.
- [ ] **TD6394-P5 - BT6:** Registration retirement leaves durable sender-visible
  terminal receipt/reason, not silent deletion/404. Prefer an allowed capable
  successor preserving exact-target identity/freshness OR explicit non-carry
  and resend-after-restart guidance. Payload installation does not replace code
  loaded by an existing CLI; no restart/replay authority follows.
- [ ] **TD6394-P6 - BT7:** Bound currentNative() metadata snapshot, session.send
  and session.abort; expose truthful held/timeout and subsequent progress.
  Preserve that this hardening was not the observed incompatible-poller cause.
- [ ] **TD6394-P7 - BT8:** Under the existing consumer owner, retain changed-
  PID/birth same-session rearming and ordered pending ordinary/queue/control
  progress. Bounded-age admitted work on a live/idle heartbeating receiver
  without recent pulls produces list/status attention AND sender receipt
  visibility AND structured WARNING. Preserve identity, single ledger,
  retirement protections and observed-incident versus unconfirmed consumer-loop
  hypothesis distinctions.

#### Local Validation

- [ ] **TD6394-LV1:** Unknown/no-start diagnostic, supported registered-agent
  start and live addressing; deterministic no-start proof needs no native launch.
- [ ] **TD6394-LV2:** Exact receipt/read/reply boundaries, consume-before-return,
  nested/unrelated/pre-consumption/empty-tool controls, rename/replay and honest
  unknowns; one consumed ID cannot clear others.
- [ ] **TD6394-LV3:** Actual supported hook delivery and real busy-session
  nudge/distinct receipts without reply; metadata-only bounded/coalesced/muted
  notices and exact-ID races. Reuse queued ROOT's separately explicit exact-
  input/fallback/late-SDK/restart controls.
- [ ] **TD6394-LV4:** Exact-capable, legacy non-exact and retired receivers;
  chosen refusal OR diagnostic alternative; skipped-poll/stale visibility and
  ordinary/control compatibility.
- [ ] **TD6394-LV5:** Durable retirement receipt/reason; allowed successor OR
  explicit non-carry/resend outcome, retaining exact identity/freshness/idempotency.
- [ ] **TD6394-LV6:** Hang each of the three named RPCs; bounded exit, truthful
  held/timeout and subsequent progress without fabricated acceptance, consumption
  or reply.
- [ ] **TD6394-LV7:** Changed-PID/birth re-registration and ordered ordinary/
  queue/replay-control progress; no loss/duplication; all three stale-consumer
  signals with healthy active-puller, empty, busy and intentionally muted controls.

Retain file-drop as an UNACCEPTED design, not an implementation checkbox or
selected prerequisite: same IDs/timestamps/sender/body, one-ledger reconciliation,
atomic claim, proposed TTL, SSH-account/self-asserted-peer trust, cheap directory-
only hook, busy-only/idle-wake gap and startup-loaded hook-registration limit.
No SSH, file effect, cleanup, hook installation or rollout authority follows.

## Validation Plan

- [ ] **AW4681-G:** Complete all AW4681-P1..3 and LV1..3 lanes: the original
  completed reattached turn's correlated terminal settlement, truthful per-wait
  deadline/result/position continuation, and represented target support OR
  explicit unsupported-capability with daemon attribution and bounded
  independent reproduction. Preserve the completed Phase 1B/archived Done
  baseline, original evidence tiers, current compatibility and all terminal/
  live-registry/source/resource custodians. No AHP, all-native, whole-parent,
  new ACK or automatic operational gate is added.

- [ ] **D4029-G:** Resolve D4029-P1/P2 and local Validation alongside the
  D4029-P3/LV3 active-turn stream/steer, reused #1448 typed-MCP/actual-tool,
  durable attributed results and structured
  lifecycle gates. Preserve original finite evidence tiers, compatibility,
  serial writers and all custodians; no AHP/all-host/whole-parent
  prerequisite or new transfer ceremony is introduced.

- [ ] **TD6394-G:** Resolve TD6394-P1-P7/LV1-LV7 together with directly reused
  incumbent identity/admission/audit, queued-steering exact-input/fallback and
  existing source/review/CI/release/adoption clauses. Preserve original evidence
  tiers, literal alternatives, source-specific owner condition, current claimants
  and genuine holds. No whole-parent/AHP/every-host gate, new ACK, implementation
  completion or resource release follows from documentary receiving.

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

### 2026-11-17 - Complete delivery and inbox residual receiving proposed

Added seven unchecked Plan, seven local Validation and one actual global
Validation rows for the complete #6394 union, reusing already-direct
identity/admission/audit and bounded queued exact-input/fallback acceptance.
The actual BT4 source/publisher decision stays in Coordination, not a new
implementation checkbox or generic acknowledgment ceremony. Generation-39/
#6420 and bounded notification credit remain distinct from unfinished receipt,
capability, retirement, RPC and consumer-rearming outcomes. The original
observation did not establish an actual spawn; the consumer-loop explanation
remains a hypothesis. File-drop remains an unaccepted alternative. All incumbent
checks and owners remain; this proposal closes no source and performs no
implementation, native operation, custody transfer or rollout.

### 2026-11-17 - Complete attention-wait follow-up proposed

Added three unchecked Plan, three local Validation and one actual global
Validation rows directly receiving #4681/#7977/#9387. Reattach settlement,
deadline/result/continuation and represented support OR explicit refusal
remain independent obligations. Current durable-event/deadline source is
credited without a new hang or regression claim. Completed Phase 1B, archived
Done credit, existing terminal/live-registry custodians and all incumbent
checks remain unchanged. Review, full current readback and separate judgment
remain ahead of source disposition; no implementation or live operation.

### 2026-11-15 - Named-definition and shell continuity residuals proposed

Added seven unchecked #4029/#4191 Plan/local/global Validation rows.
Reuse direct typed-MCP/actual-tool and stable lifecycle acceptance; retain
named-definition binding, shell keep-attached OR owner-retire/wait, and
explicit streamed progress/steering during active work. Review exposed that
bounded reads and queue-first steering did not directly prove #4171's
distinct active-turn outcomes. All owners, serial writers,
compatibility and source-specific #1448 conditions remain. No implementation,
ACK ceremony, owner/resource transfer or source disposition occurred.

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
