# agent-bridge AHP Convergence

- **Slug:** `agent-bridge-ahp-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `main`
- **Created:** 2026-08-27
- **Status:** Draft
- **Vision:** **vision-extending**
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  with an upstream AHP host face; **vision-closing**
  [`visions/native-convergence`](../../../visions/native-convergence/README.md)
  for native cloud steering and released-surface convergence
- **Umbrella issue:** [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266)
- **Related issues/dependencies:** [#989](https://github.com/ThomasMichon/copilot-extensions/issues/989)
  (native live-session steering) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (immutable event identity)

## Guiding Intent

Make agent-bridge a standards-compatible Agent Host Protocol (AHP) host without
discarding the durable, multi-venue coordination value it already provides.
AHP becomes the client-facing contract for agent discovery, shared session/chat
state, ordered replay, lifecycle control, rich content, and mediated
interaction. ACP remains the downstream protocol used to drive agent runtimes.

The same boundary should let an AHP client target agent-bridge or a compatible
native local host. A bridge-owned session remains authoritative in the bridge;
a native-host-owned session remains authoritative in the native host, with
agent-bridge acting only as a proxy, federator, or reduced-fidelity projection.
The two may own distinct resources, but never parallel lifecycle or replay
authority for the same session. Native convergence must not hard-depend on
unreleased internals or regress routing, recovery, remote venues, or existing
CLI/REST consumers.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| agent-bridge maintainer | Owns the host adapter, state model, and landing PRs | Isolated worktree and PR |
| AHP compatibility harness | Supplies versioned protocol fixtures and client/server probes | Repository test suite |
| Downstream ACP agents | Validate protocol translation without weakening ACP semantics | Existing ACP test doubles |
| Native local hosts | Optional interoperability target once a released contract exists | Feature-detected AHP endpoint |

## Coordination

- **Topology:** serial phase PRs; one state-model owner at a time
- **Host (owns PRs):** agent-bridge maintainer
- **Delegates:** protocol research, conformance fixtures, and independent
  implementation reviews may use isolated worktrees
- **Handoff:** each phase lands its contract, tests, and effort journal update
  before the next phase changes the same state model

## Context

agent-bridge is already a substantial host substrate: an authenticated local
daemon, durable session/event storage, reconnectable cursors, ACP client and
agent faces, a reattachable Session Host, workspace-provider integration, and
local/SSH/CodeSpace/container target resolution. These capabilities make it the
natural place to expose AHP.

They do not make the current service an AHP implementation. The existing
surfaces are private HTTP/JSON plus SSE, upstream/downstream ACP, and a private
Session Host envelope carrying ACP frames. AHP instead defines a JSON-RPC
client-to-host contract with initialization and version negotiation,
channel-addressed snapshots and actions, endpoint-wide authoritative
ordering, reconnect reconciliation, shared session/chat state, rich content,
resources, tools, confirmations, and durable elicitation.

The released baseline for this effort is
[AHP 0.8.0](https://github.com/microsoft/agent-host-protocol/releases/tag/spec/v0.8.0).
The specification remains a working draft and the repository is developing an
unreleased 1.0 line. The implementation therefore targets 0.8 first, negotiates
versions explicitly, and isolates version-specific codecs rather than allowing
draft behavior to leak through one mutable schema.

The detailed current-state comparison and ownership decisions are in
[compatibility-baseline.md](compatibility-baseline.md).

## Request

Converge agent-bridge toward AHP compatibility so callers can use a standard
host protocol while agent-bridge retains ACP downstream control, durable
session hosting, workspace-provider composition, and multi-venue reach.
Interoperate with compatible native local hosts through released AHP surfaces
rather than binding to private implementation details.

## Plan

### Phase 0 — Freeze the target contract

- [ ] Pin the released AHP 0.8 tag, generated JSON schemas, exported method
      maps, and reducer fixtures as test inputs; record an explicit precedence
      rule for disagreements among tagged prose, types, SDKs, and fixtures.
- [ ] Record every baseline, named-capability-gated, state-prerequisite-gated,
      `x-` extension, version-sensitive, and explicitly out-of-scope surface.
      Do not invent a generic capability advertisement mechanism.
- [ ] Define the compatibility policy for unreleased 1.0 changes: isolated
      codecs, no silent semantic drift, and no named capability or optional
      state surface without its complete state machine.
- [ ] Establish bidirectional conformance probes using an independent AHP
      client and server implementation where available.

### Phase 1 — Add the AHP edge

- [ ] Add an attributable AHP server command/endpoint alongside existing CLI,
      REST/SSE, ACP, and Session Host surfaces.
- [ ] Implement JSON-RPC framing, channel routing, `ping`, `initialize`,
      version selection, implementation metadata, client capabilities, and the
      exact named agent capabilities defined by the selected version.
- [ ] Project agent discovery into authoritative `ahp-root://` state without
      exposing session resources before their ownership and URI mapping are
      defined.
