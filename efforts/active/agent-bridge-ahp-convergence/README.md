# agent-bridge AHP Convergence

- **Slug:** `agent-bridge-ahp-convergence`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `main`
- **Created:** 2026-08-27
- **Status:** Draft
- **Vision:** **vision-closing**
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  with an upstream AHP host face; **vision-closing**
  [`visions/native-convergence`](../../../visions/native-convergence/README.md)
  for native cloud steering and released-surface convergence
- **Umbrella issue:** [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266)
- **Related issues/dependencies:** [#989](https://github.com/ThomasMichon/copilot-extensions/issues/989)
  (native live-session steering) ·
  [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460)
  (version-skew-safe contract foundation) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (immutable event identity) ·
  [#1454](https://github.com/ThomasMichon/copilot-extensions/issues/1454)
  (delegation attention/result projection) ·
  [#1308](https://github.com/ThomasMichon/copilot-extensions/issues/1308)
  (AHP 0.8 contract lock)

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
  before the next phase changes the same state model; shared compatibility
  mechanics land through
  [`agent-bridge-contract-evolution`](../agent-bridge-contract-evolution/README.md)
  rather than being reimplemented inside the AHP adapter. Any phase that changes
  the bridge-owned durable ledger waits for #1460's registry and reader/fencing
  review.

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
[AHP 0.8.0](https://github.com/microsoft/agent-host-protocol/tree/v0.8.0).
The specification remains a working draft. AHP 0.9.0 was released after this
target was reviewed; the implementation deliberately remains on 0.8.0 for the
first slice. Adopting 0.9.0 or a later line requires a separately pinned and
reviewed codec rather than allowing behavior from another version to leak
through one mutable schema.

The detailed current-state comparison and ownership decisions are in
[compatibility-baseline.md](compatibility-baseline.md).

This effort owns the exact AHP contract: JSON-RPC channels, standard
initialization and capabilities, resource identity, snapshots, actions, and
client reconciliation. The sibling
[`agent-bridge-contract-evolution`](../agent-bridge-contract-evolution/README.md)
effort owns generic mixed-version machinery such as contract provenance,
tolerant durable readers, session-pinned selections, writer fencing, and
coherent cutover generations. AHP consumes that foundation without replacing
its exact version-specific semantics with a bridge-invented generic capability
model.

## Request

Converge agent-bridge toward AHP compatibility so callers can use a standard
host protocol while agent-bridge retains ACP downstream control, durable
session hosting, workspace-provider composition, and multi-venue reach.
Interoperate with compatible native local hosts through released AHP surfaces
rather than binding to private implementation details.

## Plan

### Phase 0 — Freeze the target contract

- [ ] Supply the exact AHP release provenance and fixtures that #1460 registers
      in the shared contract inventory, while keeping AHP's standard capability
      and state-prerequisite rules authoritative. If the registry has not landed
      yet, pin them locally here and register them before the first AHP writer is
      enabled.
- [x] Pin the released AHP 0.8 tag, generated JSON schemas, exported method
      maps, and reducer fixtures as test inputs; record an explicit precedence
      rule for disagreements among tagged prose, types, SDKs, and fixtures.
- [x] Record every baseline, named-capability-gated, state-prerequisite-gated,
      `x-` extension, version-sensitive, and explicitly out-of-scope surface.
      Do not invent a generic capability advertisement mechanism.
- [x] Define the compatibility policy for later protocol changes: isolated
      codecs, no silent semantic drift, and no named capability or optional
      state surface without its complete state machine.
- [ ] Establish bidirectional conformance probes using an independent AHP
      client and server implementation where available.

### Phase 1 — Build the internal AHP kernel

- [ ] Add internal JSON-RPC framing and channel routing without registering a
      production endpoint.
- [ ] Do not enable an AHP state writer until #1460's tolerant-reader,
      old-writer-fencing, and rollback prerequisites cover every shared durable
      record the adapter will change.
- [ ] Implement `ping`, `initialize`, version selection, implementation
      metadata, client capabilities, and the exact named agent capabilities
      defined by the selected version.
- [ ] Project agent discovery into authoritative `ahp-root://` state without
      exposing session resources before their ownership and URI mapping are
      defined.
- [ ] Allocate every server action once in one durable, monotonically ordered
      `serverSeq` space per future AHP endpoint, shared across all channels and
      persisted before any observer can receive it.
- [ ] Keep the kernel transport-neutral and preserve the repository's
      local-first endpoint-discovery invariants for the later exposure phase.

### Phase 2 — Define authority, identity, and the read-only catalog

- [ ] Define the ownership matrix before the ledger: bridge-owned resources use
      bridge domain state; native-host-owned resources remain authoritative in
      the native host; AHP persistence is a projection/outbox, never a second
      lifecycle authority.
- [ ] Define stable mappings among client-chosen AHP session/chat URIs, bridge
      handles, downstream ACP session IDs, targets, and workspace-provider IDs.
- [ ] Consume #1449's logical delegated-agent identity and succession contract
      when defining those mappings; do not introduce a second bridge lifecycle
      identity inside the AHP adapter.
- [ ] After that identity contract is fixed, implement paginated `listSessions`
      plus the internal production of ephemeral `root/sessionAdded`,
      `root/sessionRemoved`, and `root/sessionSummaryChanged` notifications.
- [ ] Create every ready session with a catalogued `defaultChat`; treat
      additional chats, fork, and side-chat creation as optional behavior gated
      only by the exact `multipleChats` capability fields.
- [ ] Reject duplicate AHP resource creation and prohibit silent ACP
      conversation recreation beneath an unchanged AHP identity.
- [ ] Define how a deliberate bridge successor appears at the AHP boundary:
      either preserve the AHP URI under a proven identity-preserving handoff,
      create an explicit successor resource, or settle the client relationship
      with an observable identity transition. Never change the conversation
      beneath an unchanged URI implicitly.
- [ ] Map asynchronous creation, ready/failure, archive, disposal, turn
      cancellation, and terminal outcomes without exposing internal
      stop/resume mechanics as different AHP semantics.
- [ ] Add a versioned workspace-provider contract for directories, project
      metadata, and occupancy. Keep Git worktrees optional and require
      `multipleWorkingDirectories` before multi-directory mutation.

### Phase 3 — Expose the read-only edge and reconciliation

- [ ] Register an attributable, local-first AHP server command/endpoint only
      after the internal ordering, root-state, identity, and catalog contracts
      are coherent.
- [ ] Initially expose only `ping`, `initialize`, subscriptions, reconnect, root
      state, and the read-only session/default-chat catalog; do not enable
      create, mutation, or turn-driving methods in this phase.
- [ ] Define root/session/chat snapshots against the durable `serverSeq`
      outbox, preserving the persist-before-delivery invariant across every
      connection and transport reconnection.
- [ ] Implement subscribe, unsubscribe, reconnect replay, snapshot fallback,
      missing-resource handling, catalog re-fetch after reconnect, and
      optimistic client-action reconciliation.
- [ ] Track `clientId`, monotonic `clientSeq`, origin echoes, confirmed state,
      ordered pending actions, foreign-action rebasing, and rejected-action
      rollback before accepting client-dispatched state.
- [ ] Keep the ordered projection extensible for #1454's later
      attention/result mapping without making that integration slice a gate on
      this phase.
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
- [ ] Land #1454 through this PR lane after the AHP edge/state machines and
      #1448 delegation contract are stable, preserving one downstream
      controller while exposing standard AHP state/actions to multiple clients.

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

### Phase 8 — Reconcile deferred backlog

- [ ] Accept AHP and bridge candidates only through
      [`migration-intake`](../migration-intake/README.md)'s deduplication and
      ownership gate.
- [ ] Revalidate accepted technical scope against the current protocol and
      architecture; return obsolete or unsafe candidates for explicit disposition.
- [ ] Place each accepted public tracker item in exactly one existing phase,
      extending this plan before implementation when necessary.
- [ ] Keep reproductions synthetic and independent of any adopting environment.

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
- [ ] The current and previous supported bridge generations can each serve or
      reject the AHP edge according to the registered contract without
      partially creating a session or changing its authority.
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

The pinned 0.8.0 corpus, source hashes, method classification, capability
policy, version decision, and exception ledger live in
[`plugins/agent-bridge/tests/fixtures/ahp/v0.8.0/`](../../../plugins/agent-bridge/tests/fixtures/ahp/v0.8.0/).

## Journal

### 2026-08-31 — Compatibility-foundation reconciliation

- Kept AHP semantics in this effort while moving generic contract provenance,
  durable-state tolerance, session pinning, writer fencing, cutover authority,
  and mixed-version gates to the shared #1460 foundation.
- Made #1266 and native-sub-agent delegation convergence sibling consumers of
  the same compatibility machinery rather than parallel implementations.

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

### 2026-08-28 — Phase 0 contract lock

- Started implementation under #1308 after the architecture plan cleared
  review.
- Pinned AHP 0.8.0 at commit
  `7153143f1c6993fa886d7d59870811cdad479d83`, including schemas, specification
  documents, SDK release metadata, and the reducer and round-trip corpora.
- Recorded AHP 0.9.0 as a later release and deliberately retained 0.8.0 as the
  only accepted first-slice version; another version requires an isolated,
  reviewed codec.
- Classified every exported command and notification and recorded all named
  capabilities separately from optional state and runtime prerequisites.
- Adjudicated the tagged `disposeChat` contradiction as recognized wire
  vocabulary with no handler, `MethodNotFound`, and no `multipleChats`
  advertisement until lifecycle semantics are reviewed again.
- Kept runtime endpoint registration, ordering, replay, and live conformance
  probes out of this contract-only slice.

### 2026-09-02 — Trusted self-hosted CI gate

- Added a default-branch `pull_request_target` workflow that authorizes only
  the pull-request author's current `write`, `maintain`, or `admin` repository
  permission on a GitHub-hosted runner.
- The self-hosted job uses only the dedicated `copilot-extensions-ci` label,
  checks out the immutable pull-request head SHA without persisting a
  credential, receives no secret, and remains disabled behind the explicit
  `TRUSTED_SELF_HOSTED_CI=enabled` repository-variable latch until runner
  activation.
- Added a GitHub-hosted static contract guard and mutation tests for the event,
  permission query, authorization dependency, activation latch, exact label,
  immutable checkout, and credential boundary.
- Runner activation remains conditional on repository settings requiring
  approval for all external contributors. The static guard also rejects any
  other checked-in workflow job that routes to `self-hosted` or the dedicated
  label; this supplements repository policy but does not replace it.
