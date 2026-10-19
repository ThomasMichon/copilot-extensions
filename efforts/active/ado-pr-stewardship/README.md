# ADO PR Stewardship

- **Slug:** `ado-pr-stewardship`
- **Repo:** copilot-extensions
- **Branch(es):** Serial proposal and bounded implementation PRs against `dev`
- **Created:** 2026-10-18
- **Status:** Draft
- **Vision:** `visions/plugins/agent-worktrees/pull-requests/README.md`:
  `one-surface-no-forking`, `reviewer-capable-provider`,
  `repository-declared-pr-requirements`,
  `durable-shared-pr-transition-subscriptions`
- **Umbrella issue:** #7203

## Guiding Intent

An agent should drive an Azure DevOps PR through the existing `pr-*` commands
and shared PR observation owner until the remaining requirements genuinely need
a human. Forge mechanics belong to the ADO provider; repository requirements,
feature selection and supplemental agent guidance belong to related-repository
configuration. A consumer-specific polling program is not another PR driver.

Persistent reviewer votes and a qualifying approval after the latest source
push are separate evidence. Neither an old approval's continued validity nor
an approval's lack of commit binding proves that the latest-push requirement
was met. Discussion resolution retains its native thread and iteration identity.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Stewardship driver | Owns proposal, provider/config integration, validation and serial PRs | #7203 |
| PR owner integration delegate | Maps and integrates existing shared observation seams | Driver-coordinated bounded slice |
| Existing lifecycle owners | Retain discussion/publication recovery and durable delivery/lifecycle | #7051 and #6007 |

## Coordination

- **Topology:** serial, independently reviewable PRs against `dev`.
- **Host (owns PRs):** Stewardship driver.
- **Delegates:** bounded evidence or implementation slices with exclusive file
  ownership; no delegate publishes from a concurrently edited shared worktree.
- **Handoff:** journal completed changes, live obligations and exactly one next
  slice; an open PR or completed phase is not the effort's completion gate.
- Preserve #7051's addressable discussions and candidate-fenced publication,
  #6007's delivery/installation/lifecycle work, and #6818's broader latency/wait
  planning. This effort adds gate evidence and observation integration; it does
  not take over those campaigns.
- Repository-specific adoption stays outside this public repository. Its
  consumers use this contract, not a second implementation of it.

## Context

The existing ADO snapshot maps current reviewer votes to synthetic chronological
review IDs and reads PR metadata, but does not populate build/policy evidence.
An unchanged reviewer count can therefore hide a changed vote from a
review-ID-watermark waiter. Missing review commit metadata cannot establish
approval-after-push freshness.

The reusable seams are `PRProvider`, `PRSnapshot`, the shared `pr_contract`,
repository `PRConfig` and related-repository overlays, and the existing
`agent-pull-requests` observation owner. Extend these without creating a new
daemon, credential store, subscription lifecycle or caller-owned forge client.

The existing lifecycle proposal explicitly preserves provider distinctions:
an ADO current vote is not an ordered review-history record. This effort closes
that observation mismatch without weakening publication fences.

## Request

Public-safe capture of the operator's implementation request, with private
repository and policy identities omitted:

> There should not be a dedicated consumer-side PR watcher. Everything belongs
> in `pr-watch`, with an ADO driver, repository configuration enabling or
> disabling features and requirements, and supplemental agent guidance.
>
> Require at least one non-author approval, no blocking or rejected reviews,
> and no unresolved comments. Separately require an approval after the latest
> HEAD was posted, from anyone; this is distinct from the non-author minimum.
> Reviews and approvals persist across revisions unless the
> provider resets a required review when its owned code changes; do not impose
> exact-head approval or comment-posting semantics.
>
> Required reviewer sets each need an approval. The same approval may satisfy
> multiple sets. Some CI policies depend on other requirements, including
> latest-push approval, required-owner reviews and comment resolution;
> configuration should identify those composite policies for monitoring.
>
> Comments retain their revision-round associations, and their resolution
> status is more important than reducing all evidence to an overall verdict.
> Reflect these rules in centralized `pr-*` tools and `agent-pull-requests`;
> retire consumer-specific PR tools, or implement genuine extensions behind
> that central surface.

The operator confirmed the slug `ado-pr-stewardship`. The implementation
decomposition and validation methods below are **agent-recommended**; they
preserve the requested behavior rather than adding automatic human actions.

## Plan

### Phase 1 - Reviewed contract and coordination

- [ ] Review and merge this proposal and the corresponding vision clarification
  before implementing runtime behavior.
- [ ] Record the reusable provider/configuration/owner extension points and
  coordinate the additive slice with the existing lifecycle and watch owners.

### Phase 2 - Repository requirements and normalized evidence

