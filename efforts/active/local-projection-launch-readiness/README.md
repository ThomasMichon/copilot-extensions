---
visions:
  - visions/harness-guidance
---

# Local Projection Launch Readiness

- **Slug:** `local-projection-launch-readiness`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serially landed proposal and implementation slices
- **Created:** 2026-10-08
- **Status:** Draft
- **Vision:** `visions/harness-guidance` -- closes `resilient-safety-boundary`
  and `ambient-delivery-fails-open`; extends `attributable-context-budget` so
  budget auditing cannot withhold otherwise-safe local guidance.
- **Umbrella issue:** [#5707](https://github.com/ThomasMichon/copilot-extensions/issues/5707)
- **Related issues:** #5061 (real spawn latency), #5063 (descendant cleanup),
  #4960 (precedence ambiguity). These remain separate objectives.

## Guiding Intent

Install every applicable, safely resolved local projection before a session
starts whenever the worktree/session creation path can do so. Session-start
refresh is the repair backstop, not the first opportunity to install guidance.

Context budgets are an auditing concern, not a guidance-delivery admission
gate. An oversized enabled stack must still receive its complete local cache,
with attributable size findings available for periodic review. Do not solve
delivery by silently dropping sources, truncating policy, increasing the
configured budget, or requiring a checked-in projection-sync PR first.

Ownership, trust, safe-path, provenance, and foreign-file protections remain
binding. Unavailable or unsafe sources are diagnosed explicitly and retain the
checked-in fallback; this effort does not authorize executing arbitrary payloads
or overwriting repository-owned files.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Reviewed proposal, renderer fix, lifecycle integration, validation | Managed repository worktrees and normal PR flow |

## Coordination

- **Topology:** one driver, serial proposal/implementation PRs.
- **Host (owns PRs):** Driving agent.
- **Delegates:** none initially; any disjoint delegation is journaled first.
- **Handoff:** preserve this README, the active slice, outstanding PRs and
  resource obligations. A merged slice is not completion of the effort.

## Context

The completed `efforts/2026/10/03 local-cache-delivery-primacy` effort established
the local cache as primary and wired the agent-bridge local-spawn boundary.
`docs/patterns/worktree-scoped-dynamic-guidance.md` owns the mechanism;
`customizing-copilot` owns rendering and `agent-worktrees`/`agent-bridge` own
their respective lifecycle calls. Extend these seams rather than introduce a
second renderer or sync daemon.

Confirmed gaps:

- Existing lifecycle refresh helpers silently absorb errors and discard render
  findings. Their presence does not prove a projection was installed.
- A direct local render of a representative enabled stack reported an aggregate
  above its configured budget and wrote zero siblings. This is independent of
  a checked-in lock/marker conflict: the local-only path never needed to update
  those reviewed artifacts.
- Calls in create/resume do not, by themselves, establish coverage of JSON launch
  plans or every local session-creation path.

The local-only renderer must remain independent of the privileged checked-in
sync transaction. Audit findings can remain failures for an audit command
without preventing creation or refresh of otherwise-safe local guidance.

## Request

Operator, verbatim:

> Help drive log-term fixes for the instruction projection thing. We primarily
> want to ensure that during worktree/session creation, and then as a backup on
> session-start, we get the local projections installed.

The operator confirmed the effort slug `local-projection-launch-readiness`.
Failure-policy clarification, verbatim:

> Just always pull in the projections; we'll just periodically audit for being
> over-budget, but it can't be a hard-block that shuts off guidance silently

Interpretation: "pull in" means render the enabled, already-installed payloads
into the local cache, not perform a network/plugin update at every launch.
Existing source and destination safety checks are not context-budget checks.

## Plan

### Phase 0 - Reviewed delivery contract
- [ ] Land this proposal and the vision clarification through automated review
  before runtime implementation.
- [ ] Verify this capture against the operator's two requests and preserve the
  distinction between requested delivery and agent-recommended validation.

### Phase 1 - Budget-independent local installation
- [ ] Make per-projection and aggregate budget excess advisory for local-cache
  rendering: write every safe, unambiguous enabled source, including an
  over-budget stack, without changing the configured budget.
- [ ] Separate budget audit findings from source/destination safety failures.
  Check bounded provenance readers and existing-cache validation so a large
  local file is not reclassified as foreign merely for exceeding an audit cap.
- [ ] Keep checked-in files and projection locks byte-identical during local
  rendering, including when checked-in sync is independently blocked.
- [ ] Preserve the existing scan/report path for periodic size auditing;
  no additional resident daemon or live schedule changes are authorized.
- [ ] Update the authoritative pattern/skill documentation with the implemented
  local delivery versus audit distinction.

### Phase 2 - Pre-session lifecycle coverage and observable repair
- [ ] Inventory creation and launch paths: worktree create, resume, explicit JSON
  launch planning, local Session Host session creation, and session-start backup.
  Record the owning seam and whether rendering completes before agent startup.
- [ ] Wire missing applicable pre-session boundaries through the existing
  renderer CLI; preserve dry-run behavior and optional-plugin independence.
- [ ] Replace discarded refresh results with bounded, attributable outcomes:
  installed/unchanged, unavailable, unsafe, timeout, or failed. Budget warnings
  must not masquerade as failed installation.
- [ ] Preserve installation-cell identity and target-local source resolution.
  Avoid unrelated-project discovery on the hot path where the owning resolver
  offers a supported scoped path; do not replace provenance with PATH guessing.
- [ ] Make session-start repair missing/stale caches within its existing bounded
  lifecycle budget. Failures remain visible without blocking usable sessions.
- [ ] For a venue with no locally accessible target root, document the owning
  target-side boundary or explicitly transfer a concrete gap to a named issue;
  do not claim host-local rendering installed guidance in a remote filesystem.

### Phase 3 - Release and acceptance
- [ ] Land the implementation slices with required plugin changefiles and review.
- [ ] Verify a promoted release contains the changes and refresh the applicable
  installed payloads through the normal unified update flow.
- [ ] Demonstrate complete local-cache installation on an enabled stack that
  previously exceeded its budget, before a new session's instruction load.
- [ ] Journal the evidence, resolve every Plan/Validation item or transfer it to
  a named objective, mark Done, and archive through normal review.

## Validation Plan

The specific proofs below are **agent-recommended** ways to verify the
operator-requested contract, not additional operator requirements.

- [ ] Oversized aggregate and oversized individual source still install their
  full local content; warnings retain byte counts/source attribution.
- [ ] Audit still detects the excess, with unchanged budget configuration.
- [ ] Re-render is byte-idempotent; stale/missing cache repair completes.
- [ ] Checked-in marker mismatch or independently blocked sync cannot prevent
  a safe local-only render; checked-in content and lock bytes stay unchanged.
- [ ] Unsafe paths, foreign existing local files, ambiguous identities and
  tracked local destinations retain their safety refusals without dropping
  unrelated safe sources.
- [ ] Lifecycle ordering tests prove rendering precedes agent startup on each
  applicable path, including JSON plans; dry runs cause no cache writes.
- [ ] Session-start-only tests repair a missing or outdated cache and report
  bounded failures without silent success.
- [ ] A representative real render exercises production launch budgets rather
  than only timeout arithmetic; reuse existing latency evidence where valid.
- [ ] Run targeted contained suites, required lint/install-contract gates, and
  a relevant clean-room pre-session delivery scenario when available.
- [ ] Verify the promoted payload and a consuming worktree's installed local
  source set, not merely a successful subprocess exit or a merged PR.

## Proposal

Install local guidance first; report budget debt independently. Keep the
checked-in fallback and its privileged review transaction separate. Do not
force marker/lock repair or raise size limits to disguise the delivery gap.

## Journal

### 2026-10-08 - Inception
- Confirmed the prior primacy effort is Done; this is a follow-on reliability
  effort, not a duplicate of its vision reframing or local bridge wiring.
- Reproduced complete local-cache suppression by the aggregate budget in a
  direct render that completed discovery promptly.
- Captured the operator's explicit rejection of budget-gated delivery and
  prepared a proposal before runtime implementation.
- The proposal's push hit the known #3446 pre-push attribution defect:
  an unscoped module-size sweep blamed untouched trunk files. The proposal
  includes the small guard-invocation repair and a regression; per-file caps
  and the separate full-tree audit remain unchanged. _(agent-recommended
  publication unblocker; not projection runtime implementation)_
