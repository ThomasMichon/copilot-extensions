# Fleet Control V2

- **Slug:** `fleet-control-v2`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, serial per-slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Active; isolated runtime identity verifier in review; policy/integration/lifecycle open
- **Vision:** machine-fleet `unified-coordination-product`; agent-fabric and plugin-services explicit integrated-product profile
- **Umbrella issue:** #6236
- **Sub-issues:** #6240 release/lifecycle; #6241 command/receipt contracts;
  #6443 Gateway/index vertical slice; existing #5862 coherent observation and
  central push-backed state. Existing #5789, #6030 and #5861 retain ownership.

## Guiding Intent

Replace fragmented integrated control ownership with one standalone fleet-manager
product: one supported release/lifecycle implementation and a transactional
Gateway coordination core usable locally or for multiple machines. Preserve
independently useful guidance, existing standalone services, driver authority,
credential isolation, durable execution and accepted work.

This effort owns the cross-cutting architecture and finite migration contract,
not a competing implementation of the routing or Linux deployment campaigns.
Plan review is the gate before runtime buildout. Publishing this plan grants
neither live enrollment nor deployment, destructive retirement or capacity access.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Plan steward | Plan/vision reconciliation, issue coordination and review stewardship | Managed planning worktree; umbrella issue |
| Contract implementer | Release/role #6240, operation/receipt #6241 and isolated observation-storage #5862 contracts | Managed implementation worktree; assigned sub-issues |
| Gateway/index integrator | End-to-end new Gateway, index client and subscribed credential integration #6443 | Separate managed worktree; #6443 and index consumer #6383 |
| Existing campaign owners | Routing, standalone client, role deployment and provider integration | Existing campaign issues and canonical efforts |
| Adopter | Configuration, capacity, enrollment and live activation approval | Explicit downstream adoption workflow |

## Coordination

- **Topology:** Reviewed architecture followed by serial, independently reviewed slices.
- **Host (owns PRs):** Plan steward for this proposal; each implementation slice names its PR owner.
- **Delegates:** None assigned to runtime implementation by this plan.
- **Slice ownership:** Never assign a slice already held by another campaign.
  Coordinate #5789/#6030 and the standalone client PR #6133 before touching their files.
  Client-only installation lifecycle #6424 also retains ownership; the
  Gateway/index slice consumes it rather than opening a parallel installer.
- **Gateway/index slice:** The operator assigned its end-to-end integration to
  the Gateway/index integrator. This is a contribution to this product, not a
  competing Gateway or ledger. Existing contract, auth, deployment and migration
  owners retain their work; inventory unmerged prototypes before duplicating
  implementation. The integrator owns the slice's PRs, not the whole V2 effort.
- **Central state and observation:** Reuse #5862 for the shared backing-store
  and query/subscription plane, not a competing observer. #3761 retains origin
  agency/write-through authority; #352 retains Manager UX/mux ownership.
  A selected containerized Gateway is one deployment of the coordination
  product, not a second native/prototype Gateway beside it. Existing credential
  coordinators and unselected adopter deployments are not duplicate cleanup.
- **Handoff:** Continue from the first unresolved Plan item with its validation
  gates and current owner, not merely from the most recent merged PR.
- **Canonical home:** This effort and its linked proposal are the upstream
  implementation contract. Private presentation copies are non-authoritative
  exports and are not linked from public artifacts.
- **Completion boundary:** Plan publication is not V2 implementation completion.

## Context

[Architecture proposal](architecture.md) records the recommendation, alternatives,
authority boundaries and implementation gates. It reconciles the
[machine-fleet vision](../../../visions/machine-fleet/README.md) and the existing
[fleet-manager system proposal](../linux-role-fleet/fleet-manager-system.md).

The routing foundation #5789 provides fixed-service routing contracts, not
already-proven resident Gateway/connector operation. Linux role fleet #6030 owns
deployment adapter adoption, while #6133 proposes a thin standalone offline
client planner. #5861 owns named reconciliation and #5862 observed recovery.
Existing work is reused, not preempted or silently marked complete.

The suite already has shared immutable runtime, installer, endpoint and cutover
primitives. The redesign replaces distributed *authority and lifecycle ownership*
where integration requires it; it does not presume all existing code is defective.
Prior source investigation and a synthetic local SQLite model motivated the
proposal. Neither establishes distributed reliability, service migration safety,
real installer readiness or production performance.

## Request

Operator direction, captured verbatim:

> "Ideally, your centraliziation proposal produces a service which can be put as
> the Gateway service, and either used machine-local or act as the coordinator
> for multiple machines seamlessly."

> "This looks like a good plan. Get this plan deposited in my OneDrive (proper
> organization please). Then, get this reviewed via PR, and prepare to start
> driving it via effort buildout"

> "Sounds good. Continue driving."

Later, asked whether release authentication should support offline pinned signing
keys, GitHub Actions identity/artifact attestations, or both with an explicitly
selected policy per installation, the operator selected verbatim:

> both

When asked to authorize a bounded GitHub workflow for authentic test-manifest
attestations, the operator responded verbatim:

> Let's avoid anything require special GitHub auth. What is this for?

No fixture workflow, new GitHub signing permission or special authentication
was authorized. Continue offline pinned-key verification; the GitHub-specific
positive proof gate remains deferred, not completed or silently substituted.

The operator confirmed the slug `fleet-control-v2`. Personal artifact delivery
is handled outside this public repository. The architecture details, phase
breakdown and validation below are **agent-recommended** elaborations of the
approved direction. No runtime or live rollout authorization is inferred from
"prepare". The subsequent direction authorizes driving the recommended contract
implementation; live enrollment, deployment and destructive retirement still
require their own approval.

Subsequent operator assignment, captured verbatim:

> Own the end-to-end new Gateway/index integration slice

The operator explicitly selected the new fleet-manager Gateway and instructed
that older adopter Gateways remain untouched. The requested index-side container,
CLI query path and token requests through the Gateway to a subscribed credential
controller are captured in
the [standalone index effort](../agent-index-standalone-service/README.md).
The [Gateway/index slice](gateway-index-slice.md) below is an
**agent-recommended implementation elaboration** of that assignment.

Further operator clarification, captured verbatim (generic architecture portion):

> What you can do is look to how we're going to build a central backing store for Worktree Manager, the uber-status-monitor that receives updates via push from all machines and containers, and then supports requests with subscriptions for all UX surfaces, CLI tools, and other API requests.

Adopter-specific reconciliation and deployment identities are recorded outside
this public repository. The requested container preference selects one canonical
Gateway deployment for that adopter; it is not authority to add a third Gateway,
retire unrelated credential coordinators, or bypass current lease/data owners.
The [central backing-store amendment](central-state-backing-store.md) is an
**agent-recommended design** of the new push/query/subscription direction, not
permission for an unreviewed runtime or authority migration.

Asked to approve a first transport-neutral storage slice under #5862 in
`fleet-manager` only, the operator selected verbatim:

> approve-isolated-store

The approved **agent-proposed boundary** is bounded current rows,
origin-generation fences, idempotent updates and durable query/replay positions.
It expressly excludes producer/auth code changes, another Gateway and live
deployment changes. Authenticated transport and client integration still require
agreement with their retained owners; this is not a transfer of those scopes.

When the missing authentication interface blocked integration, the operator
directed verbatim:

> I don't think we have an interface going yet for auth so you can propose one based on the vision and what you have needed so far.

The [runtime authentication proposal](runtime-authentication.md) is the
**agent-recommended interface** responding to that direction. This authorizes
proposal authoring rather than waiting for an existing interface; it does not
assert that no prototype exists, authorize an unreviewed auth implementation,
transfer another owner's active code, or enroll/deploy a live role.

The operator then clarified presence defaults verbatim:

> Make sure that the live user does not have to continuously re-validate presence. We'll want to treat Entra/Windows' own session longevity as presence, and run until the underlyting tokens finally issue presence challenges. We'll rely on explicit revocation, or maybe the user opting into a dead-man switch, to "cut short" access if desired. Default is "run as long as possible"

This longevity/default is **operator-required**, not an optional agent
recommendation. Token/proof/authorization renewal is silent machine work while
the underlying provider session permits it; it is not periodic human presence
validation. Actual provider challenges and revocation remain binding, and the
dead-man switch is explicit opt-in rather than a default session timeout.

After the reviewed proposal actually merged, the operator directed verbatim:

