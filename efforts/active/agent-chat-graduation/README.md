# Agent Chat Graduation

- **Slug:** `agent-chat-graduation`
- **Repo:** copilot-extensions
- **Branch model:** one proposal PR, then explicitly assigned implementation slices.
- **Created:** 2026-10-19
- **Status:** Active
- **Vision:** [agent-chat](../../../visions/plugins/agent-chat/README.md),
  [agent fabric](../../../visions/agent-fabric/README.md) and
  [plugin services](../../../visions/plugin-services/README.md).
- **Umbrella issue:** #7238

## Guiding Intent

Deliver a general-purpose conversation capability that agents can actually
install, use, subscribe to and maintain. Graduate a cohesive service, client,
plugin and lifecycle design rather than advertise an incomplete plugin or add
another coordination authority.

The [plugin/native contributor's input](https://github.com/ThomasMichon/copilot-extensions/issues/7238#issuecomment-6264916134)
also requires host/service-owned multiplexing rather than one persistent
notification helper per session. This preserves the parent service model,
not an exemption tailored to a particular implementation.

The vision states the standing interaction and resilience intent.
[Architecture](architecture.md) is the proposed component and lifecycle
contract; this effort owns sequencing, qualification and progress. None of
these documents asserts that the capability is already implemented or released.

## Participants

These are contribution roles, not new worker processes or authority grants.
Use #7238 to accept a slice and record its source boundary before editing it.

| Participant | Responsibility | Reached via |
|-------------|----------------|-------------|
| Proposal editor | Single vision/effort writer; service, client and lifecycle integration | #7238 and the proposal PR |
| Plugin contributor | Supported native attachment, pending/admission state and plugin delivery contract | #7238; input integrated by the proposal editor |
| Qualification contributor | Installed-consumer, native lifecycle and platform-boundary cases | #7238; input integrated by the proposal editor |
| Existing lifecycle owners | Review integration with fleet-manager, shared process utilities and service primitives | Their existing public issues, linked below |

## Coordination

- **Topology:** one design writer; independently claimed implementation PRs only
  after the proposal clears review and their dependencies are understood.
- **Host:** the proposal editor owns the proposal PR and integrates contributor
  input. Contributors do not edit the same proposal files concurrently.
- **Handoff:** use the existing issue/task/worktree contracts for accepted
  ownership transfer. A chat message or a published plan does not transfer it.
- Preserve existing fleet-manager and machine-fleet work under #6030, managed
  lifecycle work under #6424/#6240, and service-specific authorities. Consume
  their supported seams; do not replace them through this effort.
- Normal already-authorized operations proceed within their accepted scope.
  Changed scope, destructive migration and activation remain decisions of
  their real owners, not per-message consent or a second controller.
- No release-pipeline, shared deployment, private-provider or fleet activation
  change is included in the proposal PR.

## Context

The [fleet-manager owner page](../../../fleet-manager/README.md) distinguishes
available planning, artifact and opt-in resident boundaries from still-open
managed installation and deployment work. The design must not treat that future
management contract as already available.

The fabric already separates workspace agency, session hosting, peer control,
tasks and service lifecycle. Chat adds a conversation domain, not another copy
of those stores. A portable implementation should reuse coherent existing
components and proven semantics rather than regenerate them merely to match
a new package layout.

Generic prior-art lessons inform the proposal: recipient-relevant scoping,
bounded context batches, durable actionable follow-ups separate from ambient
history, and one existing execution owner per recipient. These mechanisms do
not supply exactly-once native admission, distributed work ownership or
permission to resend uncertain effects. No external framework integration is
required to use the lessons.

## Request

Public-safe summary of the requested direction: graduate agent communication
into a complete upstream capability, including standing vision, architecture,
the connection plugin, hosted service and normal lifecycle. Support channel
subscriptions, direct mentions and all-agent attention, and use lessons from
real cooperation to improve robustness.

This is an intent summary, not a verbatim transcript. The current contribution
is the reviewed proposal. It does not claim completed qualification or grant
permission to install, activate, migrate data or restart another owner.

## Plan

All implementation details below are **agent-recommended realization**, to be
reviewed against the standing vision rather than treated as additional user
requirements.

### Phase 1 - One reviewed proposal

- [x] Review the intent-level vision, reciprocal parent/index links and
  architecture proposal, preserving the full collaboration interaction.
- [x] Integrate the plugin and qualification contributors' bounded input in
  this one plan; resolve real contradictions without creating peer copies.
- [ ] Reconcile independent packaging and optional fleet integration with
  current lifecycle owners; name unavailable primitives as exact dependencies.
- [x] Land the proposal through normal review and required CI before
  implementation. Keep #7238 open across the partial slice.

### Phase 2 - Portable service and installed client

- [x] Claim a bounded implementation slice and extract/reuse the conversation
  core, independent cursor and stable-request semantics with synthetic tests.
- [ ] Package a dependency-light client and independently operable service;
  no source checkout, sibling venv or Copilot plugin is required to host it.
- [ ] Publish bounded capabilities and errors, endpoint discovery and explicit
  setup. Preserve normal no-gap output/flush/CAS and uncertain-effect recovery.
- [ ] Resolve trusted host-init session ID and CWD through the workspace owner:
  real-worktree participant continuity, session-segmented anchor participants,
  and existing local service/bridge-owned inbox/source assignment. Keep friendly
  labels separate and preserve existing membership/cursors without migration.
- [ ] Coordinate trusted materialized-consumer and CI qualification inventory
  support with the existing release owner under #6237 before promoting the
  expanded standalone roster; source review alone is not Main activation.
- [ ] Prove a real installed-client/service/independent-peer exchange before
  widening adoption; a source test or delivered setup message is insufficient.

### Phase 3 - Plugin and native subscriptions

- [ ] Claim the connection-plugin slice: guidance, setup, attributable commands
  and a normally discovered optional native attachment.
- [ ] Agree existing host/service-owned notification multiplexing with bounded
  callbacks and no resident worker growth per session/worktree; retain an exact
  host dependency if that supported seam is unavailable.
- [ ] Qualify source selection, effective enablement, host-owned discovery and
  foreground attachment without overwriting retained authoring state or
  restarting unrelated healthy activations.
- [ ] Establish pending-event durability, epoch/gap handling, targeting and
  uncertain-admission recovery before enabling model wake.
- [ ] Prove one useful channel/idle lane, then direct mentions and all-agent
  attention, busy/non-primary correlation and explicit mute/leave/head cleanup.
- [ ] Where the native host lacks a required callable primitive, retain the
  exact host-owned dependency. Do not substitute global reload, a private
  bootstrap or another session and call it scoped native proof.

### Phase 4 - Managed lifecycle and fleet integration

- [ ] Agree the service contribution and placement boundary with existing
  fleet-manager owners before editing their implementation.
- [ ] Reuse immutable version-slot assembly, release/component identity,
  endpoint rendezvous, supervision, health-gated cutover and rollback.
- [ ] Define and test storage compatibility, safe drain boundaries and
  recovery after interrupted activation without creating a second data owner.
- [ ] Prove caller-independent health, restart/persistence and preserving
  removal through normal independently installed artifacts.
- [ ] Keep provider-specific templates, credential acquisition and organization
  policy in their own adapters, not the generic conversation package.

### Phase 5 - Qualified release and explicit migration

- [ ] Resolve every Validation Plan item or transfer it to an exact named
  tracked objective; separate native platforms and unavailable venues honestly.
- [ ] Obtain the existing deployment owners' accepted migration/cutover scope,
  including rollback and retained membership obligations.
- [ ] Publish the real installed interface and capability states, then verify
  authorized running adoption after release, not merely a merge or image build.
- [ ] Retire a predecessor or private implementation only by explicit
  migration/removal authority after preserved state and consumers are proved.
- [ ] Reconcile all Plan/Validation items and #7238 before marking Done.

## Validation Plan

- [ ] **Store and client:** monotonic event identity, independent viewers,
  bounded pages, stable request replay/conflict, flush-before-cursor-CAS,
  crash-released reader locks and visible capacity refusal.
- [ ] **Installed boundary:** fresh and repeat installation, actual console/
  payload invocation, no source fallback, compatible previous selection and
  source-independent operation after the fixture checkout is unavailable.
- [ ] **Real consumer:** authenticated service plus two genuine existing
  participants exchange one stable-ID message and consume its exact body;
  scans and waits do not advance semantic reading.
- [ ] **Native intent:** actual channel, direct-mention and attention selection;
  idle wake with supported foreground metadata and attributable run correlation.
- [ ] **No-noise:** quiet, irrelevant, self-post and reconnect-only cases
  produce zero model turns, not recurring timeout/rearm activity.
- [ ] **Resident cost:** increasing recipients/sessions/worktrees does not add
  persistent polling/helper workers; host/service multiplexing and callback
  bounds are measured separately from the session host's own process model.
- [ ] **Uncertainty and context:** durable pending hints before scan progress,
  nonce before admission, no fresh send after an unknown result, non-primary
  run ambiguity held, bounded recipient batches and explicit retention/epoch
  gaps. Crash or lose admission after preparing a hint: semantic progress stays
  unchanged and the exact pending body remains available to the recipient's
  normal read, without a cursor reset or a fresh native-send replay.
- [ ] **Membership and succession:** real obligation-before-join,
  confirmed-leave-before-settlement, mute/unsubscribe, crash/restart, changed
  head and stale-foreground refusal with unresolved generations retained and
  already-admitted late hints disclosed rather than silently retracted.
- [ ] **Initialization and assignment:** two sessions in one real worktree keep
  its participant identity; two anchor sessions at the same CWD remain separate;
  valid host initialization needs no duplicate environment session ID. The thin
  extension consumes the existing owner's inbox/source assignment while backend
  authentication, assignment isolation and native custody remain intact.
- [ ] **Host adoption:** normally selected published plugin reaches the same
  genuine foreground while unrelated extension identities/lifecycles and
  retained work remain unchanged; unsupported hosts report an exact gap.
- [ ] **Service lifecycle:** first/repeat install, different-code update,
  candidate refusal, rollback, persistence, post-caller health, resource bounds
  and preserving removal; no credential or unrelated-route regression.
- [ ] **Concurrency:** posts and health remain responsive under several held
  subscriptions; coupled maintenance uses one current owner/correlation and
  does not turn releases into acknowledgement loops.
- [ ] **Platforms:** direct Windows and native Linux cleanup, plus a separate
  real WSL-to-Windows descendant test for timeout/interruption/caller death.
  POSIX process groups are not claimed as Windows tree ownership.
- [ ] **Portfolio:** keep fast portable contracts in required change-scoped CI;
  isolate real process/native/fresh-environment matrices in their existing
  appropriate lanes. Record why a genuinely unavailable venue was not exercised.
- [ ] **Publication:** public-only references and synthetic examples,
  documentation-impact review, required CI/review and unchanged open parent
  tracker until its actual completion gate is met.

## Proposal

Review the vision and [architecture](architecture.md) as one coherent design,
then assign implementation in small compatible slices. Existing manual client
use remains valuable while an unsupported native host seam is resolved; full
graduation still requires the declared native and lifecycle guarantees.

## Journal

### 2026-10-19 - Conversation-owned safe drain boundary

- Added the core's callable drain/undrain seam for an existing hosting owner,
  independently of the held client transport revision. It closes admission
  after committed/rolled-back transactions and wakes/fences held wait/scan
  generations, including immediate undrain. Stable requests, memberships,
  semantic cursors and the durable epoch are preserved.
- The final local native Windows core portfolio passed 78 contracts in 5.71 seconds
  within the unchanged 120-second contained budget. Seventeen focused
  lifecycle/artifact cases passed separately, including an actual wheel after
  fixture-source removal, a held transaction barrier, waiter cleanup and
  restart preservation. Touched Python lint and whitespace checks passed.
- Review identified a saturated-pool precedence defect. A real one-slot held
  wait/scan reproduced all four drain/rollback failures before the correction.
  Serving-state checks, capacity admission and waiter bookkeeping now share
  the store lock, so frozen admission remains explicit even while full.
- This is a domain storage boundary, not a listener, transport flush receipt,
  overlapping-writer/schema-migration contract, authenticated management route
  or lifecycle installer. Current health remains local open-store liveness.
  Passive preparation must not open the active store to fabricate readiness.
- Reconciles to the reviewed service-lifecycle architecture, agent-chat
  resilience intent and the graceful-daemon-cutover consumer contract.
  Hosting, existing lifecycle-owner integration, native Linux qualification
  and authorized live adoption remain open; no deployment was performed.

### 2026-10-19 - Trusted initialization and participant assignment

- Integrated the [operator clarification](https://github.com/ThomasMichon/copilot-extensions/issues/7238#issuecomment-6278024161)
  into this one vision and architecture: trust supported host-init session ID
  and CWD, resolve real-worktree versus anchor identity through the workspace
  owner, and consume inbox/source assignment from the existing local
  service/bridge rather than reconstruct it in the extension.
- This supersedes redundant independent environment/foreground identity
  comparisons, not backend authentication, ownership/succession, explicit
  membership or no-replay recovery. The service/client writer owns the shared
  installed resolution seam; the connection contributor owns the thin adapter.
- The source contract is not an installed API, completed assignment service,
  migration, runtime restart or native-listening result.

### 2026-10-19 - Design intake

- Created the deduplicated public coordination issue #7238 and this single
  proposal. Integrated generic interaction, qualification and process-boundary
  input; no implementation, service activation or release change is asserted.

### 2026-10-19 - Reviewed proposal and first implementation slice

- Proposal #7239 merged as `9f935357283ed246590a1144936ce9a3baea5b31`
  after current-head approval with no open findings and passing required CI.
  The parent #7238 remains open.
- Integrated the native contributor's recovery correction: preparation sends
  ID/delta hints only; body reads happen in the admitted recipient turn, with
  explicit crash/lost-admission qualification. No new retrieval API, body
  store or semantic acceptance authority is introduced.
- The next bounded slice is the portable conversation core/service package
  and synthetic tests, using the repository's standalone service layout.
  Shared test/release integration will be named before editing its owned
  registry. This is not a deployed service, plugin activation or migration.
- Fleet-specific packaging/placement, native host adoption and cross-OS
  process ownership remain their exact existing owner dependencies. Independent
  conversation-core work does not change those owners or imply their readiness.
- The first [portable core distribution](../../../services/chat/README.md)
  contains no listener or resident launcher. Its actual installed artifact
  executes the conversation contract after the fixture source is removed.
  Remaining client, hosting, native and lifecycle work is not completed by
  this core implementation.

### 2026-10-19 - Core publication review

- Implementation review exposed coupled qualification-inventory omissions.
  Its source fixes require both explicit, statically routed Chat CI jobs and add
  executable materialized-consumer recipes while preserving the older
  standalone roster. Unknown or narrowed coverage still refuses.
- Review also identified committed-post recovery after leave. Persisted
  request reconciliation now precedes active-membership admission; changed
  content conflicts and new posts still require membership. The regression
  exercises leave and restart without silently rejoining.
- The existing trusted-runner guard rejected a dynamic OS matrix. Separate
  fixed hosted-platform jobs preserve the same public check names and bounded
  coverage without a runner-policy exception or matrix-specific validator.
- Repeated-clock testing exposed a membership fence bug in both held operations.
  Instance-local generations now invalidate only after a committed leave,
  preserving idempotent joins and rollback without a storage migration.
- The trusted tooling activation dependency is recorded with the existing
  #6237 release owner. That owner's Main/pause, publisher and qualification
  scope are unchanged; delivery and acceptance are not asserted here.
