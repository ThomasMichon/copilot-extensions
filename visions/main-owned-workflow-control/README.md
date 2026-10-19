# Main-Owned Workflow Control

- **Subject:** efficient workflow orchestration with recoverable release control
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-18
- **Reality docs:** `docs/pipelines.md`, `docs/patterns/dev-main-promotion-pipeline.md`

## Purpose & Intent

The workflow portfolio should deliver timely contributor feedback, dependable
releases and useful code review with the least redundant execution and status
reporting needed to preserve assurance. Each invocation should have an explicit
purpose, coverage contract and accountable owner. Consolidation should improve
observed review and delivery headroom, not merely make a workflow list shorter.

The release branch should retain a narrow, owner-governed control layer that can
be repaired independently of ordinary contribution validation. A repair should
survive the next release without opening a route for unvalidated product changes.
Reusable implementations should continue through reviewed, validated contribution
and promotion, including implementations submitted by administrators. Independent
control is an enabling mechanism, not a prerequisite for every safe reduction.

## Concepts & Components

- **Release control:** critical workflow declarations and explicit control
  metadata owned by the release branch.
- **Promoted implementation:** reviewed source, reusable workflow implementations,
  tests and documentation delivered through the contribution branch.
- **Composed release:** validated contribution content plus explicitly preserved
  control content, with attributable input identities and final-tree validation.
- **Trusted admission:** authorization independent of candidate-controlled code,
  applying to complete diffs rather than branch names or claimed authorship.
- **Workflow portfolio:** contributor acceptance, source qualification,
  generated-release validation, deep/platform coverage, review and maintenance
  form one legible orchestration map without losing their distinct contracts.
- **Validation identity:** source, recipe, selected coverage, platform and
  execution evidence determine whether prior work can satisfy a requirement.
- **Execution placement:** hosted and explicitly admitted self-hosted capacity
  run only workloads whose trust, tooling, isolation and resource contracts fit.
- **Consented contribution stewardship:** the submitter delegates bounded
  branch maintenance and completion to trusted repository automation without
  surrendering review vetoes or source-worktree responsibility.

## Features

### minimal-redundant-invocations

Compatible event handlers share established trusted entrypoints rather than
creating repeated workflow runs and checks for the same obligation. Distinct
trust boundaries and timely failure feedback remain visible. A skipped job is
not reported as an eliminated workflow invocation.

### coverage-preserving-reuse

Equivalent trusted validation is reused only with complete, current evidence
for its actual input and coverage contract. Source and generated-output checks,
native platforms, installation acceptance and stress coverage retain their
independent assurance where their contracts differ.

### supported-capacity-and-review-headroom

Qualified execution capacity performs useful admitted work without duplicating
its hosted equivalent. Code review retains a supported execution path and
explicit capacity ownership. Routing, failover and load-balancing claims reflect
provider guarantees and actual observed assignments, not the mere presence of
another runner pool.

### independent-control-repair

The authenticated repository owner can submit a reviewed critical workflow or
metadata repair to the release branch without waiting for an unrelated trunk
failure. Only those explicitly declared paths qualify.

### explicit-preservation

Promotion preserves precisely the declared release-control content. Missing,
ambiguous or unsafe declarations fail closed; unrelated release-branch drift
does not silently survive promotion.

### validated-composition

Release evidence identifies both contribution and control inputs and validates
the resulting composition, not just source before transformation. Concurrent
control repairs cannot be overwritten by a stale promotion.

### consented-maintainer-completion

An authorized Maintainer can explicitly consent to repository-owned completion
after satisfying review and validation. The steward maintains a mergeable
branch when supported, requires fresh evidence after changes, and returns
actual conflicts to the original submitter rather than resolving them blindly.
Its work remains discoverable until actual merge and source reconciliation.

## Behaviors

### one-coordinated-portfolio

One canonical effort records the audit, prioritized reductions, slice owners,
dependencies and acceptance evidence. Existing release, runner, coverage and
remediation authorities retain their responsibilities; a new coordinator does
not create a competing controller or preempt an active contribution.

### measurable-assurance-preserving-reduction

Before and after comparisons distinguish workflow invocations, allocated jobs,
check records, selected coverage and observed queue/review outcomes over
comparable events. Missing, skipped or failed evidence cannot authorize removal.
Independent runner and review-service limits are reported separately.

### contribution-authority-is-not-an-admin-exemption

All other contributions go through the contribution branch, including helper
implementations, tests, documentation and administrator-authored changes.
Generated promotions are independently verified release operations, not a
human contribution bypass.

### one-compatible-execution

Control declarations invoke reviewed implementations from the same release
composition. Privileged execution never resolves mutable contribution-branch
code or arbitrary candidate-supplied imports.

### gated-reversible-migration

Replacement enforcement is proven before existing protections are retired.
Recovery remains bounded, attributable and compatible with preserved control
ownership; rollback does not undo an independent control repair.

### missing-approval-exception-is-not-a-review-veto-override

A repository may authorize an actor to complete without an additional required
reviewer's approval. That exception never overrides an outstanding blocking
review, required validation, explicit hold or revision mismatch. Agents and
automation receive a consistent account of both their authority and the
remaining gates instead of treating routine authorized completion as an
unexplained administrative emergency.

## Non-Goals / Boundaries

- No blanket release-branch editing, broad preservation glob or admin bypass.
- No duplicate competing release writers or mutable second implementation.
- No weakening contributor acceptance, fork isolation or required validation.
- No automatic rollout or temporary policy relaxation inferred from plan approval.
- No shorter workflow list obtained by hiding failures or dropping distinct
  platform, generated-output, installation or stress assurance.
- No unsupported failover claim, untrusted workload admission or capacity grant
  inferred from an available runner.
- No competing test portfolio or release controller; this vision governs their
  workflow orchestration and ownership seams.

## See Also

- `../README.md`
- `../coverage-guided-ci/README.md`
- `../test-portfolio/README.md`
- `../release-pipeline/README.md`
- `../ci-failure-remediation/README.md`
- `../../efforts/active/main-owned-workflow-control/README.md`
