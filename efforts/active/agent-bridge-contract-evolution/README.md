# Agent-Bridge Contract Evolution

- **Slug:** `agent-bridge-contract-evolution`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-phase PR worktrees to `main`; one shared-contract
  writer at a time
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** closes
  [`visions/plugins/agent-bridge`](../../../visions/plugins/agent-bridge/README.md)
  with §Concepts/`negotiated-contract-envelope`,
  §Features/`version-skew-safe-contract-evolution`, and
  §Behaviors/`negotiate-before-side-effects`,
  `session-contract-survives-default-changes`, and
  `readers-expand-before-writers`
- **Umbrella issue:** [#1460](https://github.com/ThomasMichon/copilot-extensions/issues/1460)
- **Related issues:** [#1266](https://github.com/ThomasMichon/copilot-extensions/issues/1266)
  (AHP host convergence) ·
  [#1448](https://github.com/ThomasMichon/copilot-extensions/issues/1448)
  (native-sub-agent delegation semantics) ·
  [#1138](https://github.com/ThomasMichon/copilot-extensions/issues/1138)
  (immutable event identity) ·
  [#954](https://github.com/ThomasMichon/copilot-extensions/issues/954)
  (venue parity)

## Guiding Intent

Give agent-bridge one compatibility foundation that lets its public and internal
contracts evolve while independently updated clients, daemons, session hosts,
providers, relays, and durable records coexist.

This is not a third protocol convergence. It is shared safety infrastructure
beneath two existing directions:

- the AHP effort owns the standard client-to-host contract and native-host
  interoperability; and
- #1448 owns the task-shaped lifecycle, attention, and bounded-result semantics
  that make delegation feel like native sub-agent control.

This effort owns only the machinery both need: explicit contract provenance,
tolerant state, pre-side-effect negotiation, session-pinned semantics, coherent
authority cutover, mixed-version evidence, and evidence-gated retirement.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Contract foundation driver | Registry, durable-state rules, negotiation, and phase sequencing | Isolated planning and implementation worktrees |
| AHP convergence lane | Exact AHP codecs, state machines, and native-host behavior | #1266 and its serial PR worktrees |
| Delegation convergence lane | Task-shaped lifecycle, attention boundaries, and bounded results | #1448 and [`agent-bridge-delegation-convergence`](../agent-bridge-delegation-convergence/README.md) |
| Ground-state owners | Own worktree claim/lease record semantics; supply fixtures and adapters consumed by the bridge | agent-worktrees changes through their own vision and PR ownership |
| Venue-provider owners | Own namespace-provider lifecycle, trust, and launch declarations | agent-codespaces, agent-containers, and #954 |
| Venue validation lane | Hosts the local, container, CodeSpace, and restricted-denial scenario runner | #954 parity-harness PR worktrees |

## Coordination

- **Topology:** the foundation lands in serial phases; AHP and delegation
  consumers may proceed independently only after the shared prerequisite they
  consume is merged.
- **Host (owns PRs):** contract foundation driver.
- **Delegates:** consumer lanes own their public semantics and contribute
  fixtures to the shared registry; they do not add competing negotiation or
  durable-state frameworks.
- **Handoff:** each phase updates the registry, fixtures, mixed-version evidence,
  and this Journal before a consumer enables a new writer or default. Changes to
  another plugin's owned record or parity runner land through that owner's
  effort and PR lane. The contract foundation driver is the single arbiter for
  bridge-owned durable schema changes; AHP or delegation phases that touch the
  shared ledger land behind its registry and reader/fencing review.

## Context

agent-bridge spans several independently deployed boundaries: CLI and HTTP
clients, the bridge daemon, reattachable Session Hosts, namespace providers,
credential relay state, venue claims, runtime routing, remote authority records,
and durable event projections. Package versions can and do skew across those
boundaries while active sessions continue to run.

Two current convergence efforts increase the cost of implicit compatibility:

- [`agent-bridge-ahp-convergence`](../agent-bridge-ahp-convergence/README.md)
  adds a released, versioned host protocol with exact standard state and
  capability semantics; and
- [`agent-bridge-delegation-convergence`](../agent-bridge-delegation-convergence/README.md)
  adds a compact delegation control model whose waits, results, and message
  admission must remain truthful across local and remote execution.

Both require compatible readers, stable identity, session continuity, bounded
fallback, and fail-before-side-effect behavior. Implementing those guarantees
inside each adapter would create parallel compatibility systems and ambiguous
authority. The foundation instead supplies one internal evolution discipline;
each edge maps its own public contract onto it.

The proposed contract taxonomy, ownership split, and initial boundary inventory
are in [design.md](design.md).

## Request

> Ah, right. We should make sure this is is reconciled upsream with other vision
> build-outs of agent-bridge in copilot-extensions. We have a covergence epic
> for AHP Agent Host Protocol and a convergence nudging toward alignment with
> Copilot sub-agent ergonomics. Let's fit this in nicely.

## Plan

### Phase 0 — Freeze the compatibility floor

- [ ] Add a machine-readable registry for every live and durable bridge
      contract, including owner, semantic generation, supported range, optional
      capabilities, state records, fixtures, release provenance, support window,
      bridge-contract rollback window, and removal gate.
- [ ] Freeze fixtures for the current and previous supported HTTP, Session Host,
      provider, relay, claim, route, target, authority, and event shapes.
- [ ] Register AHP from the exact provenance and fixtures supplied by #1266, and
      register delegation control from the contract supplied by #1448; do not
      flatten either into an invented universal protocol.
- [ ] Make CI reject an unregistered contract change or a contract-generation assertion
      without fixtures and source/release provenance. Event/history identity,
      projection references, and rebuild-generation semantics are registered
      from #1138 rather than defined here.

### Phase 1 — Make durable records tolerant and fenced

- [ ] Give shared records explicit schemas or record kinds, atomic replacement,
      owner generation plus process-start identity, and predecessor/successor
      lineage.
- [ ] Preserve unknown additive fields across compatible read-modify-write
      operations.
- [ ] For each record a supported old runtime can rewrite, choose an immutable
      sidecar/new record kind or an enforceable generation/schema fence.
- [ ] Change only bridge-owned records and bridge-side adapters here.
      Worktree-claim and venue-provider record semantics remain owned by their
      plugins and require their own vision and PR agreement.
- [ ] Run the actual previous runtime as a writer against each new-format
      fixture and prove that it preserves the record or is refused without
      modification.

### Phase 2 — Negotiate and pin live semantics

- [ ] Distinguish implementation version, protocol generation, named semantic
      capability version, durable-state schema, and live instance/generation
      identity.
- [ ] Select a mutually supported contract before claim, provider, relay, host,
      launch, route-publication, or prompt-admission effects.
- [ ] Persist the selected semantics across the session, target, claim, relay,
      host, and recovery records.
- [ ] Keep numeric and legacy shapes available through explicit compatibility
      adapters while their support window remains open.

### Phase 3 — Make bridge cutover one authority transition

- [ ] Keep immutable runtime publication, `current-version`, and
      `last-known-good` under the install contract. Add a separate bridge-local
      operating-generation transaction for route, provider-adapter refresh,
      relay adoption, and claim-adapter mutation, keyed to the selected immutable
      runtime's attributable installation receipt.
- [ ] Never mutate install markers from the bridge transaction or present
      application-state rollback as runtime rollback.
- [ ] Prevent candidate resources from publishing or mutating authoritative
      state before commit.
- [ ] Make recovery follow the recorded transaction rather than independently
      inferring ownership from partially updated files.
- [ ] Define partial rollback honestly: retain the newer runtime for sessions or
      state an older reader cannot own safely.

### Phase 4 — Establish the mixed-version gate

- [ ] Produce current/previous client, daemon, host, provider, relay, and
      durable-writer fixtures in both directions for #954's parity runner.
- [ ] Record #954's acceptance of the mixed-version scenario scope before
      changing its runner, then land those changes through the venue-parity PR
      lane and consume foundation-owned fixtures.
- [ ] Until that acceptance lands, keep a narrow fixture-only matrix in this
      effort so #954 is an integration target rather than a blocking dependency.
- [ ] Cover frontend replacement, transport loss, provider refresh, relay
      interruption, claim conflict, cutover failure, rollback, and process/PID
      reuse.
- [ ] Emit a bounded, scrubbed evidence package naming the exact fixture and
      runtime provenance for every matrix arm.
- [ ] Keep local, trusted-container, CodeSpace, and restricted-denial behavior
      on the same contract gate.

### Phase 5 — Let consumers adopt the foundation

- [ ] Let #1266 use the registry, durable outbox prerequisites, and
      current/previous gate while retaining exact AHP version and state-machine
      semantics.
- [ ] Let #1448 use session-pinned control capabilities, attention results, and
      bounded projections while retaining ownership of the delegation
      vocabulary, but enable no delegation-state writer until
      `agent-bridge-delegation-convergence` Phase 0 registers its contract and
      this effort's durable reader/fencing gate is green.
- [ ] Let provider, relay, claim, and event owners add typed or versioned
      behavior only through registered capabilities and the reader-first gate.
- [ ] Measure legacy adapter and record use before changing defaults.

### Phase 6 — Contract and retire

- [ ] Stop legacy writers before removing legacy readers.
- [ ] Maintain a live and durable reference census for every retained generation
      and adapter.
- [ ] Require zero live references, zero recoverable records, an expired
      bridge-contract rollback window, a green current/previous matrix, and an explicit
      retirement record before removal.
- [ ] Re-evaluate any larger protocol boundary only after the additive
      foundation and both convergence consumers have produced evidence.

## Validation Plan

- [ ] Every registered contract has exact old/new fixtures and release
      provenance.
- [ ] New readers accept all supported old records and preserve unknown
      additive fields.
- [ ] A supported old writer cannot erase new ownership, authorization,
      fencing, identity, or recovery metadata.
- [ ] Unsupported peers and capabilities fail or degrade before any
      authoritative side effect.
- [ ] Existing sessions survive frontend, provider, and default changes on
      their selected adapter without duplicate children or split authority.
- [ ] A deliberate handoff creates a successor session that renegotiates and
      records its own contract; the predecessor's contract is never rewritten
      in place.
- [ ] Rollback restores one coherent route/provider/relay/claim generation, or
      reports a bounded partial rollback without pretending a downgrade occurred.
- [ ] AHP conformance remains owned and tested by #1266; foundation tests do not
      declare bridge-invented behavior to be AHP.
- [ ] Delegation ergonomics remain owned and tested by #1448; foundation tests
      prove transport and compatibility, not product vocabulary.
- [ ] The mixed-version matrix passes on local, trusted-container, and CodeSpace
      venues, with restricted authority expansion still denied.

## Proposal

Treat the compatibility foundation as an internal waist beneath multiple public
faces:

| Concern | Owner | Foundation relationship |
|---------|-------|-------------------------|
| AHP JSON-RPC, channels, standard capabilities, snapshots, actions, and client reconciliation | #1266 | Registers exact released contracts and consumes tolerant state, pinning, cutover, and mixed-version gates |
| Native-sub-agent-like create/read/steer/wait/cancel ergonomics, attention boundaries, and bounded results | #1448 and [`agent-bridge-delegation-convergence`](../agent-bridge-delegation-convergence/README.md) | Defines the lifecycle vocabulary and consumes the same session-pinned capability and result transport |
| Immutable event and evidence identity | #1138 | Defines identities safe to expose through either consumer |
| Venue transport and lifecycle parity | #954 | Supplies the shared cross-venue compatibility and fault harness |
| Bridge-side provider adapters, relay integration, claim adapters, routes, and Session Host internals | #1460 | Evolve through the registry and reader-first rollout without changing record semantics owned by agent-worktrees or venue providers |

The detailed contract map is [design.md](design.md).

## Journal

### 2026-08-31 — Kickoff and convergence reconciliation

- Created #1460 as the public coordination token.
- Extended the agent-bridge vision with an explicit negotiated-contract and
  reader-before-writer guarantee.
- Positioned this effort beneath #1266 and #1448 so it supplies shared
  compatibility mechanics without taking ownership of either public contract.
- Reconciled the AHP effort to consume this foundation while preserving exact
  AHP semantics.
