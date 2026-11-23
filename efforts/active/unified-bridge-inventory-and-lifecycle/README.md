# Unified Bridge Inventory and Lifecycle

## Goal

> Update agent-brige's vision, make an effort captureing this intent/spark, and get to work implementing.

**Root outcome:** implement the approved, project-scoped Bridge inventory and
lifecycle described in the [inception](inception-transcript.md), with one
conversation identity across hosting modes and a separate potential-target
surface. This outcome statement is the agent's reading of the request, not
additional authority.

**Corrected operator rule:**

> "resuming worktrees routes to the previous session in that worktree". Creating a new worktree should create a new session within it.

**Current ordered assignment:** first reproduce and fix queued Bridge messages
that are not submitted when the target becomes idle, using a real sender and
target to distinguish idle detection from submission failure. Then continue the
unified inventory slice with fleet-wide discovery across the five selected
hosts, including remote registrations, managed ACP conversations and standalone
hosts, with host-qualified routes. This priority amendment preserves the root
outcome rather than replacing it with one intermediate fix.

- **Kind:** completion
- **Now:** the scoped and explicit-host inventory slice merged in #14034.
  The Phase 3 shared exact selector and read-only `show` consumer merged in
  #14206; release verification remains open. Explicit local read/wait adoption
  of exact identity and bounded result observations is qualified in source;
  its review, CI and merge gates remain open. Default migration, remote
  observation, effectful selectors and potential targets follow.
  This sequencing is the author's next implementation slice, not
  additional operator scope.
  Queued-delivery attribution remains separately open at the installed-adoption
  boundary: the real notification reached the owned model, but its original
  receipt lacked consumption. Relevant fixes #13862 and #13883 are released;
  that does not prove adoption or resolve every queued-input stall.
- **Return:** finish the remaining selector, guarded lifecycle, qualification
  and release gates below, then return the driving worktree to its retained
  earlier objectives. Do not equate this source slice with receiving recovery.

**Acceptance:** every applicable Plan and Validation Plan item is resolved or
explicitly transferred to a named tracked objective; source changes are reviewed,
merged and verified in the normal release; the public docs and guidance describe
the new contract. Source delivery does not require or authorize a live rollout.

