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
[Architecture](architecture.md) is the component and lifecycle contract;
this effort owns sequencing, qualification and progress. Qualified core
slices do not establish a complete or released capability.

The existing process-utility contributor relayed a renewed operator direction:
keep driving with the existing coordinator toward a plugin and independent
service delivered through the standard plugin/service installation flow and
Worktree Manager, enhanced rather than replaced by fleet-manager. This is a
public-safe restatement, not a copy of private actor names or a new rollout,
release-activation, transport-policy or predecessor-retirement grant.

## Participants

These are contribution roles, not new worker processes or authority grants.
Use #7238 to accept a slice and record its source boundary before editing it.

| Participant | Responsibility | Reached via |
|-------------|----------------|-------------|
| Proposal editor | Sole shared vision/effort/service-lifecycle writer; `services/chat` core, client and hosting integration | #7238 and the proposal PR |
| Plugin contributor | Disjoint `plugins/agent-chat` connection/native contribution; exact paths claimed before edits | #7238; input integrated by the proposal editor |
| Collaboration contributor | Claimed continuation of the linked collaboration/thread/attention specification and its coordinated service/client slice; no transport or lifecycle takeover | [Bounded continuation claim](https://github.com/ThomasMichon/copilot-extensions/issues/7238#issuecomment-6358796470) |
| Chat setup adapter contributor | Derive the Chat-owned standard setup adapter, reusable role-selectable construction seam and container/template contract; preserve existing service lifecycle ownership | The operator's direction below and this reviewed effort |
| Qualification contributor | Installed-consumer, native lifecycle and platform-boundary cases | #7238; input integrated by the proposal editor |
| Conduct contributor | Generic scoped-peer ambient policy and discoverable rules-of-engagement skill, after the intent amendment clears review; no Chat/Bridge/Logger source | [Accepted disjoint scope](https://github.com/ThomasMichon/copilot-extensions/issues/7238#issuecomment-6303930285) |
| Process utility contributor | Canonical `libs/agent-procutil` caller-bounded cross-OS source, tests and library docs | [Public source claim](https://github.com/ThomasMichon/copilot-extensions/issues/7238#issuecomment-6274245326) |
| Existing lifecycle owners | Review integration with fleet-manager, shared process utilities and service primitives | Their existing public issues, linked below |

## Coordination

- **Topology:** one design writer; independently claimed implementation PRs only
  after the proposal clears review and their dependencies are understood.
- **Host:** the proposal editor owns the proposal PR and integrates contributor
  input. Contributors do not edit the same proposal files concurrently.
- **Handoff:** use the existing issue/task/worktree contracts for accepted
  ownership transfer. A chat message or a published plan does not transfer it.
- **Collaboration continuation:** the contributor's claimed specification is
  the one shared-plan writer for this bounded slice. Review its
  [contract](collaboration.md) before dependent code; exact implementation
  paths are claimed separately. Existing component/source owners remain.
- Preserve existing fleet-manager and machine-fleet work under #6030, managed
  lifecycle work under #6424/#6240, and service-specific authorities. Consume
  their supported seams; do not replace them through this effort.
- Normal already-authorized operations proceed within their accepted scope.
  Changed scope, destructive migration and activation remain decisions of
  their real owners, not per-message consent or a second controller.
- The process utility lane owns its canonical implementation, not another Chat
  proposal. Required generated copies are coordinated with their existing
  consuming owners first; Chat consumes published APIs without a vendor fork.
  Utility qualification remains parallel to ordinary working native clients.
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

This is an intent summary, not a verbatim transcript. The proposal and bounded
core have landed; remaining qualification is open. This does not grant
permission to install, activate, migrate data or restart another owner.

The collaboration amendment requests one canonical conversation store with
linked typed collaboration, per-thread/recipient attention and multi-machine
views; durable addressed follow-ups without transcript scraping or another
delivery authority; same-operator authenticated in-scope peer authority without
duplicate approval; collective decisions, attenuated delegation and genuine
countermand; explicit external-domain capability/information-flow boundaries;
and minimally disclosed authoritative intake provenance, not centralized
transcripts. Complete generic Chat, Gateway/SSH (including public Azure Bastion
ingress) and Gateway/Vault setup belong upstream; internal provider
drivers, private configuration and secret references remain adopter-owned.
Standard installation and Worktree Manager remain the baseline, with optional
fleet-manager enhancement. These are public-safe intent summaries; adapter
shapes and validation details below are agent-recommended realization.

The renewed direction prioritizes practical, prior-art-informed conversations:
typed threads and evidence, scoped recipient views, and durable reply attention
outside bounded ambient history. This public-safe summary does not identify
private research sources or add another transport/controller. The
[collaboration contract](collaboration.md) is agent-recommended realization of
that existing intent, not installation or activation authority.

Further operator direction:

> Derive the Chat-owned standard setup adapter; only coordinate by making scoped changes, don't reach out to other agents directly or via PR/issue comments

This extends the source derivation, not deployment authority or another
component's implementation ownership. The proposed realization is in
[Standard Chat setup adapter](architecture.md#standard-chat-setup-adapter).

After the construction trace identified the all-local-package admission
boundary, the operator explicitly authorized its narrow shared prerequisite:

> Let's provide a reusable role-selectable install flow.

This permits a canonical installer-engine extension and its generated
consumer copies, not a Chat-specific engine or a takeover of service
supervision, Fleet, Gateway or SSH. The no-outreach/no-comment coordination
boundary and the absence of runtime rollout authority remain unchanged.

The operator further directed:

> The vision of the Gateway and Fleet Manager is continuing to build out, so ensure your effort and vision onver a proper coainer spec for Chat, with approproate configuration in dotfiles from a template, to be driven by the Komodo deployment engine.

This requires a proper Chat container specification, portable templates,
actualization in the adopter's resolved private configuration repository, and
Komodo-driven fleet deployment. The
[container role specification](container-service.md) is agent-recommended
realization of that request. "Dotfiles" does not name a repository to hardcode,
and the direction does not grant live deployment, destructive migration or
ownership of Gateway/Fleet Manager implementation.

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
- [x] Package a dependency-light installed client with no source checkout,
  sibling venv or Copilot requirement for its actual command.
- [ ] Provide selected-client identity/catalog and explicit setup through the
  existing lifecycle primitives before plugin/harness adoption. The service
  writer owns this integration; connection and adopter code must not fork
  an installer or infer selection from PATH.
- [x] Package an independently operable hosted service with no source checkout,
  sibling venv or Copilot plugin requirement.
  The foreground `service` artifact merged in #7361; #7266 subsequently
  qualified both native Windows and Linux artifact/host cases. This resolves
  independent artifact packaging, not the managed installation/activation
  obligations in Phase4.
- [x] Publish client bounds, errors, offline scan capability and explicit
  configuration. Preserve output/flush/CAS and retained stable-request recovery.
- [ ] Supply hosted endpoint discovery and explicit service setup through the
  existing lifecycle owners; client configuration is not hosting readiness.
  Explicit installed-client consumption of canonical local endpoints is
  qualified and merged in #7517. This does not complete governed service setup,
  activation or selected release adoption.
- [ ] Resolve trusted host-init session ID and CWD through the workspace owner:
  real-worktree participant continuity, session-segmented anchor participants,
  and existing local service/bridge-owned inbox/source assignment. Keep friendly
  labels separate and preserve existing membership/cursors without migration.
  The corrected candidate consumes the owner's project-scoped
  `record --cwd` contract rather than enrolling, resident-backed or global
  head calls. Qualification and review must establish that corrected boundary;
  authoritative inbox/source assignment and native admission also remain open.
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

#### Standard Chat setup adapter

- [x] Review the [Chat-owned adapter derivation](architecture.md#standard-chat-setup-adapter),
  including the standard Manager baseline and the exact current construction/
  selection/supervision gaps. No neighboring implementation is claimed.
  #8401 merged after current-head approval and required CI. This resolves the
  derivation review, not an executable installer or any adoption gate.
- [x] Establish a canonical role-selective construction input before implementing
  client-only setup. #8430 adds explicit `local_packages` alongside selected
  extras, retaining the complete admitted source graph and canonical custody.
  Final-head native Linux/Windows controls prove client/service closure,
  omitted-distribution refusal and immutable reuse. This is a construction
  prerequisite, not selected Chat setup or an installed command publication.
- [ ] Implement selected manual-client setup and preserving removal through
  supported canonical custody, then qualify independently installed invocation.
  Do not turn the existing local wheel catalog into release-selection authority.
- [ ] Integrate the existing service lifecycle owner's stable-launcher,
  health/cutover and recovery binding; no second Chat supervisor, installer
  engine, command ledger or prerequisite Fleet deployment.
- [ ] Add optional Manager/readiness discovery only after the independently
  runnable adapter and its readiness meanings are real. Keep the parent
  service, native, migration and adoption gates open until actually proved.

#### Container role, private templates and Komodo

- [x] Review and land the [container role specification](container-service.md)
  against the evolving Chat, Gateway and loadout visions before implementing
  the container path or publishing downstream configuration.
  #8522 merged after current-head approval and required CI. This closes the
  intent/specification review only, not image delivery or private deployment.
- [ ] Deliver an independently operable Chat service image and portable
  role/configuration template with explicit artifact, storage, identity,
  Gateway, placement and resource bindings through existing owners.
- [ ] Actualize that template in the resolved private configuration repository's
  dedicated worktree and publication flow, preserving overrides and selected
  template/source revisions rather than embedding private deployment state here.
- [ ] Bind the admitted `container.service` allocation to the existing Komodo
  deployment engine and supported effect executor. Preserve independent Core
  bootstrap, current fleet admission and prepared-plan confirmation; a named
  Stack projection alone is not deployment.
- [ ] Qualify the actual image, Gateway exposure, post-caller health,
  persistent single-writer restart/update/rollback and preserving removal.
  Keep unsupported owner bindings explicit instead of adding a Chat controller.

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

### Parallel track - Collaboration and scoped peer conduct

- [x] Review and land this single vision/architecture/plan amendment before
  dependent realization. This gate is independent of the retained client
  platform qualification and managed installer rollout.
- [x] The accepted conduct contributor implements only its claimed
  `plugins/agent-conduct-guidance/` kernel, skill and ordinary declaration,
  tests and changefile metadata after that intent gate. No new grant/domain
  API, deployment, root policy edit or neighboring source is included.
  Source merged in #7464 at `e620c95778df579b706f709b07af91df1f4203aa`;
  declarative decision cases and fresh-adopter delivery were qualified,
  not actual model behavior, runtime enforcement or live policy adoption.

#### Collaboration contract continuation

- [x] Review and merge the claimed [collaboration/thread/recipient contract](collaboration.md)
  over existing Chat state before dependent implementation, preserving the
  coordinated client/service/plugin extension points.
  Reviewed contract #8134 merged as
  `85942fd63be8c298ba880bf0d42b5a5747f15c24`; its portable implementation
  subsequently merged in #8159. Broader native and lifecycle gates remain open.
- [x] Implement and qualify optional structured posts and non-consuming
  thread/inbox projections through the existing store and installed client.
  Pending reply attention must outlive an ambient window without becoming a
  task queue, native inbox assignment or another delivery authority.
  #8159 qualified the current Windows source/installed-artifact slice and
  required Linux source units. Current native Linux, genuine multi-machine
  use, assignment, attachment and managed adoption remain in the parent
  Validation Plan; they are not inferred from this source completion.

#### Retained conduct and ingress integration

- [ ] Coordinate any required new domain/intake or external-ingress binding
  with its actual host/client owner. State missing APIs precisely without
  holding existing explicitly authorized same-domain work behind them.
- [ ] Preserve Gateway/SSH, Vault, lifecycle, workspace and task ownership;
  existing #6030/#6240/#6424/#6681 work is not reassigned by this amendment.

## Validation Plan

- [x] **Portable store:** monotonic event identity, independent viewers,
  bounded pages, stable request replay/conflict and visible capacity refusal;
  installed source-independent artifacts and qualified native Windows/Linux
  on-demand lanes. Required source CI remains the admitted unit-only packet,
  not this native portfolio.
- [x] **Installed client:** flush-before-cursor-CAS and crash-released reader
  locks through the actual installed command, not just the callable core.
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
- [ ] **Container contract:** independent image build/start with declared
  release/platform identity, read-only application content, bounded resources,
  explicit configuration/secret references and separately retained storage;
  no source/plugin/sibling-runtime dependency or backend exposure bypass.
- [ ] **Private composition and Komodo:** template actualization preserves
  private overrides/provenance; current support/capacity admission and exact
  prepared-plan confirmation precede real manager execution. Offline
  projection, a source merge and a submission receipt never substitute.
- [ ] **Container recovery and route closure:** two real clients use the
  admitted Gateway route; restart/caller loss/update/rollback preserve
  conversations and uncertain effects, fence a possible prior writer, and
  ordinary removal preserves data/configuration while withdrawing only owned
  exposure and execution.
- [ ] **Concurrency:** posts and health remain responsive under several held
  subscriptions; coupled maintenance uses one current owner/correlation and
  does not turn releases into acknowledgement loops.
- [ ] **Platforms:** direct Windows and native Linux cleanup, plus a separate
  real WSL-to-Windows descendant test for timeout/interruption/caller death.
  POSIX process groups are not claimed as Windows tree ownership.
  Direct native client/host and caller-death cases passed in #7266; the
  separately assigned cross-OS process utility merged in #7254 with its own
  native witnesses. Chat's consumption of that merged utility and future native
  integration remain open; utility qualification alone does not close this item.
- [ ] **Portfolio:** keep fast portable contracts in required change-scoped CI;
  isolate real process/native/fresh-environment matrices in their existing
  appropriate lanes. Record why a genuinely unavailable venue was not exercised.
  Client qualification requires the operator-approved180s acceptance packet and complete
  600s suite on the same immutable candidate. Their mechanically checked
  family/parameter membership must retain every existing assertion and case;
  a selected packet alone is not complete qualification.
- [ ] **Publication:** public-only references and synthetic examples,
  documentation-impact review, required CI/review and unchanged open parent
  tracker until its actual completion gate is met.
- [ ] **Collaboration projections:** linked answers/reviews/disputes/
  supersession and multi-machine recipient/thread attention use one canonical
  store; addressed priority does not supply isolation; delivery/read/answer/
  accepted/completed states remain distinct and owner-offered actions stay owned.
- [ ] **Scoped peer conduct:** discoverable policy/skill admits an existing
  authenticated in-scope peer's ordinary authorized work without duplicate
  approval, while preserving attenuated scope, source custody, repository and
  resource gates, genuine authoritative countermand and accepted handoff.
- [ ] **External ingress and intake:** copied envelopes, quoted instructions
  and external bodies cannot promote themselves, expand capability, bypass
  information-flow boundaries or request raw transcripts. Minimal provenance
  references do not turn delivery/capture receipts into semantic grants.
- [ ] **Compatibility and retained follow-ups:** context truncation, later
  arrivals and unknown admission preserve captured-batch attribution and normal
  reading; no second log, cursor reset, administrative skip or fresh-ID resend.

## Proposal

Build the remaining reviewed [architecture](architecture.md) in small
compatible slices. Existing manual client
use remains valuable while an unsupported native host seam is resolved; full
graduation still requires the declared native and lifecycle guarantees.

### Current native integration seams and retained owners

The connection contributor's read-only inventory distinguishes actual callable
foundations from the bindings this effort must still supply. The client
baseline for that inventory is #7266 at
`295de3cc39040262a428b4a90fd200ab4fb9245e`; the repository foundations were
inspected at `03019a44d3cb16746a9a8bd95c6a40f65afae4a8`. SDK evidence is the
declarations bundled with CLI `1.0.101-0`, not a claim about a newer runtime,
an independent SDK version or live native qualification.

| Boundary | Available foundation | Exact remaining binding and owner |
| --- | --- | --- |
| Trusted initialization and workspace | SDK `joinSession`, `sessionId` and `rpc.metadata.snapshot().workingDirectory`; corrected candidate's project-scoped Worktrees `record --cwd` projection | The client/service writer qualifies the non-enrolling, non-activating owner read, stable worktree identity and startup-session anchor segmentation before adoption. The plugin forwards trusted initialization once; no duplicate environment-ID check or registry. Standalone explicit actors remain independent. |
| Recipient inbox and eligible sources | The client accepts explicit actor/channel controls; Bridge `inspect_live_inbox` projects direct-message receipts | No examined client API supplies authoritative Chat recipient/source assignment. The client/service writer coordinates that versioned assignment and its isolation/rebind errors with the existing local service/Bridge owner. A direct-message receipt projection is not this API. |
| Shared notification intake | Bounded Chat `scan` v1; Bridge remote-event multiplexing and protocol13 `remote/events` | Remote runtime events do not subscribe to the conversation store. The service writer and existing host/Bridge owner must publish the Chat-feed registration, generation, finite callback, cancellation and quiescence binding. No per-session Python helper or continuous Node polling is an acceptable substitute. |
| Native admission and completion | SDK `session.send`, primary final-ID semantics and optional `originatingMessageId`; bounded queue/event-log observations | Exact own primary correlation is usable. Unknown or non-primary admission remains held until its native host supplies authoritative reconciliation; queue absence, unrelated idle and expired history do not supply it. |
| Commands, discovery and setup | SDK tools/commands, source-qualified extension identities and host-only reconciliation; installed client-catalog v1 with nine checked resources | The connection writer can expose the supported inert/manual surface. The client and existing lifecycle owners still owe governed selection/setup; a local wheel RECORD or a loaded tool is not that authority. Host reconciliation stays host-owned. |
| Resident cost | The native host's ordinary extension process is the baseline | Existing service/host multiplexing owns shared listening. Measure increasing recipients, bounded callback time/bytes, zero quiet/self/irrelevant/reconnect-only model turns and exact deregistration separately from that baseline. |

Repository API homes are `services/chat/src/agent_chat_service/`,
`plugins/agent-worktrees/src/agent_worktrees/` and
`plugins/agent-bridge/src/agent_bridge/`. The SDK declaration inventory names
`extension.d.ts`, `session.d.ts`, `generated/rpc.d.ts`, `types.d.ts` and
`session-events.d.ts`; declaration availability is not runtime execution proof.

The original connection writer retains `plugins/agent-chat/` and its narrowly
coordinated catalog/changefile entries. A later proposed connection lease is
limited to that plugin's manifest, README, guidance/hooks, setup skill,
session-guidance writer, extension/subscription modules and tests. It does not
transfer client, service, shared-plan, workspace, Bridge, library or lifecycle
source. Supported inert/manual work need not wait for unrelated platform
qualification; native callbacks still require the actual assignment and
shared-multiplex contracts above.

## Journal

### 2026-10-24 - Retain the current canonical import closure

- The corrected456-node head completed both lanes on all four fresh
  Linux/Windows3.10/3.11 combinations, with complete membership and only the
  two declared Windows POSIX skips. Those results belong to
  `be61115f28e1895baa51887083d646c126b644b1`, not a later rebased candidate.
- Reconcile the independent native-selector additions without dropping any
  dispatch exclusion. The updated canonical constructor from #8534 also
  imports the wheel snapshot/inspection companions: deliver both unchanged
  through the same artifact hooks and include them in resource/import-scope
  and missing-input controls. This does not select Chat wheel profiles or
  change artifact-admission authority.
- Focused fresh-artifact and real-construction controls pass26 cases after
  that dependency update. Preserve all previous cases and add the six companion
  controls;462-node full/fast qualification and fresh final-head execution
  remain required before treating the updated candidate as qualified.

### 2026-10-24 - Fresh-host qualification corrections

- Fresh Linux3.10/3.11 lanes completed acceptance and full execution. Both
  Windows floors exposed an actual workspace deadline subtraction rounding
  above60s; keep the strict limit and cap forwarding to the original budget.
  Two deterministic binary-exponent boundary regressions add coverage without
  changing or removing any previous case.
- Use two literal hosted OS routes with the existing Python-floor matrices,
  rather than weakening the trusted-CI prohibition on dynamic runner routes.
  The actual trusted-CI guard and52 bounded workflow/runner contracts pass.
- The corrected Windows3.11 candidate passes417/456 acceptance nodes
  (415 passed,2 POSIX skips,39 full-only deselections) in117.64s within180s,
  and full454+2 skips in184.71s within600s. Both report complete execution and
  membership
  `855b1be0c87ee399cf619154debd38e3d8325c32cd872d99dea0b7fa54b142b0`.
  Re-run all four fresh hosted combinations against the corrected head;
  earlier results do not certify this changed candidate.

### 2026-10-24 - Current installed-constructor qualification

- The current Windows Python3.11.9 candidate passed the complete unfiltered
  acceptance lane413+2 POSIX-only skips with39 full-only deselections in105.63s
  within180s, and full452+2 skips in165.14s within600s. All454 collected nodes
  retain membership
  `c131716bfc0410a61a203204451e7512c4988dbd42206bf167363c32fdd35c95`;
  execution is complete in both lanes.
- Actual Python3.10.21 full compatibility passed452+2 skips in135.64s.
  Python3.14.7 acceptance passed413+2 skips/39 deselections in109.28s, and
  full452+2 skips in197.45s. Installed application/process-helper bytes were
  checked against current source rather than assuming a cached venv was current.
- Earlier attempts exceeded fast limits or failed native startup/fault
  prerequisites. Those are failed observations, not retroactive passes.
  Preserve every case under the operator-approved180s allowance, with600s full
  and all existing per-operation/read/HTTP/custody bounds unchanged. The current
  focused native controls passed3 cases in22.59s before the full unfiltered
  qualification; no native assertion was removed or marked skipped.
- Reuse only exact metadata-keyed installations; build immutable base/library
  artifact inputs once, keep separate fresh version/role environments, and use
  the existing caller-owned CWD/environment seam without another child adapter.
  The optional native matrix pins the actual prepared interpreter and runs
  both lanes on each selected Linux/Windows floor. Fresh hosted execution is
  still required; a workflow definition is not its result.
- This advances internal client construction, not selected setup/removal,
  container delivery, private configuration or live adoption. Preserve the
  reviewed #8522 container contract and every still-open parent gate.

### 2026-10-24 - Container and Komodo amendment reviewed

- #8522 merged as `e38e62981c410de3041215d1a6eafc24a1aff18e`
  at08:03:41Z after current-head Copilot approval and all required checks.
  The complete review reported no findings and no review threads remained.
  Managed completion used the authorized additional-CODEOWNER review
  exception, not a CI bypass.
- Re-read the complete Plan and Validation Plan after merge. Close only the
  container specification review item. Image/template delivery, private
  actualization, Komodo execution and every container acceptance item remain
  open; #7238 remains Open.
- Continue the retained construction/client qualification slice before
  claiming selected setup. The reviewed container contract now governs its
  later fleet realization; no private configuration or runtime rollout is
  implied by this merge.

### 2026-10-24 - Add template-owned Chat container and Komodo intent

- Capture the operator's container/template/private-configuration/Komodo
  direction without changing existing Gateway, Fleet Manager or service
  ownership. Revise the Chat vision as pure intent and put the concrete
  [proposed contract](container-service.md) in this effort.
- Add explicit implementation and validation gates for the portable image and
  template, private actualization, current Komodo execution and complete
  Gateway/storage/credential/resource closure. Current role projection remains
  planning evidence, not an effect or deployment receipt.
- Container realization and private configuration publication follow review of
  this amendment. No container, manager, private configuration or running
  service is changed by capturing the plan.
- The independent construction consumer and its outstanding qualification stay
  separate. This amendment is not selected-client setup, managed service
  installation or completion of the parent effort.

### 2026-10-24 - Consume canonical construction from installed Chat

- Continue the Chat-owned setup adapter through the delivered resources, not a
  constructor copy. `construction.client_candidate` supplies the exact client
  extra/local closure and installed health to canonical `numbered_candidate`.
  The loader verifies the wheel RECORD, refuses occupied canonical import
  names and removes its temporary top-level aliases after loading.
- The existing installed construction callback binds the canonical
  `runtime_launcher.py` and runtime family into the unpublished slot. Health
  verifies that exact bootstrap on fresh construction and immutable reuse.
  An independent Chat sdist plus three independent declared-library sdists
  build the actual candidate without checkout resources or sibling runtimes.
  The manual client runs through the retained slot-local launcher after all
  source inputs are removed; no selector is published.
- Add focused role-delegation, import-scope, Python-floor, completed-slot and
  bootstrap-drift controls. The optional `chat_construction` native workflow
  selects the complete Chat portfolio on Linux/Windows Python3.10/3.11 instead
  of adding subprocess work to required unit CI. Source-only artifacts and
  native test definitions do not prove that this workflow has executed.
- Actual freshly prepared Windows Python3.14.7 and Python3.10.21 full portfolios
  each passed422 with2 POSIX-only skips,424/424 nodes and complete execution,
  in168.66s and145.30s respectively within600s. The3.10 constructor refusal is
  explicit and occurs before a runtime is created. Current acceptance passed383
  with2 platform skips and39 full-only deselections in94.52s within120s.
  Both lanes bind membership
  `5ccc2afd362c113e5e2f5bbe234bdc591c3bbfb0e37131d20311dd639c7c6fa1`.
  Initial focused collection exposed a stale managed test wheel; canonical
  `--prepare` refreshed the changed package before these final full runs.
- This is an internal construction consumer, not independently runnable
  selected setup or preserving removal. Keep the manual-client selection,
  installation ownership, service lifecycle, native assignment/shared-feed,
  migration, release/adoption and parent completion gates open. No live
  installation, command, configuration, service or subscription is changed.

### 2026-10-24 - Deliver construction resources in Chat artifacts

- The Chat-owned build now consumes the existing canonical
  `standalone_build.py` hooks for the complete runtime family and Python
  constructor/dependency-policy companions. Wheels contain unchanged canonical
  bytes; sdists also carry the same helper so a subsequent wheel build needs
  no checkout. Standalone preparation retains both canonical resource owners.
  This is artifact delivery, not another construction engine or an executable
  selected-installation adapter.
- The installed wheel catalog covers all eighteen Python resources through
  RECORD without changing its local-only evidence meaning. Source-independent
  wheel/sdist equality, incomplete-sdist refusal, installed constructor imports
  and resource-drift controls exercise the actual artifact boundary.
- Native Windows qualification retained all410 parameter nodes: acceptance
  passed371 with2 platform skips and37 explicit full-only deselections in106.28s
  within120s; full passed408 with2 platform skips in147.40s within600s.
  That initial resource inventory's membership digest is
  `3ade790e12cd7539612d26b19525eb8b2e16d896389979c7480246ee87ddd5fb`.
  An earlier acceptance attempt exhausted120s; a duration-reporting run of the
  same unfiltered lane passed without changing its budget. A missing-helper
  negative control was corrected to recognize the native exception type rather
  than requiring a POSIX English diagnostic.
- The adjacent qualification guard was stale after PR units moved to
  `ci_pr_unit_reuse.py`; it now checks that exact CI owner and retains the
  direct promotion producer check. No workflow or required CI matrix changes.
  The initial PR CI correctly refused the changed standalone declaration's
  stale source hash. Refresh only that admitted hash; the complete1274-node
  packet then passed locally with coverage in110.468s, preserving its unit
  inventory, effects policy, coverage targets and execution budget.
- Review exposed the separate Python floors and an old preparation fixture.
  Keep core/client Python3.10 support and the canonical constructor's3.11+
  bootstrap requirement distinct. The installed resource control proves the
  constructor's expected3.10 refusal without skipping the case; its positive
  artifact-contained import remains exercised on a compatible interpreter.
  Actual Python3.10.21 full qualification passed408 with2 POSIX-only skips
  in147.84s,410/410 nodes, execution complete, with updated membership digest
  `0e9bb0cd4a83e6baf022e674d418a5ef873266336d08015a19da1e7af529520a`.
  The compatible-interpreter artifact/inventory controls separately passed10
  cases in13.93s. All six real fresh-preparation resource expectations and the
  adjacent qualification contracts passed54 cases in6.51s; Chat retains the
  installer engine, while the extra procutil build resource remains Fleet/PR-only.
  These fixes do not add a parser dependency, lower the canonical constructor's
  floor or change any workflow.
  Native Linux artifact qualification and selected manual-client setup/removal
  remain open. No runtime rollout, service activation, migration or native
  attachment is performed by this slice.

### 2026-10-24 - Reusable role-selectable construction merged

- #8430 merged to `dev` as `93f4546a0d65a3b2ead03de8872886fcfdd9429c`
  at 03:47:17Z. Current-head review approved
  `bf7f1596c64e6e72fd832c38e8e2402afbbf525e` with no open findings;
  both review threads were resolved and required CI passed. The managed merge
  used only the authorized additional-CODEOWNER exception, not a CI bypass.
  #7238 remains open.
- The shared constructor installs the caller's exact local distribution closure
  and declared extras while retaining/fingerprinting all admitted sources.
  Canonical dependency policy owns generated exclusion constraints, including
  their path canonicalization and source-layout-independent retention.
  UV remains the resolver; no Chat installer fork, project mutation or
  registry fallback for an omitted first-party distribution is introduced.
  Default callers retain their previous construction/profile behavior.
- Final-head manual acceptance run `40059200321` passed all 16 selected role
  cases on each fresh platform: native Linux 7.91s and Windows 16.64s, with 52
  existing unrelated candidate-engine cases explicitly deselected per lane.
  The final local workflow/policy selection passed 69 cases, including actual
  short-path offline builds and canonical owned-temp aliases. Required source
  CI, install-contract checks, canonical synchronization, module-size checks
  and focused lint also passed. These are reusable construction controls,
  not full Chat installed-adopter, native-host or service-lifecycle proof.
- Re-read the complete parent Plan and Validation Plan after merge. Resolve
  only the canonical role-input prerequisite; continue independently packaged
  Chat manual-client setup, selected invocation and preserving removal through
  supported custody. Service supervision, host assignment/shared feed,
  migration and live adoption remain open. No runtime rollout was performed.

### 2026-10-24 - Derive the standard Chat setup adapter

- #8401 merged to `dev` as `e7a4b2f349cd7042522d566e236501d387df9e49`
  at02:09:40Z. Current-head Copilot review approved with no findings, all
  review threads were clear and required CI passed. The managed merge used
  the authorized Maintainer exception for the additional CODEOWNER vote,
  not a CI bypass. Re-read the full parent Plan/Validation after merge and
  resolve only derivation review. Continue role-selective canonical input
  before executable setup; the effort remains Active with no rollout.
- The operator extends this slice to the Chat-owned adapter, with coordination
  through scoped changes only and no direct agent outreach or PR/issue comments.
  The [derivation](architecture.md#standard-chat-setup-adapter) is proposed
  realization under the existing intent, not a runtime rollout or transfer of
  neighboring source.
- Read-only tracing distinguishes Manager bootstrap/catalog knowledge,
  installer/readiness discovery/planning, canonical construction/retention and
  Chat's local wheel catalog from governed selected-client/service setup.
  Optional Fleet source transitions remain with their existing owners.
- The current canonical package-root constructor traverses all local sources;
  Chat declares both optional role closures. Role-selective construction must
  therefore be established, not inferred from the client extra or achieved by
  copying the engine. Chat's workspace result still explicitly lacks assignment;
  Bridge live-message receipts and remote hosted-session events do not supply
  Chat recipient assignment or shared conversation-feed registration.
- No installer, service, credential, native attachment or migration was invoked.
  No executable adapter is claimed by this documentation change. Review the
  derivation before dependent code and preserve the full Plan/Validation gate.

### 2026-10-24 - Portable collaboration merged; full graduation stays active

- #8159 actually merged to `dev` as
  `bc6fec54992c80293170c867be37775ab1dc6f9f` at00:18:37Z, from qualified
  `c902cdaa25bf95a3cc839d6a717326fb3ec7976b`. Current-head Copilot review
  `5635745409` approved with no open findings, every review thread was resolved
  and required Linux source-unit/guard CI passed. Managed completion used the
  repository-authorized Maintainer exception for the additional CODEOWNER
  vote; no CI gate was bypassed.
- Re-read the complete parent Plan and Validation Plan after merge. Resolve
  only the portable collaboration implementation item. Native assignment,
  shared feed/multiplexing, governed selection/setup, lifecycle, migration,
  current platform qualification and genuine multi-machine adoption remain
  open with their existing owners. The effort and #7238 stay Active.
- The same immutable candidate passed both unchanged native Windows lanes:
  acceptance361 passes, two specifically allowed POSIX skips and33 full-only
  deselections in95.79 seconds under120; full394 passes and the same skips
  in129.46 seconds under600. Both complete396-node maps have identical
  membership digest
  `74ef7d3dd3f75955377d23a4e729adb8d01b1cf10c9b6847bd7e35d718c6670d`,
  without missing, unexpected, rejected or duplicate calls. All326 original
  cases remain;70 are additive. Eighteen pure metadata/control units stay in
  the existing admitted packet, not an expanded native CI matrix.
- Review-driven regressions close the lock-held query class for unrelated,
  pending and settled histories. Each indexed inbox branch and their merge
  bound candidate examination before retirement. An empty scan page can
  continue within its captured head without skipping an omitted pending entry;
  exact returned counts and SDK progress guards preserve that contract.
  Answered, resolved and superseded100-to5000 histories satisfy the unchanged
  1000-instruction growth bound. The publication-race control injects only
  after a successful real read, so stale existence checks cannot bypass it;
  normal/fault cases keep actual child identity, crash and five-second bounds.
- #8271's canonical Windows discovery repair pairs shared reads with native
  retained-reader replacement and synchronizes the three intentional consumer
  mirrors. Its131 native library contracts and the actual source-removed host
  crash/rebind case pass. PID/strict parsing, mutation locks, record shape and
  POSIX replacement remain unchanged; #5903/#5904 are separate.
- Validation tiers retain real CLI/provider/HTTP, independent installed
  participants, source-removed fresh core/client/service artifacts and actual
  caller death before observer cleanup. No installer/bootstrap changed, so a
  separate full-machine clean-room rig was not run. The configured local
  Docker engine refused connection; no service was started and no native
  Linux result is claimed. Actual model wake, external deployment and managed
  adoption were not exercised or authorized. Earlier failed/stale-snapshot
  diagnostics do not substitute for this final candidate.
- Next bounded stretch _(agent-recommended)_: inspect the current workspace,
  Bridge and lifecycle owners' published selection/setup and assignment/feed
  seams, then claim the smallest supported Chat-owned consumer continuation.
  Do not invent a missing host API, duplicate an installer or take neighboring
  source. Keep current native Linux qualification explicit with its existing
  owner rather than letting an unavailable local lane block unrelated
  supported manual/client work. No runtime rollout is implied.

### 2026-10-23 - Bound each pending branch before lookahead merge

- The second review on `8d2d8fab9a` confirmed the first two findings resolved,
  but its body retained a real Medium issue despite the overview's
  "No open issues" label: small inbox pages still sorted an entire genuinely
  pending backlog. No merge followed that ambiguous summary.
- Regression-first measurement reproduced4634 versus205357 SQLite instructions
  for100 versus5000 unanswered directed questions. Each indexed branch now
  applies its snapshot/retirement filters and ordered lookahead limit before
  merging their bounded IDs and loading canonical bodies.
- Two additive cases cover unanswered requests and mixed answer/question
  backlogs without changing pending semantics, captured paging, reader progress
  or the120/600-second qualification allowances. The387-node acceptance packet
  passes with352 passes, two allowed POSIX skips and33 full-only deselections
  in78.78 seconds. Final full qualification and the next real-head review/CI
  remain open.

### 2026-10-23 - Review-driven projection work bounds

- The first current-head review of #8159 found two real lock-held query costs:
  an empty inbox scanned unrelated questions and a sparse thread scanned
  unrelated channel events. Regression-first measurements reproduced growth
  from5373 to225511 and1698 to75166 SQLite instructions, respectively, as
  unrelated history grew from100 to5000 events.
- Candidate selection now starts with indexed channel/recipient or canonical
  reply-author relationships; thread IDs and the root are bounded before event
  bodies are loaded. Derived channel/reply-author scalars are committed with
  the canonical event and metadata, not writable attention state. No native
  listener, transport or lifecycle source is involved.
- Three additive regressions include unrelated questions, the viewer's own
  ambient messages, another channel's correspondence and sparse-thread
  pagination. Each requires no more than1000 extra SQLite instructions between
  the small and large fixtures. The existing120-second acceptance lane passes
  with349 passes, two allowed POSIX skips and33 full-only deselections in98.14
  seconds;384 nodes are collected. Final same-candidate full qualification and
  the next actual-head review/CI remain required before merge.
- That full run exposed a Windows fixture race: the reader-lock child's ready
  pathname became visible while replacement still temporarily denied reading
  it. The fixture now requires a readable complete record within its unchanged
  five-second deadline, still proves the same live child/lock and actual crash
  release before Job cleanup, and adds one deterministic publication-race case
  without removing the normal case. This does not change production reader
  logic or hide a permanent publication failure.
- Final candidate `d3265690f7` passed the unchanged full and acceptance lanes
  without further source edits: full383 passes and two allowed POSIX skips
  in108.58 seconds; acceptance350 passes, the same skips and33 full-only
  deselections in77.97 seconds. Both complete385-node maps share
  `e1b8371d38ee3691daed1058b305eae2ba63f393d2927fbdd7a0a4c02464f4e1`.
  The original326 cases remain and59 cases are additive. The next current-head
  review and required CI remain merge gates, not implied approvals.

### 2026-10-23 - Portable collaboration candidate qualified

- Implementation candidate `f95502367dc4880be906de48f22b30eb897ba95b`
  adds optional typed posts, atomic canonical metadata/recipient indexes,
  stable annotated recovery and non-consuming captured thread/inbox views.
  Existing text event/page shapes, ordinary cursor settlement and the
  transport/lifecycle/workspace owners remain unchanged.
- The unchanged native Windows acceptance and full lanes exercised that same
  immutable candidate: acceptance selected348 of381 nodes, with346 passes,
  two allowed POSIX-only skips and33 full-only deselections in81.26 seconds;
  full selected381, with379 passes and the same two allowed skips in98.72
  seconds. Both maps were complete and shared membership digest
  `b1fd10b81848876c5b93a22d5d65fb1ed2c2ef9f0f92b24ab3305afa59d51ac4`.
  The original326 nodes remain;55 cases were added.
- Simple end-to-end and fresh-artifact tiers use the existing real HTTP,
  provider and source-removed installed client/core/service fixtures. They
  cover lost structured responses, explicit original-ID recovery, recipient
  and thread projections, and unchanged ordinary reading. No installer or
  bootstrap changed, so a separate fresh-machine clean-room rig was not
  required; these artifact environments are not managed-host deployment.
- Eighteen focused pure metadata/control cases pass locally and are enrolled
  additively in the existing source-unit manifest. Whole-packet Windows
  qualification is not claimed: the earlier canonical packet refused a
  native loader in activation-preservation after856 passes and one platform
  skip. The exact boundary is retained with the existing portfolio owner in
  [#1303](https://github.com/ThomasMichon/copilot-extensions/issues/1303#issuecomment-6360280182);
  no observer or neighboring implementation was weakened. The complete
  required Linux source-unit packet still has to pass on the review head.
- Changed-source lint, install-contract and documentation checks pass.
  The registered `services/chat` consumer has one generated patch changefile;
  no release version is hand-selected. Native Linux Chat, native feed
  assignment, foreground attachment and managed adoption are not inferred
  from these Windows source/artifact results.
- Review/merge and the parent completion gate remain open. No runtime update,
  deployment, private provider or external venue activation is authorized or
  performed by this qualification.

### 2026-10-23 - Collaboration contract reviewed

- The four-document specification merged in #8134 at
  `85942fd63be8c298ba880bf0d42b5a5747f15c24`, after current-head Copilot
  approval with no findings and passing required checks.
- Re-read the complete Plan and Validation Plan after merge. Only the contract
  review item is resolved; structured post/view implementation, installed
  qualification and the broader native/lifecycle/adoption gates remain open.
- Continue in the same source worktree from the reviewed contract. Claim the
  exact service/client/test boundary before code rather than finalizing the
  still-Active parent effort.

### 2026-10-23 - Collaboration contract continuation

- Resumed the existing open interaction slice under its public source claim,
  without opening another effort or taking transport/lifecycle source.
- The linked specification combines typed references and evidence with scoped
  recipient projections and durable reply attention. Plain conversation,
  stable uncertain requests and output/flush/cursor semantics remain intact.
- This is proposed realization of existing vision items. Review and merge
  precede code; no implementation, installed capability, native delivery or
  deployment result is asserted.

### 2026-10-22 - Preserve canonical worktree-root aliases

- A later review correctly found that lexical CWD lookup could reject a
  tracked checkout stored under a symlinked/junction root spelling. The new
  symlink-root regression failed before correction; a second regression also
  proved the old lookup chose one of multiple records for the same checkout.
- The metadata reader now matches canonical record paths only inside its
  selected project, refuses ambiguous matches and retains the strict exact
  record re-read/root check. Both negative controls failed as expected; the
  corrected owner compatibility selection passed186 cases. The ordinary
  inference/adoption helper and existing client wire contract are unchanged.

### 2026-10-22 - Corrected owner/client boundary qualified on Windows

- Candidate `71862f3961c36e2602e6d980ff2c65afb425be16` passed the unchanged
  native Windows120-second acceptance packet with291 passes, two declared
  POSIX skips and33 full-only cases deferred, in106.61 seconds. The complete
  600-second suite passed324 plus those same two skips in161.11 seconds.
  Both reports account for all326 nodes with complete execution and digest
  `a2cb2b3d53c353a4f9bcedff812a39bd2a2b57b231de58ba418f048b3cfecce7`.
- The expanded Worktrees record/identity/shape compatibility selection passed
  184 cases. A real-Git isolated integration then exercised the actual prepared
  owner CLI and noneditable installed Chat client: stable participant identity
  across startup sessions, the selected project's asserted revision7 rather than
  a colliding foreign record, explicit untracked/foreign refusal, no fallback,
  and unchanged fixture repository/registry/tracking bytes. The owner in this
  integration was prepared editable source, not an independent owner-wheel proof.
- Preserve the earlier acceptance timeout and five-second installed-reader
  startup failures. The reader-lock crash witness also exposed a Windows test
  defect: killing a venv redirector need not kill the lock-holding interpreter.
  The corrected witnesses use the canonical direct interpreter with preserved
  venv context; the lock witness asserts its actual PID and prefix. Ordinary
  installed console operations remain covered, and no threshold or node was
  removed or relaxed.
- Linux qualification was attempted in a fresh, non-root, read-only local
  Docker test container with bounded writable storage and no new agent.
  Normal dependency preparation failed on public artifact TLS handshakes.
  Exact authenticated, seasoned offline wheels were obtained, but the normal
  dependency owner correctly refused flat-index age admission; that guard was
  not bypassed. System-certificate TLS also failed. No Linux test ran and every
  owned container was removed. Current Linux qualification and the full parent
  Platforms/Portfolio items remain open, not silently reported as passing.
- The source correction grants no installation, service activation, Chat
  assignment, shared-feed registration, native listening or retained-state
  migration. Those parent obligations remain with their existing owners.

### 2026-10-22 - Finish the minimal owner dependency in the retained source

- The operator explicitly authorized the retained writer to finish the narrow
  Worktrees dependency and corrected Chat consumer before closing its source
  worktree, without spawning another contributor. This does not reassign
  construction/lifecycle, unrelated source, deployment or activation.
- Extended the existing record boundary with mutually exclusive `--cwd`.
  Filesystem project selection and scoped tracking lookup never enroll a
  checkout or ask the resident; strict re-read checks the exact path/project
  and derives label/head from the same record. Anchors retain the existing
  configured-identity reader. The ordinary exact-ID record output is unchanged.
- The Chat consumer now invokes that owner contract once, with no legacy
  inference, resident `list`, global head lookup or fallback. The focused
  owner suite passed147 cases, including seven new untracked, stopped-resident,
  cross-project collision, nested checkout, record-race and anchor cases.
  Complete corrected-client qualification and actual review remain required;
  these focused contracts alone do not close the compound Plan item.

### 2026-10-22 - Process utility source complete; consumer integration still open

- The assigned canonical process-utility contribution #7254 merged to `dev`
  as `f58ab97e973b7749954a574f2bc073daf1b0b2d2` at
  `2026-10-21T21:38:27Z`, from reviewed head
  `5ab983a229ce621273f338a49cb40b67e2385724`. The contributor's
  [completion evidence](https://github.com/ThomasMichon/copilot-extensions/issues/7238#issuecomment-6327209693)
  records current-head approval, resolved lifetime/setup findings and passing
  required CI.
- That source qualification reports 343 admitted units, 37 Linux API/regression
  cases, and 34 native Windows/WSL passes with nine declared POSIX skips.
  Fresh source-removed installed-wheel and negative-control evidence belongs to
  that utility contribution; it is not qualification of the current Chat draft.
- The proposal editor retains Chat consumer integration and the full
  Platforms/Portfolio items. Preserve the utility contributor's separate #7020
  ownership. No utility merge implies a release, runtime update, native Chat
  adoption or service lifecycle completion.
- Corrected the current workspace summary and seam table to reflect the
  confirmed owner-contract defects rather than advertise the draft's observed
  tuple as a safe read-only boundary. The existing workspace owner retains the
  required CWD/project-head contract; do not qualify stale snapshots or replace
  that authority. The effort remains Active.

### 2026-10-21 - Review exposed missing non-enrolling owner resolution

- Review of #7553 found that the proposed owner calls do not establish the
  promised read-only boundary: `get worktree-dir` can adopt an untracked linked
  checkout, cache-only `list` can activate its resident, and project-agnostic
  `head-session` can choose another project's colliding ID. Source tracing
  confirmed all three. The earlier genuine already-tracked/running-owner
  smoke is not evidence for these negative states.
- Keep #7553 draft while the substantive corrections are being built, with
  no merge, native adoption or platform waiver. Existing qualification retains
  any finite attempt already running, then stops stale-head starts and retries.
  The published snapshot's acceptance run failed its unchanged five-second
  installed-reader startup assertion: 280 passes, two exact POSIX skips and
  one failure, in 90.18 seconds; whole preparation/cleanup command 123.996
  seconds. Its distinct full run passed 314 plus those two skips in 258.46
  seconds, 265.334 whole. That full pass does not erase the acceptance failure
  or the owner-contract defects.
- Normalize malformed owner JSON, including `ChatError`, to the client's
  structured response error. Check anchor provenance is a string before set
  membership so arrays/objects cannot escape as an unhandled `TypeError`.
  Ten focused malformed-response/type regressions pass through the existing
  environment lease, preparation and containment helpers. They are not a
  replacement for platform or negative-state owner qualification.
- The existing Worktrees owner must identify or supply a supported
  non-enrolling, non-activating CWD lookup with project-scoped asserted-head
  semantics. Exact `record --worktree-id` reads are appropriately scoped and
  non-activating but require an already known ID; the claims ownership snapshot
  reports resource claims, not CWD resolution. No private YAML parsing,
  Git-derived ID, sibling-runtime import, registry, accidental resident-routing
  flag bypass or owner-source takeover is an acceptable substitute.
- Retain the owner-boundary dependency explicitly with the existing workspace
  owner. This request grants no shared-family thaw, new source lease,
  installation/update, release or host operation. Inbox/source assignment,
  shared notification intake, governed setup and the parent effort remain open.

### 2026-10-21 - Optional trusted workspace resolver

- Added the optional installed client's `workspace --request-file -` boundary
  and callable resolver. Trusted initialization session/CWD is consumed without
  an environment-session comparison, foreground resnapshot, workspace registry,
  head mutation, enrollment or installer. Explicit standalone actors remain
  independent of this operation and Worktrees.
- Real managed worktrees retain their qualified owner and stable participant
  tuple across startup sessions. Friendly codenames are separate labels.
  Exact cached tracking rows, versioned execution tuples and existing
  `head-session` observations are checked without assigning succession.
  Anchors are segmented by startup session and require the owner's configured
  execution-identity contract; no fictitious worktree head is queried.
- An actual installed-client/selected-owner check exposed an unnecessary
  requirement for that newer anchor capability on ordinary worktrees. Removed
  that requirement in the client: existing qualified owner-ref, exact tracking
  row/path and head remain the tracked-worktree authority. The corrected
  installed client then resolved a real worktree from trusted initialization
  supplied while running in a different directory. The existing selected
  owner was not changed. Live anchor qualification remains unavailable on an
  owner without the configured-identity capability, not a simulated success.
- Preserved all 267 prior parameter nodes and lane assignments, adding 49
  workspace/catalog cases. The current 316-node membership digest is
  `03c115567e6bac5ab021f7608ab687f7d52d30d40fb9d2d192852a3665000709`.
  Current native Windows acceptance passed 281 tests plus the two exact
  POSIX-only skips, with 33 deferred to full, in 102.98 seconds under the
  unchanged 120-second contained bound. Full passed 314 plus those two skips
  in 144.48 seconds under 600. These are pytest timings, not a claim about
  dependency preparation or complete command duration.
- The fresh source-removed installed fixture exercised the real isolated
  client, selected finite owner processes, trusted CWD and ambient-session
  independence. Canonical caller ownership, separate capture bounds and one
  total resolver deadline remain in force. Catalog RECORD evidence includes
  the new workspace module; the default core acquires no dependency.
- Retain the earlier development attempt's five-second installed-reader
  synchronization failure and 120-second termination. A separate earlier
  308-node full pass and the intermediate installed-owner refusal are not
  current-source platform proof. Source-bound JUnit timing reports and exact
  membership maps are retained locally; current-source native Linux
  qualification is still required. No retry-for-speed, skipped family,
  relaxed deadline or new CI lane is introduced.
- Every workspace result explicitly reports Chat assignment unavailable,
  with no actor/inbox/source assignment or native-listening claim. The existing
  local service/Bridge owner retains that binding and shared feed registration,
  generation, finite callback, cancellation and quiescence. Governed setup,
  selected-release adoption and all remaining parent Plan/Validation items
  stay open.

### 2026-10-21 - Local endpoint consumer merged; workspace binding next

- #7517 actually merged to `dev` as
  `5da08bb25ffeeaed44871c7c7c61b18adb113d48` at06:49:56Z from qualified
  `ba8e95252e04ca01e046239fdc2a44ec3a298e74`. Current-head review had no
  open issues, both original threads were resolved and required checks passed.
  The normal managed merge reported no review-gate bypass. The merged Chat,
  three canonical libraries and standalone runner/containment/registry closure
  are byte-identical to that qualified source.
- All four current-head native reports retain the same267-node map and digest
  `4ad9a7e53a10031216bc13144d336262613f2ca6d478bddbc802aad6afcb8d88`,
  including all260 previous IDs/lane assignments and exactly seven new refusal
  parameters. Actual execution is complete with zero missing, unexpected,
  rejected or duplicate nodes and zero test/final exit codes.

| Native platform | Acceptance120: pytest / whole-command seconds | Full600: pytest / whole-command seconds |
| --- | --- | --- |
| Linux | 234 passed, no skips,33 deferred;82.41 /135.793 including preparation/cleanup | 267 passed, no skips;88.05 /97.081 |
| Independent Windows, once only | 232 passed, exactly two declared POSIX skips,33 deferred;49.17 /65.844 including preparation/cleanup | 265 passed, the same two skips;74.34 /77.219 |

- Linux's120-second **contained** allowance passed; its135.793 whole exceeds120
  and is not an end-to-end-under120 claim. Neither platform changed a selector,
  guard or deadline, and no retry chased a faster sample. Native Unix executes
  on Linux; the only Windows skips are the named POSIX private-permission and
  Unix-client cases. The actual installed stale publisher, valid alternate,
  restart/rebind, pending/viewer recovery and lifetime witnesses pass.
- The optional external Windows archive observer was unavailable; raw Windows
  fixture-wheel SHA values were not captured before ordinary cleanup. Linux
  hashes are not substitutes or authenticated release evidence. Actual Windows
  source/installed RECORD/module/three-role assertions and final reports/logs
  are retained; Linux's collector captured its actual four wheels in each lane.
  Source-host Windows failures remain failures, not retroactively fixed by a
  different host's pass or an unproved overload explanation.
- Re-read both canonical Plan/Validation sets after merge. Only endpoint
  consumption is complete: standard service/plugin setup and selection,
  shared notification intake, workspace/inbox assignment, native adoption,
  migration and the full parent remain open. The next owned client slice is
  optional trusted host-init workspace binding. The existing local service/
  Bridge owner still owes authoritative Chat inbox/source assignment and the
  shared-feed registration/generation/cancellation/quiescence binding; direct
  message receipt projection is not either API. No replacement registry,
  supervisor or ownership transfer is implied.

### 2026-10-21 - Review correction: refuse stale or incomplete advertisements

- Current-head review of #7517 identified two real endpoint-boundary defects:
  publisher liveness was not checked before alternate selection, and a TCP
  record without its port could default to port80. Six new primary/alternate
  refusal cases reproduced both defects before the fix:11 passes and six
  failures, each reaching the forbidden connection constructor.
- Reuse canonical `is_stale` on the original advertisement, before `usable`
  can select an alternate without its PID. Require an explicit valid TCP
  port before constructing the connection. Bound the untrusted PID field and
  refuse native integer overflow; no replacement PID/lifecycle framework,
  implicit route, network probe or replay was added.
- Preserve all260 previous nodes and add seven refusal parameters, including
  PID overflow:267 complete,234 acceptance and the same33 full-only. The
  existing live TCP case also exercises an actual supported alternate, and the
  installed host case now restores a genuinely dead publisher's advertisement
  and requires explicit refusal before real restart/rebind and pending recovery.
- An initial focused post-edit run still imported the older non-editable
  package in the managed test environment. Its seven failures remain recorded
  as stale-package diagnostics, not validation of the edited runtime. Normal
  leased preparation rebuilt the owned package; all31 focused cases passed
  with one owning-platform skip in70.40 seconds, including the source-removed
  installed dead-publisher/restart contract.
- The earlier exact `b4dc805db0f3ab3f74152fc95f5da6299834baf5` passed both
  native platforms' normal lanes, but these review corrections require a new
  immutable candidate, both normal120/600 lanes, the existing native qualifier
  and a current-head review. Previous candidate results do not waive them or
  complete selected setup, deployment, migration or the parent effort.

### 2026-10-21 - Local endpoint membership and retained qualification failures

- The local endpoint leaf retains every one of the qualified client's 242
  nodes and adds 18 explicit local-rendezvous nodes: 260 complete, 227
  acceptance and the same 33 full-only nodes. Both lanes retain the exact
  actual-execution guard. Windows may skip only the two named POSIX
  permissions/Unix-host cases; their owning platform still requires execution.
- Candidate `a3f6c6a90` did not qualify on native Windows. The normal
  120-second acceptance runner returned 124 with execution still pending;
  the whole command including preparation took 148.374 seconds. Its complete
  600-second lane finished with 257 passes, two owning-platform skips and
  one failure in 246.46 pytest seconds. Both maps retain membership digest
  `bd55afd32a889d1034e744858a89a01af5cbadea21cdb6c7b6516b9f44db8ae0`.
- The failing installed-console death witness did not enter its held-read
  barrier within the existing five seconds. A contained focused diagnostic
  reproduced the failure with the console alive and neither provider nor
  descendant ready: 23 passes, one skip and one failure in 39.91 seconds.
  Added test-only entry/readiness diagnostics without changing the five-second
  startup bound, three-second retirement bound, birth-pinned ancestry or
  required observation before outer cleanup. A distinct stage diagnostic then
  passed that actual installed witness in 29.68 seconds; this is neither a
  root-cause fix nor a passing acceptance/full qualification. All failed
  attempts remain evidence, not samples to discard after a retry.
- Source-only publication supplies an immutable candidate for the existing
  native qualifier. It does not open a ready PR, satisfy either lane, authorize
  deployment or change the already-qualified predecessor's evidence.
  Managed service/plugin/Manager setup, assignment, migration, native matrix
  and the parent effort remain open.

### 2026-10-21 - Qualified installed client merged; local consumption next

- Original #7266 actually merged at
  `1538d76d8dbbdf0470344118d2878eafce21f477` from exact qualified
  `da79da43f140886ca4c5f2b374b5dec90391577a`. Actual current-head
  review5594420511 had no open issues, required CI passed and every review
  thread was resolved before the normal managed merge; no review/CI bypass
  or extra approval ceremony was used.
- Independent native Linux ran both normal lanes on that same candidate:
  acceptance209 passing calls/zero skips/33 deferred75.63s pytest under120;
  complete242 passing calls/zero skips83.60s pytest/95.444s whole under600.
  Actual execution was complete with no missing/extra/rejected/duplicate
  outcomes, and both maps matched the declared242-node050845af membership.
  Installed caller/provider/leaf birth pins retired before observer cleanup
  and the held watchdog, with normal lock/body recovery; source removal,
  POSIX private retained posts and native Unix-host cases also passed.
- Timing scope remains literal: acceptance with environment preparation and
  cleanup took138.887s whole. That is **not** an end-to-end-under120s result.
  The unchanged standalone timeout bounds the contained test phase, not
  preceding preparation; the first-touch artifact fixtures remain inside
  that phase. No retry chased a faster whole time or moved fixture work
  outside its budget. Prior failed attempts remain failures.
- Re-read the complete parent Plan/Validation after merge. The client and
  foreground artifact slice is qualified, not a released/selected installation,
  live-agent/JWT proof, full plugin/native adoption or managed lifecycle.
  #7238 stays Active. The same writer's already-claimed local-endpoint leaf
  now builds on the merged predecessor; it retains the qualified execution
  guards, native death witness and all historical journals. Its new runtime/
  installed tests need their own membership and platform qualification.
- Continue standard selection/setup, host-init workspace/inbox assignment,
  shared notification multiplexing, typed collaboration and normal lifecycle
  with their existing owners. No competing installer/Bridge/library source,
  migration, role, peer restart or live deployment is authorized by this merge.

### 2026-10-21 - Qualification requires execution, not only collection

- Reproduced the subsequent932 finding4332340681: inherited fixture-listing
  mode returned zero after collecting209 of242 nodes without executing them.
  The collected map was not execution proof; that result is retained as a
  demonstrated false success, not qualified coverage.
- Reject both fixture-listing options explicitly. In addition, session
  completion now reconciles actual passed-call reports and the one declared
  Windows/POSIX-permission setup skip against the exact selected node set.
  Missing/unexpected/duplicate reports, failing phases, unexpected skips and
  xfail outcomes cannot establish completion. The early map stays pending
  until execution is reconciled; a zero result without completed execution
  refuses with or without external retention. Existing nonzero outcomes stay
  unchanged. This closes the no-execution class rather than relying only on
  an ever-growing list of pytest options.
- Pure execution cases cover both lane selections, no calls, missing/extra/
  duplicate calls, bad phases, unexpected skips, xfail and the explicit native
  platform skip. The combined runner contracts passed119 with one platform
  skip, including collection-only and malformed execution data in both export
  modes. Real fixture-listing tests cover both options, both lanes and both
  retention choices: all eight refused with4, preserving both exit codes in
  requested reports. No source assertion, operation deadline or budget changed.
- Normal Windows acceptance without export passed208 with one POSIX skip/
  33 deferred in106.65s/120; complete with retained proof passed241 with the
  same skip in99.86s/600. Both collect242 with the unchanged050845af map and
  explicit complete execution. All236 original nodes/six guards remain.
  The replacement source still needs its own native Linux lanes and current
  review/CI; previous932/271 results are not new-head credit.

### 2026-10-21 - Membership validation is mandatory, retention is optional

- Addressed the subsequent current-head finding4332180223: normal commands
  without an export flag now perform the same mandatory membership validation.
  An early zero exit before collection cannot qualify merely because no
  external report was requested. External retention alone remains optional;
  original nonzero containment results are still preserved.
- Expanded the missing/collected/partial/wrong-type map matrix across both
  retained and non-retained modes, including default Chat selection. Updated
  the synthetic alias consumer to supply its real collection map rather than
  bypass the new contract. Combined runner contracts passed107 with one native
  platform skip. Real inherited pytest help without an export flag returned
  the required nonzero refusal. A version-option diagnostic instead executed
  tests and reached124; that invalid early-exit probe is not claimed as a
  passing help/no-collection test or used to alter pytest semantics.
- Both documented normal Windows commands then passed with no export flag,
  filters or diagnostic options: acceptance208 passed/one POSIX skip/33
  deselected109.20s/120; complete241 passed/the same skip118.46s/600.
  Each collected242 nodes with the unchanged050845af membership digest.
  The full original236-node set and all six new guards remain selected in
  complete qualification. No runtime, utility, operation deadline or other
  consumer default changed. Independent native qualification and review/CI
  must still be bound to the replacement immutable source.

### 2026-10-21 - Preserve failure evidence and bound fixture command ownership

- Addressed the current optional-report finding at its failure boundary.
  Membership is atomically persisted after complete validated collection, not
  deferred until terminal summary. Timeout before summary retains that map;
  incomplete/unreadable collection exports explicit unavailable membership.
  The original nonzero containment result survives reporting failure, and
  missing membership after a zero test exit refuses qualification. Reports
  distinguish test and final runner exit codes and never overwrite old files.
- Thirteen added runner regressions cover timeout124 with a collected map but
  no terminal summary, pre-collection failure, missing/partial/wrong-type maps,
  zero-exit refusal and failed export. The combined runner contracts passed95
  with one platform skip. A real five-second pre-collection timeout exported
  unavailable membership with the original124, not an invented zero count.
  A later actual120s timeout retained available validated membership and124.
- Follow-up Windows runs exposed fixture subprocess stalls; those failures
  remain failures. A native stack dump found parallel fresh-role preparation
  and a CLI invocation waiting in `subprocess.run` pipe-reader joins. Focused
  and complete diagnostics passed between failures, so they were not used to
  relabel the failed runs or infer failed case names from progress dots.
- Bounded artifact/CLI test commands now use the published caller-owned API,
  without a utility fork or new lifecycle owner. A finite child adapter
  supplies the artifact CWD/PYTHONPATH behavior that API does not expose;
  explicit inherited streams preserve input/output and avoid another capture
  layer. The initial Windows exec-overlay adapter returned before its target,
  and its first waiting adapter failed to forward stdin; both failed
  regressions were corrected, not hidden. A real CWD/stdin/environment
  contract now protects that path. Existing20/30s helper limits,5s readiness,
  10s hold and the direct death/pinned-before-cleanup witness remain unchanged.
- Immediate failed-phase evidence is flushed before a possible outer timeout.
  All236 original exact parameter nodes remain, with six qualification/helper
  guards. Final normal Windows acceptance:208 passed, one native POSIX skip,
  33 deselected,85.88s/120. Complete:241 passed, the same one skip,118.62s/600.
  Both maps collect242 with digest
  `050845afbf586fc645f7e5a113102014e2cb2324beeb6ae72f951060398e917e`.
  The new source still requires its own independent native Linux lanes and
  current-head review/CI; historical460 qualification is not final-head credit.
- Separately, the accepted conduct-only slice merged through #7464 after the
  reviewed #7454 intent. Its87 passing contracts,21 new decision/delivery
  cases and two explicit producer-platform skips prove declarative policy
  and fresh-adopter delivery, not model behavior or a deployed grant API.
  Source ownership was normally finalized; broader collaboration, native
  assignment/multiplexing, lifecycle, migration and adoption remain Active.

### 2026-10-20 - Coherent collaboration and peer-conduct amendment

- The amendment merged through #7454 at
  `406e7731e2dac161abebd2a886835e98c00ad7ee`, with an exact-head approving
  review and required CI. Its intent gate is satisfied; the accepted
  conduct-only contributor can realize that scope through normal review.
  This is not runtime adoption or completion of the parent effort.
- Integrated the independently gathered interaction and authority lessons as
  one generic intent revision, not a port of another controller or private
  implementation. Existing Chat state remains authoritative; typed views,
  collective decision links and scoped authority are proposed contracts.
- Recorded the already-claimed conduct-only contributor boundary and its
  reviewed-intent dependency. Policy realization follows this amendment's
  normal review gate; unrelated client/native and lifecycle work stays separate.
- Retained the portable/adopter boundary, standard installation and Worktree
  Manager baseline, and existing Gateway/SSH, Vault and lifecycle owners.
  No runtime API, new process, deployment or permission grant is supplied.

### 2026-10-20 - Explicit acceptance and complete-suite qualification

- The qualification-contract decision selected a120s real acceptance packet
  plus the mandatory complete600s suite, retaining all coverage. This changes
  future qualification, not the failed published295/120s result or required CI.
  No production deadline or process/fixture synchronization bound changed.
- Added a checked family/parameter-count map. New, missing or duplicate nodes,
  unassigned families and lost parameter cases refuse qualification. Comparing
  both actual maps to the prior full packet proves all236 original exact
  parameter nodes remain, with five new membership guards. Acceptance selects
  208 of241 nodes; the full lane executes all241. Collection/setup-only
  execution refuses rather than returning a success-shaped qualification.
  Host-only lifecycle families
  are deferred from acceptance, not deleted, skipped by policy or omitted from
  the complete lane. Optional exclusive report output preserves the map and
  actual exit code after normal sandbox cleanup.
- The installed-console death case now observes its installed Chat provider,
  sleeping descendant and console ancestry through the canonical utility's
  test-only birth-pinned Windows handle/Linux pidfd observer. All are alive
  before the fault and must be gone before external cleanup. Windows venv
  launcher ancestry is retained rather than mistaken for the provider itself.
  Real join, held read, reader-lock recovery and unread-body recovery remain.
  The additional process-observer dependency is development-only.
- Final Windows normal-run results: acceptance207 passed, one explicit native
  POSIX skip and33 full-only deselections in71.37s/120; complete240 passed and
  the same one POSIX skip in137.12s/600. Both collected241 nodes with membership
  digest `074d0284d500d105ffecd67dfc9ae6146f7af50d413edbbdbd0b93c5f81fd5cc`.
  Positive native Linux execution remains required; Windows skip evidence
  does not satisfy it.
- Shared runner contracts passed82 with one native POSIX skip. Their existing
  obsolete discovery-job assertion was updated for the actual unit-only
  source CI, retaining the historical smoke/full helper separately. New lane
  tests and that CI contract were extracted to keep the old module shrinking.
  No workflow, other consumer's defaults, preparation lease, containment or
  source-unit-only CI behavior changed. F/E9 checks pass.
- Validation tiers: changed pure contracts and the real installed/native
  Windows lane passed; fresh offline role installs and source removal exercise
  the clean first-touch boundary. Independent native Linux qualification is
  assigned to the existing qualification contributor on the next immutable
  source pin; no substitute worker, deployment or external venue is implied.
  Current-head review and required CI remain merge gates. The full graduation
  effort, standard setup/lifecycle, assignment/multiplexing and adoption stay open.

### 2026-10-20 - Literal platform result and measured portfolio follow-through

- Independent native Linux qualification executed the exact published295
  candidate through the normal standalone runner, with preparation and the
  explicit120s limit, without filters or source changes. It emitted28 progress
  dots and exited124 at the limit. Completed counts and skips are unknown;
  prepared role resources are not a positive installed-consumer/lifetime or
  Unix-host result. Canonical scoped cleanup completed, but observer cleanup
  is not credited as a passing lifetime witness. No unchanged retry or merge
  is authorized by that result.
- The next test-only source packet retains the230 cases and adds six cheap
  exact CLI-control checks. Independent wheel builds and three separate fresh
  role installations use bounded parallel builders, without sharing installed
  environments. Source fixtures omit unrelated test trees, not package inputs.
  One real source-removed installed console/catalog exchange now covers the
  producer, independent reader, ordinary forward reading and the fixed-route
  stdio-to-HTTP provider. Core wheel/offline catalog qualification remains
  separate; real credential/isolated-HTTP, death, shadow, lost-response,
  deadline and raw-output cases are retained rather than replaced by parser
  mocks. Member-page validation remains in its client/store/HTTP contracts.
- A premature removal of the installed join before the console-death fault
  exposed a real five-second startup-barrier failure. Restored that genuine
  precondition; no synchronization or held-response bound was increased.
  Actual death, lock reacquisition and recovery still occur before the
  observer closes its Job and before the held-response watchdog expires.
- An intermediate complete236-case packet passed235 with one native POSIX
  skip in139.39s under the existing600s evaluation allowance. Later unchanged
  120s attempts still timed out; that is not an acceptance pass. The final
  changed-path packet (`test_cli or test_distribution`, also matching client
  unit contracts) passed65 with one explicit POSIX skip in88.93s/120, with
  170 cases deselected. Its installed exchange cost10.21s, shared artifact
  setup6.43s, death call12.93s, lost-response call7.86s and installed CWD
  shadow call8.83s. Shared setup is not charged only to its first user.
  These are distinct measurements, not a current full-matrix120s claim.
- The retained recommendation is a bounded native/installed/process contract
  packet plus the exhaustive on-demand portfolio. It has not replaced the
  explicit unfiltered qualification contract, changed CI/default selection or
  waived any gate. The qualification coordinator retains that concrete
  acceptance-contract decision; the source writer retains fixes and review.
  The full parent, normal selection/setup, shared assignment/multiplexing,
  independent lifecycle and actual adoption remain Active.

### 2026-10-20 - Isolated local endpoint consumer

- Added explicit `endpoint_dir` configuration as an alternative to an origin,
  not ambient discovery or a different installer. The installed client reads
  the canonical bounded local record inside its existing finite isolated HTTP
  worker, uses a directly owned native Unix socket or advertised loopback
  endpoint, and refuses unsupported/invalid discovery without fallback.
- The logical run-directory namespace preserves pending requests and viewer
  state across endpoint rebinds. The existing credential provider remains
  explicit; the client does not read the host's key file or silently migrate
  origin-based pending state. Core dependencies remain empty; the client role
  explicitly adds the small canonical rendezvous library.
- Reused the existing fresh installed host fixture for two actual installed
  clients. After source removal, normal reading advances only its viewer;
  an unavailable advertisement leaves the original post pending. A real host
  crash/restart republishes discovery, preserves the reader position and pending
  namespace, and permits only explicit original-ID recovery. No second
  artifact-build matrix, live deployment or observer-manufactured cleanup
  witness was added.
- The first focused Windows packet passed62 with one explicit native Unix skip
  in74.30s/120. The earlier full248-case evaluation passed246 with two native
  POSIX skips in183.93s/600; it predates the subsequent shared test-portfolio
  restructuring and is not credited to a changed full packet. The final
  focused local-endpoint/transport/installed-host packet passed62 with one
  explicit native Unix skip and191 deselected in68.33s/120. The real installed
  host/client crash/rebind call cost45.43s, shared artifact setup7.46s and
  actual local TCP consumption6.75s. This focused result is not full120s or
  native POSIX qualification.
- This next slice remains separate from the original client's qualification
  pin. Reconcile that predecessor before publishing it; new source needs its
  own explicit platform pin and relevant execution. Standard lifecycle,
  connection-plugin ownership, recipient assignment/multiplexing and full
  parent completion remain unchanged.

### 2026-10-20 - Review correction: private retained post creation

- Addressed the current review's retained-body permission finding in the
  client, not a utility fork. New reader namespaces and pending directories
  use POSIX mode0700; each atomic temporary is created exclusively with
  mode0600 before writing and keeps that mode after replacement. Existing
  directory permissions are not silently migrated, and Windows account ACLs
  remain the owned state-root prerequisite.
- The creation-order regression first failed against the prior client, then
  passed with real retained uncertain-request content and the actual atomic
  replacement. It costs0.01s in the Windows evaluation. A separate native
  POSIX subprocess sets its own umask022 and checks namespace/directory/file
  modes and retained content; it is explicitly skipped on Windows, not
  credited as native evidence or implemented by changing pytest's process-wide
  umask.
- The first current full230-case runs exhausted120s, with no functional
  failure observed; the verbose diagnostic reached the installed artifact
  test after the client contracts. Under the operator's bounded600s evaluation
  allowance,229 passed and the one native POSIX permission case was explicitly
  skipped in153.824s. The broad CLI flow cost23.71s, source-independent artifact
  22.06s and shared first-use role setup22.24s. This is not a current120s pass,
  a permanent gate change or proof that pruning these valuable process
  boundaries is safe.
- Tightened the existing installed-console-death witness so lock reacquisition
  must occur before the unchanged10s held-response watchdog expires. A slower
  run cannot gain false cleanup credit from fixture timeout. No production
  deadline, test synchronization allowance or containment boundary is widened.
  With that explicit witness, the full matrix again passed229 with the one
  native POSIX permission case skipped in170.250s under600s. The complete
  console-death call was11.66s including its normal join/setup; the direct
  monotonic assertion proves the actual held-response cleanup witness
  preceded watchdog expiry rather than inferring it from total call time.
  F/E9 checks pass. Positive native Linux qualification, current-head review
  and required CI remain required before merge; no120s pass is claimed.

### 2026-10-20 - Released caller-owned API consumed by the retained client

- Integrated the actually published #7372 `run_caller_owned` API in the existing
  client transport, without changing the canonical library or its other
  consumers. RPC, HTTP-worker and credential stdout have separate raw-byte
  budgets; stderr has its own 64-KiB budget. Overflow refuses after canonical
  cleanup, without partial output or replay. `OwnedCleanupError` propagates
  unchanged with exact process/Job custody rather than becoming an ordinary
  recoverable provider failure.
- Removed the blanket native POSIX refusal, not the separate cross-OS refusal.
  Missing capability still refuses before the legacy helper or target can run.
  The finite HTTP worker remains isolated and receives credentials on stdin,
  with the same total operation deadline and no automatic retry. Native Linux
  prerequisites and cleanup remain owned by the canonical utility.
- Six new cases and the renamed missing-capability control are assessed below.
  Call times are the terminal report's hundredth-second precision; `0.00` is
  below that resolution, not zero cost. Keep these inexpensive contract guards
  with transport changes rather than counting them as redundant cases.

| Case/family | Cases | Windows call time | Value and distinct evidence |
| --- | ---: | ---: | --- |
| Separate capture budgets and stdin | 1 | 0.00s | Exact canonical API forwarding; small mocked contract, not native lifetime proof |
| Real provider overflow | 2 | 0.12s each | Real stdout and stderr overflow must beat a blocked provider's deadline, without exposing content |
| Exact unconfirmed-cleanup custody | 1 | 0.00s | The same typed exception/process/Job must escape, not be discarded or retried |
| Credential-specific raw budget | 1 | 0.00s | Credential and internal HTTP capture limits differ; isolated worker and stdin-only credential preserved |
| Source-removed installed transport | 1 | 1.61s | Actual offline wheel interpreter exercises UTF-8 stdin, separate streams, both overflows, raw CRLF-before-normalization and strict UTF-8 refusal |
| Renamed missing-capability control | 1 existing | 0.00s | An older helper refuses before legacy execution; not positive Linux evidence |

- The full current Windows matrix, including the broad CLI integration case and
  fresh core/client/service role wheels after fixture source removal, passed
  **228 tests in111.620s** (terminal111.71s) under the unchanged120s supervisor,
  with no failures, errors or skips. The installed-console-death call was5.22s,
  below its existing10s hold watchdog. Shared first-use wheel/role setup was
  16.97s; do not charge it solely to that case.
- The earlier222-case CSV and42/221-case candidate measurements remain a
  historical pre-consumption comparison, not timings or complete selectors for
  this new matrix. Neither candidate was redefined, enrolled in CI or credited
  as current full acceptance. No test was deleted or process deadline widened.
- Qualification tiers: changed-unit contracts passed; real CLI/provider/HTTP
  and source-removed offline-wheel checks passed on Windows. Fresh temporary
  installations prove the artifact path, not a managed machine installer.
  Positive native Linux installed-consumer qualification is still pending with
  the existing platform owner; no alternative venue or new worker was assumed.
  Live external deployment was not performed: this is a source-only candidate,
  not authority to alter selected clients, credentials or services. Review,
  required CI and the positive platform gate still precede merge. The full
  parent, standard lifecycle and public native-plugin adoption remain open.

### 2026-10-20 - Per-test value and smaller local portfolio evaluation

- At the operator's direction, evaluated the entire expanded portfolio with the
  existing 600-second allowance rather than deleting cases to force it into
  120 seconds. All222 cases passed in126.610s, without failures, errors or skips.
  Every case has call/setup/teardown timing and a source-grounded value/risk
  assessment in [the per-test matrix](test-portfolio.csv), covering80 families.
  This is the pre-#7372-consumer Windows candidate: base443f, component input
  digest `cddc75b0c8f420c48358e2b0daf6bb4c1794798856496aaee9b25165a8b4d43f`.
  Inputs were verified unchanged across the measured runs.
- Priorities describe contract importance and distinct evidence, not measured
  line coverage or automatic CI eligibility. `P0` protects durable progress,
  uncertainty, custody, execution lifetime, resource limits or actual artifacts.
  `P1` remains worthwhile validation/diagnostics or broader integration.
  Zero displayed call time means below report resolution, not free execution.
  JUnit call times retain millisecond precision; terminal setup/teardown values
  are rounded. The11.10s first-use setup includes shared actual wheel/role
  environments and is not a cost unique to the console-death test.

| Evaluation | Cases | JUnit suite elapsed | Meaning |
| --- | ---: | ---: | --- |
| Complete matrix | 222 | 126.610s | All cases, evaluated with600s; not a120s full-matrix pass |
| Focused canary candidate | 42 | 75.976s | Selected hard boundaries and known regressions; incomplete contract coverage |
| Broad confidence candidate | 221 | 84.022s | All but the broad normal CLI integration flow; complete expected node set verified |

- Prefer the221-case confidence candidate for ordinary fast feedback over
  aggressively pruning cheap contracts: removing179 more cases bought only
  8.046s in these separate runs. These are observations, not a controlled causal
  speedup or latency guarantee. The31.231s broad normal CLI test alone consumed
  about a quarter of the complete run; it is high-value but overlaps the
  installed client, store and host checks. Keep it in the full matrix and run it
  for CLI, transport, actor/identity or end-to-end fixture changes. No test was
  deleted, no default selector or required CI packet changed, and neither
  smaller candidate is credited as full222 acceptance.
- The42-case candidate is only an early canary. Both candidate runs used the
  unchanged120s supervisor and were checked against their exact expected node
  sets, not merely a passing count. Their matrix flags are explicit proposals,
  not new release/adoption gates. The complete source remains the full suite.
- Source review also records where names can overstate evidence: the short-key
  test does not yet exercise its named non-ASCII variant; duplicate-host refusal
  needs a constructor witness to assert before-store-open ordering directly;
  and console-death proof should explicitly reject expiry of the held-response
  watchdog. The complete run's5.309s console-death call was shorter than that
  10s watchdog, but slower future runs must not gain a false cleanup witness.
  Negative POSIX/cross-OS controls are not positive platform qualification.
- The canonical lifetime/stream API is now actually published by #7372 and
  released containing the same API in immutable Main
  `d018b57f094ae068fbc9d58edd9a86e077d29607`; its `owned_process.py` blob
  `b192016355e7d71210a828134a9d1255f59ea05b` matches the reviewed Dev merge.
  The next retained consumer step is actual library integration and positive
  installed/platform qualification, not another availability/consent loop or
  utility-source takeover. This timing baseline does not prove that consumption,
  native POSIX Chat, managed installation or public native-plugin adoption.

### 2026-10-20 - Merged host integrated with the retained client

- Own #7361 actually merged to `dev` as
  `443fa1fce46daf06e9ef5e645ae32f997b4f71ac` at09:17:50Z after current-head
  Copilot review had no Medium/High findings and all required checks passed.
  All three concrete review defects were reproduced and corrected; the final
  refreshed native Windows host portfolio passed111 contracts in48.74s under
  the unchanged120-second bound. Native POSIX readiness, managed lifecycle and
  live activation were not inferred from that source merge.
- Re-read the canonical Plan and Validation Plan after merge; the full parent
  stays Active. The completed sessionless host child was finalized normally,
  settling only its own parent resource. The retained client is the next source
  stretch, not another proposal, connection-plugin writer or installer.
- Preserve the complete previously published client tip in an explicit local
  backup. One three-way squash integration onto fresh `dev` reconciles the
  owned host/client documentation and role metadata without raw reset,
  amendment, stash, force push or repeatedly resolving eight historical commits.
  The actual original PR remains the only client publication target.
- The new host module must join the client's explicit installed RECORD closure,
  and the client must expose the core's opt-in bounded membership pages.
  Measure the actual combined installed portfolio under the unchanged budget.
  The canonical #7341 capability was initially unavailable. Its later actual
  #7372 publication and containing Main release now permit consumer integration;
  an agreed signature or unrelated Windows source merge alone never cleared it.

### 2026-10-20 - Independent local host artifact

- Added an explicit foreground `service` role to the existing distribution,
  consuming canonical single-instance and endpoint-rendezvous libraries without
  adding dependencies to the callable core. The role does not depend on the
  held client transport or a process-provider helper.
- The host serves authenticated bounded RPC on a private Unix socket, or the
  explicitly documented temporary OS-assigned Windows loopback fallback.
  Its existing store drain fence wakes held requests; the embedding lifecycle
  owner retains process supervision, selection, update and cutover.
- Actual native Windows qualification covers fresh offline wheels with fixture
  sources removed, default-core refusal without service extras, installed
  isolated host invocation and independently invoked participant processes.
  It exercises authentication, repeated reads before cursor CAS, deduplication,
  confirmed fixture-Job crash, restart persistence and leave, before outer
  test cleanup. Direct contracts exercise absolute header/body deadlines,
  aggregate header and worker admission bounds, health during several held
  waits and bounded drain.
- The initial Windows run exposed a fixture assumption: the venv redirector's
  PID is not necessarily the serving Python PID. Readiness now uses the actual
  published generation, and the canonical strictly contained fixture Job owns
  and positively retires the full host tree. Two protocol/socket assertions
  were also corrected against the real existing wire/platform behavior.
- The exact refreshed full Windows portfolio passed 102 tests in 35.19 seconds
  under the unchanged 120-second contained budget. Native POSIX execution is
  not available in this local Windows venue; Unix-socket source/tests remain
  unqualified until the existing native qualification owner exercises that
  platform. Synthetic installed participants are not a live-adopter exchange.
- Current-head review found that a timed drain failure could release singleton
  custody while an admitted transaction was still writable. A real held HTTP
  transaction reproduced that failure. Close now waits for the storage
  transaction boundary even when drain times out, retires workers and storage
  before custody release, and still raises the original drain failure.
  The focused case passes with real competing-host refusal during cleanup and
  preserved committed data after replacement. The refreshed full Windows
  portfolio passes 103 tests in 51.25 seconds under the same 120-second bound.
- The next actual review reported two additional Medium findings in its body,
  despite having no new inline threads: busy health disconnected instead of
  returning structured 503, and a valid full 256-member channel with 4096-byte
  metadata exceeded the wire response limit. Both reproduced against real HTTP.
  Health now maps the core error and recovers after transaction release.
  Explicit `members limit/after` pages retrieve every record within the wire
  bound; the callable core's ordinary full response and normal-sized legacy
  HTTP replies remain unchanged. Oversized unpaged HTTP calls now refuse with
  actionable capacity/409 rather than 500. The exact 256-member fixture,
  257th-member refusal, paging controls and full metadata preservation pass.
  Eight focused cases and the refreshed full 111-case Windows portfolio
  passed, the latter in 48.74 seconds under the unchanged 120-second bound.
- No production install, external host activation, trusted release selection,
  stable launcher, passive-candidate cutover, rollback or post-installer health
  is claimed. Those remain the existing #6424/#6240 lifecycle boundary.
  POSIX client consumption of the Unix endpoint also remains explicit work.
  This slice does not take the plugin or process utility source, modify CI
  enrollment or retire the working predecessor. The full parent remains Active.
- Separately, the existing adopter's predecessor runtime now has actual
  one-peer native idle-wake/read/primary correlation, quiet/self suppression,
  mute/resume and busy admission with a higher pending watermark followed by
  two correctly sequenced queued-primary runs. That is neither this new host's
  adoption nor non-primary/steered qualification. Remaining native uncertainty,
  gaps, succession and cleanup stay with the retained connection owner.

### 2026-10-19 - Isolated worker and qualification-reference corrections

- Preserve isolated mode in the finite HTTP worker, not only its invoking
  catalog argv. Real fresh installed invocations from a shadow working
  directory and with a shadow `PYTHONPATH` both reproduced unintended worker
  imports before the fix; explicit child `-I` makes both use the installed
  worker and complete the authenticated synthetic RPC.
- Corrected this PR's stale testing-reference claim: the standalone portfolio
  is on-demand, while required source CI runs the admitted unit-only packet.
  Its green checks do not establish native client qualification. No CI lane,
  selection exemption or native test deadline was added or changed.
- The complete current Windows portfolio passed 171 tests in 99.42 seconds
  under the unchanged 120-second bound. Six focused installed/deadline/crash
  cases also passed. Initial full runs exceeded the bound; profiling identified
  repeated auxiliary parser startup and durability work in preexisting
  capacity-fixture setup. Auxiliary dispatch remains backed by real providers
  and HTTP; actual CLI round trips, installed-console death, artifact and shadow
  boundaries remain separate real processes. All cases and the actual durable
  128th admission, 129th refusal and uncertain-request preservation remain.
- The existing utility owner proposed an opt-in finite caller-owned execution
  API. This consumer accepts that direction and returned exact lifetime and
  separately bounded stdout/stderr requirements without taking over canonical
  library or other consumer ownership. API agreement is not publication:
  streaming capture and positive native POSIX lifetime qualification remain
  real merge gates. The original client PR remains reviewable, not parked
  as a dependency-wait draft.

### 2026-10-19 - Client review readiness is separate from merge readiness

- The original #7266 is reviewable with the native POSIX lifetime dependency
  explicitly retained in its description and open finding, rather than parked
  as a draft solely while waiting for that dependency. This changes neither
  execution support nor the remaining qualification and merge gates.

### 2026-10-19 - Original client PR review follow-up

- Prepared the client-owned HTTP total-deadline and real installed-console
  crash fixes on the original #7266, together with its qualified offline
  installed invocation catalog. Keep that PR draft while native POSIX
  caller-death-safe execution remains unavailable from the canonical helper.
- The native POSIX refusal is explicit before helper execution; a focused
  negative control does not replace or hide the existing positive platform
  cases. This is an incomplete portable transport, not a Windows-only scope
  change or a claim that all three review findings are resolved.
- Utility implementation and native host adoption remain with their existing
  owners. This source follow-up does not alter another contributor's PR,
  drive its merge, restart a peer or substitute another installer.

### 2026-10-19 - Merged drain integrated with the held client

- The independent core drain slice #7284 actually merged to `dev` as
  `af4ef48d7175d6e2a3aace81bc61017efd45e315`, after exact-head Copilot approval,
  no open findings and current required checks passed. Its sessionless source
  worktree was finalized normally. This is source delivery, not Main promotion,
  hosting activation or completed lifecycle integration.
- Rebased the retained client/catalog work onto that core, preserving the
  operator's trusted-init/assignment contract and both component histories.
  No transport review finding was cleared or client revision pushed merely
  because the independent core landed.
- The combined native Windows portfolio passed all 168 contracts in 114.03
  seconds under the unchanged 120-second contained limit, with the current
  rebuilt artifact verified against source. Initial combined runs exceeded
  that limit. Profiling isolated quadratic capacity-test setup: repeated client
  posts re-read the growing complete pending inventory. The fixture now seeds
  127 valid durable records, exercises the actual 128th uncertain post and
  rejects the 129th without transport, then proves a later refusal preserves
  that actual uncertain request. The exact production bound remains 128;
  no runtime change, test exclusion, deadline increase or fake passing status.
- Current source CI has moved to the reviewed unit-only packet. Its green
  checks are not native Linux or fresh installed-client proof. The existing
  periodic hardened Linux route does not enroll this standalone service;
  its Windows job is disabled. Existing release/qualification ownership under
  #6237 retains that explicit platform/inventory gate. No new lane or unrelated
  full-portfolio dispatch was introduced.
- Public #7266 still awaits the canonical caller-death-safe POSIX utility API
  and its native qualification. The client/catalog candidate remains local and
  unpublished; source progress does not change the existing selected clients.

### 2026-10-19 - Installed catalog prerequisite qualified

- The explicit offline `catalog` command now returns this executing
  interpreter-local wheel's identity, RECORD-bound Python resource digests and
  exact isolated client argv. It refuses source-only, user-site-only,
  redirected, incomplete or inconsistent installations rather than selecting
  a command through PATH. It reads no client configuration or credentials.
- Fresh offline wheels with both fixture sources removed qualify the actual
  console, returned argv and authenticated synthetic conversation exchange.
  The full changed Windows portfolio passed152 contracts in88.57 seconds under
  the unchanged120-second contained deadline. Focused catalog/artifact cases
  and touched-Python lint passed. Native Linux and live external service
  qualification remain unperformed for this local revision.
- Current lifecycle-owner evidence under #6424 still exposes admission only:
  construction and activation are not available through that published
  boundary. The catalog therefore does not claim trusted release selection,
  managed setup, executable-dependency readiness or native listening. Those
  portions of Phase2 remain open; no duplicate installer was added.
- This prerequisite remains local on the held #7266 source workspace. The
  canonical caller-death-safe POSIX utility dependency still blocks publishing
  and merging that transport revision. No review thread was resolved on the
  strength of unrelated catalog work.

### 2026-10-19 - Intent amendment landed; transport recovery qualification

- The canonical initialization/participant amendment #7279 merged to `dev`
  as `1e3bf7a802f8422a78524c17788914acd4dfdccc` after exact-head Copilot approval,
  no open findings and all checks passed. This refines the one existing design;
  it does not complete participant assignment, hosting or native adoption.
- Installed-client review follow-up now exercises actual trickle-header/body
  total HTTP deadlines and lock/unread recovery. The fresh wheel-console death
  case checks offline lock acquisition before releasing the held response or
  the outer fixture containment boundary. All132 changed Windows contracts
  passed in87.96 seconds under the existing120-second contained limit.
- Native POSIX caller-death ownership remains a canonical process-utility
  dependency. The incomplete local transport revision is not a released
  cross-platform client; the open #7266 cannot merge until that review defect
  and the appropriate platform qualification are resolved.
- The next integration stretch remains selected-client identity/catalog/setup
  and service-owned assignment, with the connection contributor consuming the
  shared trusted-init seam. #7238 and all remaining native/lifecycle gates stay
  open.

### 2026-10-19 - Independent installed client slice

- Added the explicit optional client role to the existing distribution, with
  canonical native owned provider execution and no implicit home, native
  claim authority, daemon or source-runtime fallback.
- Local qualification covers the actual UTF-8 CLI/provider/HTTP/core chain,
  forward-only reader progress, stable-request uncertainty, body-free
  returned-only scans and CLI death before outer fixture cleanup.
- Fresh isolated core installation proves the helper is absent. A separate
  offline client-role installation consumes both real wheels and runs the
  installed console after both fixture sources are removed.
- The existing fixed platform CI lanes and 120-second contained budget remain
  unchanged. Cross-OS execution stays unsupported until the canonical utility
  owner publishes and qualifies its explicit opt-in seam. No candidate
  adoption, hosted lifecycle, native wake or live migration is asserted here.
- The installed wheel command is qualified, but a normally selected runtime,
  attributable catalog and explicit setup entry point remain the service
  writer's next delivery prerequisite for connection/harness consumers.

### 2026-10-19 - Portable core landed

- Core PR #7242 merged to `dev` as
  `900578f06f9e63250f0b8aa8e5952f549985554e` after exact-head Copilot approval
  with no open findings and all required checks. The parent #7238 remains open.
- The installed core has 62 passing contracts, including a real built wheel
  after fixture-source removal, post recovery after leave and fixed-clock
  membership fencing. Both fixed Linux/Windows core lanes, native runner
  contracts and source-owned publication contracts passed.
- This completes only the bounded core extraction and store qualification.
  The next stretch is the dependency-light installed client and explicit
  hosted transport boundary. Native attachment, managed lifecycle, release
  activation under #6237 and live migration remain open owner-scoped work.

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