- [ ] Preserve the repository's local-first transport and endpoint-discovery
      invariants; AHP must not introduce a fixed or globally exposed port.

### Phase 2 — Define authority, identity, and the default chat

- [ ] Define the ownership matrix before the ledger: bridge-owned resources use
      bridge domain state; native-host-owned resources remain authoritative in
      the native host; AHP persistence is a projection/outbox, never a second
      lifecycle authority.
- [ ] Define stable mappings among client-chosen AHP session/chat URIs, bridge
      handles, downstream ACP session IDs, targets, and workspace-provider IDs.
- [ ] After that identity contract is fixed, implement paginated `listSessions`
      plus ephemeral `root/sessionAdded`, `root/sessionRemoved`, and
      `root/sessionSummaryChanged` notifications.
- [ ] Create every ready session with a catalogued `defaultChat`; treat
      additional chats, fork, and side-chat creation as optional behavior gated
      only by the exact `multipleChats` capability fields.
- [ ] Reject duplicate AHP resource creation and prohibit silent ACP
      conversation recreation beneath an unchanged AHP identity.
- [ ] Map asynchronous creation, ready/failure, archive, disposal, turn
      cancellation, and terminal outcomes without exposing internal
      stop/resume mechanics as different AHP semantics.
- [ ] Add a versioned workspace-provider contract for directories, project
      metadata, and occupancy. Keep Git worktrees optional and require
      `multipleWorkingDirectories` before multi-directory mutation.

### Phase 3 — Build durable ordering and reconciliation

- [ ] Define root/session/chat snapshots and allocate each server action once
      in one durable, monotonically ordered `serverSeq` space per exposed AHP
      server endpoint, shared across every state channel, client, and transport
      reconnection on that endpoint.
- [ ] Persist actions before delivery and remove the current crash window where
      a live consumer can observe an event before durable commit.
- [ ] Implement subscribe, unsubscribe, reconnect replay, snapshot fallback,
      missing-resource handling, catalog re-fetch after reconnect, and
      optimistic client-action reconciliation.
- [ ] Track `clientId`, monotonic `clientSeq`, origin echoes, confirmed state,
      ordered pending actions, foreign-action rebasing, and rejected-action
      rollback before accepting client-dispatched state.
- [ ] Keep rebuild epochs internal or behind a negotiated `x-` extension so an
      identity never resolves to different history without changing core AHP
      schemas; coordinate with #1138.
- [ ] For native-backed resources, either expose the native host as a separate
      AHP endpoint or remap its authoritative lifecycle snapshots/actions into
      the bridge endpoint's single `serverSeq` projection. Never expose two
      sequence authorities through one endpoint.

### Phase 4 — Implement chats, content, and resources

- [ ] Add ordered turn state on the required default chat rather than treating a
      bridge session as an implicit conversation.
- [ ] Preserve text, images, audio, embedded resources, URI resource
      references, annotations, transcript references, model selection, and
      provider metadata end to end where supported.
- [ ] Implement lazy content references and the standard resource operations
      the host can authorize; unsupported or unauthorized operations return
      defined errors rather than relying on a nonexistent generic capability
      bit.
- [ ] Make unsupported content fail explicitly instead of accepting and
      discarding non-text blocks.

### Phase 5 — Implement interaction state machines