- [ ] Extend existing related-repository PR configuration for selected observation
  features, approval/discussion requirements, human-only gates, composite-policy
  identification and supplemental agent guidance. Preserve overlay precedence.
- [ ] Define additive, capability-honest gate/readiness evidence with known,
  unknown and incomplete states; keep persistent votes, approval-after-push,
  overlapping required sets, unresolved threads, builds and policies distinct.
- [ ] Preserve native review persistence and provider review-reset behavior.
  New pushes invalidate latest-push evidence, not every historical approval.
  Do not loosen the existing analyzed-candidate fence for publishing a review.

### Phase 3 - ADO driver and centralized observation

- [ ] Read complete, current ADO PR/iteration/reviewer/thread/policy/build/status
  evidence through the existing internally authenticated provider transport.
  Retain stable actor, object, group and iteration identities.
- [ ] Establish approval-after-push from authoritative provider events or native
  policy evidence. Missing or ambiguous evidence stays unknown; a locally
  observed vote or current positive vote alone cannot fabricate freshness.
- [ ] Replace chronological-ID assumptions for ADO vote changes. Detect changed
  votes, fresh same-actor approval, thread resolution/replies, policy/build
  transitions and source pushes through the existing wait/cursor contract.
- [ ] Use the same observations in `pr-status`, foreground `pr-watch` and
  `agent-pull-requests`. Preserve subscription, delivery, timeout and restart
  ownership; do not introduce another polling owner or service transaction.
- [ ] Report dependent policies as waiting on their actual prerequisites when
  evidenced. Configuration classifies a gate; it cannot waive it or convert
  an unexplained failure to success.

### Phase 4 - Agent workflow and adoption contract

- [ ] Make centralized reports distinguish agent-actionable feedback/failures,
  pending work, human-only requirements and unknown evidence. Return a
  human-ready checkpoint only when all other required gates are proven clear
  for the current source push.
- [ ] Reuse the existing owning agent for code changes, tests and publication.
  Preserve ordinary build triggers and bounded, explicitly authorized retry
  operations; never manufacture approvals or human attestations.
- [ ] Document the consumer migration contract and prove centralized feature
  parity before consumers remove their legacy watcher and its call sites.
- [ ] Land implementation and report contribution, release and authorized
  rollout separately. Do not change a running shared service as a side effect
  of implementing observation.

## Validation Plan

All items below are **agent-recommended** acceptance checks.

- [ ] Provider-transport fixtures cover complete and paginated reads, missing
  pages, unavailable policy evidence, concurrent pushes, stale evaluations,
  composite-policy prerequisites and build failure diagnostics.
- [ ] Persistent approvals remain valid after unrelated pushes; missing
  latest-push approval still blocks. A fresh same-actor approval is detectable
  without inventing a new chronological review ID or requiring a commit tag.
  A latest-push approval by the author does not satisfy a separate non-author
  minimum, but is not discarded merely because that other requirement exists.
- [ ] Non-author minimum approval, blocking/rejected votes, overlapping required
  reviewer sets, provider-reset required reviews, unresolved/outdated/reopened
  iteration-bound threads and authoritative resolution status are distinguished.
- [ ] Foreground CLI and shared-owner cases observe equivalent vote, discussion,
  policy/build and head transitions. Cursor compatibility, deduplication,
  cancellation and restart preserve responsibility without duplicate wait owners.
- [ ] Related-repository configuration and overlay tests prove defaults, strict
  input validation, feature/requirement selection and surfaced supplemental
  guidance; unsupported or unknown facts never become human-ready.
- [ ] Existing GitHub/Gitea verdict, consent, lifecycle and observation behavior
  remains intact. Runtime changes pass the relevant contained tests, fatal lint,
  install-contract gate, documentation-impact review and required CI.
- [ ] A bounded real-transport/CLI integration exercises the changed consumer
  contract. Where an authorized live ADO read is available, compare its
  centralized gate report with native evidence without mutating a real PR.
- [ ] Consumer retirement demonstrates no remaining legacy watcher invocation,
  no lost observation/remediation responsibility, and a supported centralized
  tool version. Missing rollout permission remains an explicit tracked gate.

## Proposal

Pending automated review. No runtime implementation, live approval, human
attestation, policy bypass, service cutover or production rollout is authorized
by this planning commit.

## Journal

### 2026-10-18 - Intake and ownership

- Operator selected centralized ADO stewardship and confirmed the effort slug.
- Claimed #7203 after searching existing issues. Retained #7051, #6007 and
  #6818 ownership rather than duplicating their campaigns.
- Source inspection and an isolated pure observation example identified the
  synthetic-vote-ID and missing approval-freshness gaps. These are diagnosis,
  not implementation or live deployment.
