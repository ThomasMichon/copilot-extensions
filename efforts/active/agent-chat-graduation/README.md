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
- [ ] Package an independently operable hosted service with no source checkout,
  sibling venv or Copilot plugin requirement.
  The foreground `service` artifact merged in #7361 with Windows qualification;
  native POSIX qualification and managed installation/activation remain open.
- [x] Publish client bounds, errors, offline scan capability and explicit
  configuration. Preserve output/flush/CAS and retained stable-request recovery.
- [ ] Supply hosted endpoint discovery and explicit service setup through the
  existing lifecycle owners; client configuration is not hosting readiness.
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
- [ ] The proposal editor defines the future versioned collaboration/thread/
  attention representation over existing Chat state and installed composition;
  agree extension points with the original client/service/plugin owners first.
- [ ] Coordinate any required new domain/intake or external-ingress binding
  with its actual host/client owner. State missing APIs precisely without
  holding existing explicitly authorized same-domain work behind them.
- [ ] Preserve Gateway/SSH, Vault, lifecycle, workspace and task ownership;
  existing #6030/#6240/#6424/#6681 work is not reassigned by this amendment.

## Validation Plan

- [x] **Portable store:** monotonic event identity, independent viewers,
  bounded pages, stable request replay/conflict and visible capacity refusal;
  installed source-independent artifacts and both native platform CI lanes.
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
- [ ] **Concurrency:** posts and health remain responsive under several held
  subscriptions; coupled maintenance uses one current owner/correlation and
  does not turn releases into acknowledgement loops.
- [ ] **Platforms:** direct Windows and native Linux cleanup, plus a separate
  real WSL-to-Windows descendant test for timeout/interruption/caller death.
  POSIX process groups are not claimed as Windows tree ownership.
- [ ] **Portfolio:** keep fast portable contracts in required change-scoped CI;
  isolate real process/native/fresh-environment matrices in their existing
  appropriate lanes. Record why a genuinely unavailable venue was not exercised.
  Client qualification requires the explicit120s acceptance packet and complete
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
| Trusted initialization and workspace | SDK `joinSession`, `sessionId` and `rpc.metadata.snapshot().workingDirectory`; Worktrees `find_worktree_id_by_cwd`, `head-session` and explicit anchor context | The client/service writer supplies the optional workspace binding: worktree identity across sessions, startup-session segmentation for anchors, and the existing head decision. The plugin forwards trusted initialization once; it does not add an environment-ID check or another registry. Ordinary standalone explicit actors remain independent of Worktrees. |
| Recipient inbox and eligible sources | The client accepts explicit actor/channel controls; Bridge `inspect_live_inbox` projects direct-message receipts | No examined client API supplies authoritative Chat recipient/source assignment. The client/service writer coordinates that versioned assignment and its isolation/rebind errors with the existing local service/Bridge owner. A direct-message receipt projection is not this API. |
| Shared notification intake | Bounded Chat `scan` v1; Bridge remote-event multiplexing and protocol13 `remote/events` | Remote runtime events do not subscribe to the conversation store. The service writer and existing host/Bridge owner must publish the Chat-feed registration, generation, finite callback, cancellation and quiescence binding. No per-session Python helper or continuous Node polling is an acceptable substitute. |
| Native admission and completion | SDK `session.send`, primary final-ID semantics and optional `originatingMessageId`; bounded queue/event-log observations | Exact own primary correlation is usable. Unknown or non-primary admission remains held until its native host supplies authoritative reconciliation; queue absence, unrelated idle and expired history do not supply it. |
| Commands, discovery and setup | SDK tools/commands, source-qualified extension identities and host-only reconciliation; installed client-catalog v1 with seven checked resources | The connection writer can expose the supported inert/manual surface. The client and existing lifecycle owners still owe governed selection/setup; a local wheel RECORD or a loaded tool is not that authority. Host reconciliation stays host-owned. |
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