> Great. Start driving the work

This authorizes driving the reviewed implementation, beginning with an isolated
verifier prerequisite. It does not select live trust roots, enroll principals,
transfer another campaign's active source ownership or authorize deployment.

## Plan

All detailed slices are **agent-recommended** and remain subject to review.

### Phase 0 - Reviewed intent and coordinated buildout
- [x] Confirm canonical effort placement and slug; deduplicate and claim #6236.
- [x] Land the effort, [architecture proposal](architecture.md), and explicit
  integrated-product vision/pattern amendments through actual PR review.
- [x] Re-read the merged plan; coordinate existing campaign owners and assign
  non-overlapping sub-issues before any runtime implementation.
- [x] Capture actual launch/update/recovery baseline and select first vertical
  slice acceptance criteria in [the baseline/gate record](baseline-and-vertical-slice.md);
  unsupported product lifecycle is explicit, not invented performance.

### Phase 1 - One release and role-selective lifecycle
- [ ] Define a fleet-manager release manifest for thin client, Gateway and node
  roles; reuse existing lifecycle code rather than implementing parallel installers.
  Declaration and offline snapshot inspection landed in #6285; actual product
  assembly and lifecycle activation remain pending.
  Role-selected actual artifact integrity preflight landed in #6437 as a prerequisite;
  client packaging/diagnostics/managed installation remain owned by #6424.
  The [both-method release authentication plan](release-authentication.md)
  landed in #6451, pinned-key manifest verification in #6475 and retained
  authenticated artifact staging in #6569. Staging yields selected immutable
  bytes, not candidate construction, lifecycle admission or activation.
  Explicit selected-release data landed in #6637 and correlates a trusted current
  decision with the manifest, sequence, scope and role closure; it supplies no
  selection authority or durable admission. The reviewed
  [client admission plan](../linux-role-fleet/client-lifecycle-admission.md)
  retains installation-owned state and candidate execution with #6424.
  GitHub-positive proof is deferred under the operator's authentication constraint;
  no live trust-root, signing or activation authority follows from verification.
- [ ] Prove independent setup/update/doctor/recovery and bounded activation,
  with no installation or update from Copilot hooks.
- [ ] Prove thin client installation has no controller/container-manager effects;
  keep ordinary standalone services usable outside the integrated profile.

### Phase 2 - One authority, isolated execution
- [ ] Implement identified command admission, content-bound idempotency,
  transactional intent/claims/audit/outbox and target-side receipt reconciliation.
  Intent/receipt contracts and local admission/outbox/audit persistence landed
  in #6357/#6360; explicit node acceptance/effect receipts landed in #6388.
  Durable receiver cursors landed in #6395 and local allocation claims in #6403.
  Cooperative SQLite effect-owner checks landed in #6411 and explicit local
  retirement/release/generation transfer in #6418. External effect-owner fencing
  and authenticated runtime integration remain open. Installed two-node journal
  process recovery is a bounded prerequisite, not resident Gateway/node proof.
- [ ] Prove objective -> workspace -> host -> prompt -> outcome -> settlement
  locally, then through one Gateway and two separate nodes.
- [ ] Persist scope/generation fences at cooperative effect owners; define
  UNKNOWN and safe blocked reassignment for unsupported/uncertain effects.
- [ ] Keep accepted agent/mux work, credentials and expensive workloads outside
  coordinator lifetime; all integrated roles share one lifecycle implementation.

### Phase 2a - Gateway/index vertical slice
- [ ] Review the [slice contract](gateway-index-slice.md) and coordinate #6443,
  #6383 and retained fleet/auth/deployment owners before runtime changes.
- [x] Review the shared [runtime authentication interface](runtime-authentication.md)
  under #5789/#6443.
- [ ] Agree exact auth records/provider support and exclusive implementation
  scopes before wiring consumers.
  The first isolated verification prerequisite has its own new module, optional
  JOSE dependency and proof tests; enrollment, scoped policy and resident
  consumers remain separate gates. See the
  [implemented API boundary](../../../fleet-manager/docs/runtime-authentication.md).
- [ ] Supply the resident role-selected Gateway and fixed-service index route
  using the existing release, offer/request and operation/receipt contracts.
- [ ] Authenticate and authorize client/node/controller principals, service
  generations and corpus/source grants at admission and backend execution.
- [ ] Route correlated source-credential requests to an authorized subscribed
  credential controller, with refresh, expiry/revocation and explicit disconnect
  outcomes.
- [ ] Wire explicit index CLI Gateway transport and detached-worker credential
  consumption without changing unselected native/SSH behavior.
- [ ] Prove the real CLI/Gateway/container/controller path, independently
  persistent index state and scoped negative cases before any native offload.

### Phase 2b - Central push-backed current state
- [x] Review the [backing-store amendment](central-state-backing-store.md) under
  existing #5862, preserving #3761 origin authority and #352 client/mux ownership.
- [ ] Agree authenticated origin/push, current-projection, persistence and
  query/subscription interfaces with the retained owners before runtime edits.
  The subsequent operator approval permits only an isolated transport-neutral
  storage prerequisite; the later auth-proposal direction permits specifying
  that interface, not unreviewed producer/auth/client integration.
- [ ] Persist fenced machine/container updates, indexed current projections and
  publication positions coherently, without a rival task/worktree/claim writer.
  Isolated point-observation storage and bounded replay landed in #6685; actual
  authorized origin push and complete roster publication remain unimplemented.
- [ ] Serve UX, CLI and API requests through one scoped current-state contract,
  with snapshot/subscribe consistency, bounded recovery and isolated backpressure.
- [ ] Prove authorized one-Gateway adoption with owned storage, preserved source
  responsibilities, explicit offline/gap states and no per-client fleet polling.

### Phase 3 - Transfer authority without duplicate writers
- [ ] Inventory V1 authorities, state schemas, live hosts, receipts and obligations;
  coordinate imports with their current owners.
  The [bounded source inventory and migration gates](v1-migration.md) document
  inspected storage seams and unresolved owner/live-evidence prerequisites;
  source reports and selected existing tests do not complete this inventory.
- [ ] Implement restartable import/reconciliation, explicit admission freeze,
  and verified adoption or drain without dual authoritative writes.
- [ ] Establish per-domain drain deadline and owner/escalation decisions; do not
  kill live work just because a compatibility window elapsed.
- [ ] Retire only verified owned writers/registrations and prove repeated
  installation cannot resurrect the previous lifecycle.

### Phase 4 - Adoption evidence and closure
- [ ] Record compatibility, data rollback/commit-forward boundaries, platform
  support, diagnostics and mixed-version per-target outcomes.
- [ ] Obtain separate adopter approval and prove a controlled live deployment;
  no publication or green mock test substitutes for enrollment authority.
- [ ] Resolve or transfer every Plan/Validation item, archive this effort,
  and release ownership only after its full completion gate.

## Validation Plan

All entries are **agent-recommended implementation gates**, not completed tests.

