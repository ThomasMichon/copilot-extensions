# PR Review Workspaces

- **Slug:** `pr-review-workspaces`
- **Repo:** copilot-extensions
- **Branch(es):** Planning proposal, then separately admitted implementation slices against `dev`
- **Created:** 2026-10-29
- **Status:** Active
- **Vision:** `visions/plugins/agent-pull-requests/README.md` and `visions/plugins/agent-worktrees/pull-requests/README.md`
- **Umbrella issue:** #9535
- **Related owners:** #6007 / #7188 (PR service), #7051 (provider conformance)

## Guiding Intent

Let one reviewer obtain PR data, analyze exact source, synchronize subsequent
changes, and submit its selected comments and verdict through one PR-domain
toolset. The successor `agent-pull-requests` plugin owns this capability. Its
existing service is the candidate owner of a shared caching database; a client
can materialize a coherent local representation without another agent
assembling and interpreting bespoke review packets.

The planning baseline cleared review in #9537. Source implementation is now
admitted in incremental, reviewed slices under #9535. Installation, updates,
deployment, credential changes and live review effects remain separately gated.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning coordinator | Capture intent, reconcile existing owners, publish the planning proposal | This effort and #9535 |
| PR-domain implementer | Own the admitted source-only read-generation slice and its tests | #9535 |
| Reviewer integrator | Future admitted consumer and end-to-end qualification; not assigned by this proposal | Separately claimed source issue |

## Coordination

- **Topology:** A documentation-only proposal followed by independent, reviewed
  source slices against `dev`; implementation admission is separate.
- **Host (owns proposal PR):** Planning coordinator.
- **Delegates:** One PR-domain implementer; no recursive delegation.
- **Existing owners:** Preserve the PR service/watch/registration scope of
  #6007 and #7188, #7051 conformance, credential providers, service lifecycle,
  and Worktrees source/publication/resource authority. Extend their contracts,
  not their campaigns or authorization.
- **Handoff:** Continue from this README and its linked architecture proposal.
  Future participants claim concrete slices before editing overlapping source.
- **Boundary:** No second watcher, credential issuer, review task queue,
  posting ledger, service activation, or fleet rollout is created by this plan.

## Context

The existing PR capability already has versioned native snapshots, full-thread
reads and provider/account resolution. `agent-pull-requests` has a thin
provider-compatible client and an existing watch/service ownership boundary.
These are reuse points, not proof that a complete PR workspace/cache or
review-submission interface already exists.

Relevant current references:

- [PR plugin](../../../plugins/agent-pull-requests/README.md)
- [Native observation](../../../plugins/agent-worktrees/docs/pr-observation.md)
- [Existing service campaign](../durable-pr-watch-delegation/README.md)
- [PR lifecycle conformance](../pr-lifecycle-conformance/README.md)
- [Service contract evolution](../../../docs/patterns/service-contract-evolution.md)

The proposal advances the existing provider-neutral reviewer and first-class
PR-service intent. New guarantees about coherent workspaces, incremental
synchronization and cache/receipt separation should be reconciled into the
existing owning visions before runtime realization. Do not copy implementation
schemas or progress into those visions.

## Request

**Summary of settled operator direction:** one agent should be able to read PR
data, analyze it and post its review through tools. Provide an initial local
representation and subsequent incremental synchronization. An interim ADO
agent may update the feed and post an explicit review, but should not remain a
semantic evidence-translation layer. ADO is the first usable end-to-end slice;
the design remains provider-neutral. Place the capability in the successor
`agent-pull-requests` plugin/toolset and consider its service as the caching
database host. Capture the remaining vision and architecture build-out in this
canonical effort.

The verbatim operator inputs, scope selections and preceding design discussion
are preserved in [inception-transcript.md](inception-transcript.md).

## Plan

The decomposition and technical acceptance below are **agent-recommended**.
The source implementation continuation is admitted; operational effects are not.

### Phase 0 - Capture and review the planning baseline
- [x] Confirm `pr-review-workspaces`, canonical source placement and ADO-first
  scope; capture the request without private deployment context.
