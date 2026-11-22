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
- **Now:** a real queued notification reached the owned model after idle, while
  its original receipt remained at SDK acceptance. The executing extension
  reports a retained native-call timeout. Relevant history-isolation and live
  consumption fixes are already merged and present on the release branch,
  but absent from the observed installed extension. Preserve that adoption
  boundary; continue fleet inventory without duplicating the existing fixes.
- **Return:** after the queued-delivery fix clears its source gates, resume
  fleet-wide inventory and the remaining lifecycle Plan below.

**Acceptance:** every applicable Plan and Validation Plan item is resolved or
explicitly transferred to a named tracked objective; source changes are reviewed,
merged and verified in the normal release; the public docs and guidance describe
the new contract. Source delivery does not require or authorize a live rollout.

- **Slug:** `unified-bridge-inventory-and-lifecycle`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed worktrees, one dependent delivery slice at a time
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

- **Topology:** independent per-slice PRs in dependency order.
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

- [ ] Implement the canonical bridge projection over existing owner records:
  one current or last-assigned conversation per worktree, including dormant
  session-backed worktrees and live anchors, without requiring registration.
- [ ] Add the project/CWD/repository-binstub-scoped list and machine, project,
  namespace, lifecycle and live filters, with always-emitted status and cached
  title/disposition/activity, hosting/transport, location and creator/controller.
- [ ] Retain independent source completeness, freshness, liveness and control
  evidence; missing metadata and failed registration must not disappear as zero.
- [ ] Compose remote registries, managed ACP conversations and standalone hosts
  across the configured five-host fleet, retaining host-qualified routes and
  explicit unavailable-source evidence.
- [ ] Prove the source CLI against a mixed isolated consumer fixture before
  promoting this slice through its required checks and source PR.

### Phase 3 - Align selectors and separate potential targets

- [ ] Route ordinary show, send, read, wait, resume, stop and end selectors
  through the common bridge identity with equivalent ACP and Mux semantics.
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
