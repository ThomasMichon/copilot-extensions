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
- **Sub-issues:** [#1468](https://github.com/ThomasMichon/copilot-extensions/issues/1468)
  (contract registry and baseline fixtures)
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

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Continuing source-specific Transport/Bridge custody

#### Source #1460

The foundation remains Draft and retains bridge-owned machinery
only. AHP, delegation, #1138 event/reference semantics, provider/relay,
ground claims and venue parity keep their owners. Registry metadata does
not enable a writer/default or retire an adapter/resource. Preserve all
32 inherited #1885/#4185/#4235/#1164/#1915 clauses and operative references
to unfinished inventory, with exact direct-receiving/readback/no-cycle
conditions; they do not impose a new source-owner ACK or later-runtime
completion gate. The specifically concluded #9362 HTTP correction claim
does not release any other claim.

**External semantic dependency, not received scope:** #1138 remains the
specification owner for authority/epoch/sequence and observable rebuild/
authority-change boundaries, restart recovery without identity reuse,
stable projection-to-evidence references, retention/redaction/unavailable
evidence and old/new client coexistence. References never resolve to
different history after rebuild, restart, cursor reset or authority change;
qualify those identity, epoch, recovery, reference, compatibility, retention,
redaction and mixed-version cases at their original tiers. The foundation
registers supplied provenance/fixtures; #1266/AHP and delegation consume
them without defining their meaning. No storage-engine/ledger rewrite,
controller leases or AHP ownership is added. Private opaque-token/
continuity safety is narrower credit, not public semantic completion.
#1138 remains standalone; this coordination note is not a receiving or
supersession decision.


- [ ] **R1460-C1:** Foundation driver retains bridge-owned machinery, not
  AHP/delegation/event/provider/ground/relay/venue semantics. Actual owners/claims remain; registry
  metadata/package versions do not enable writers/defaults or retire adapters/resources.

- [ ] **R1460-C2:** Preserve specific954 runner-scope acceptance before its runner changes AND the
  allowed narrow fixture-only alternative until then. Finite1915 inventory does not depend on
  completing the later runtime programme; no new universal ACK/all-native/full-parent gate.

- [ ] **R1460-C3:** Fence by operation capability/current incarnation, NOT software age: older
  elected rollback writer can remain valid with additive data, newer revoked writer is fenced.
  Unsupported required semantics gets scoped compatibility/doctor outcome, not foreign-owner
  blame;7947 reconstruction remains separate under exclusive actual authority.

- [ ] **R1460-C4:** Preserve actual1164 named still-open registry contract and exact complete direct
  unchecked body/readback/final-open/no-cycle gates, not a parent link. Its original text requires
  no separate source-owner ACK or resource handoff; do not introduce one.

- [ ] **R1460-C5:** Preserve finite1915 inventory and direct
  receiver/readback/operative-pointer/no-cycle conditions; source1915 closure is not completed
  durable evidence or a live historical claim token. Do not make later tolerant-reader/runtime
  programme completion a gate on the finite inventory.

- [ ] **R1460-C6:** Preserve exact4235 whole original-clause-in-open-canonical tracking condition,
  not runtime completion, writer/default activation, source/resource retirement or a new owner
  grant.

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

> Ah, right. We should make sure this is reconciled upstream with other vision
> build-outs of agent-bridge in copilot-extensions. We have a convergence epic
> for AHP Agent Host Protocol and a convergence nudging toward alignment with
> Copilot sub-agent ergonomics. Let's fit this in nicely.

## Plan

### Phase 0 — Freeze the compatibility floor

The first implementation stretch is carved into
[`agent-bridge-contract-baseline`](../agent-bridge-contract-baseline/README.md)
under #1468. It lands the registry, fixtures, and CI checker without changing
runtime behavior.

- [ ] Add a machine-readable registry for every live and durable bridge
      contract, including owner, semantic generation, supported range, optional
      capabilities, state records, fixtures, release provenance, support window,
      bridge-contract rollback window, and removal gate.
- [ ] Freeze fixtures for the current and previous supported HTTP, Session Host,
      provider, relay, claim, route, target, authority, and event shapes.
- [ ] Register AHP from the exact provenance and fixtures supplied by #1308
      under the #1266 lane, and register delegation control from the contract
      supplied by #1448; do not flatten either into an invented universal
      protocol.
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

### Bug sweep — linked open bugs (2026-09-24)

_Correlated via a facility-driven sweep of open `bug`-labeled issues against active efforts (VEI + direct review). Not yet triaged into a numbered phase — listed here as upcoming work for whoever picks this effort back up._

- [ ] **#2991** agent-bridge contract registry: routes/sessions.py hash doesn't match its own committed content on main
  - A contract-registry hash mismatch is exactly this effort's version-skew-safe-contract-evolution scope.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Direct retained Transport/Bridge acceptance

**Agent-recommended additive tracking amendment.** Preserve every incumbent
Request, status, checked credit, original row and first unresolved gate. The
direct rows below retain unfinished work at its original finite scope,
alternatives and evidence tiers. They do not authorize implementation,
native/installed trials, operational changes, source closure, reassignment
or claim/resource release. Normal review, actual merge and complete current
receiving readback precede any separate source-specific disposition.


#### Source #1460

#### Operation authority is not software age

- [ ] **P1460-operation:** Decide mutation authority by the selected operation
  capability and current owning incarnation, not package age or a raw
  selector mismatch. Preserve an older elected rollback writer with compatible
  additive data AND fence a newer revoked writer. Patch preservation retains
  unknown ownership/security fields; unsupported required semantics produce
  a scoped compatibility/doctor outcome, not foreign-owner blame.
- [ ] **P1460-sequence:** Keep the finite durable inventory prerequisite
  before its first tolerant-reader/fenced-writer activation. Do not make
  completion of the later runtime programme a new gate on the finite
  inventory. Preserve #954's actual runner-scope acceptance before changing
  its runner AND the allowed fixture-only matrix until then.


- [ ] **R1460-1460-I1885-1:** Define the non-destructive live-refresh protocol before applying a
  changed provider generation to a surviving Session Host. Reattach and preserve every healthy
  host/child by default; a manifest change alone must not replace live credentials, launch metadata,
  host process or conversation identity.

- [ ] **R1460-1460-I1885-2:** Expose the authoritative provider generation, each session-bound
  generation and any pending/deferred generation in bounded diagnostics without exposing credential
  values.

- [ ] **R1460-1460-I1885-3:** Admit live refresh only when the provider explicitly declares a
  compatible atomic reconnect/rotation capability with observable completion. Negotiate it before
  effects and bind the completed generation once before the next turn.

- [ ] **R1460-1460-I1885-4:** If that capability is absent, unsupported or not safely completable,
  keep the surviving session's valid bound state and report the mismatch; defer the new generation
  to the next safe stopped-session respawn rather than inventing a replacement or second authority.

- [ ] **R1460-1460-I1885-5:** Preserve availability of the first request after a change without a
  connection-reset window. If a consequential request's outcome becomes ambiguous, surface that
  uncertainty and never retry it as if it were an idempotent lookup.

- [ ] **R1460-1460-I1885-6:** Cover live-host generation change with retained host/child/ACP
  identities; atomic-capability refresh exactly once before the next turn; unsupported-capability
  mismatch/defer and later stopped respawn; authoritative/bound/pending diagnostics; and ambiguous
  non-idempotent request refusal.

- [ ] **R1460-1460-I1885-7:** Apply the existing #1460/#1915 source-provenance,
  old-reader/writer-fencing, session-pinning, rollback and mixed-version gates. Do not enable a new
  writer/default, silently upgrade a provider, or retire the old bound adapter merely because
  registry metadata or an implementation version changed.

- [ ] **R1460-1460-I4185-1:** Version the actual live plugin communication boundaries and negotiate
  supported capabilities so independently updated peers degrade safely rather than assuming equal
  package versions. Cover the existing client/daemon, Session Host and provider/relay-facing
  boundaries under their current owners.

- [ ] **R1460-1460-I4185-2:** Keep implementation version, contract generation, semantic capability,
  durable schema and live instance identity distinct. Advertise/select compatible behavior before
  consequential effects and preserve the selected session contract across frontend/default changes
  and recovery.

- [ ] **R1460-1460-I4185-3:** Treat supported old/new combinations, missing optional capabilities,
  unknown/unversioned or unsupported peers and ambiguous post-admission failures explicitly. A safe
  unsupported refusal is not a retry grant after uncertain execution; never probe capability by
  performing the mutating operation.

- [ ] **R1460-1460-I4185-4:** Preserve compatibility adapters and old readers/writers for their
  accepted support window; retain the existing provenance, fencing, rollback and mixed-version
  qualification/removal gates before new writers or defaults activate.

- [ ] **R1460-1460-I4185-5:** Credit the implemented HTTP generation/health/per-attempt-floor
  foundation without treating it as complete negotiation across all live boundaries. Retain
  AHP/ACP's exact external semantics and the separately owned event, provider, relay, claim and
  venue-parity contracts.

- [ ] **R1460-1460-I4235-1:** Preserve compatible-reader evolution across live and durable
  boundaries. Expand actual supported readers before new writers; preserve unknown
  ownership/security metadata and explicit incompatible/unavailable states rather than treating an
  unreadable record as absent or silently erasing it.

- [ ] **R1460-1460-I4235-2:** Fence old/incompatible writers at the real mutation boundary using the
  existing selected contract/schema/generation authority, not only package-version agreement. Keep
  accepted per-session semantics pinned through default/frontend changes and negotiate before
  claim/provider/relay/host/launch effects.

- [ ] **R1460-1460-I4235-3:** Preserve TYPED provider contracts, declared
  lifecycle/trust/launch/auth capabilities and provider-owned durable records. Reuse the existing
  provider/namespace owners; a manifest/version change cannot silently rotate a live host,
  credentials or native conversation. Keep every #1885 compatible-atomic-refresh/defer clause
  intact.

- [ ] **R1460-1460-I4235-4:** Preserve durable authority with one owner for each
  routing/session/task/worktree/claim fact and one recoverable operating-generation transition.
  Compose exact source identity, current head, native/host identity, rollback and ambiguity without
  another compatibility ledger or parallel state owner.

- [ ] **R1460-1460-I4235-5:** Retire old contracts/adapters/writers only through declared
  support/rollback and current mixed-version evidence gates. Distinguish contract retirement from
  physical process/claim management; elapsed age, stale observation, failed health or metadata is
  not evidence or permission to kill, expire or reassign another owner.

- [ ] **R1460-1460-I4235-6:** Keep typed provider, reader/writer, old/new live/durable,
  local/remote/trusted/restricted and interrupted transition/rollback evidence where the
  ORIGINAL/current contract requires it. Registry fixtures and source maintenance are credited but
  are not the entire foundation; no new unrelated all-native/future-effort gate is added by this
  tracking consolidation.

- [ ] **R1460-1460-I1164-1:** Maintain the machine-readable live AND durable inventory with exact
  current/legacy provenance: original HTTP1/1 and2/1 history plus later supported generations,
  SessionHost H1 framing/messages, provider manifests0/1 and structured/legacy targets, relay
  profile, claim outcomes/exit translation, HostIndex, SpawnTarget, route/cutover authority and
  event/evidence shape. Do not drop a family because two live registry entries already work.

- [ ] **R1460-1460-I1164-2:** Every original entry/family carries owner, epoch/semantic generation,
  capabilities/range, exact source/release/capture provenance, fixtures, mixed-version scenarios and
  support/rollback/removal gate metadata. Provide implemented evidence OR a scoped NAMED tracked
  deferral under its real owner; a dead token or parent link is not evidence or completion. Keep
  durable/integration gates before tolerant-reader/new-writer activation.

- [ ] **R1460-1460-I1164-3:** The checker validates registry schema, fixture
  existence/integrity/provenance, current constants/shapes and source freshness, and fails on
  uncovered contract change. A registry maintenance operation changes no runtime default or wire
  behavior and grants no activation/retirement permission.

- [ ] **R1460-1460-I1164-4:** Preserve the accepted bounded nonsemantic path: explicit justified
  classification plus current fingerprint refresh can leave unrelated golden packets byte-identical.
  Do not reintroduce shared-capture-commit/per-packet-live-source overconstraints or rewrite the
  whole corpus for an implementation-only refactor.

- [ ] **R1460-1460-I1164-5:** Semantic changes retain independently authored meaningful expectations
  and checked capture/reproduction at their real source. Stale fingerprints, absent rationale,
  uncovered semantic changes, forged/corrupt provenance and altered packet hashes remain failures; a
  fingerprint swap alone is not semantic qualification.

- [ ] **R1460-1460-I1164-6:** Keep focused schema/checker/real-corpus consumer and
  old/new/mixed-version/removal tests. Record source/mock/native/installed evidence honestly
  according to the ORIGINAL family; no unrelated all-native future matrix is added merely by
  consolidation.

- [ ] **R1460-1460-I1164-7:** Register, but do not redefine,1138 public event/reference semantics,
  assigned1266 exact AHP,1448 delegation and provider/worktree-owned records. Existing private
  opaque tokens/current wire fixtures are not public semantic or complete native-product proof.

- [ ] **R1460-1460-I1915-1:** Freeze immutable CURRENT AND actual PRIOR-RUNTIME fixtures for
  HostIndexv1, SpawnTarget, route and remote-authority records, relay profile and rendezvous state,
  and bridge-side claim adapter. Preserve exact generation versus prior-runtime distinctions; do not
  derive an invented old shape from current producers.

- [ ] **R1460-1460-I1915-2:** Register provider manifest schemas0/1 as externally owned
  NON-NORMATIVE references tied to their semantic owner. Keep worktree/provider/relay semantics
  and1138 event/reference definitions with their existing owners rather than redefining them here.

- [ ] **R1460-1460-I1915-3:** Inventory EVERY ownership, authorization, process/process-start
  identity, fencing and recovery field a supported old writer must preserve or be refused rewriting.
  Distinguish incompatible/unreadable evidence from absence and retain the existing
  unknown-field/security metadata contract.

- [ ] **R1460-1460-I1915-4:** Extend the EXISTING registry/checker/source fingerprints and
  mixed-version scenario inventory. Every normative fixture carries exact owner, source/runtime
  provenance, capture method, hash, support/rollback windows and removal gate; checker rejects stale
  fixtures, uncovered semantic source changes and provenance/source drift. Retain accepted
  nonsemantic maintenance without making a fingerprint swap semantic proof.

- [ ] **R1460-1460-I1915-5:** Complete this finite inventory and name the FIRST bridge-owned durable
  record for subsequent tolerant-reader/old-writer-fencing implementation. Keep the inventory
  prerequisite before that activation while changing NO runtime reader, writer, negotiation or
  default in this evidence slice. Completing the later runtime programme is not a new completion
  gate for the finite inventory itself.

- [ ] **R1460-1460-I1915-6:** Preserve independent immutable old/current expectations, meaningful
  source/release/capture qualification and original family-specific native/installed limits.
  Existing live baseline/maintenance are credited, not this deferred inventory's completion; no
  unrelated all-host/native portfolio or new ACK ceremony is added.

- [ ] **R1460-1460-I1915-7:** Keep all existing1460/1164 clauses, phase sequencing, semantic/source
  custody and support/rollback/removal gates. Existing registry/baseline references to1915 continue
  to denote THIS unresolved live subsection after consolidation, never a completed or dead
  live-claim token; reconcile operative pointers through their normal reviewed owner flow without
  rewriting frozen history or silently enabling consumers. Verify final-open eligibility/no
  cycle/full direct readback before duplicate closure.

- [ ] **SEM-8-P:** Live AND durable registry/provenance/fixtures and distinct
  implementation/protocol/capability/schema/instance identity; registry presence is not runtime
  negotiation.

- [ ] **SEM-9-P:** Supported readers preserve unknown ownership/security fields before consequential
  writers; actual prior-runtime writers preserve opaque fields or refuse, with scoped
  reader/fencing/rollback evidence.

- [ ] **SEM-10-P:** Negotiate before claim/provider/relay/Host/launch effects and persist
  session-bound semantics through frontend/default/recovery without ambiguously replaying admitted
  work.

- [ ] **SEM-11-P:** One recoverable route/provider/relay/claim operating-generation transition
  distinct from immutable install markers/current/LKG and application data rollback.

- [ ] **SEM-12-P:** Current/previous live/durable/venue mixed-version and evidence-gated
  support/retirement remain original finite duties, not package-version equality/all-future-native
  programme.

- [ ] **SEM-13-P:** AHP/delegation/event/provider/ground/relay/parity owners retain semantics;
  consumers wait only for exact shared prerequisites they use.

- [ ] **SEM-14-P:** Paired older-elected/new-additive preservation AND newer-revoked fencing with
  opaque-field patch preservation; package age/raw selector mismatch is not actor authority.

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

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Global validation of retained Transport/Bridge acceptance

#### Source #1460

- [ ] **V1460-operation:** Exercise BOTH older elected compatible
  rollback writer/additive-field preservation AND newer revoked writer
  refusal. Verify the actual operation/incarnation fence and scoped
  compatibility result, not a newer-package-wins rule. Preserve the finite
  inventory exit and actual runner-acceptance OR fixture-only alternative;
  no unrelated future/native portfolio is added.


- [ ] **R1460-V-1460-I1885-1:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Define the
  non-destructive live-refresh protocol before applying a changed provider generation to a surviving
  Session Host. Reattach and preserve every healthy host/child by default; a manifest change alone
  must not replace live credentials, launch metadata, host process or conversation identity.

- [ ] **R1460-V-1460-I1885-2:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Expose the
  authoritative provider generation, each session-bound generation and any pending/deferred
  generation in bounded diagnostics without exposing credential values.

- [ ] **R1460-V-1460-I1885-3:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Admit live refresh
  only when the provider explicitly declares a compatible atomic reconnect/rotation capability with
  observable completion. Negotiate it before effects and bind the completed generation once before
  the next turn.

- [ ] **R1460-V-1460-I1885-4:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: If that capability is
  absent, unsupported or not safely completable, keep the surviving session's valid bound state and
  report the mismatch; defer the new generation to the next safe stopped-session respawn rather than
  inventing a replacement or second authority.

- [ ] **R1460-V-1460-I1885-5:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Preserve availability
  of the first request after a change without a connection-reset window. If a consequential
  request's outcome becomes ambiguous, surface that uncertainty and never retry it as if it were an
  idempotent lookup.

- [ ] **R1460-V-1460-I1885-6:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Cover live-host
  generation change with retained host/child/ACP identities; atomic-capability refresh exactly once
  before the next turn; unsupported-capability mismatch/defer and later stopped respawn;
  authoritative/bound/pending diagnostics; and ambiguous non-idempotent request refusal.

- [ ] **R1460-V-1460-I1885-7:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Apply the existing
  #1460/#1915 source-provenance, old-reader/writer-fencing, session-pinning, rollback and
  mixed-version gates. Do not enable a new writer/default, silently upgrade a provider, or retire
  the old bound adapter merely because registry metadata or an implementation version changed.

- [ ] **R1460-V-1460-I4185-1:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Version the actual
  live plugin communication boundaries and negotiate supported capabilities so independently updated
  peers degrade safely rather than assuming equal package versions. Cover the existing
  client/daemon, Session Host and provider/relay-facing boundaries under their current owners.

- [ ] **R1460-V-1460-I4185-2:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Keep implementation
  version, contract generation, semantic capability, durable schema and live instance identity
  distinct. Advertise/select compatible behavior before consequential effects and preserve the
  selected session contract across frontend/default changes and recovery.

- [ ] **R1460-V-1460-I4185-3:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Treat supported
  old/new combinations, missing optional capabilities, unknown/unversioned or unsupported peers and
  ambiguous post-admission failures explicitly. A safe unsupported refusal is not a retry grant
  after uncertain execution; never probe capability by performing the mutating operation.

- [ ] **R1460-V-1460-I4185-4:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Preserve
  compatibility adapters and old readers/writers for their accepted support window; retain the
  existing provenance, fencing, rollback and mixed-version qualification/removal gates before new
  writers or defaults activate.

- [ ] **R1460-V-1460-I4185-5:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Credit the
  implemented HTTP generation/health/per-attempt-floor foundation without treating it as complete
  negotiation across all live boundaries. Retain AHP/ACP's exact external semantics and the
  separately owned event, provider, relay, claim and venue-parity contracts.

- [ ] **R1460-V-1460-I4235-1:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Preserve
  compatible-reader evolution across live and durable boundaries. Expand actual supported readers
  before new writers; preserve unknown ownership/security metadata and explicit
  incompatible/unavailable states rather than treating an unreadable record as absent or silently
  erasing it.

- [ ] **R1460-V-1460-I4235-2:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Fence
  old/incompatible writers at the real mutation boundary using the existing selected
  contract/schema/generation authority, not only package-version agreement. Keep accepted
  per-session semantics pinned through default/frontend changes and negotiate before
  claim/provider/relay/host/launch effects.

- [ ] **R1460-V-1460-I4235-3:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Preserve TYPED
  provider contracts, declared lifecycle/trust/launch/auth capabilities and provider-owned durable
  records. Reuse the existing provider/namespace owners; a manifest/version change cannot silently
  rotate a live host, credentials or native conversation. Keep every #1885
  compatible-atomic-refresh/defer clause intact.

- [ ] **R1460-V-1460-I4235-4:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Preserve durable
  authority with one owner for each routing/session/task/worktree/claim fact and one recoverable
  operating-generation transition. Compose exact source identity, current head, native/host
  identity, rollback and ambiguity without another compatibility ledger or parallel state owner.

- [ ] **R1460-V-1460-I4235-5:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Retire old
  contracts/adapters/writers only through declared support/rollback and current mixed-version
  evidence gates. Distinguish contract retirement from physical process/claim management; elapsed
  age, stale observation, failed health or metadata is not evidence or permission to kill, expire or
  reassign another owner.

- [ ] **R1460-V-1460-I4235-6:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Keep typed provider,
  reader/writer, old/new live/durable, local/remote/trusted/restricted and interrupted
  transition/rollback evidence where the ORIGINAL/current contract requires it. Registry fixtures
  and source maintenance are credited but are not the entire foundation; no new unrelated
  all-native/future-effort gate is added by this tracking consolidation.

- [ ] **R1460-V-1460-I1164-1:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Maintain the
  machine-readable live AND durable inventory with exact current/legacy provenance: original HTTP1/1
  and2/1 history plus later supported generations, SessionHost H1 framing/messages, provider
  manifests0/1 and structured/legacy targets, relay profile, claim outcomes/exit translation,
  HostIndex, SpawnTarget, route/cutover authority and event/evidence shape. Do not drop a family
  because two live registry entries already work.

- [ ] **R1460-V-1460-I1164-2:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Every original
  entry/family carries owner, epoch/semantic generation, capabilities/range, exact
  source/release/capture provenance, fixtures, mixed-version scenarios and support/rollback/removal
  gate metadata. Provide implemented evidence OR a scoped NAMED tracked deferral under its real
  owner; a dead token or parent link is not evidence or completion. Keep durable/integration gates
  before tolerant-reader/new-writer activation.

- [ ] **R1460-V-1460-I1164-3:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: The checker validates
  registry schema, fixture existence/integrity/provenance, current constants/shapes and source
  freshness, and fails on uncovered contract change. A registry maintenance operation changes no
  runtime default or wire behavior and grants no activation/retirement permission.

- [ ] **R1460-V-1460-I1164-4:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Preserve the accepted
  bounded nonsemantic path: explicit justified classification plus current fingerprint refresh can
  leave unrelated golden packets byte-identical. Do not reintroduce
  shared-capture-commit/per-packet-live-source overconstraints or rewrite the whole corpus for an
  implementation-only refactor.

- [ ] **R1460-V-1460-I1164-5:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Semantic changes
  retain independently authored meaningful expectations and checked capture/reproduction at their
  real source. Stale fingerprints, absent rationale, uncovered semantic changes, forged/corrupt
  provenance and altered packet hashes remain failures; a fingerprint swap alone is not semantic
  qualification.

- [ ] **R1460-V-1460-I1164-6:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Keep focused
  schema/checker/real-corpus consumer and old/new/mixed-version/removal tests. Record
  source/mock/native/installed evidence honestly according to the ORIGINAL family; no unrelated
  all-native future matrix is added merely by consolidation.

- [ ] **R1460-V-1460-I1164-7:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Register, but do not
  redefine,1138 public event/reference semantics, assigned1266 exact AHP,1448 delegation and
  provider/worktree-owned records. Existing private opaque tokens/current wire fixtures are not
  public semantic or complete native-product proof.

- [ ] **R1460-V-1460-I1915-1:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Freeze immutable
  CURRENT AND actual PRIOR-RUNTIME fixtures for HostIndexv1, SpawnTarget, route and remote-authority
  records, relay profile and rendezvous state, and bridge-side claim adapter. Preserve exact
  generation versus prior-runtime distinctions; do not derive an invented old shape from current
  producers.

- [ ] **R1460-V-1460-I1915-2:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Register provider
  manifest schemas0/1 as externally owned NON-NORMATIVE references tied to their semantic owner.
  Keep worktree/provider/relay semantics and1138 event/reference definitions with their existing
  owners rather than redefining them here.

- [ ] **R1460-V-1460-I1915-3:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Inventory EVERY
  ownership, authorization, process/process-start identity, fencing and recovery field a supported
  old writer must preserve or be refused rewriting. Distinguish incompatible/unreadable evidence
  from absence and retain the existing unknown-field/security metadata contract.

- [ ] **R1460-V-1460-I1915-4:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Extend the EXISTING
  registry/checker/source fingerprints and mixed-version scenario inventory. Every normative fixture
  carries exact owner, source/runtime provenance, capture method, hash, support/rollback windows and
  removal gate; checker rejects stale fixtures, uncovered semantic source changes and
  provenance/source drift. Retain accepted nonsemantic maintenance without making a fingerprint swap
  semantic proof.

- [ ] **R1460-V-1460-I1915-5:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Complete this finite
  inventory and name the FIRST bridge-owned durable record for subsequent
  tolerant-reader/old-writer-fencing implementation. Keep the inventory prerequisite before that
  activation while changing NO runtime reader, writer, negotiation or default in this evidence
  slice. Completing the later runtime programme is not a new completion gate for the finite
  inventory itself.

- [ ] **R1460-V-1460-I1915-6:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Preserve independent
  immutable old/current expectations, meaningful source/release/capture qualification and original
  family-specific native/installed limits. Existing live baseline/maintenance are credited, not this
  deferred inventory's completion; no unrelated all-host/native portfolio or new ACK ceremony is
  added.

- [ ] **R1460-V-1460-I1915-7:** At the original finite source/native tier, qualify this entire
  retained clause, preserving its actual OR/deferral/maintenance alternatives: Keep all
  existing1460/1164 clauses, phase sequencing, semantic/source custody and support/rollback/removal
  gates. Existing registry/baseline references to1915 continue to denote THIS unresolved live
  subsection after consolidation, never a completed or dead live-claim token; reconcile operative
  pointers through their normal reviewed owner flow without rewriting frozen history or silently
  enabling consumers. Verify final-open eligibility/no cycle/full direct readback before duplicate
  closure.

- [ ] **SEM-8-V:** At the original finite supported tier, demonstrate without substituting
  source/helper/receipt/count: Live AND durable registry/provenance/fixtures and distinct
  implementation/protocol/capability/schema/instance identity; registry presence is not runtime
  negotiation.

- [ ] **SEM-9-V:** At the original finite supported tier, demonstrate without substituting
  source/helper/receipt/count: Supported readers preserve unknown ownership/security fields before
  consequential writers; actual prior-runtime writers preserve opaque fields or refuse, with scoped
  reader/fencing/rollback evidence.

- [ ] **SEM-10-V:** At the original finite supported tier, demonstrate without substituting
  source/helper/receipt/count: Negotiate before claim/provider/relay/Host/launch effects and persist
  session-bound semantics through frontend/default/recovery without ambiguously replaying admitted
  work.

- [ ] **SEM-11-V:** At the original finite supported tier, demonstrate without substituting
  source/helper/receipt/count: One recoverable route/provider/relay/claim operating-generation
  transition distinct from immutable install markers/current/LKG and application data rollback.

- [ ] **SEM-12-V:** At the original finite supported tier, demonstrate without substituting
  source/helper/receipt/count: Current/previous live/durable/venue mixed-version and evidence-gated
  support/retirement remain original finite duties, not package-version equality/all-future-native
  programme.

- [ ] **SEM-13-V:** At the original finite supported tier, demonstrate without substituting
  source/helper/receipt/count: AHP/delegation/event/provider/ground/relay/parity owners retain
  semantics; consumers wait only for exact shared prerequisites they use.

- [ ] **SEM-14-V:** At the original finite supported tier, demonstrate without substituting
  source/helper/receipt/count: Paired older-elected/new-additive preservation AND newer-revoked
  fencing with opaque-field patch preservation; package age/raw selector mismatch is not actor
  authority.

- [ ] **G1460:** Resolve every directly retained #1460 Plan and Validation clause at its original
  finite evidence tier, preserving alternatives, completed credit and existing custodians.
  Administrative tracking is not technical completion or claim release.

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

### 2026-08-31 — First implementation stretch carved

- Opened #1468 and carved
  [`agent-bridge-contract-baseline`](../agent-bridge-contract-baseline/README.md)
  as the behavior-neutral implementation of Phase 0.
- Kept AHP corpus ownership in #1308, delegation semantics in #1448, event
  identity in #1138, and provider/worktree record semantics with their owning
  plugins.

### 2026-08-31 — Kickoff and convergence reconciliation

- Created #1460 as the public coordination token.
- Extended the agent-bridge vision with an explicit negotiated-contract and
  reader-before-writer guarantee.
- Positioned this effort beneath #1266 and #1448 so it supplies shared
  compatibility mechanics without taking ownership of either public contract.
- Reconciled the AHP effort to consume this foundation while preserving exact
  AHP semantics.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### 2026-10-28 - Additive Transport/Bridge tracking contracts

Authored direct finite Plan and actual global Validation with continuing

custody after complete current receiving-document and source-contract

research. Normal review, actual merge and complete current readback remain

separate publication gates; no future delivery or disposition is claimed.

Credited #1926 baseline, #7534 nonsemantic maintenance, #7593 capability work and reported #9362 HTTP correction (181 plus 53 source cases). These are attributed existing source results, not tests run by this amendment, deployment or whole-foundation completion.
