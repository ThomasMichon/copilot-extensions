# Main-Owned Workflow Control

- **Subject:** recoverable release control independent of ordinary contribution
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-12
- **Reality docs:** `docs/pipelines.md`, `docs/patterns/dev-main-promotion-pipeline.md`

## Purpose & Intent

The release branch should retain a narrow, owner-governed control layer that can
be repaired independently of ordinary contribution validation. A repair should
survive the next release without opening a route for unvalidated product changes.
Reusable implementations should continue through reviewed, validated contribution
and promotion, including implementations submitted by administrators.

## Concepts & Components

- **Release control:** critical workflow declarations and explicit control
  metadata owned by the release branch.
- **Promoted implementation:** reviewed source, reusable workflow implementations,
  tests and documentation delivered through the contribution branch.
- **Composed release:** validated contribution content plus explicitly preserved
  control content, with attributable input identities and final-tree validation.
- **Trusted admission:** authorization independent of candidate-controlled code,
  applying to complete diffs rather than branch names or claimed authorship.

## Features

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

## Behaviors

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

## Non-Goals / Boundaries

- No blanket release-branch editing, broad preservation glob or admin bypass.
- No duplicate competing release writers or mutable second implementation.
- No weakening contributor acceptance, fork isolation or required validation.
- No automatic rollout or temporary policy relaxation inferred from plan approval.

## See Also

- `../README.md`
- `../coverage-guided-ci/README.md`
- `../ci-failure-remediation/README.md`
- `../../efforts/active/main-owned-workflow-control/README.md`