- [ ] Complete the [central-state gates](central-state-backing-store.md#validation-gates-and-rollout-boundary):
  actual machine/container push, scoped shared reads/subscriptions, commit/ack
  crash recovery, snapshot/stream consistency and one owned serving deployment.
- [ ] Complete the [Gateway/index slice gates](gateway-index-slice.md#validation-and-adoption-gates):
  real CLI/query/credential transport, scope enforcement before retrieval,
  crypto/correlation/revocation, fresh container persistence and live adoption.
- [ ] Complete the [runtime auth interface proof](runtime-authentication.md#implementation-and-validation-gate):
  real identity/proof verification, current policy/enrollment, scoped backend/
  observation enforcement, revocation/recovery and provider/recipient isolation.
  Prove long-lived provider/OS sessions renew without Gateway-added presence
  prompts, with real challenge, explicit revocation and optional dead-man
  outcomes distinct from token/lease churn or temporary unavailability.
- [ ] Real first-touch Windows/Linux install with no Copilot/plugin/runtime cache;
  offline installed use and precise missing/incompatible/unauthorized failures.
- [ ] Concurrent activation/admission: one authoritative writer, unique resource
  ownership and bounded contention, with real process boundaries.
- [ ] Crash injection at intent/effect/receipt and activation boundaries;
  accepted work and queryable original operation survive without duplicate effects.
- [ ] Separate Gateway and two node processes/hosts: reconnect, revoked connector,
  stale offer, cross-fleet denial, slow-node isolation and result replay.
- [ ] Partition plus attempted reassignment: expiry does not prove prior effects
  stopped; actual resource owner prevents unsafe concurrent authority.
- [ ] Real agent/mux host survival and reattachment across controller replacement,
  including lost prompt acknowledgments and explicit UNKNOWN outcomes.
- [ ] Schema migration/rollback, Gateway storage relocation and writer fencing;
  never restore stale state over acknowledged post-upgrade writes.
- [ ] Rolling release with offline/older nodes and invalid config revision:
  individual acceptance/readiness/convergence, not false global success.
- [ ] Two authority/account domains and separate credential providers:
  no automatic grant widening or local fallback on Gateway outage.
- [ ] V1 import with live work and unresolved obligations, then retirement/
  reinstall/uninstall: no lost responsibility, double writers or revived feeders.
- [ ] Unit, simple E2E, clean-room and available live-venue tiers recorded for each
  implementation slice; skipped tiers and unsupported platforms remain explicit.
- [ ] Required repository guards/CI and actual review pass before each merge;
  runtime readiness and rollout remain separately proven.

## Proposal

[Architecture and migration contract](architecture.md).

## Journal

### 2026-10-12 - Gateway/index ownership assigned
- Operator explicitly assigned the end-to-end new Gateway/index integration
  slice, after selecting the new product rather than an older adopter Gateway.
- Claimed #6443 after deduplicating existing routing, credential and index work.
  Existing #6240/#6241/#5789/#6030 responsibilities remain intact.
- Prepared the bounded slice proposal before runtime implementation. Current
  merged fleet primitives are not cryptographic auth, resident routing or
  subscribed credential controller delivery; unmerged prototypes need inventory
  and coordination before reuse or replacement.

### 2026-10-11 - Approved direction and plan preparation
- Operator approved the adversarial redesign, requested organized personal
  artifact preservation and PR review, and confirmed this effort's slug.
- Verified compatible target effort adoption and repository-scoped identity;
  claimed #6236 after searching existing fleet issues and open PRs.
- Kept #5789/#6030/#6133 ownership intact. Prepared only architecture/intent and
  implementation gates; no runtime, host enrollment or live cutover performed.

### 2026-10-11 - Plan reviewed and contract intake prepared
- #6238 squash-merged into `dev` at `2026-10-11T22:23:40Z`, commit
  `3c5d10ef5410455d52e4fd469e338f7cb69288ad`, after actual Copilot APPROVED review
  on the corrected head and green required CI.
- Addressed revision-date and PR cutover-impact observations. Preserved the
  Request spelling verbatim rather than silently altering operator intent.
- Re-read the canonical Plan. Created #6240 for release/lifecycle contracts and
  #6241 for command/node-receipt contracts after deduplication; neither assigns a
  competing runtime implementation owner.
- Next gate is coordinating the existing campaign owners and assigning distinct
  contract slices. The full effort, runtime proof, adoption and retirement remain
  open. No runtime implementation or live deployment began.

### 2026-10-12 - First bounded contract slice
- Operator authorized continued driving after selecting release/role/readiness
  contracts as the first starting point.
- Rechecked #6240 and adjacent work; #6133 is merged and the standalone client
  remains an offline planner. Assigned the first #6240 implementation slice and
  retained routing/auth/deployment campaign ownership.
- Transitioned the implementation participant to Phase 1 for the bounded #6240
  slice; canonical operation/node-receipt implementation remains unassigned.
- Added strict release role declarations and explicit non-mutating observation
  inspection. Snapshot readiness is not a live probe or signed-artifact proof.
  Installation, Gateway/node service execution and migration remain pending.

### 2026-10-12 - Release snapshot contract landed; receipt seam started
- #6285 merged at `2026-10-12T20:40:47Z`, commit
  `1604313dc1971fc37e551aaa2ee05575b6068757`, after current-head Copilot APPROVED
  with zero open findings and green required CI. Installed contained suite:
  104 passed; lint and contract guards passed.
- Review corrections preserve observation validity windows, refuse oversized
  epoch integers without tracebacks and retain manifest mismatch precedence.
  Results remain supplied-snapshot assertions, not live readiness.
- Re-read the full Plan and pulled forward the verified squash merge, preserving
  newer documentation-owner and effort-index changes.
- Claimed a distinct first #6241 slice: immutable intent/receipt shape validation,
  content/scope/generation correlation and conservative UNKNOWN semantics.
  Existing routing/auth owners and production queues remain untouched.
- Next is durable admission/outbox and node receipt state after this contract
  clears review. No runtime activation, fleet enrollment or deployment performed.

### 2026-10-12 - Receipt contract reviewed; local persistence boundary
- #6357 merged at `2026-10-12T21:07:34Z`, commit
  `33b6ad00340d1e8759d69c3ee6a5e0c1036dd38f`, with current-head Copilot APPROVED,
  zero findings and green required CI. Installed contained suite: 143 passed.
- Re-read the full Plan and pulled forward before starting the local journal
  primitive under #6241. This adds explicit admission/outbox/audit and receipt
  transactions without a CLI mutator, executor or production task allocator.
- Allocation claims, durable node-side effects, authentication, distributed
  fencing, installer lifecycle and live adoption remain unresolved proof gates.

### 2026-10-12 - Local journal reviewed and merged
- #6360 merged at `2026-10-12T22:35:58Z`, commit
  `e214622ffee76280e055afe60a81d4f6441075c4`, after current-head Copilot APPROVED
  with zero findings and green required CI. Installed contained suite: 156 passed.
- Review fixes preserve deferred query-only reads, fail without recreating lost
  storage and reject foreign databases including view-only schema objects.
  Corrected a misplaced regression try/finally and reran the full suite; no failed
  validation was waived.
- Re-read the parent Plan and pulled forward the verified squash merge.
  Local durable admission is a building block, not complete distributed execution.
- Next slice under #6241: durable node acceptance/effect receipts and result
  reconciliation, followed by allocation/authorization and actual effect-owner
  fencing. Installation, Gateway service, live fleet adoption and migration
  gates remain open. No deployment or enrollment was performed.

### 2026-10-12 - Node acceptance and effect receipts landed
- #6388 merged at `2026-10-12T23:27:09Z`, commit
  `a7d327c6f2d1b332b2369f421ba003b48d42b30e`, after actual current-head Copilot
  APPROVED with zero findings and green required and ordinary CI.
  Installed contained suite: 177 passed.
- Separate process evidence covers cooperative effect/receipt commit, pre-commit
  crash rollback, post-commit lost reply with one effect after retry, concurrent
  duplicate effects, and external-attempt crashes preserving UNKNOWN.
  This does not establish arbitrary external exactly-once or remote fencing.
- Re-read the parent Plan and advanced the clean worktree to the verified merge,
  retaining a backup of the published commit. The full effort remains active.
- Next is durable Gateway receiver cursor plus receipt/outbox/audit reconciliation
  in one transaction, so reconnect after a lost receiver reply does not replay
  stale acceptance over a later outcome. Allocation/authorization/effect-owner
  fencing and real local/two-node runtime proof follow. Existing routing/auth
  and deployment ownership remains intact; no fleet was enrolled or deployed.

### 2026-10-13 - Durable receiver replay landed
- #6395 merged at `2026-10-13T00:06:21Z`, commit
  `d0a64764aadd61ac16fb4507f8a570cf219b2cd5`, after fresh rebased-head Copilot
  APPROVED with zero findings and every required/ordinary check green.
  Installed contained suite: 196 passed.
- Receiver cursor, correlated receipts, outbox and audit commit together.
  Separate receiver processes prove page rollback before cursor commit and
  restart from the durable position after a committed but lost reply.
- Known schema-1 journals upgrade transactionally to schema 2; foreign
  inventory and ordinary/virtual/stored generated columns are refused without
  mutation. Kept the effort index aligned, preserved a concurrent effort owner's
  updated row during rebase, and reran the installed suite afterward.
- Re-read the full Plan; none of its broader runtime or lifecycle gates is
  complete from these local primitives. The next bounded seam is transactional
  allocation claims and cooperative effect-owner fencing, coordinated with
  existing routing/auth owners before any runtime interface is changed.
  Live enrollment, deployment, authority migration and retirement remain gated.

### 2026-10-13 - Allocation claim slice prepared
- Under #6241, added combined identified allocation admission/claim/outbox/audit,
  exact intent/resource retry and exclusive resource ownership across targets,
  principals and accounts in one explicit local ledger. No receipt status releases
  a claim, and there is no expiry, release or reassignment API.
- Schema 1/2 upgrade adds an empty claim table without inventing historical
  ownership. Legacy unclaimed admissions cannot acquire claims retroactively.
- Installed contained suite: 224 passed. Separate-process crash/lost-reply and
  four-process distinct/duplicate contention exercise the real installed package.
  Fixed the migration fixture's missing explicit commit and reran the suite.
- Validation tiers: unit and local installed-package E2E exercised; fresh local
  database creation exercised, but full first-machine clean-room omitted because
  this slice has no installer/bootstrap changes. Live external-venue validation
  omitted: no integrated Gateway/node service or authorized fleet is available.
- Documentation describes local claim evidence, canonical resource resolution,
  migration and lack of effect-side exclusion. This closes only the storage seam
  of machine-fleet `unified-coordination-product`; reviewed architecture and
  independence/effect-owner-fencing invariants remain unchanged.
- Next after review/merge is cooperative effect-owner resource fencing and explicit
  release evidence, not TTL-based reassignment. The full Plan and Validation Plan
  remain open; routing/auth and deployment owners retain their scopes.

### 2026-10-13 - Allocation claims landed; effect-owner seam started
- #6403 merged at `2026-10-13T00:40:17Z`, commit
  `a8a7e0636e86b80d1534be0b7242eca430e46788`, after current-head Copilot APPROVED
  with zero findings and green required and ordinary CI. Final installed suite:
  224 passed. Pulled forward the verified merge with managed sync.
- Re-read the parent Plan and Validation Plan. Local storage joins are now present;
  authenticated runtime integration, effect exclusion and lifecycle/adoption proof
  remain open. This is not complete Phase 2 or objective settlement.
- Next bounded #6241 slice persists a cooperative local SQLite resource owner at
  node acceptance and checks exact owner/intent/generation in the effect
  transaction. Retained ownership blocks competing generations; no expiry or
  external-resource fencing is inferred. Explicit verified release evidence follows.

### 2026-10-13 - Cooperative allocation owner slice prepared
- Added node-local held ownership with acceptance/spool, and exact
  resource/intent/generation checks inside the cooperative SQLite effect
  transaction. Callback effects and outcomes still commit or roll back together.
  Rechecks reject a callback that mutates its own binding.
- Generic unbound allocation effects and external allocation attempts fail
  explicitly. Node-local SQLite ownership is not a fence for a filesystem,
  provider, running process or resource shared across nodes/databases.
- Known schema-1 upgrade preserves accepted work, result spool and effect tables,
  adds no historical ownership, and refuses reserved-name/core-column collisions.
  No claim release, TTL or generation reassignment was introduced.
- Unit and simple installed-package local E2E: 247 passed, including four real
  competing/duplicate node clients, acceptance and effect precommit crashes,
  postcommit lost replies, resource storage failure, receipt rollback and
  Gateway result reconciliation. Lint, install contract and version consistency
  passed. Full machine clean-room omitted: no installer/bootstrap seam changed.
  Live external-venue omitted: no adopted integrated Gateway/node runtime exists.
- Documentation states the exact cooperative boundary and changed failure paths.
  This realizes part of the reviewed machine-fleet effect-owner intent without
  changing standalone lifecycle/defaults or existing routing/auth ownership.
  Next after review/merge is explicit verified release evidence and safe local
  generation transfer, not expiration-based reassignment. Full runtime, partition,
  host-survival, installation and adoption gates remain open.

### 2026-10-13 - Cooperative local ownership landed
- #6411 merged at `2026-10-13T01:04:24Z`, commit
  `4b9a0fe246d45d78aec8a4c17dbee13910eda355`, after current-head Copilot APPROVED
  with zero findings and every required/ordinary check green. The first review
  window timed out; an explicit request and fresh wait produced the actual verdict.
  Installed contained suite: 247 passed.
- Managed sync pulled forward the verified merge. Re-read the full parent
  Plan/Validation Plan; the cooperative SQLite seam does not prove external
  fencing, authenticated runtime, partition behavior or complete Phase 2.
- Next under #6241 is immutable, exact-resource/scope/generation-bound cooperative
  release evidence with atomic effect-owner retirement and retained tombstones.
  Gateway release must consume validated matching evidence, never infer it from
  a terminal outcome or timer. Safe local generation transfer follows only after
  that release; provider/shared-resource fences remain separate.

### 2026-10-13 - Cooperative retirement and generation transfer prepared
- Added immutable exact intent/scope/generation/digest/resource/node-installation
  release evidence. A trusted SQLite-only retirement callback, permanent
  tombstone and node-owner removal commit together. The Gateway consumes
  authenticated-by-caller matching evidence with claim removal and audit in one
  transaction; outcome receipts and elapsed time still never release claims.
- Exact historical admission/retirement repeats preserve the original operation
  and proof without resurrecting ownership. Old effects and old proofs cannot
  affect successors; new allocations require a strictly higher resource-specific
  generation across identities. Connector epochs are not allocation generations.
- Known node schemas 1/2 upgrade to 3 and Gateway schemas 1-3 to 4, preserving
  held claims, accepted work, receipts and cursors without inventing retirement.
  Unsupported writers and reserved-name/core-column collisions fail closed.
- Unit and simple installed-package E2E: 284 passed, including real node/Gateway
  precommit crashes, postcommit lost replies, four concurrent retirement clients,
  competing successors and stale installed-process effects after reassignment.
  Lint and install-contract checks passed. Fresh local DB/legacy-upgrade paths
  exercised; full machine clean-room omitted because no installer/bootstrap
  changed. Live external-venue omitted because no adopted integrated runtime
  exists; no enrollment, deployment or credential authority was changed.
- Documentation-impact review updated the product README and operation/node/
  ledger contracts. This realizes the reviewed cooperative effect-owner intent,
  not external/provider fencing or a complete Phase 2. After review/merge,
  select the authenticated vertical-slice integration boundary with existing
  routing owners; full baseline/lifecycle/runtime/migration/adoption gates remain
  open and no parent Plan/Validation checkbox is closed by this primitive.

### 2026-10-13 - Release review follow-through
- #6418 review identified possible callback mutation of earlier permanent
  tombstones and unindexed history scans in both admission paths. Protected
  journal DML/schema/index/trigger changes are now refused by the cooperative
  authorizer, including generic effects; effect tables remain writable.
- Both journals now store released generations with a verified descending
  resource/generation index. Admission performs one maximum lookup rather than
  scanning/decoding proofs. Missing/foreign index definitions fail explicitly.
  New regressions attempt prior-tombstone/core-state mutation through both
  callbacks and bound the actual lookup under 5,000 retained releases.
- Installed contained suite: 333 passed; lint passed. These are fixes to the
  current unmerged release slice, not a new runtime/adoption milestone.

### 2026-10-13 - Cooperative release and generation transfer landed
- #6418 merged at `2026-10-13T02:24:11Z`, commit
  `a27d57dc2ddc83fc1a8e92a0a026e5f64b5cb7b2`. Actual current-head Copilot APPROVED
  with zero findings after all three initial findings were fixed; every review
  thread resolved and all 24 checks succeeded or legitimately skipped before
  merge. Required CI was queued for an extended period; no gate was bypassed.
  Installed contained suite: 333 passed.
- Managed sync encountered the already-published squash-history conflict and
  aborted safely. Verified a clean worktree, only the two published commits,
  and exact equality of every owned changed file with upstream; retained both
  managed recovery and a backup branch before moving onto the verified merge.
  No unpublished work or peer rows were dropped.
- Re-read every parent Plan/Validation item. No complete item is closed by
  local SQLite release primitives: live authentication, external fencing,
  full vertical operation, lifecycle, migration and adoption remain unresolved.
- Next stretch captures the actual launch/update/recovery baseline and precise
  first vertical-slice acceptance criteria under Phase 0, then advances the
  independently owned release/product lifecycle portion of #6240. Before
  resident Gateway/node integration, agree the unpublished JWT/JWKS and
  connector-proof attribution interface with #5789; do not add a competing
  authentication implementation. No live rollout is authorized.

### 2026-10-13 - Actual baseline and artifact preflight prerequisite
- Captured installed, isolated native Windows client launch/help and explicit
  setup/update/recover refusals in the [baseline/gate record](baseline-and-vertical-slice.md).
  Only one observation per command; no product service exists to measure
  successful activation/recovery, and no performance target is invented.
- Recorded source-level V1 version-slot/cutover/recovery reuse seams and precise
  full local objective-to-settlement/lifecycle gates. No live V1 update or
  replacement was performed; generic ZDD routing is not writer fencing.
- Reconciled the new #6424 client packaging/diagnostics/owned lifecycle owner.
  The Contract implementer transitions back to Phase 1 for this bounded
  artifact preflight and retained product lifecycle gate.
  The distinct #6240 prerequisite reads bounded actual role-selected artifact
  streams using the existing release schema and explicit source paths. It
  preserves the thin client dependency boundary and does not touch the client's
  existing parser, package assembly, installer or routing/auth files.
- Integrity output explicitly denies authenticity, activation permission and
  readiness. Signed trust, owned immutable staging, actual Gateway/node
  assembly, first-touch lifecycle and every later parent gate remain open.
  No live deployment, enrollment, credential or capacity authority was changed.
- Unit and simple installed E2E exercised role isolation, real artifact bytes,
  the actual 64 MiB boundary, bounded reads, native links, observed file races,
  tamper refusal and path-free diagnostics. Fresh fixture roots and installed
  execution outside the checkout cover first-touch input behavior; full
  machine clean-room is skipped because no installer/bootstrap changes.
  No live external lane exists for this unassembled product and none was
  enrolled. Documentation impact covers the product README, artifact contract
  and canonical baseline/vertical gates. The full effort remains Active.
- Native Windows CI exposed false change refusals from comparing path and
  handle timestamps. Identity/size correlation remains across the open;
  content-change stamps now compare the same handle before/after streaming.
  The regression models timestamp disagreement while real mutation/tamper
  checks remain required. No retry or CI bypass hides the failure.

### 2026-10-13 - Artifact preflight landed; release trust decision captured
- #6437 merged at `2026-10-13T04:20:54Z`, commit
  `5b6d18f14727e9756b364f73f90c769633a0043b`, after actual current-head Copilot
  APPROVED with zero findings, a resolved title thread, successful Windows/Linux
  native lanes and all required CI. A redundant review-request automation job
  was still queued; it was not an outstanding test or required merge check.
  No CI gate was bypassed. Final local installed contained suite: 365 passed,
  one native FIFO platform skip.
- Re-read the full parent Plan and Validation Plan. Phase 0's explicit offline
  baseline/gate record is resolved; no Phase 1-4 or full validation item is
  closed by unsigned integrity preflight. Retain the worktree and full effort.
- Captured the operator's literal `both` release-trust selection in Request
  and the [next authentication plan](release-authentication.md). The plan
  recommends independently selected pinned-key or GitHub-attestation policy,
  with no automatic fallback, no inferred root and real cryptographic/offline
  evidence before integration. These elaborations remain agent-recommended.
- Next is review of that plan, then the non-overlapping #6240 verifier
  implementation with package ownership agreed before shared metadata changes.
  #6424 owns the client lifecycle and #5789 principal/connector authentication.
  No signing key, live trust root, workflow/token or deployment was changed.

### 2026-10-13 - Release authentication plan reviewed; verifier seam coordinated
- #6451 merged at `2026-10-13T05:33:50Z`, commit
  `e0ba68ce7982c97a1a12a7eb49616c70ad6b6e91`, after required CI passed.
  The current-head Copilot COMMENTED review had zero findings and explicitly
  reported a Lite fallback because the full agentic review did not start
  before its timeout. This meets the documented owner-authored clean-Comment
  gate; it is not evidence of a full agentic review. No CI gate was bypassed.
- Re-read every parent Plan/Validation item. Reviewed intent does not close
  release authentication, lifecycle activation or any Phase 1-4/full validation
  item; the effort and worktree remain active.
- #6424's lifecycle owner agreed the optional verification capability boundary:
  default client dependencies stay empty, verifier imports stay lazy, and
  unavailable selected verification refuses rather than downgrading to
  integrity-only preflight. Parser, build resources, doctor and installer
  remain with that owner. Exact optional-extra/API coordination precedes any
  shared metadata change.
- Next is the bounded #6240 policy/proof and pinned-key cryptographic slice;
  real GitHub attestation evidence and lifecycle integration remain distinct
  gates. No trust roots, signing workflow or live deployment were changed.

### 2026-10-13 - Bounded pinned-key verification prepared
- Added a separate, lazy-import verification API and explicit trusted
  policy/signed-byte contract. Real Ed25519 verification authenticates the
  exact canonical manifest digest, signed authority/sequence/lifetime and
  configured key role/validity/revocation policy. No missing or selected
  GitHub method falls back to another method. GitHub verification itself is
  still unimplemented and explicitly refuses.
- The positive fixture was generated independently by Node's signer; no
  private fixture key was retained. Tampered bytes/signature, wrong key,
  revoked/disallowed/expired policy, duplicate/noncanonical encoding,
  scope mismatch and configured rollback-floor replay all refuse.
- Native Windows installed contained suite: 410 passed, one native FIFO
  platform skip. Real verification ran outside the checkout with Python
  socket entry points denied; an isolated `-I -S` child loaded the installed
  package without site packages and proved actual absent-crypto refusal.
  This is not OS-level network isolation, clean-machine installation or
  native Linux evidence. Full machine clean-room is deferred because no
  installer/bootstrap changes; no live signing/deployment lane is authorized.
- The first run explicitly exposed missing crypto support in the managed
  test environment and an oversized parametrized test name on Windows.
  Installed the chosen crypto dependency only into that test environment
  and used bounded test IDs; no failures were skipped. Shared production/dev
  metadata remains untouched pending the exact #6424 extra/API confirmation.
  Fresh declared-dependency CI validation and publication await that agreement.
- Extended the independently generated signing fixture to bind real component
  bytes, exercised all three role closures and changed-file refusal, and ran
  the installed isolated client through authentication plus actual artifact
  preflight against the same manifest. Focused verifier suite: 48 passed.
  This proves the pinned-method chain, not GitHub verification or immutable
  staging/activation; the full both-method gate remains open.
- Documentation impact covers the product README, new authentication contract
  and this canonical journal/index. No parent Phase 1-4/Validation item is
  closed by a prepared verifier, and no live trust or activation changed.

### 2026-10-13 - Exact optional dependency seam approved
- #6432's client resource/diagnostic prerequisite merged at
  `2026-10-13T06:26:04Z`, commit `ca7b3e5630673bf2dee2c7519c950575ad91b825`;
  its owner explicitly retained the optional verification seam and the
  independently owned managed lifecycle. The operator then explicitly approved
  the proposed `release-verification = ["cryptography>=46,<47"]`, matching
  crypto development dependency and `authenticate_release(...)` API.
- Integrated that prerequisite, preserving its build dependencies and resource/
  diagnostic implementation. Added only the agreed optional/development metadata
  and directly dependent wheel-metadata assertion. Default dependencies remain
  empty; parser, build helper, doctor and installer logic were not changed.
- Next gate is installed validation using declared dependencies, actual review
  and required CI. No trust enrollment, signing workflow or rollout is approved
  by this package-metadata decision.
- Fresh managed native Windows environment, provisioned solely from the
  declared development dependencies: 450 passed, one native FIFO platform skip,
  in 44.62 seconds. The real built wheel's metadata proves crypto is restricted
  to `dev` or the explicit `release-verification` extra; no unconditional
  runtime requirement was introduced. Lint, whitespace and the 13-plugin
  install-contract gate passed. Actual PR review/native CI remain next.

### 2026-10-13 - Pinned-key review follow-through
- #6475's first current-head Copilot review reported a Lite fallback and three
  findings: incomplete/native crypto import failures, brittle wheel requirement
  formatting and an unclosed test-input handle. The two Medium findings block
  merge; neither a timeout nor existing green checks satisfies that review gate.
- Map explicit `ImportError` (including missing dependencies) to the selected
  capability refusal and cover both missing/native import failure without
  leaking details. Parse actual wheel requirements with the packaging parser
  supplied by pytest, compare normalized names/specifier sets and evaluate
  optional-extra markers. Read subprocess inputs with a bounded-lifetime helper.
- A later base change also made the PR conflict; reconcile the published slice
  with the newer base and rerun its contracts before another review. No review
  or CI gate is bypassed, and the full effort remains active.
- Operator asked to avoid special GitHub authentication. No new fixture/signing
  workflow or permission was authorized. Existing read-only verification may
  consume an already-authenticated manifest bundle, but an unrelated wheel
  cannot satisfy the canonical manifest chain. The GitHub-positive gate remains
  deferred; no claim that both methods are implemented follows from pinned keys.
- Preserved #6443's independently assigned Gateway/index ownership while
  resolving the effort-index conflict; its participant and plan amendments
  remain intact. Reconciled the newly merged #6435 artifact-admission vision:
  this consumer adds no publisher or parallel provenance/identity and does not
  claim production signing support from test signatures.
- Rebased installed contained suite: 452 passed, one native FIFO platform skip,
  in 40.83 seconds. Focused import/metadata review regressions also passed
  (61 selected tests). Publication and fresh current-head review/required CI
  remain the next gate; no parent completion item was closed.

### 2026-10-13 - Pinned-key verification landed; remaining authority gates retained
- #6475 merged at `2026-10-13T10:02:24Z`, commit
  `dcd46884f223892ec36d8e43170a5c9e11793545`, after actual current-head
  Copilot APPROVED review with zero findings (Balanced effort), all three
  threads resolved and every required CI check passed. The Windows/Linux
  installed fleet test steps actually ran and succeeded; aggregate status alone
  was not used as evidence of those consumer tests. No CI gate was bypassed.
- An author thread-reply COMMENTED review was not mistaken for Copilot approval;
  the older dismissed approval on the prior head also did not satisfy the gate.
  The reviewed import/metadata/input-handle fixes and final 452-pass native
  suite are retained. Verified #6236 and #6240 remain open after merge.
- Re-read the full parent Plan/Validation, including #6443's assigned Gateway/
  index slice. No complete Phase 1-4/full validation item is closed by the
  verifier. Product assembly, current publisher proof, owned immutable staging,
  lifecycle activation, authenticated runtime, V1 migration and adoption remain
  open. GitHub-positive proof stays deferred, not silently replaced by fixtures.
- Prepared a bounded, source-only V1 authority inventory through three
  read-only evidence tracks: agency records/Git leases, dispatch task/audit/
  wake/reservation storage, and bridge sessions/delivery/acquisition/host custody.
  Independently exercised their existing contained contracts: worktrees
  69 passed, dispatch 246 passed, bridge 67 passed with one platform skip.
  This proves selected local contracts, not live-host inventory, general absence
  of migration APIs, external fencing or safe import/authority transfer.
- Next Phase 1 stretch coordinates the shared verified-staging/admission seam
  with #6424 and the release/assembly owners before implementation; it must
  reuse approved release identities and existing lifecycle primitives, not
  introduce another publisher or installer. #6443 owns resident Gateway/index
  integration. No deployment, trust enrollment or retirement was performed.

### 2026-10-13 - Shared staging owner confirmed; migration evidence bounded
- #6424's owner confirmed the common role-selected authenticated staging seam:
  retain the manifest/scope/policy binding and immutable selected bytes,
  re-authenticate current policy/freshness and re-hash before construction or
  activation. Sequence and slot ownership remain lifecycle checks, not grants
  from a serialized verification report. Gateway/node assembly can consume the
  reviewed common contract later; no competing staging/installer was started.
- Coordinated this distinct source-only Phase 3 proposal under #6236.
  The [V1 migration record](v1-migration.md) preserves the bounded three-domain
  inventory and selected contract evidence, distinguishes source generations/
  delivery ACKs from effect fences/settlement, and names owner-approved
  snapshot, freeze, responsibility-conservation, adoption/drain and retirement
  gates. Existing #6076/#6419 acquisition/layout work retains its ownership.
- No live state was imported, writer frozen, host adopted or registration
  retired. All Phase 1-4/full validation items remain open. This documentation
  slice adds no runtime behavior; unit/E2E/clean-room/live implementation tiers
  are not claimed. Next is proposal review and current-owner interface agreement
  before migration adapters; role lifecycle and Gateway/index work continue
  with their assigned owners.

### 2026-10-13 - Migration proposal reviewed and merged
- #6513 merged at `2026-10-13T11:03:11Z`, commit
  `c8fe1b62ec504ab6ec5247b26907b717f8379024`, after actual current-head
  Copilot APPROVED review with zero findings (Balanced effort). All checks
  completed successfully or legitimately skipped, including the aggregate
  required PR gate and workflow lockdown guard. No CI gate was bypassed.
- Re-read every parent Plan/Validation item and pulled forward with managed
  sync. The bounded source inventory and proposed migration sequence are now
  reviewed, not implemented; no full Phase 1-4/Validation item is complete.
- Relayed the shared staging contract to #6443. #6424 retains its implementation;
  #5789's unpublished auth/connector reuse interface still requires agreement.
  Migration adapters likewise require current-owner export/freeze/custody
  agreement. No competing runtime, live import, deployment or retirement began.

### 2026-10-14 - Separate-process two-node journal recovery
- Added a bounded installed-package integration test under #6241: a real node
  process holds its original operation at UNKNOWN while a second node commits
  one cooperative SQLite effect. A separate Gateway receiver process exits
  either during the second stream's audit transaction or after both commits.
  Replacement receivers resume the independent durable cursors, retain the
  original operations and reconcile explicit terminal evidence without duplicate
  effects or audit. Imports are isolated and verified under the test interpreter.
- Native contained full portfolio: 454 passed and one existing native FIFO skip,
  42.08 seconds within the unchanged 120-second aggregate deadline. Both new
  crash variants passed. An initial collection failure exposed a stale managed
  test-package installation; the canonical runner's explicit preparation
  refreshed that test environment before the passing runs. Shared installed
  runtimes were not changed.
- Unit and simple local-process integration tiers were exercised. This changes
  tests/documentation only, not first-touch installation or live deployment;
  clean-room provisioning and a live external venue were not exercised for this
  slice. Synthetic resolution evidence and file-based test signaling are not
  authentication or a real transport. Full objective/workspace/agent execution,
  authenticated Gateway/two-node operation, lifecycle, migration and adoption
  gates remain open; no Plan or Validation checkbox is closed.
- Common authenticated staging remains owned by #6424/#6569. Its current
  consumer contract returns component identities and bounded immutable
  `read_artifact(component, current_policy)` byte snapshots, not reusable public
  staging paths. Role consumers must retain their own admitted construction,
  custody, health, sequence and activation checks. No competing installer,
  publisher, Gateway, credential or authentication implementation was started.
- #6609 actually merged at `2026-10-14T01:05:06Z`, commit
  `1b40e6525127f50a73ddcbca2eebdffda946a066`, after exact-head Copilot
  APPROVED review with zero findings (Balanced effort) and all required CI.
  No CI gate was bypassed. Re-read the complete parent Plan/Validation after
  merge and synchronized the retained effort worktree; full gates stay open.
- Next stretch consumes the common staging/lifecycle and authenticated runtime
  interfaces from their retained owners rather than creating a second
  implementation. The index owner's #6605 separately proposes authorized-store
  migration and serving-eligibility gates; it is not merged runtime evidence.
  Released-runtime follow-through remains blocked by the existing #6535/#4876
  promotion owner. Neither a dev merge nor successful artifact attestation
  proves publication or an installed runtime.

### 2026-10-14 - Common staging landed; lifecycle consumer seam retained
- Confirmed #6569 actually merged at `2026-10-14T03:30:07Z`, commit
  `fb06b7ae8b516cff4c432019603d598afc4e93cf`, and synchronized the retained
  effort worktree. Read the delivered
  [staging contract](../../../fleet-manager/docs/artifact-staging.md) and the
  complete parent Plan/Validation before selecting the next stretch.
- Client, Gateway and node consumers can now share the context-lifetime
  authenticated immutable-byte boundary. They must not reopen a saved stage
  path, treat a serialized report as a grant, or build/activate inside the
  temporary spool. Current policy, freshness, retention and selected bytes
  remain independently verified at use.
- Requested the retained #6424 owner's common durable sequence/content-conflict
  and candidate construction/health/activation seam before Gateway/node
  assembly under #6240. This avoids a parallel installer while the client
  lifecycle remains open. Historical #6499 was closed as superseded work on a
  separate installation-mode pointer contract, not merged installer evidence.
- The release incident has a newly explicit trusted-main bootstrap reservation
  under #6535; its owner reports maintainer authorization and retains actual
  review, source gate and normal full promotion. This parent has not patched
  main, dispatched a release, waived artifact evidence or installed dev bytes.
  All full Phase 1-4/Validation gates remain open.

### 2026-10-14 - Explicit selected-release input contract
- Confirmed the retained client's #6624 admission plan merged at
  `2026-10-14T05:35:58Z`, commit `4060b69774489615b397b8e376937f49141783cd`.
  Read its actual home under `linux-role-fleet` and the full parent Plan before
  taking only the missing product-release input shape under #6240.
  Coordinated exclusive source scope with #6424; no admission journal, installer
  or competing release selector was added.
- Added [immutable selected-release intent](../../../fleet-manager/docs/release-selection.md):
  attributable logical owner/decision/revision, authority/installation/role,
  exact manifest digest/sequence and sorted complete component closure.
  Current intent must be independently trusted and byte-identical; a signed
  or higher-sequence candidate is not automatically selected. Parsing and
  correlation are not owner authentication, currentness proof, persistence,
  compatibility, execution or activation authority.
- Unit/contract selection: 82 passed, including malformed/canonical/immutable
  inputs, stale current decisions, wrong scope/sequence and partial/widened
  closures. Simple installed isolated consumers exercised all three roles,
  verified installed imports, denied Python socket entry points and created
  no working-directory state. This is not OS-level network isolation.
- Full native contained portfolio: 582 passed and six platform skips in
  53.43 seconds under the unchanged 120-second deadline. Five skips are POSIX
  quarantine contracts; one is native FIFO unavailability. The first full run
  exposed the existing staging wheel test letting `uv` choose ambient Python
  3.9 while its consumer used managed Python 3.11. Bound that one real offline
  install to `sys.executable`, matching existing distribution-test prior art;
  the actual signature/byte/cleanup assertions remain unchanged.
- Clean-room provisioning and live external deployment were not exercised:
  this is an execution-free data API, not install/bootstrap or resident transport.
  The full portfolio retains real fresh-wheel and installed-stage proofs;
  hosted native platform gates remain separate. #6424 owns durable admission
  and the construction/health/removal follow-ons; #6443/#5789 retain authenticated
  runtime integration. All full Phase 1-4/Validation items remain open.
- #6637 actually merged at `2026-10-14T06:27:20Z`, commit
  `bf1ec290b0c175e047f281417b529d103252e0a3`. The exact final head received
  current Copilot APPROVED review with zero findings (Balanced effort), and all
  required CI passed. An initial ten-minute review timeout led to an explicit
  re-request; the earlier dismissed review was not used as the completion gate.
  No CI gate was bypassed. Re-read the complete parent Plan/Validation and
  synchronized before selecting the next stretch.
- Delivered the actual common input API to #6424. Its installation-owned
  admission and execution follow-ons remain with that owner; authenticated
  resident Gateway/index and credential transport remain under #6443/#5789.
  Current interface work does not establish owner authentication or complete
  product assembly. Source merge, signed publication and authorized runtime
  rollout remain distinct outcomes.

### 2026-10-14 - Central backing-store clarification
- Captured the operator's generic Worktree Manager push/subscription direction
  in Request; adopter-specific deployment identities and live reconciliation
  remain outside this public repository. The selected deployment uses one
  Gateway authority; credential coordinators are not automatically duplicates.
- Reconciled the design with the existing #5862 observation/recovery charter,
  #3761 origin write-through daemon, #352 Manager/mux separation and the
  machine-fleet vision. Reserved only the plan amendment on #5862 rather than
  starting a competing observer, Gateway or agency writer.
- Extended standing intent with a shared central current-state backing store
  and push-backed coherent reads. The linked proposal recommends one Gateway
  module, attributable persistent state, fenced origin updates and one query/
  snapshot/subscription boundary, while canonical domain owners retain writes,
  commands, receipts, credentials and execution.
- This slice changes vision/effort documents only. Unit, process E2E,
  clean-room and live implementation proofs are specified, not claimed run or
  delivered. No runtime, deployment, data migration, capacity or lease
  mutation follows from the proposal. Full Phase 1-4/Validation gates stay open.
- #6670 actually merged at `2026-10-14T08:13:38Z`, commit
  `a5c71dfc912a6249a6fe61276619172a761ec886`. The exact head
  `78e8a52007ecc3075b9ef7c04f2aa5f40818406b` received current Copilot
  APPROVED review with zero findings (Balanced effort); every check completed
  successfully or legitimately skipped before the normal merge. An initial
  review timeout was followed by an explicit re-request. No CI gate was bypassed.
- Re-read the full parent Plan/Validation after merge and synchronized the
  continuing worktree. Only the Phase 2b design-review item closes; the shared
  interface, runtime, lifecycle, migration and adoption gates remain open.
- Next Phase 2b stretch consumes the existing origin-current-projection and
  observation contracts, agrees the bounded storage/push/query/subscription
  seams with their retained owners, then implements the first reviewed store
  slice under #5862. The approved plan is not a deployed central backing store
  or permission to replace an adopter's current serving/credential owner.

### 2026-10-14 - Isolated current-observation storage boundary approved
- Traced the committed origin projection and local held feed directly.
  The origin's bounded current rows, per-project revision, resident generation,
  demand enrollment and availability notifications are reusable seams;
  the held feed is not durable central replay or a complete fleet subscription.
- Retained origin/auth interface requests had no reply. Rather than infer
  consent or take over their files, asked the operator about the first isolated
  storage boundary and captured `approve-isolated-store` verbatim in Request.
  Reserved that implementation under #5862. Source/auth/Manager and live
  deployment ownership remain unchanged.
- The Contract implementer transitions from Phase 1 to the isolated Phase 2b
  storage prerequisite. This is not assignment of origin push, principal/
  connector authentication, Manager UX or installer implementation.
- Added explicit local SQLite current slots and bounded replay with distinct
  admitted binding, producer, export sequence, source revision and central cursor.
  Atomic update/slot/head persistence, content-bound retries and bounded point/
  page reads implement only the approved storage prerequisite. Pages do not
  assert complete roster coverage; typed internal cursors are not access tokens.
- Installed contained storage selection passes 22 cases, including process
  crash/retry, independent producer/query processes, exact replay/page/byte
  bounds and measured point-read access independent of unrelated current rows.
  The full native contained suite passes 604 with six platform skips in
  65.32 seconds, within the existing 120-second aggregate deadline; touched
  lint and the 13-plugin install-contract guard pass. Actual PR review and
  hosted native platform gates remain pending.
  Clean-room install/live deployment are not exercised by this unintegrated
  storage-only slice; no service, transport or installer is changed.
- Documentation impact covers the product README, storage contract and this
  canonical plan/index. This realizes part of reviewed shared-state intent
  without introducing a daemon or duplicating an authority. No full Phase 1-4
  or Validation Plan item closes, and the effort remains Active.
- #6685 actually merged at `2026-10-14T09:17:24Z`, commit
  `10855559d664d936f8dfed9f95d97c46a967f356`. The exact head
  `1042de5330aee25e4b4aa83a79d1be039d367fed` received actual Copilot APPROVED
  review `5515664127` with zero findings (Balanced effort), no inline comments,
  and successful or legitimately skipped checks before the normal merge.
  The first ten-minute review wait timed out; an explicit re-request produced
  the passing review. No CI or review gate was bypassed.
- Re-read the complete parent Plan/Validation and synchronized the continuing
  worktree onto the actual merge. The storage primitive is merged to `dev`,
  not a released/installed central service or completed Phase 2b.
- Next is explicit agreement on the producer export, admitted principal/
  connector binding and query/subscription adapter with #3761/#5789/#6443/#352.
  The approved storage exception does not authorize those edits. Preserve
  current deployments, credential controllers and source responsibilities
  while those integration boundaries remain unresolved.

### 2026-10-14 - Runtime authentication interface proposed
- The operator directed proposing the missing auth seam from the vision and
  actual consumer needs, rather than remaining blocked waiting for one.
  Captured that Request verbatim. This is proposal authority, not trust/key
  enrollment, runtime takeover or live deployment permission.
- Reconciled the current machine-fleet vision, routing foundation, bounded
  operation and observation contracts, and the index/credential slice.
  Existing shape/correlation checks are not authentication. Bounded source/
  tracker inspection found no named current auth interface or conflicting open
  auth PR; it does not establish absence of unpublished prototypes.
- Reserved only this documentation proposal under existing #5789/#6443.
  Recommended a shared verified identity -> enrollment -> policy permit boundary,
  explicit HTTP JWT/DPoP and local OS-peer profiles, current revocation/leases,
  independently checked delegation and provider-owned credential delivery.
- Made consumer seams concrete without replacing their owners: actor/account
  operation binding, admitted origin/producer fencing, scoped pre-query and
  subscription decisions, protected per-view cursors, and credential request/
  recipient correlation. Global store positions and invalidations are not a
  scoped wire interface; that adapter still needs its own proof.
- All profile limits and implementation details are agent-recommended and
  require reviewed DTO/library/source assignments before runtime work. Runtime
  identity does not create release-signing or special GitHub permissions.
  Documentation impact is this canonical effort/index and its linked consumer
  plans. Unit/process/clean-room/live implementation tiers are not run or claimed
  for this docs-only proposal; their proof obligations are specified.
  No parent implementation or Validation item closes.
- The operator clarified that provider/Windows session longevity is presence:
  default run as long as possible, without continuous user revalidation.
  Updated the proposal to distinguish silent token/proof/view renewal from
  actual provider challenges, removed the extra five-minute access lifetime,
  and made a scoped dead-man switch opt-in/off by default. Revocation and
  real validity/grant boundaries remain enforced; temporary outage, idle,
  lock/sleep and reconnect are not invented human-presence requirements.
  Captured the full clarification verbatim and added longevity/challenge/
  revocation/opt-in validation obligations.
- #6732 actually merged at `2026-10-14T19:32:26Z`, commit
  `f660fcb02adda309bfbfe59f3e39daf3945b633b`. Its final head
  `dc51c7b66d3a04044b2d61c6da9a5a3bfe1f16d4` received actual Copilot APPROVED
  review `5523090951`, zero findings (Balanced effort) and no inline comments.
  Natural CI `38618214730` completed successfully before merge; all 25 observed
  checks succeeded or legitimately skipped. Queued jobs were waited out without
  rerun, cancellation, runner repair or CI/review bypass.
- Re-read the full parent Plan/Validation after actual merge. Only the shared
  auth proposal review item closes; exact provider/record/source assignments,
  implementation, integration and every full validation/adoption gate stay open.
  The default long-lived provider-session presence and opt-in/off dead-man
  behavior are reviewed intent, not implemented or deployed authentication.
- Managed pull-forward safely aborted on the two-commit squash-history conflict.
  Verified a clean workspace, only published commits and exact owned-file equality
  with the confirmed merge; retained a backup and dropped only that published
  prefix onto the fetched base. No unpublished work or claim was discarded.
  Continuing work remains in the full effort's owned worktree, not finalized.
- Next is agreement on the proposed DTO/provider support and exclusive source
  scopes with #5789/#6443/#3761/#352, followed by the bounded verifier/admission
  contract slice. Proposal review does not enroll a principal, select live keys,
  change a provider session policy or authorize a Gateway deployment.

### 2026-10-14 - Isolated identity verifier implementation started
- The operator authorized driving implementation after actual proposal merge.
  Rechecked current #5789/#6443 reservations, open fleet PRs and #6424 shared
  metadata ownership, then reserved only new verifier/test/doc paths and the
  directly coupled optional dependency/isolation assertions. No reserved
  resident Gateway, enrollment, provider, index or lifecycle source was edited.
- Implemented explicit `jwt-pop-v1` ES256/P-256 access-token and DPoP
  verification using maintained `joserfc`, including public-key thumbprints,
  strict token/proof parsing, request/key/token binding and an atomic bounded
  nonce-epoch replay fence. It returns immutable non-secret request evidence,
  not a principal enrollment, grant or reusable authorization token.
- Default client dependencies remain empty. The optional capability is loaded
  only when explicitly requested. Real fresh installed-wheel diagnostics prove
  that the default client runs without JOSE; explicit verification without the
  capability refuses rather than downgrading.
- Unit and simple process E2E: focused identity/client-isolation selection
  passes 64; the full contained native portfolio passes 666 with six platform
  skips in 74.69 seconds under the unchanged 120-second deadline. Tests exercise
  real synthetic issuer/proof keys, invalid signatures/claims/bindings,
  concurrent replay, live-capacity refusal, expiry/rollback and a replacement
  process rejecting the old nonce epoch. Many-day valid token/proof renewal
  introduces no Gateway presence timer. Touched lint and install-contract
  validation pass.
- The first full run exposed the metadata test's prior two-extra whitelist;
  updated it for the explicit new extra while preserving empty default
  requirements and independently checking both crypto extras. A subsequent
  candidate refresh prevents a cached installed test package from masking
  source changes. No test deadline or assertion was waived.
- Clean-room/live external deployment tiers were not exercised: this is an
  unintegrated verification library with no installer, listener, trust
  enrollment or selected identity-provider adapter. Existing fresh-wheel
  install/removal and a standard-library-only capability refusal are exercised;
  hosted native-platform validation and actual PR review remain pending.
- This closes part of machine-fleet `authenticated-scoped-and-revocable`
  intent, using the reviewed common boundary without a daemon or per-UX auth
  engine. Policy/enrollment, provider challenge/revocation semantics, scoped
  query/delegation/credential delivery and encrypted role transport still need
  their own owner-agreed implementation and proofs. No full Plan/Validation
  item closes; the parent effort stays Active.
- First PR review found a real concurrency defect: request timestamps could be
  admitted out of completion order and produce a false clock-rollback refusal.
  Replaced per-request timestamps with a required trusted owner clock sampled
  under the lock before verification and at atomic admission. Trust/token/proof
  validity is rechecked at that final sample, so slow verification cannot
  admit expired evidence. Added real-crypto reverse-completion and delayed
  expiry regressions. Revised focused verification passes 66, and the full
  contained portfolio passes 670 with six skips in 87.26 seconds under the
  unchanged 120-second deadline; lint and install-contract gates pass.
  Re-review and current-head hosted CI remain pending; the earlier review is
  not a passing verdict.
- The corrected verifier head received genuine Copilot APPROVED review with
  zero open findings. Hosted validation then exposed a reproducible Windows
  120-second portfolio timeout at the existing 1024-event retention test.
  One unchanged-head reproduction confirmed it; no further blind rerun or
  deadline increase was used. The separate index-suite job's eight-minute
  cancellation recovered on its single targeted rerun.
- Narrowed the retention test's expensive setup, not its acceptance boundary:
  generate the valid history through the real API and real in-memory SQLite,
  back it up to a real temporary database, then exercise the exact last-valid/
  first-expired cursor, overflow, retry and reopen against ordinary durable
  disk transactions. The literal 1024 bound, production `synchronous=FULL`,
  actual crash/process tests, test selection and 120-second deadline remain.
  An attempted WAL-only fixture did not improve timing and was discarded.
- Revised focused crypto/retention/crash contracts pass 69 in 4.82 seconds;
  exact retention preparation/overflow takes 0.75 seconds instead of 10-12
  on the same local host. Full unchanged-coverage portfolio passes 670 with
  six skips in 72.23 seconds. This improvement still needs current-head
  hosted native validation and re-review; local timing is not that proof.
- The optimized candidate subsequently received exact-head Copilot APPROVED
  with zero open findings and passing hosted checks, but could not merge after
  a concurrent upstream retention-test change. Consulted rather than choosing
  between the overlapping approaches unilaterally; the operator selected
  `preserve-upstream`.
- Rebased onto current `dev` and dropped this slice's fixture optimization.
  The retention test now matches upstream exactly, including its existing
  small/full portfolio and valid-history setup. The optional JOSE extra,
  verifier, real-crypto regressions and client-isolation assertions remain.
  The previous fixture timing describes a superseded experiment, not the final
  source. Revalidation and exact-new-head review/CI are required before merge;
  an approval or green check on the earlier head is not carried forward.
- Rebased full validation passes 672 with six skips in 161.01 seconds under
  the existing full/manual budget. The exact preserved upstream required
  selection separately passes 664 with six skips and eight full-matrix
  deselections in 107.47 seconds under its unchanged 120-second deadline.
  Updated the product README's test command to match that canonical upstream
  split; no test, marker, workflow or budget is changed by this slice.
  Touched lint, install-contract validation and exact upstream retention-file
  equality pass.