- [ ] Project downstream tool calls into durable AHP tool states.
- [ ] Implement confirmation, denial, cancellation, authentication-required,
      pending-result, and completion transitions before exposing the associated
      state and actions.
- [ ] Represent elicitation as durable input-request response parts with stable
      IDs, drafts, and accept/decline/cancel outcomes.
- [ ] Retain transport-level bearer authentication; add AHP protected-resource
      discovery and `authenticate` only when the full protected-resource flow
      is complete.

### Phase 6 — Support many AHP clients over one downstream controller

- [ ] Track subscriptions, authorization, and reconnect state per logical
      `clientId`, independently of implementation name.
- [ ] Serialize multiple authorized callers through the bridge's one-controller
      invariant while keeping all clients reconciled to server order.
- [ ] Define observer-only and reduced-fidelity representations honestly rather
      than advertising mutation or replay guarantees they cannot satisfy.

### Phase 7 — Converge with native hosts and migrate consumers

- [ ] Probe released native AHP hosts and classify baseline behavior, exact
      named capabilities, optional state, and `x-` extensions before delegating
      a local primitive.
- [ ] Support agent-bridge and a native local host as peers behind the same AHP
      client contract while preserving one owner per session: proxy or federate
      native-owned resources rather than mirroring them as bridge-owned state.
- [ ] Publish migration guidance for private REST/SSE consumers and retain
      compatibility until equivalent AHP behavior is proven.
- [ ] Feed the results back into native-convergence Phase D (#989) without
      turning task scheduling, Git worktrees, mux control, or federation into
      false AHP core requirements.

## Validation Plan

- [ ] A released AHP 0.8 client initializes, subscribes to root state, discovers
      an agent, fetches the paginated session catalog, creates and subscribes to
      a session, observes `session/ready`, resolves its catalogued
      `defaultChat`, subscribes to that chat, completes one turn, cancels,
      reconnects, re-fetches ephemeral catalog state, reconciles, and disposes
      the session.
- [ ] Additional chat creation, fork, side-chat, and multiple-working-directory
      tests run only under their exact named capability fields.
- [ ] Unsupported versions, invalid state prerequisites, unauthorized resource
      operations, and unsupported `x-` extensions fail with defined errors; no
      optional behavior is silently accepted and discarded.
- [ ] Two clients concurrently observe and drive one session and converge on
      identical server-ordered state without creating two downstream
      controllers.
- [ ] Killing and restarting the AHP frontend preserves committed history,
      resource identity, and reconnect behavior.
- [ ] A failed downstream ACP resume never silently replaces conversation
      identity beneath an existing AHP URI.
- [ ] Rich content and resource fixtures survive end to end or fail explicitly
      without data loss.
- [ ] Tool confirmation and elicitation tests cover every advertised terminal
      outcome.
- [ ] Existing ACP, CLI, REST/SSE, workspace-provider, remote-target, and
      Session Host suites remain green throughout migration.
- [ ] Clean-room tests prove the AHP endpoint is local-first, discoverable,
      independently installable, and functional without sibling plugins.
- [ ] A client can switch between agent-bridge and a compatible released native
      host using the same AHP core behavior, with differences represented only
      by named capabilities, optional state, or negotiated extensions, and
      without creating two authorities for one session.

## Proposal

The initial architecture and compatibility matrix are captured in
[compatibility-baseline.md](compatibility-baseline.md). Phase 0 may revise that
baseline as AHP versions evolve, but implementation must not begin from an
uncited or version-neutral interpretation of the protocol.

## Journal

### 2026-08-27 — Kickoff

- Created public umbrella #1266 and linked native-convergence Phase D (#989)
  plus immutable event identity work (#1138).
- Classified the current stack as an AHP-capable substrate, not an AHP
  implementation.
- Chose agent-bridge as protocol authority, ACP as the downstream agent
  protocol, workspace managers as providers, and native local hosts as
  feature-detected peers.
- Pinned released AHP 0.8 as the first compatibility target while keeping
  version-specific codecs ready for the unreleased 1.0 line.
- No implementation begins until this plan clears review.