- [x] Check existing efforts/issues and retain service/provider/lifecycle owners.
- [x] Record the [architecture proposal](architecture.md), risks and validation
  plan; distinguish desired behavior from existing source capabilities.
- [x] Land the documentation-only proposal through ordinary review and CI (#9537).
  This does not admit runtime implementation or rollout.

### Phase 1 - Reconcile standing intent and architecture
- [x] Reconcile the existing PR plugin/capability visions; revise their
  intent-level features and behaviors where this proposal extends them.
- [ ] Specify PR-domain ownership, service-backed cache and client workspace
  roles without retaining a parallel forge implementation.
- [ ] Resolve the proposal's explicit architecture decisions and document
  capability discovery, supported peer behavior and expand/migrate/contract.
- [ ] Admit concrete implementation slices through their own reviewed plans
  and source claims; keep operational rollout separately authorized.
  The first bounded [read-generation slice](read-generation-slice.md) is claimed
  under #9535; its contract amendment cleared review in #9641 before source
  realization. Further cache/acquisition/submission contracts remain open.

### Phase 2 - Build the ADO read and source path after admission
- [x] Implement the admitted offline read-generation builder and comparison
  entry point with synthetic contract tests; live acquisition remains open below.
- [x] Implement the reviewed protected-owner embedding and shared provider
  collection seam; actual owner adoption and live qualification remain open.
- [ ] Provide initial normalized load and section-aware incremental sync using
  the existing provider and credential authorities.
  Next is the finite [authorized acquisition slice](acquisition-slice.md),
  extending the existing provider reader without another HTTP client.
  Its source is merged in #9667; live qualification requires an actual trusted
  owner. The [owner-executed adapter proposal](owner-read-adapter.md) addresses
  adopters without a qualified typed credential owner, without inventing one.
- [ ] Add coherent cache generations, completeness/freshness, bounded recovery
  and materialized local read views.
  The [immutable generation-cache slice](generation-cache-slice.md) merged in
  #9693, reusing the existing PR storage owner and keeping operation receipts
  separate. Service/client adoption and materialized source views remain open.
- [x] Implement the reviewed source-only immutable generation cache, scoped CAS,
  retained lookup, explicit bounded recovery and receipt-isolation controls.
- [ ] Attach exact shallow source/head/common-baseline workspaces through the
  existing source owner; retain previous review generations and refs.
  The [exact local source-attachment slice](source-attachment-slice.md) starts
  with already prepared objects through the existing workspace/resource owner;
  it does not invent a fetcher or silently adopt a read-only reference checkout.
- [ ] Preserve existing GitHub behavior and use a provider-neutral contract.

### Phase 3 - Build explicit submission and reconciliation after admission
- [ ] Accept selected comments, owned-thread updates and a named verdict bound
  to the reviewed candidate; preserve caller/repository authority boundaries.
- [ ] Perform fresh required checks and readback inside the operation, without
  coupling review authorization to unrelated merge-readiness requirements.
- [ ] Preserve completion settings and other reviewers' state; represent
  confirmed, unattempted and unknown partial outcomes without blind replay.
- [ ] Keep durable submission intent/receipts separate from rebuildable cache
  data, even if they share an owning service or physical database.

### Phase 4 - Integrate and qualify the reviewer after admission
- [ ] Let the reviewer own load/sync, local analysis and explicit submission
  without an ADO interpretation intermediary.
- [ ] Preserve caller-owned rubric, consent and required independent finding
  checks; cache contents or a review-plan file are not authority grants.
- [ ] Prove one eligible ADO review end-to-end, then qualify concurrent
  independent reviewers and unchanged-head follow-up.
- [ ] Record release and separately authorized installed/serving qualification;
  resolve or transfer every remaining acceptance item before completion.

## Validation Plan

- [x] Planning capture matches the operator inputs; all additions remain
  visibly agent-recommended and public artifacts contain no private context.
- [ ] Initial load, no-change sync, author reply/edit/deletion, new source head
  and target-only movement preserve honest revision and completeness evidence.
- [ ] Pagination omissions, partial reads and unsupported deltas never appear
  as empty-success evidence; explicit bounded section refresh is supported.
- [ ] Cache scope separates provider, endpoint, repository and account/principal;
  credentials are renewed per request/page through their existing owner.
- [ ] Interrupted/concurrent sync cannot publish a half-written current
  generation or hide a newly observed head behind old source.
- [ ] Exact source/head/base/path/blob mapping, renames, deletions and binary
  coverage remain explicit, with no merge, build or dependency restore.
- [ ] Changed-head submission refuses or reports the provider's actual race
  limitation; no atomic multi-action guarantee is fabricated.
- [ ] Lost responses and partial review batches preserve platform IDs and
  unknown outcomes; retry does not duplicate confirmed comments or votes.
- [ ] Cache rebuild/eviction cannot erase pending/unknown submission receipts,
  revive canceled work, or create posting authority.
- [ ] Native and service clients use compatible semantics, explicit capability
  discovery and honest unavailable-backend behavior without silent fallback.
- [ ] One real eligible ADO review covers load, sync, source analysis, required
  independent finding check, submission and verified readback; stage timings
  are generated by operations rather than reconstructed later.
- [ ] Existing observation/subscription, provider, publication and service
  lifecycle consumers retain their own conformance and rollout gates.

## Proposal

[Architecture proposal](architecture.md) records the current working design,
including service-backed caching, local materialization, source attachment,
submission recovery and remaining decisions. It is not a shipped CLI or wire
contract.

## Journal

### 2026-10-30 - Exact source contract and implementation continuation
- Contract #9703 merged as `5267c7010200a53acaee48dfe9e9e6876a2cb790`
  after an actual current-head approval, resolved feedback and passing CI.
  Corrected the distinction between absent paths and existing zero-byte blobs.
- Implement the admitted Worktrees local source owner and separate PR-domain
  attachment envelope over already prepared objects. Extend the existing bounded
  process owner with opt-in exact binary capture; preserve default text behavior,
  lifetime cleanup and per-stream limits rather than adding a second owner.
- Initial contained controls: shared process owner 88 passed / 12 platform skips;
  source/tree contracts 52 passed / 2 existing opt-in skips. Final source and
  malformed-result controls remain in progress; this is not effort completion.
- PR #9708 review identified four legitimate mapping/effect/aliasing/performance
  findings. Protect the validated scope from in-place owner request mutation,
  require a common-side object for original paths, disable internal Git hooks
  through the existing Worktrees mechanical policy, and batch tree/blob IO.
  Twenty actual synthetic source controls pass, including hostile owner mutation,
  a real reference-hook witness, misleading rename coverage, 108-path bounded
  process counts and deep Windows ref paths. Fix the two CI source-admission/
  generated-archive failures through their existing manifest/generator owners;
  no failing gate is waived.

### 2026-10-30 - Priority cursorless native change paging
- Merged source PR #9697 as `eef6ddbd87f1c708a6ac334fa622f1dfa7abf44e`
  after 112 contained tests, lint/install-contract gates, passing required CI
  and assessed review. Fixed the review's documentation-impact omission.
  Publication is not release, installation or live-owner qualification.
- Preserved the open cache implementation PR and its remaining review findings
  while isolating native response compatibility under the same source objective.
- Native change pages may omit both continuation fields. Probe with the actual
  consumed-record offset and unchanged requested page size until an explicit
  empty page; never infer completion from a short nonempty page.
- Keep partially supplied/malformed cursors, repeated pages, nonadvancing cursors,
  HTTPS/redirect, deadline and payload/record/page holds unchanged. Synthetic
  credential and protected-owner paths prove reader-to-generation completeness
  only after a real terminal witness, with owner checkpoints on physical pages.
- The separate warm-host large-response framing gap is already tracked in
  #1803. It is not fixed or broadened into this source slice; no installed
  transport, authentication or service state is changed.

### 2026-10-30 - Resume owned cache review after native compatibility
- Restored the held #9693 implementation onto the merged compatibility base.
  The original cache issue/PR remain owned; no competing cache PR is opened.
- Address the two remaining review findings through explicit lock-held staging
  recovery and post-retirement tombstone reclamation. Preserve unrelated-file
  refusal, uncertain-clear ordering and replay/operation receipt isolation.
- Merged #9693 as `f337b407a4d799544961ff5c12be6a2e83db6ab7` after both
  remaining findings were fixed, 69 contained cache/workspace controls passed,
  lint/install-contract gates passed, and the fresh current-head review
  explicitly approved with no active issues alongside passing required CI.
- The source-only cache subtask is complete, not this effort or Phase 2.
  Continue the original exact-source attachment contract/implementation next;
  submission/reconciliation and actual adopter qualification remain open.
  No release, installed runtime, backend effect or service activation is claimed.

### 2026-10-30 - Priority native nullable-properties compatibility
- Isolated the normalizer correction from the unpublished cache implementation,
  preserving that cache source checkpoint for continuation.
- Native discussion property bags may validly be null. Preserve that explicit
  null separately from absence, retain existing dictionary filtering, and still
  reject scalar/list bags. Wholly synthetic fixtures exercise actual generation,
  source CLI load and offline comparison, not a bypassed record normalizer.
- The focused source packet passed 68 cases. Backend qualification/retest remains
  with the existing authorized owner; this source lane makes no claim about a
  particular live failure frame and performs no live backend or auth changes.
- #9689 merged as `216d31d4acd897013ad2a3411442a8c3514d97d4` after
  current-head Copilot approval and required green checks; the contained focused
  packet also passed 68 cases. Re-read the parent plan and retained all remaining
  live/source/cache/submission acceptance items.
- Prepared a committed-source SDK archive for the adopter's captured-data and
  read-only retest. That test, not the source correction alone, determines the
  particular live outcome. Cache continuation remains preserved and separate.

### 2026-10-30 - Cache contract merged; bounded storage source
- #9685 merged as `92473fa43ebcbdf27303c15932735734b4fb9d21` after
  current-head Copilot approval and required green checks. Re-read the canonical
  Plan/Validation Plan and reviewed storage contract before implementation.
- Implemented explicit private/native or service-embedded generation storage
  under the dedicated cache child, reusing atomic JSON and operation locks.
  Complete target-scope keys, immutable objects and CAS pointers retain prior
  state on conflict/quota/pre-replace failure and expose post-replace uncertainty.
- Added retained lookup, explicit valid-cache rebuild with durable pointer
  tombstones, namespace/service/descriptor checks and linked-path refusal.
  Existing replay and unknown-operation sentinel bytes remain untouched.
- The source packet passed 82 cases across real file/lock/concurrency/recovery
  controls and existing generation/owner tests. No provider, daemon, live backend,
  installed-cache adoption or operation effect was invoked.
- Resumed the preserved cache checkpoint after the isolated native compatibility
  correction. The current cache/generation/owner packet passed 87 cases while
  retaining the merged nullable-properties behavior.
- Review found that large scope strings could produce an unreadable current
  pointer despite fitting storage quota. Added a pre-write encoded-pointer bound
  and proved refusal leaves both artifact sets/current lookup/rebuild intact.
  The updated source packet passed 88 cases and contained cache controls passed 20.

### 2026-10-30 - Protected embedding merged; next cache contract
- #9676 merged as `69064505eb9db002e462f000bcf4d8aa42ab4f47` after
  current-head Copilot approval, resolved feedback and required green checks.
  Review fixes covered malformed generation comparison, final identity-switch
  refusal and unchanged in-collection owner permission errors.
- Re-read the parent Plan/Validation Plan. The current source packet passed
  205 cases; contained validation covered 141 Worktrees and 63 PR-domain cases,
  followed by 29 final owner-propagation controls. These are source receipts,
  not actual owner/live qualification or installed rollout.
- Existing backend permission and input gates remain authoritative; context
  isolation is not an extra permission or delegation-time/read-count limit.
  Interface unavailability is distinct from an actual backend permission denial.
- Proposed the next bounded immutable-cache contract, reusing current JSON,
  atomic persistence and lock ownership without a new service/database or
  operation ledger. Implementation awaits that contract's review.

### 2026-10-30 - Protected owner embedding source
- #9670 merged as `9466727cd34e9eed526dc60e1dc4c7b78db4c05d` after
  current-head Copilot approval and required green checks. Re-read the parent
  plan before implementing the reviewed generic contract.
- Added a strict small owner request, protected binding/handler callbacks and
  explicit owner work/payload limits. The offline inspector reports no
  operational owner or backend access; it cannot authorize an operation.
- Reused the native acquisition orchestration through an owner reader seam,
  without a credential DTO, issuer or second HTTP/pagination implementation.
  Added local comparison of returned immutable generations.
- Made the principal decoder/metadata explicit and shared: only full native
  `connectionData.authorizedUser.id` establishes effective scope. Delegated
  `authenticatedUser.id` may differ and cannot be a fallback. Both profiles
  carry synthetic positive and refusal controls for that distinction.
- Generic component, scope/authority-loss, budget/payload, parser and delegated-
  identity controls passed: 198 in the owned source environment, then 140
  Worktrees and 58 PR-domain cases through the contained runners. The install
  contract gate also passed. Actual protected
  owner transport/adoption is still an external qualification prerequisite;
  no live ADO call or deployment occurred in this source lane.
- Review corrections reject correctly hashed but structurally malformed
  generations before comparison, with structured CLI errors, and prove that a
  principal switch seen only by the final identity probe prevents publication.
  The current source packet passed 204 cases, including unchanged propagation
  of an actual owner permission denial separately from interface unavailability.
- A cold native multi-helper fixture exhausted its old outer setup-inclusive
  budget after one synthetic request. Its bounded outer fixture/cleanup allowance
  now covers cold SDK/venv startup; production credential/owner work deadlines
  are unchanged. Re-ran the native controls and complete focused packet.
- Clarified that agent/process context isolation is not an extra permission,
  issuer, provenance, read-count or delegation-time requirement. The embedding
  reuses existing authorized owner gates; its explicit work/payload contracts
  remain separate.
- Follow-up review found the provider checkpoint wrapper still reclassified an
  actual `PermissionError`. Removed that conversion and proved an in-collection
  revocation preserves the original owner exception and stops further reads.
  The updated complete source packet passed 205 cases.

### 2026-10-30 - Acquisition merged; live owner adoption remains gated
- #9667 merged as `14ef6f358ad3dfa0991ea0e9bcfeca63d0172755` after
  current-head Copilot approval, resolved feedback and required green checks.
  The current source packet passed 134 cases; final contained acquisition
  controls passed 21 cases after the pagination correction.
- Re-read the canonical plan. The acquisition source is delivered, not a
  completed live ADO review or campaign. Publication does not install an issuer
  or make an owner API available.
- The credential consumption shape and full ADO principal namespace are now
  explicit. Authentication or secret retrieval alone cannot supply its missing
  policy/account/target authority; do not manufacture a typed binding.
- Proposed a minimal owner-executed read adapter through the existing owner's
  protected pipeline, retaining credentials there and reusing acquisition/
  normalization rather than creating another issuer or HTTP client. Generic
  contract review and actual owner implementation/adoption remain prerequisites.

### 2026-10-30 - Offline slice merged; acquisition implementation
- #9647 merged as `fdd60cfc27c6cf14c803e8b610c6462dbc9d6efe` after
  current-head Copilot approval, all five findings fixed/resolved, and required
  green checks including eligible units with coverage. The final contained
  workspace regression packet passed 39 cases; the isolated source CLI smoke
  passed without installed-runtime selection.
- Re-read this README and its remaining acceptance items. The offline slice is
  delivered; the campaign remains Active. Its reviewed acquisition amendment
  now admits the next finite source slice, not operational rollout.
- Implementing the provider-owned exporter using existing typed observe
  credentials and HTTP reader. Each new opaque credential is bound to an
  observed provider principal before PR reads; retained identity digests are
  operation-local, not a credential store or another issuer.
- Added fixed identity and iteration-change reads, explicit pagination evidence,
  fresh metadata/iteration bracketing and source-selected CLI capabilities.
  The contained acquisition/existing-observation packet passed 89 cases; all
  17 current exporter controls passed in the owned isolated source environment,
  including additional wrong-PR/scope and total-byte-budget negatives.
- Touched Python lint, quiescent install-contract and documentation-consistency
  checks passed. Actual source-module capabilities and a pre-network invalid-
  budget refusal passed. No live backend call, source attachment, submission
  or service effect has occurred; live qualification needs the caller's existing
  authorized typed observation-credential command.
- Acquisition review identified terminal-page replay being accepted as complete
  and the helper's fixed timeout exceeding the caller's remaining budget.
  Corrected replay detection before appending/termination and passed the shared
  deadline to the existing credential-command owner, preserving its bounded
  cleanup and unchanged legacy default. The focused source packet passed
  132 cases, including a real slow-helper retirement control.
- Clarified that the CLI consumes a credential DTO, not a shipped ADO issuer:
  adopters must supply their existing authorized owner/adapter. The expected
  principal is the full ADO `authorizedUser.id`, not an AAD identity or label.
- Follow-up review found that continuation `nextTop` was validated but not used.
  Subsequent page requests now honor both native continuation values, with a
  differing-page-size regression and explicit oversized-continuation refusal.

### 2026-10-30 - Offline read-generation source candidate
- #9641 merged after current-head Copilot approval with no findings and required
  green checks. Re-read this effort before starting the admitted source slice.
- Implemented the pure ADO adapter and strict offline module CLI, retaining
  scope, completion presence, discussion contents/deletions and revision binding.
- Focused contained validation passed 94 tests across the new workspace cases
  and existing CLI/native CLI contracts; touched Python lint and install-contract
  checks passed. Actual source-module capabilities and sample load passed without
  runtime installation or backend effects.
- The candidate has no live acquisition, source attachment, database publication
  or submission capability. Those obligations and real eligible ADO qualification
  remain open; offline source completion is not campaign completion or rollout.
- Follow-up input hardening rejects duplicate JSON fields; all 30 focused
  workspace tests pass. The local whole-unit packet found a Windows-incompatible
  POSIX signal fixture after 2,873 passing cases. Corrected its mocked signal
  namespace and refreshed only that admitted source hash; all 64 cases in the
  affected unit file pass. Full required-packet acceptance remains the ordinary
  CI gate; the failed local run is not passing proof.
- Proposed the next finite provider-owned acquisition slice for review alongside
  this source candidate; it retains existing transport and credential owners.
- A consumer reported 14 offline replay scenarios passing for the initial
  immutable candidate, including omitted policy evaluations and distinct absent/
  null/zero votes. This is not live acquisition or independently executed
  reviewer qualification.
- The source PR's first current-head CI passed, including eligible units with
  coverage. Its code review found five real input/revision issues: endpoint
  parser error disclosure, a newer nonmatching iteration hidden by historical
  matching data, unusable changed paths, non-round-trippable size bounds and a
  null sync baseline. Added focused fixes and regressions rather than treating
  the clean consumer replay as proof those edge cases were sound.

### 2026-10-30 - Source continuation admitted
- Source implementation was explicitly authorized, with incremental candidates
  for reviewer feedback; installation and live review effects remain separate.
- Re-read the canonical proposal and existing service/provider owners. Claimed
  the initial read-generation slice under #9535 rather than duplicating the effort.
- Reconciled the owning visions and specified the bounded first slice for review
  before implementation. This is not completion of the full workspace campaign.

### 2026-10-29 - Canonical planning capture
- Confirmed the effort slug, canonical source home and ADO-first slice.
- Claimed #9535 for planning/documentation only and checked existing related
  campaigns rather than assigning a second service/watch owner.
- Captured the operator direction and a clearly separated agent-recommended
  architecture and validation plan. Runtime and operational effects remain
  outside this capture.