- **Slug:** `unified-bridge-inventory-and-lifecycle`
- **Repo:** copilot-extensions
- **Branch(es):** the existing managed source worktree, with serial dependency-ordered PR slices
- **Created:** 2026-11-21
- **Status:** Active
- **Vision:** [Bridge inventory](../../../visions/plugins/agent-bridge/README.md#listing-defaults-to-registered-excludes-archived), [potential targets](../../../visions/plugins/agent-bridge/README.md#potential-target-discovery), [creation](../../../visions/plugins/agent-bridge/README.md#deliberate-creation-prefer-owned)
- **Umbrella issue:** #13811

## Guiding Intent

Make Bridge a legible conversation layer rather than a choice among
managed-session, interactive-registration and workspace catalogs. Discoverable
does not mean currently controllable. Preserve independent evidence for
assignment, process liveness, source completeness and delivery.

Reuse Worktrees' workspace/head/assignment facts, Driver hosting and Bridge's
existing registration, target, lifecycle and namespace owners. Do not construct
another authoritative registry or make listing trigger execution.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Normalization author | Owns reviewed intent, implementation, qualification and source PRs | #13811 and its managed source branches |
| Registration recovery author | Provided the merged registration and passive-catalog prerequisite | #13797 |
| Existing workspace, Driver and venue owners | Retain their allocation, hosting, policy and resource boundaries | Their existing source contracts and linked efforts |

## Coordination

- **Topology:** serial PRs in dependency order; retain the existing worktree's
  ownership and pull it forward after each merge.
- **Host (owns PRs):** Normalization author.
- **Delegates:** none assigned by this plan.
- **Handoff:** retain this canonical effort, exact current slice, review state
  and original limits; a source PR does not transfer deployment custody.
- Reuse merged #13797 rather than opening a replacement. Its registration
  and passive local catalog owners retain their boundaries; resolve actual
  content overlap through the original author.
- Preserve the separate freshness work in #11298 and namespace latency work in
  #5761. Unavailable observations do not authorize repairing their services.

## Context

Existing Bridge sources already compose managed sessions, the Host index,
interactive registrations and workspace discovery in a typed target inventory.
The public command surface still exposes different categories and selection
rules. A process can remain alive while its receiving extension fails and its
workspace discovery source is incomplete. A zero in one index is therefore not
a machine-wide process count or proof that no bridge exists.

The change extends the [Bridge vision](../../../visions/plugins/agent-bridge/README.md).
It follows [service-contract evolution](../../../docs/patterns/service-contract-evolution.md)
and the existing [session-hosting boundary](../../../visions/session-hosting/README.md).
The implementation must expand receiving owners before migrating consumers,
preserve supported legacy callers and rollback, and retire duplicate public
terminology through a declared transition rather than change an old JSON shape
silently.

## Request

The [public-safe inception](inception-transcript.md) retains the original
request, the explicit new/resume correction and the instruction to reconcile
the driving worktree's charter. Its one example project name is generalized.
The current contract is the approved proposal with that correction applied.

## Scope Approvals

The operator explicitly approved the proposed normalization and assigned vision,
effort capture and implementation. Normal relevant source tests, source PRs,
understood rebases and required review/CI are included.

This assignment does not authorize live installation, daemon replacement,
extension reload, receiver enrollment, queue replay, credential/access changes,
conversation replacement, unrelated source repair or fleet rollout. Preserve
existing installer, registration, receiving and resource owners. A reviewed
plan, a released package or a process observation is not new effect authority.

The later ordered assignment permits a bounded real sender/target reproduction
of queued delivery. It does not authorize replay of previously uncertain
messages, receiver enrollment, conversation replacement or deployment.
Copilot Driver core code remains with its existing owner and must not be edited
by this effort. Keep test packets bounded on a busy host. The fleet scope uses
the configured five-host selection, not committed personal machine names.

For the initial inventory slice only, the operator approved one local
backup-preserving history normalization before publication: soft-reset to the
branch's merge base, then make one commit with an identical tree, public commit
identity and both required attribution trailers. This avoids an installed
publisher's title-only multi-commit squash. It permits no hard reset, other
branch rewrite, runtime change or gate bypass. The normal managed PR flow
continues afterward; this is not standing rewrite authority.

For the selector/inspection PR only, the operator separately approved a backup,
soft-reset of unpublished local commits to their merge base, and an identical-tree
recommit with the no-reply identity and both attribution trailers before normal
publication. This excludes hard reset, shared-history rewrite, source discard,
runtime changes and review/CI bypass. It is not approval for later serial PRs.
The driving owner retains the exact question and affirmative response.

### Native validation prerequisite

The operator approved a bounded test-harness correction after the inventory
branch's Linux native telemetry check read records but lacked the receiver's
final cost report. The approval question, verbatim:

> The required PR checks are green, but an additional Linux Bridge telemetry test fails: it reads records, then closes the receiver before the receiver writes its final cost report. This test is outside the inventory change, and I found no tracked flake exception. May I make a bounded test-harness fix through the normal source PR gates? This excludes runtime changes and any weakening of the assertions.

Response: **true**. This permits qualifying and correcting that fixture's
completion protocol through normal review and CI. It does not authorize
runtime changes, weaker assertions, installation or deployment. The suspected
close-versus-settlement race still requires source and consumer evidence; the
approval itself is not a diagnosis.

### Merge-author metadata

The operator confirmed the server-selected author email used by #14034 as
approved public metadata for this repository's GitHub-generated merge commits.
Locally authored commits continue to use the verified no-reply address.
This confirmation does not approve any other address or repository, account
settings changes, or shared-history rewrites. The driving owner retains the
exact question and affirmative answer.

## Plan

### Phase 1 - Review the contract and capture the inception

- [x] Claim #13811 and identify #13797 as an independent prerequisite and owner.
- [x] Reconcile the driving worktree's charter and earlier unfinished objectives.
- [x] Publish and merge this effort and the revised Bridge vision before implementation.
- [x] Inventory affected CLI/API consumers and declare the supported transition,
  compatibility aliases, capability selection and rollback behavior.
  The first slice adds `list`, `/bridges` and an opt-in source-union
  `live-sessions list`; existing `sessions`, `targets`, registration JSON and
  mutation selectors retain their meaning. See the
  [conversation contract](../../../plugins/agent-bridge/docs/target-inventory.md#conversation-inventory).

### Priority 1 - Attribute and fix queued idle delivery

- [x] Reconcile the current Bridge source and existing queue/extension owners
  before a bounded real sender/target reproduction.
- [ ] Capture original enqueue, idle transition, submission and consumption
  evidence separately; identify which side fails without replaying unknown effects.
- [ ] Fix any remaining Bridge-owned failure with focused regression tests and
  the normal source review, CI and merge gates, without editing Driver core.
  Account for the independently merged #13862 and #13883 without treating them
  as proof that every queued-input stall is resolved.
- [ ] Repeat the real consumer flow through an authorized validation lane and
  record any installed-adoption boundary separately from source completion.

### Phase 2 - Prove one truthful bridge inventory

Source implementation and contained consumer qualification landed in #14034.
Installed fleet adoption is not claimed.

- [x] Implement the canonical bridge projection over existing owner records:
  one current or last-assigned conversation per worktree, including dormant
  session-backed worktrees and live anchors, without requiring registration.
- [x] Add the project/CWD/repository-binstub-scoped list and machine, project,
  namespace, lifecycle and live filters, with always-emitted status and cached
  title/disposition/activity, hosting/transport, location and creator/controller.
- [x] Retain independent source completeness, freshness, liveness and control
  evidence; missing metadata and failed registration must not disappear as zero.
- [x] Compose remote registries, managed ACP conversations and standalone hosts
  across the configured five-host fleet, retaining host-qualified routes and
  explicit unavailable-source evidence.
- [x] Prove the source CLI against a mixed isolated consumer fixture before
  promoting this slice through its required checks and source PR.

### Phase 3 - Align selectors and separate potential targets

- [x] Qualify the common exact identity selector and read-only `show` consumer
  over the negotiated provider contract before migrating effectful operations.
- [ ] Route the remaining ordinary send, read, wait, resume, stop and end selectors
  through the common bridge identity with equivalent ACP and Mux semantics.
  The current source slice first adds opt-in local read/wait over existing
  bounded result owners. Legacy history, event-cursor, attention and workspace
  following remain unchanged until their replacement contracts are ready.
- [ ] Separate potential placements from bridges, including empty worktrees,
  eligible roots, containers, CodeSpaces, configured ACP hosts and external
  endpoints, with occupancy and allocation behavior visible.
- [ ] Keep dormant anchor history out of the default roster and provide explicit
  archive lookup through the existing retained-history owner.
- [ ] Update CLI help, owning skill guidance and real consumers so namespace
  and cross-project discovery do not depend on prior agent knowledge.

### Phase 4 - Prove guarded creation and continuity

- [ ] Make new-worktree creation start a new session and existing-worktree
  resume select its prior session, honoring an authoritative legacy handoff.
- [ ] Enforce atomic active/last-assigned placement admission through the
  existing lifecycle owner; require explicit end before another creation.
- [ ] Preserve assignment on stop, disconnect, process loss and daemon restart;
  preserve conversation history when explicit end releases assignment.
- [ ] Default anchor-root creation to managed worktree allocation; require
  explicit anchor and scoped break-glass for direct or additional anchor use.
- [ ] Qualify the full new/list/send/read/stop/resume/end flow in isolated
  consumers without touching retained operator conversations.

### Phase 5 - Release and close the source assignment

- [ ] Run required targeted and cross-boundary checks, with explicit evidence
  and reasons for any unavailable validation tier.
- [ ] Complete documentation-impact review, changefiles and supported-contract
  migration notes, and drive all owned PRs through required review and CI.
- [ ] Verify actual release source and required packaged consumer closures.
- [ ] Reconcile all Plan and Validation Plan items, publish the Done/archive
  disposition and finalize only this effort's eligible source worktrees.
- [ ] Return the driving worktree to its explicitly retained earlier objectives;
  do not infer their completion from this source delivery.

## Validation Plan

- [ ] A real sender and target prove queue admission, target idle observation,
  exact submission and eventual model-facing consumption without fresh-key replay.
- [ ] All configured five hosts contribute their independently observed registry,
  managed and standalone-host sources or an explicit source failure; remote routes
  name the owning host and never masquerade as local handles.
- [ ] Mixed owner evidence produces one bridge per workspace conversation,
  including running headed hosts with missing registration, dormant worktrees,
  live anchors, independent ACP/HTTP/WebSocket endpoints and unavailable sources.
- [ ] Sessionless workspaces appear only as targets; dormant anchors and
  archived history require explicit lookup; missing paths remain honest.
- [ ] Explicit project and repository entry points agree with CWD scope;
  machine/namespace/status/live filters preserve evidence and disambiguation.
- [ ] Listing and inspection allocate no workspace, start/resume no conversation,
  enroll no receiver and perform no per-row transcript scan or service recovery.
- [ ] Current/prior CLI and provider fixtures preserve supported syntax, JSON
  meaning, effects, capability refusal and documented rollback behavior.
- [ ] New worktree gets a new session; resume reaches the original session;
  pending legacy handoff never resurrects its predecessor.
- [ ] Concurrent create, active assignment, last assignment, unknown launch and
  daemon restart cannot admit a second bridge or lose original effects.
- [ ] Stop preserves assignment; explicit end releases it without deleting
  history; unrelated owners and original message IDs remain intact.
- [ ] Anchor allocation and both break-glass exceptions enforce repository
  eligibility and current custody before effects.
- [ ] Focused unit/Node tests, Python lint/typing, install-contract and required
  source CI pass for the exact published source.
- [ ] Real parser/process/API consumers and practical fresh-state/clean-room
  controls pass; record any unavailable live external lane without claiming it.
- [ ] Reviewed source is merged and released; no unapproved local rollout or
  earlier receiving acceptance is reported as complete.

## Proposal

Use the existing target projection and owner adapters as the starting point.
Keep the bridge inventory, potential placements and retained archive distinct.
The public vocabulary converges on bridges; internal session storage need not
be renamed solely for presentation. Compatibility adapters are transitional,
not another permanent public discovery path.

## Journal

### 2026-11-23 - Represented content origin and observation deadlines qualified

- Represented aliases combine event histories before a later selector can run.
  Exact represented reads now request `native_only` and refuse combined or
  mismatched-origin history with 409. Legacy reads retain the combined history.
  Private API controls create the real reservation/process alias first, then
  prove refusal for both IDs without deleting or changing the old answer.
- The wait deadline now starts before selection. One command budget reaches
  capability checks, each HTTP request and protocol retries. It caps retry
  delays and checks remaining time before another read or poll. A late idle
  response cannot become a successful observation; timeout retains only the
  last in-budget snapshot, or null when none was observed.
- The focused behavior packet passes 98 cases. A separate 31-case legacy
  connection/protocol packet and 12-case result/HTTP-deadline packet pass.
  Whole-file strict typing passes for all 20 changed Python files on the
  Python 3.10 Linux and Windows targets. Lint, the 13-plugin install contract
  and documentation consistency checks pass.
- Both hosting modes' native projections share `native_result_snapshot.py`;
  the live-session route remains within the unchanged module-size limit.
  After this extraction, all 53 selected API/native-origin cases and the same
  20-file strict typing packet pass.
- The earlier required unit run passed its tests but failed coverage because
  the extracted request helper was not observed. Three new eligible actor-free
  controls now exercise the helper and the shared deadline. Local execution
  and coverage observe both production targets. The committed unit manifest
  explicitly adds those cases, the deadline source and its coverage target;
  no existing coverage target, test or deadline is removed or weakened.
  Full exact-head CI remains a separate publication gate.
- Two combined local runs exhausted their aggregate preparation/test budget
  and are not counted as passes. Separate packets retain the original limits.
  Machine provisioning is unaffected. Fresh private state covers first reads;
  the existing execution and rollout limits still exclude a live external
  lane. Review, required CI, merge and release remain open.

### 2026-11-23 - Retained content origin and source gates qualified

- Fresh selection after native recreation must not relabel an earlier native
  conversation's answer. Exact owned observations now request `native_only`
  from the existing result API. Completed text requires its original turn's
  recorded native origin; unconfirmed content is explicitly unavailable.
- Unscoped managed event/client projections are omitted with reduced fidelity.
  Legacy reads retain their original history and full owned fidelity. The
  implementation adds no schema migration, producer enrollment or stored-state
  mutation, and does not use installation admission as a state-read gate.
- Extracted bounded request and identity helpers keep existing module limits
  intact. Refreshed the two changed admitted client hashes and included the
  extracted client helper in source admission and measured coverage. Unit
  eligibility and deadlines are unchanged.
- The final behavior packet passes 88 cases, including fresh selection after
  recreation, matching/different/missing original content origins and actual
  CLI/client/API serialization. Eight separate legacy result cases pass.
  Whole-file strict typing passes for all 16 changed Python files on the
  Python 3.10 Linux and Windows targets. Lint, module size, the 13-plugin
  install contract and documentation consistency checks pass.
- Fresh private fixtures preserve the old completed row and legacy answer
  while the exact view omits it. Machine provisioning is unaffected. A live
  external lane remains unavailable under the existing execution and rollout
  limits; source review, exact-head CI, merge and release remain open.

### 2026-11-23 - Native identity and explicit project observations qualified

- Managed control IDs can survive native conversation recreation. Result
  identity now adds the native ID and advertises `result_native_identity: 1`.
  Explicit read/wait pins both identities and refuses replacement content or
  idle state. A native change during projection also returns an explicit 409.
- Existing result APIs and legacy client defaults remain available. The new
  mode checks the executing provider's capability on each result attempt;
  unsupported providers refuse before the result read.
- Explicit `--project` reaches read/wait only with `--bridge`. Legacy forms
  retain their previous rejection rather than silently ignoring the scope.
- The final focused CLI/client/API packet passes 77 cases. Eight separate
  legacy result cases pass. Controls cover both hosting modes, provider
  upgrade/rollback, native recreation before and during projection, wait-poll
  identity changes, and actual project parsing without native execution.
- Whole-file strict typing passes for all 12 changed Python files on the
  Python 3.10 Linux and Windows targets. Focused lint, the 13-plugin install
  contract and documentation consistency checks pass.
- An earlier combined packet exceeded its 120-second limit and is not counted
  as passing. Smaller behavior packets retain the same limits. Full-machine
  provisioning is unaffected; private fresh-state fixtures cover first reads.
  A live external lane remains unavailable under the existing execution and
  rollout limits. Review, exact-source CI, merge and release remain open.

### 2026-11-23 - Explicit local read/wait observations qualified

- Added opt-in `read --bridge` and `wait --bridge` over the common exact
  selector and existing bounded result owners. Both hosting modes retain
  their actual fidelity. Local-only observation pins the selected conversation;
  it does not hydrate metadata, resume, follow a successor or change a cursor.
- Ordinary defaults remain unchanged. Historical/event reads, workspace
  following, remote carrier observations and represented attention require
  their own receiving contracts before default migration.
- The final bounded packet passes 104 cases, including real CLI/client/API
  serialization for both hosting modes, provider capability versions 0/1/2,
  fresh private registry state, unchanged legacy reads, attention and streams.
  The real consumer exposed a global `--json` value overwritten by `wait`'s
  local parser default; the corrected default preserves either flag position.
- Whole-file strict typing passes for all five changed Python files on the
  Python 3.10 Linux and Windows targets. Focused Python lint, the 13-plugin
  install contract and documentation consistency checks pass.
- The first combined run exhausted its aggregate budget during preparation;
  that run is not counted as passing. Separate preparation retains the
  original test, group and aggregate limits.
- Full-machine provisioning is not affected. The fresh-state API fixture proves
  the first observation without starting native conversations. A live external
  lane is not exercised: installed rollout, protected receiver changes and
  native conversation execution are not authorized. Source review, exact-head
  CI, merge, release and the full effort's remaining gates stay open.

### 2026-11-23 - Exact inspection merged; read/wait migration follows

- #14206 merged as `e81c69beadd8491c5a95996b6b503de4b97525b9`.
  The final owner-authored review reported no open findings on the exact head,
  all threads were resolved, and required CI passed. The normal Maintainer
  merge path omitted only the separate CODEOWNER vote, not review or CI.
- The final behavior packet passed 29 cases, and all ten changed Python files
  passed strict typing on the Linux and Windows targets. Both native telemetry
  lanes passed on the same Python source; the last commit corrected only a
  documentation capability reference.
- Normal pull-forward safely aborted on the squash history. The complete local
  and merged patch IDs were equal, the local tree was clean, and all four local
  commits belonged to the merged PR. An empty-range rebase reconciled only that
  already-merged history, after which normal sync completed. Recovery refs remain.
- Retain this worktree for the full effort. Next prove read/wait selection of
  the same conversation without profile fallback or replacement. Other selectors,
  potential targets, guarded creation, release and installed delivery remain
  separate open gates. No local rollout or receiving completion is claimed.
- The pinned release check at `main`
  `599820454166a041762a91e099a5d7a2ec382576` still advertises
  `bridge_inventory: 1`, not the required version 2. This slice is merged but
  not yet released; no installation or deployment follows from the check.

### 2026-11-22 - Exact inspection consumer qualified

- Added a shared exact selector over the already released inventory provider,
  plus the first read-only `show` consumer. No prerequisite API, conversation
  execution or existing mutation-selector change is introduced.
- Thirteen focused selector/parser cases and four actual CLI/client/API
  roundtrips passed, including legacy-provider refusal and unchanged list
  negotiation. Fresh private registry state supplies the consumer fixture.
- Whole-file strict typing passed for the seven changed Python files on the
  Python 3.10 Linux and Windows targets. Focused Python lint, install-contract
  and documentation-consistency checks passed.
- The initial combined local packet reached its preparation budget, and a
  later larger packet reached its wall limit. Neither is counted as a pass.
  Separate preparation and focused selections retained the original item and
  group limits. Required exact-source CI and review remain publication gates.
- A live external lane is not exercised: this slice changes read-only
  inspection, and the current assignment does not authorize installed rollout,
  protected receiver changes or native conversation execution. This does not
  resolve the separate installed-delivery gate.
- The first review exposed two mixed-host identity defects: reconciliation
  dropped managed aliases when preferring a registration, and the selector
  accepted conflicting dormant heads. Both new regressions failed before the
  correction. Reconciliation now retains only aliases tied to the selected
  native head, and conflicting retained heads refuse inspection with 409.
- The corrected packet passes 22 cases, including active and retained managed
  aliases, reversed host order, exclusion of previous-head routes, malformed
  alias collections and the real CLI/client/API negotiation controls. Separate
  environment preparation was necessary after one preparation-heavy run used
  its aggregate budget; that incomplete run is not counted as a pass.
- Strict whole-file typing passes for all eight changed Python files on the
  Python 3.10 Linux and Windows targets after the corrections. Focused lint,
  install-contract and documentation-consistency checks pass.
- Follow-up review found that conflicting-head managed aliases must also match
  for refusal, and the previous version-1 provider cannot certify alias absence.
  Inspection now requires `bridge_inventory: 2` through the existing handshake;
  version 1 still supports listing. Older remote observations preserve positive
  evidence but cannot produce a false complete-discovery 404.
- The same identity rule applies before and after fleet reconciliation.
  Compatible secondary managed aliases survive local projection and merging.
  Conflicting dormant histories require either an authoritative last head or a
  409 refusal; their managed aliases remain matching evidence, not execution
  targets. No RPC is added, and no resident lifecycle or installed state changes.
- The expanded contract packet passes 29 cases, including versions 0, 1 and 2
  for actual CLI/client/API reads and the previous remote-provider boundary.
  Whole-file strict typing passes for ten Python files on both targets after
  correcting a test fixture's dictionary annotation. Install-contract and
  documentation-consistency checks pass; source review and CI remain required.

### 2026-11-22 - Inventory slice merged; selector work continues

- #14034 merged as `a742affe652b4a598fd632234923cb80c24b1fcb` after the
  exact-head required CI, whole-file typing and Linux/Windows native acceptance
  passed. The owner-authored final review reported no open issues; all threads
  were resolved. The normal Maintainer merge path waived only the separate
  CODEOWNER vote, not CI or actionable findings.
- Reconciled the known squash history only after verifying a clean checkout,
  the reviewed local head, the merge on `dev`, and identical complete patch IDs.
  The normal pull-forward then completed. Existing recovery references remain.
- Retain this worktree for serial Phase 3/4 PRs under its full objective,
  rather than finalize an unfinished effort or allocate a continuation worktree.
  Release verification remains open; no local update or live receiving
  acceptance is inferred from this merge.
- Verified the inventory slice on pinned `main`
  `7ea0394df86506be2276df551bbe60e11cf2e150`: all 24 nonshared affected files
  match the merge exactly. The later shared health source retains
  `bridge_inventory: 1`; the promotion unit-manifest difference is generated
  source admission, not a missing implementation. This is release evidence for
  that slice only, not installation or completion of the full effort.

### 2026-11-21 - Inception and scope

- Captured the approved request and explicit new/resume correction.
- Claimed #13811 and identified the independent #13797 owner.
- No implementation, deployment or original-message delivery is claimed.

### 2026-11-22 - Ordered delivery and fleet-discovery amendment

- Reviewed intent merged in #13813 after exact-source approval and required CI.
- Preserved the two staged inventory/listing tests; the interrupted runner has
  no recorded result and is not reported as passing.
- Adopted queued idle-delivery reproduction and correction as first priority,
  followed by five-host inventory. Driver core remains outside this author's scope.
- The exact current steer is retained in the driving owner's checkpoint;
  personal routing details are not copied into this public effort.
- A real CLI sender and the owned native target proved queue admission, SDK
  acceptance and model-facing delivery after turn completion. Original receipt
  readback still lacked consumption; a second existing target was waiting on an
  operator question and was not interrupted or answered for the experiment.
- The source confirms that the legacy live-session endpoint reads one local
  registration table. The first implementation slice composes the existing
  target inventory instead of changing Driver ownership or introducing a
  competing registry. Explicit host reads use the existing authenticated carrier
  and preserve host-qualified routes and unavailable-source evidence.
- The new CLI/API boundary is additive and capability-gated. Legacy discovery
  and mutation consumers remain unchanged; common selector and lifecycle
  migration stay in their later Plan phases.
- The contained source packet passes 46 focused controls, including the real
  parser/client/API roundtrip, five-host composition, unsupported peers,
  registration-only compatibility, project aliases, unknown metadata and
  process-versus-mux liveness. It performs no native conversation execution.
- Focused lint, owned-file formatting and the install-contract guard pass.
  An already-installed x64 interpreter avoids the selected ARM interpreter's
  `cryptography` source-build prerequisite. No global default, package manifest,
  runtime installation or rollout is changed.
- A larger local packet exhausted its wall budget and is not reported as
  passing. The strict whole-file checker environment was prepared, but its
  actual MyPy run exhausted the fixed 120-second limit before diagnostics.
  Required CI and source review remain open; neither limit is waived.
- Fresh private registry state and mixed source fixtures exercise first-read
  behavior. Full-machine provisioning and live fleet adoption are not run:
  this slice changes discovery, not installation, and grants no rollout.
- Read-only attribution found an outstanding native-call fence in the running
  extension. #13862 already isolates read-only history from fresh delivery, and
  #13883 already reconciles original receipts from live native input. Both are
  merged and their relevant source behaviors are present on `main`; the
  observed installed copy lacks both. This is not qualification of new code.
- Source work was synchronized through the normal owner after committing the
  interrupted inventory tests. No original unknown message was replayed, no
  Driver-core code was edited, and no deployment or reload was performed.
- A later operator-relayed observation identifies a separate possible boundary:
  unsubmitted text in a native input buffer may leave scheduled or queued input
  pending while the model is idle; submitting the buffered text resumes work.
  Treat this as a hypothesis to qualify through the existing input owner, not
  permission to inject keys into another active conversation or alter Driver core.
- Review qualification uses owner-produced registrations, attached clients and
  parser/serializer observations. It distinguishes Host control handles from
  native identities, retains native-backed managed worktrees during discovery
  failure, and checks known-empty versus unavailable repository catalogs.
  Unknown observations cannot join conflicting project or conversation scopes.
- The native telemetry lane exposed a separate fixture completion gap: producer
  pipe writes could finish before receiver fsync and final reporting. Under the
  bounded approval above, the reading fixture now settles its actual accepted
  record count before cleanup. Real processes, overflow accounting, failure
  modes and fsync assertions remain; no runtime behavior or timeout is widened.
- The combined private-state consumer packet passes 97 cases, including all
  native performance modes and legacy target-identity controls. Live source CI,
  review, release and the remaining lifecycle phases still require their own
  evidence; no installed runtime or retained conversation was changed.
- Native Linux and Windows acceptance passed for the finite-receiver revision.
  Further owner-shape controls distinguish process/lock observations from the
  legacy running/idle display hint. Fresh, complete omission excludes retained
  history only after matching its configured owner, project and transport;
  stale, incomplete and unrelated observations preserve the fallback.
- All 18 new or changed owner controls pass in a serial bounded packet. A larger
  concurrent packet exhausted its wall budget, and a later typing pre-check was
  stopped after prolonged local inventory work; neither is a passing result.
  Exact-source typing, native acceptance and review remain required in CI.
