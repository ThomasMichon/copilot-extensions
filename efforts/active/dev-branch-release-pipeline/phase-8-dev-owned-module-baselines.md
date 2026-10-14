# Phase 8 - Dev-owned module-size baseline updates

**Status: Superseded by the canonical implementation and rollout in
[issue #6430](https://github.com/ThomasMichon/copilot-extensions/issues/6430)
and [main-owned-workflow-control Phase 5](../main-owned-workflow-control/README.md#phase-5---finish-validation-and-automation-consolidation).**

The Plan, Validation Plan, and proposed mechanism below are retained historical
evidence from reviewed plan #6436, not a competing current contract. The operator
selected merged #6438's widening-only automation: no automated tightening,
graduation/removal, or exact-current-parent admission. Those conflicting parts
of this proposal are retired. Remaining compatible validation and actual
trusted-main activation obligations are transferred to the named canonical
objective above; neither this supersession nor the dev merge proves activation.

## Guiding Intent

Baseline maintenance must not create an independent release-branch writer that
invalidates a generated promotion while it is waiting to merge. `dev` owns the
module-size policy baseline; ordinary validated promotion carries that policy
to `main`. Keep post-merge maintenance reviewable and keep privileged execution
under trusted release-control code.

This is vision-closing for
[`main-owned-workflow-control`](../../../visions/main-owned-workflow-control/README.md):
`one-compatible-execution`, `validated-composition`, and the boundary against
duplicate competing release writers. It follows the existing
[promotion pattern](../../../docs/patterns/dev-main-promotion-pipeline.md).

## Request

> Another set of agents is dealing with 6408. We should fix pipeline behavior
> so module size baseline updates can't much up promotion

The prevention driver owns source changes only. Live recovery of PR #6408,
candidate edits, workflow cancellation, and manual release intervention remain
outside this slice.

## Context

Generated candidate #6408 was based on a release tip that baseline-refresh
PR #6071 subsequently advanced. Both changed `tools/module-size-baseline.json`;
the candidate became conflicting after validation had passed. Its exact-PR
waiter then retained the promotion workflow's exclusive queue slot.

The candidate's baseline blob is identical to the baseline in its validated
`dev` input. An independent baseline PR to `main` is therefore not needed to
establish baseline authority. Serializing only workflow execution would not
serialize an already-open housekeeping PR's later merge.

Coordination token: [issue #3336](https://github.com/ThomasMichon/copilot-extensions/issues/3336).
The publication-attestation work under #6332 retains its existing owner.

## Scope and Design Contract

_(Agent-recommended)_ The authority migration below and its adversarial
validation scenarios are the proposed mechanism for satisfying the operator's
prevention request, subject to the normal plan review.

- Baseline maintenance reads one pinned, committed `dev` snapshot as data and
  proposes a baseline-only PR to `dev`. Normal review and contribution CI
  remain required; the updater does not approve or merge its own change.
- Workflow declarations and helper implementations execute from trusted,
  compatible release control. Do not switch privileged execution to mutable
  `dev` YAML, import `dev` tooling, or run candidate-supplied scripts with write
  credentials. A source checkout used for measurement is data, not executable
  authority.
- Preserve default shrink-only behavior and the existing deliberate,
  post-merge widening exception. Never automatically grandfather a new
  over-cap module or widen an ordinary contributor PR's own diff.
- Baseline generation is deterministic for the pinned snapshot. Refuse unsafe
  source paths, malformed baseline data, and stale publication inputs with
  explicit diagnostics; do not overwrite a newer maintenance PR silently.
- Generated/vendored path differences need actual promotion-fixture coverage.
  If release materialization requires derived ceilings, derive and validate
  them inside the generated promotion, not through another `main` writer.
- Retire the baseline-only `main` admission exception only after its
  replacement is proven and its trusted-control rollout is coordinated.
  Ordinary promotion, rollback, and declared control repairs keep their own
  acceptance boundaries.
- Do not alter the live #6408 candidate or its pipeline run, bypass checks,
  force a release, or modify #6332's signing/attestation surfaces.

## Plan

- [ ] Land this reviewed design before changing the mechanism.
- [ ] Inspect and reuse the trusted-main admission/implementation pattern and
  existing module-size refresh semantics.
- [ ] Implement pinned-data measurement and reviewed `dev`-targeted
  maintenance, preserving the workflow's release-control trust boundary.
- [ ] Wire all relevant workflow, admission, verification, and documentation
  surfaces so none still advertises or opens baseline-maintenance PRs to `main`.
- [ ] Add focused real-Git and workflow-contract regressions, then run the
  smallest bounded test selections that cover these surfaces.
- [ ] Drive the implementation PR through review and merge; record the
  coordinated release-control rollout and its actual evidence.

## Validation Plan

- [ ] Reproduce the old candidate/baseline collision in isolated Git fixtures.
  Prove the replacement updater changes only its `dev` PR branch and leaves
  the existing `main` tip and generated promotion head unchanged.
- [ ] Prove ordinary and baseline-maintenance PRs still pass through review
  and required contribution checks; a baseline-only PR to `main` cannot use
  the retired exception after the replacement rollout.
- [ ] Cover unchanged, grown, shrunk, graduated, and newly over-cap modules;
  new offenders fail rather than silently receiving grandfathered ceilings.
- [ ] Cover generated/vendored source layouts through the actual promotion
  helper; no materialized module loses its intended guard protection.
- [ ] Prove pinned inputs, deterministic JSON, unsafe-path refusal, and
  concurrent `dev` advancement fail-safe behavior.
- [ ] Put deliberately hostile tooling in the data snapshot and prove it is
  not imported or executed. Verify write credentials are unavailable to
  measurement and the trusted helper controls all privileged publication.
- [ ] Validate the actual workflow declarations and helper invocation
  boundaries, not merely a stand-in function.
- [ ] Run targeted tests through the bounded supervisor, with no production
  workflow dispatch, cancellation, closure, or release used as a test.
- [ ] Reconcile the trusted-main control rollout with its owner and inspect
  the deployed baseline workflow's target and authority before claiming live
  prevention. A merged `dev` implementation alone is not rollout evidence.

## Coordination and Completion

The Baseline prevention driver owns this slice's source PRs and evidence under
#3336. Other participants retain Phase 7, live release recovery, and publication
attestation. Completing this slice does not declare the broader release-pipeline
effort or any downstream effort Done.

## Journal

### 2026-10-12 - Prevention scope proposed

The operator assigned durable prevention separately from live recovery.
Read-only Git and provider evidence established the single-file baseline
collision and the competing publication paths. No live release intervention
was performed for this proposal.

The plan's CI exposed a separate shared-library fixture defect: the
cross-process dial-log test observed 239 of 240 lines. Its intended 30-second
lock wait used `functools.partial`, but the production call's explicit
two-second argument overrode that partial keyword. The fixture now sets the
constant read by that call; production best-effort logging is unchanged. The
named regression passed through the bounded supervisor on Windows. Required CI
and plan review subsequently passed; plan #6436 merged as `df15d7ce2`.

### 2026-10-13 - Duplicate source superseded; activation audit

Merged #6438 (`87c5f5ddcca1`) implements the same prevention through the existing
trusted CI-completion invocation. Its producer pins trusted tooling and committed
dev data in credential-free checkouts, runs isolated Python, fences publication
with the live dev ref API, and publishes only a baseline PR to dev. The operator
selected that canonical widening-only policy and authorized closing duplicate
#6493; its branch and review evidence remain retained. No competing workflow,
strict freshness gate, or automated tightening is carried forward.

The actual-main audit at `9c4d6acade64` still found the old main-only baseline
producer and verification workflow, with the baseline-only main admission
exception intact. Legacy baseline PR #6444 still targets main. Thus the
dev-landed correction is not yet live prevention. Named covering run
`38479483177` reported failure and `38480618519` cancellation; release recovery
remains with the existing #6237/#6535 owners. No run, legacy proposal, candidate,
or attestation surface was changed by this audit.

The canonical Phase 5 activation item and #6430 retain the covering-release and
deployed producer/admission proof. This historical slice is superseded, not a
claim that the release-pipeline effort or deployed system is complete.

## See Also

- [Parent effort](README.md)
- [Release mechanics](../../../docs/pipelines.md)
