# Agent-Bridge Delegation Convergence

- **Slug:** `agent-bridge-delegation-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `main`
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** closes
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  §Features/`task-shaped-delegation-control`,
  `attention-boundary-subscriptions`, `bounded-delegated-results`, and
  `single-stream-message-admission`, plus
  §Behaviors/`attention-requires-a-live-relationship` and
  `prompt-injection-requires-single-stream-proof`
- **Umbrella issue:** [#1448](https://github.com/ThomasMichon/copilot-extensions/issues/1448)
- **Related issues/dependencies:** [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460)
  (version-skew-safe contract foundation) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (immutable event identity) ·
  [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266)
  (AHP host convergence) ·
  [#1267](https://github.com/ThomasMichon/copilot-extensions/issues/1267)
  (coordinator-first delegation guidance)

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

- **Topology:** serial contract and implementation phases; surface changes wait
  for the shared #1460 prerequisite they consume.
- **Host (owns PRs):** delegation contract driver.
- **Delegates:** compatibility machinery lands through #1460, AHP mapping through
  #1266, and cross-venue runner changes through #954.
- **Handoff:** each phase lands one independently usable control increment with
  CLI/API documentation, compatibility fixtures, and Journal evidence before
  the next phase changes defaults. Any phase that changes the bridge-owned
  durable ledger waits for #1460's registry and reader/fencing review.

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

- [ ] Inventory current create, send, read, wait, interrupt, stop, resume, end,
      event, and result surfaces and map each to the intended compact lifecycle.
- [ ] Define one stable delegated-agent identity and the relationship among
      session instance, session lineage, target, caller cursor, and successor.
- [ ] Define attention-boundary reasons for completion, failure, input,
      permission, policy decision, and unrecoverable reachability loss.
- [ ] Define attached, subscribed, and detached caller relationships without
      promising an asynchronous wake-up channel after the caller disappears.
- [ ] Register the selected semantics and fixtures through #1460 before enabling
      a new delegation-state writer or default. If the shared registry has not
      landed yet, pin the contract locally here and register it before the first
      writer is enabled.

### Phase 1 — Present one task-shaped control surface

- [ ] Provide one compact create/identify/read/steer/wait/interrupt/end model
      across CLI and authenticated API surfaces.
- [ ] Return a stable delegated-agent handle and durable position rather than
      requiring a caller to reconstruct identity from process or transport data.
- [ ] Keep advanced bridge operations available, but make ordinary delegation
      independent of venue-specific commands or raw protocol details.
- [ ] Preserve existing command compatibility while the new surface is
      capability-gated and canaried.

### Phase 2 — Deliver bounded accumulated results

- [ ] Return bounded current state, latest result, and incremental work since a
      caller-held position.
- [ ] Keep raw events and full detail available through explicit fidelity and
      recovery access rather than injecting them into the ordinary caller path.
- [ ] Use only stable event or projection identities supplied by #1138.
- [ ] Represent truncation, unavailable detail, and reduced-fidelity targets
      explicitly.

### Phase 3 — Make waits attention-oriented

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

### Phase 4 — Serialize steering and cancellation

- [ ] Admit each accepted prompt exactly once to the authoritative controller for
      the session lineage.
- [ ] Durably queue steering sent to a busy target and preserve sender
      attribution and order.
- [ ] Distinguish interrupting the current turn, stopping future work, ending the
      session, and retiring its durable representation.
- [ ] Deny prompt injection when a represented interactive target cannot prove
      authoritative identity, serialized admission, and one-turn creation.

### Phase 5 — Map the contract across venues and host faces

- [ ] Run the same lifecycle and attention fixtures against local, peer-owned,
      trusted-container, and CodeSpace targets through #954.
- [ ] Expose the model through AHP only where #1266 can map it to standard
      lifecycle, state, actions, or explicitly negotiated extensions.
- [ ] Preserve reduced-fidelity representations for interactive or native-owned
      sessions instead of advertising unsupported control.
- [ ] Verify that a provider or frontend update does not reinterpret the selected
      delegation contract of an existing session.

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
- [ ] Two concurrent callers produce one serialized prompt stream and at most one
      model turn per accepted prompt across retries and successor handoff.
- [ ] Busy-target steering queues durably in order and survives caller,
      frontend, and transport loss.
- [ ] Unsupported interactive-session injection exposes only the fidelity it can
      prove and never forks a hidden second controller.
- [ ] Current and previous supported bridge generations negotiate, serve, or
      reject the delegation surface before side effects through #1460.
- [ ] Local, peer, trusted-container, and CodeSpace scenarios produce the same
      lifecycle outcomes; restricted targets cannot gain authority through a
      richer capability.
- [ ] AHP mapping passes #1266 conformance without turning bridge-only attention
      or federation behavior into false AHP core requirements.

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
