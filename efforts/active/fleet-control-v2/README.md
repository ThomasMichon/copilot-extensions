# Fleet Control V2

- **Slug:** `fleet-control-v2`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, serial per-slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Active; offline baseline captured, role artifact preflight in progress
- **Vision:** machine-fleet `unified-coordination-product`; agent-fabric and plugin-services explicit integrated-product profile
- **Umbrella issue:** #6236
- **Sub-issues:** #6240 release/lifecycle; #6241 command/receipt contracts. Existing dependencies #5789, #6030, #5861, #5862 retain ownership.

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
| Contract implementer | Release/role contract #6240; first operation/receipt shape slice #6241 | Managed implementation worktree; assigned sub-issues |
| Existing campaign owners | Routing, standalone client, role deployment and provider integration | Existing campaign issues and canonical efforts |
| Adopter | Configuration, capacity, enrollment and live activation approval | Explicit downstream adoption workflow |

## Coordination

- **Topology:** Reviewed architecture followed by serial, independently reviewed slices.
- **Host (owns PRs):** Plan steward for this proposal; each implementation slice names its PR owner.
- **Delegates:** None assigned to runtime implementation by this plan.
- **Slice ownership:** Never assign a slice already held by another campaign.
  Coordinate #5789/#6030 and the standalone client PR #6133 before touching their files.
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

The operator confirmed the slug `fleet-control-v2`. Personal artifact delivery
is handled outside this public repository. The architecture details, phase
breakdown and validation below are **agent-recommended** elaborations of the
approved direction. No runtime or live rollout authorization is inferred from
"prepare". The subsequent direction authorizes driving the recommended contract
implementation; live enrollment, deployment and destructive retirement still
require their own approval.

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
  assembly, signature verification and lifecycle activation remain pending.
  Role-selected actual artifact integrity preflight is a separate prerequisite;
  client packaging/diagnostics/managed installation remain owned by #6424.
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
  and authenticated runtime integration remain open.
- [ ] Prove objective -> workspace -> host -> prompt -> outcome -> settlement
  locally, then through one Gateway and two separate nodes.
- [ ] Persist scope/generation fences at cooperative effect owners; define
  UNKNOWN and safe blocked reassignment for unsupported/uncertain effects.
- [ ] Keep accepted agent/mux work, credentials and expensive workloads outside
  coordinator lifetime; all integrated roles share one lifecycle implementation.

### Phase 3 - Transfer authority without duplicate writers
- [ ] Inventory V1 authorities, state schemas, live hosts, receipts and obligations;
  coordinate imports with their current owners.
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
