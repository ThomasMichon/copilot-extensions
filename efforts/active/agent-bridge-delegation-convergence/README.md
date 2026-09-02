# Agent-Bridge Delegation Convergence

- **Slug:** `agent-bridge-delegation-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** issue-bound PR worktrees to `main`; parallel readers and
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
- **Umbrella issue:** [#1448](https://github.com/ThomasMichon/copilot-extensions/issues/1448)
- **Sub-issues:**
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

- [ ] Let a retained caller or subscriber wait for selected attention boundaries
      rather than only transport completion or one successful turn.
- [ ] Return a bounded structured reason, durable position, and current target
      identity at settlement.
- [ ] Resume ordinary transport interruptions by cursor inside the retained
      relationship instead of settling the wait prematurely.
- [ ] Carry an attention subscription across a deliberate successor handoff
      only when the successor negotiates semantics compatible with the caller's
      retained contract. Otherwise settle with an explicit contract-changed
      attention reason and the successor identity.
- [ ] Consume #22's terminal-event reconciliation for transport failure; #1450
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

## Validation Plan

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
  [`agent-bridge-delegation-contract`](../agent-bridge-delegation-contract/README.md)
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
  [`agent-bridge-attention-waits`](../agent-bridge-attention-waits/README.md)
  stretch.
- Selected a deterministic earliest-boundary attention projection over the
  existing event, succession, and bounded-result authorities, retaining the
  HTTP or CLI invocation itself as the wake channel and adding only the narrow
  permission correlation needed for an answerable live request.
- Kept terminal-event emission with #22, handoff creation with #112, and durable
  contract registration and writer fencing with #1460/#1468.
