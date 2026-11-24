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

**Approved prerequisite scope:** the operator selected `both` in the
prerequisite-source chooser recorded below: resolve the Dispatch native
validation failure and release materialization failure through normal source
gates. This extends the source assignment for those prerequisites; it does not
replace the root outcome or lift the retained runtime and ownership holds.

- **Kind:** completion
- **Now:** the scoped and explicit-host inventory slice merged in #14034.
  The Phase 3 shared exact selector and read-only `show` consumer merged in
  #14206; its source release is verified. Explicit local read/wait adoption
  of exact identity and bounded result observations merged in #14227 after
  approval and exact-source CI; all 22 changed Bridge paths are verified in
  the normal `0.9.533-dev1` Main promotion. This is not installed adoption.
  The opt-in exact local ordinary-send consumer merged in #14273 after
  approval, exact-head strict CI and Linux/Windows native qualification.
  All nine changed runtime files match Main `60abcf8e8e7c`, Bridge
  `0.9.537-dev1`; this is source-release evidence, not installed adoption.
  The remote native-result provider foundation and its approved Dispatch
  empty-outbox contention prerequisite merged in #14329 after fresh required
  CI, whole-file strict checks and Linux/Windows native acceptance.
  All 14 non-changefile paths match Main `5fac4d477175`, Bridge
  `0.9.540-dev1`, after normal promotion #14363. The authenticated remote HTTP
  result adapter and explicit client method merged in #14365 after a fresh
  approval, exact-source CI and Linux/Windows native qualification. All 12
  non-changefile paths match Main `a2c673b4b3ed`, Bridge `0.9.542-dev1`,
  after normal promotion. Explicit-host CLI read/wait adoption merged in
  #14370 after a clean approval, required CI and both native jobs. All ten
  non-changefile paths match Main `6cf7f63dadf6`, Bridge `0.9.543-dev1`,
  after normal promotion. The carrier-only exact native prompt provider
  merged in #14380 after exact-head approval, required CI and both native
  jobs. All 16 non-changefile paths match Main `88fc5bc7eca1`, Bridge
  `0.9.546-dev1`, after normal promotion. The additive HTTP native-prompt
  adapter and explicit client method merged in #14397 after fresh approval,
  required strict CI and both native   jobs. All 15 non-changefile paths match Main
  `5cb8a030cf283f0249c8b0b783c471e6bb61def0`, Bridge
  `0.9.550-dev1`, after normal promotion `45414806842`.
  The opt-in explicit-host managed send consumer is prepared and passes its
  initial contained checks. Exact-source required CI, native qualification
  and review remain before publication can be reported as delivery.
  The original
  application materializer correction is verified in Main from #14339;
  its independently owned #14349 follow-up and regression match that same
  Main snapshot. Full-effort release closure remains open; no competing
  materializer implementation was added.
  The carrier expansion did not migrate CLI or HTTP callers. The subsequent
  HTTP expansion does not change CLI selection or defaults.
  Default migration, remote sends, effectful selectors and potential
  targets follow.
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

For the remote-result provider PR only, the operator approved this separate
question, verbatim:

> The next Bridge PR includes the retained merge journal and the new remote-result provider. The installed publisher's multi-commit squash drops the required commit trailers.
>
> May I make one local history normalization in the existing source worktree: preserve a backup branch, soft-reset only its unpublished commits to their merge base, and create one commit with an identical tree, the public commit identity and both required trailers? Normal PR review and CI still apply. This excludes hard reset, shared-history rewrite, source discard and runtime changes.

Response: **true**. This permits one backup-preserving normalization for this
provider PR, not later slices. The driving owner retains the original response
and actual-use evidence; all independent holds and publication gates remain.

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

### Native validation and release source prerequisites

Source: the authenticated operator's `prerequisite_source_scope` chooser.
The tool supplied no exact response timestamp. Question, verbatim:

> ThomasMichon/copilot-extensions#14329 is approved and required CI passes. Two prerequisites outside the Bridge source slice remain:
>
> 1. An additional Windows job timed out in Dispatch’s 49-task telemetry fixture. The local probe exhausted its 600-second preparation/execution budget, so it did not confirm or clear that failure.
> 2. Normal release is blocked by ThomasMichon/copilot-extensions#14331: the materializer rejects an embedded library with two legitimate packages.
>
> May I take on these source prerequisites, including diagnosis, fixes, relevant tests, normal PR review/CI, rebases and revalidation after reviewed corrections until the selected gates clear? I will preserve the workload, assertions and timeout budgets. All Driver-core, installed-runtime, installer, receiver, queue, credential and resource holds remain. No CI bypass is included.

