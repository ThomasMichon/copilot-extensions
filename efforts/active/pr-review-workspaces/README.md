# PR Review Workspaces

- **Slug:** `pr-review-workspaces`
- **Repo:** copilot-extensions
- **Branch(es):** Planning proposal, then separately admitted implementation slices against `dev`
- **Created:** 2026-10-29
- **Status:** Draft
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

Capture the vision and architecture build-out now. This effort is a reviewed
planning proposal, not authorization to implement, install, update, deploy,
change credentials, or perform live review effects.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning coordinator | Capture intent, reconcile existing owners, publish the planning proposal | This effort and #9535 |
| PR-domain implementer | Future admitted workspace/cache/provider slices; not assigned by this proposal | Separately claimed source issue |
| Reviewer integrator | Future admitted consumer and end-to-end qualification; not assigned by this proposal | Separately claimed source issue |

## Coordination

- **Topology:** A documentation-only proposal followed by independent, reviewed
  source slices against `dev`; implementation admission is separate.
- **Host (owns proposal PR):** Planning coordinator.
- **Delegates:** None assigned for this capture.
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

The requested capture is planning-only. The decomposition and technical
acceptance below are **agent-recommended**, not additional operator grants.

### Phase 0 - Capture and review the planning baseline
- [x] Confirm `pr-review-workspaces`, canonical source placement and ADO-first
  scope; capture the request without private deployment context.
- [x] Check existing efforts/issues and retain service/provider/lifecycle owners.
- [x] Record the [architecture proposal](architecture.md), risks and validation
  plan; distinguish desired behavior from existing source capabilities.
- [ ] Land the documentation-only proposal through ordinary review and CI.
  This does not admit runtime implementation or rollout.

### Phase 1 - Reconcile standing intent and architecture
- [ ] Reconcile the existing PR plugin/capability visions; revise their
  intent-level features and behaviors where this proposal extends them.
- [ ] Specify PR-domain ownership, service-backed cache and client workspace
  roles without retaining a parallel forge implementation.
- [ ] Resolve the proposal's explicit architecture decisions and document
  capability discovery, supported peer behavior and expand/migrate/contract.
- [ ] Admit concrete implementation slices through their own reviewed plans
  and source claims; keep operational rollout separately authorized.

### Phase 2 - Build the ADO read and source path after admission
- [ ] Provide initial normalized load and section-aware incremental sync using
  the existing provider and credential authorities.
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

- [ ] Planning capture matches the operator inputs; all additions remain
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

### 2026-10-29 - Canonical planning capture
- Confirmed the effort slug, canonical source home and ADO-first slice.
- Claimed #9535 for planning/documentation only and checked existing related
  campaigns rather than assigning a second service/watch owner.
- Captured the operator direction and a clearly separated agent-recommended
  architecture and validation plan. Runtime and operational effects remain
  outside this capture.
