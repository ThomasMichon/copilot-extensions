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
- [ ] Provide initial normalized load and section-aware incremental sync using
  the existing provider and credential authorities.
  Next is the finite [authorized acquisition slice](acquisition-slice.md),
  extending the existing provider reader without another HTTP client.
- [ ] Add coherent cache generations, completeness/freshness, bounded recovery
  and materialized local read views.
- [ ] Attach exact shallow source/head/common-baseline workspaces through the
  existing source owner; retain previous review generations and refs.
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