Response: **`both`**, selecting **Resolve both prerequisites through normal
source gates (recommended)**. The offered alternatives selected Dispatch alone,
materialization alone, or the unchanged Bridge scope. The driving owner retains
the original chooser and response.

This authorizes source qualification and directly relevant corrections for
Dispatch's native telemetry validation path and the release materializer's
multi-package rejection, with the stated normal delivery and revalidation
follow-through until those gates clear. Keep local runs serial under their
existing budgets; preserve all 49 task requests, assertions and timeout bounds.
Do not remove legitimate library packages to satisfy the materializer.
Existing owners retain their source/resource custody; reconcile any overlapping
fix rather than opening a competing change. No Driver-core change, installed
runtime or service intervention, installer operation, receiver enrollment,
queue replay, credential/access change, private-worker activation, claim
override, review bypass outside the normal resolved role, or CI bypass is added.

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
- [x] Qualify opt-in exact local read/wait observations over the existing bounded
  result owners, including native content origin and the shared deadline (#14227).
- [x] Qualify opt-in exact local ordinary sends through the existing managed
  turn and registered inbox owners, preserving unknown admission and legacy
  defaults (#14273).
- [x] Qualify and release the provider-first remote native-result operation
  over the existing carrier, with exact identity, shared bounds/deadlines and
  truthful prior-peer refusal, before migrating remote read/wait callers.
  The source foundation merged in #14329 and matches normal Main promotion
  `5fac4d477175` (#14363), Bridge `0.9.540-dev1`.
- [x] Qualify opt-in explicit-host CLI read/wait observations over the released
  HTTP/carrier provider, including remote post-snapshot registration checks,
  exact host/owner/native binding and prior-peer refusal (#14370).
- [x] Qualify and release exact keyed native prompt admission over one carrier
  attempt, preserving no-resume, request framing, original identity/key and
  refusal versus unknown admission (#14380). All 16 non-changefile paths
  match Main `88fc5bc7eca1`, Bridge `0.9.546-dev1`.
- [x] Qualify the additive HTTP native-prompt adapter and explicit client
  method, preserving one POST, exact binding, unknown admission and prior-peer
  refusal (#14397). All 15 non-changefile paths match Main
  `5cb8a030cf283f0249c8b0b783c471e6bb61def0`, Bridge `0.9.550-dev1`.
- [ ] Qualify opt-in explicit-host managed ordinary sends after that release:
  exact host/owner/native/key admission, JSON/no-wait receipts and a bounded
  completed-turn text wait. Managed event streaming and remote exact registered
  inboxes need their own provider contracts; do not silently select them.
- [ ] Route the remaining ordinary send, read, wait, resume, stop and end selectors
  through the common bridge identity with equivalent ACP and Mux semantics.
  Opt-in local and explicit-host read/wait have landed; default migration,
  remote sends and lifecycle selectors remain open.
  The remote HTTP adapter merged in #14365 and matches normal Main promotion
  `a2c673b4b3ed`, Bridge `0.9.542-dev1`. Explicit-host CLI observations merged
  in #14370 and match Main `6cf7f63dadf6`, Bridge `0.9.543-dev1`.
  The carrier-only exact native prompt provider merged and released in
  #14380. Its additive HTTP admission adapter merged in #14397.
  Both providers' normal source releases are verified. Explicit-host
  managed send qualification is the next consumer slice.
  The existing remote create operation is not a prompt-submission fallback.
  Negotiate the serving daemon's
  capability on every attempt; pin host, result owner and native identity,
  retain bounds and the shared deadline, and preserve explicit prior-peer
  refusal. Do not select result-owner IDs from a different host's merged row.
  Legacy history, event-cursor, attention and workspace
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

- [x] Resolve the approved Dispatch native-validation prerequisite in #14329:
  both native jobs pass with the original workload, assertions, timeouts and
  source/resource owners preserved.
- [x] Qualify the original multi-package emission prerequisite: #14339's
  materializer matches normal Main promotion `60abcf8e8e7c`. The separate
  #14349 source-resource follow-up and regression match Main `5fac4d477175`.
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

Dated pages retain every original journal entry.

- [2026-11-24](journal/2026/11.24-remote-results-and-validation.md) - Result bounds, delivery and validation.
- [2026-11-23](journal/2026/11.23-exact-observations-and-sends.md) - Exact inspection, observations and sends.
- [2026-11-22](journal/2026/11.22-inventory-and-selectors.md) - Fleet inventory and selectors.
- [2026-11-21](journal/2026/11.21-inception.md) - Initial scope and coordination.
