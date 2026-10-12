# Main-Owned Workflow Control

- **Slug:** `main-owned-workflow-control`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees; ordinary changes target `dev`
- **Created:** 2026-10-12
- **Status:** Draft; implementation waits for plan review
- **Vision:** `visions/main-owned-workflow-control/README.md`
- **Umbrella issue:** #6237
- **Related campaigns:** #3336; `promotion-failure-reactive-fix-agent`; #6142

## Guiding Intent

Make critical release control independently repairable without admitting
unvalidated content or unwanted contributions. Preserve an explicitly bounded
main-owned control layer across validated dev promotion. All other contribution,
including admin-authored implementation, continues through dev.

Workflow ownership is a separate completion gate from validation consolidation.
The latter starts only after ownership and final-candidate enforcement are proven.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign driver | Own plan, implementation slices, review and release evidence | Umbrella issue #6237 and per-slice managed worktrees |
| Repository owner | Critical-path acceptance and bounded administrative decisions | Owner-authored PRs and issue steering |
| Automated reviewer | Review each plan and implementation PR | Repository Copilot review flow |

## Coordination

- **Topology:** independent per-slice PRs, serial landing.
- **Host (owns PRs):** Campaign driver, explicitly appointed by the operator.
- **Delegates:** no competing implementation assigned.
- **Handoff:** journal the current slice, exact evidence and outstanding gates;
  successors continue this canonical effort, not a peer plan.
- **Reconciliation:** the other planning agent's #6237 comment delegated
  canonical vision/effort publication here and retained a separate ownership
  completion gate. No published competing effort or PR was found.

## Context

The current pattern regenerates main wholesale. Critical workflow repairs can
therefore be delayed by unrelated dev failures and erased by later promotion.
The proposed exception is narrow, not permission to keep arbitrary main drift.
See [design.md](design.md) for the proposed contract and staged cutover.

The October 11 audit is historical evidence, not current failure status. Live
policies and active runs must be refreshed before each enforcement cutover.
Related review-request repair #6233 was already released; do not repeat it.

## Request

> One proposal I have with another agent is to support main have its own,
> direct workflow declarations, with a second set pushed from dev. This way,
> we can direct-to-main control over critical pipelines, while still allowing
> dev to drive most CI-flow pipelines and receive proper review and validation.

> Note that I want main PRs scoped to (critical) workflow and metadata updates
> only; all other contributions, even from admins, must go via dev.

> Sounds good. I'll put you in charge of this, start driving

The operator confirmed the explicit main-owned control/dev-owned content model.
The detailed decomposition and validation cases below are **agent-recommended**,
reconciled with the other planning agent's public handoff on #6237.

## Plan

### Phase 0 - Reviewed contract and baseline

- [ ] Land this vision/effort through ordinary dev plan review.
- [ ] Snapshot branch protection, rulesets, environments, workflow catalog,
  credentials and in-flight releases; identify enforced freshness and admission.
- [ ] Resolve the trusted-check publication limitation explicitly: the common
  Actions app and a check name cannot prevent deliberate Write-actor spoofing.
  Do not claim stronger guarantees than the selected enforceable boundary.

### Phase 1 - Inert preservation support

- [ ] Extend the existing composer with strict, exact-path preservation from
  pinned main; absent manifest retains existing behavior.
- [ ] Reject unsafe/missing files, collisions and conflicting ownership; record
  control provenance and test intentional deletion.
- [ ] Land implementation, tests and related documentation via dev, then promote
  while no preservation manifest is active.

### Phase 2 - Trusted admission and composed validation

- [ ] Implement full-diff owner authorization on both relevant targets,
  protecting wrappers, metadata and promoted implementation/actions.
- [ ] Validate final composed output, same-commit helper compatibility and
  exact dev/main evidence; fence stale candidates before merge.
- [ ] Prove failure/rejection and rollback contracts before activation.

### Phase 3 - Narrow main control activation

- [ ] Establish reviewed critical workflow/metadata allowlist; no helper source,
  tests or docs admitted directly to main.
- [ ] Activate the exact preservation manifest only after support is released.
- [ ] Land thin control wrappers through an owner-authored main PR; reusable
  implementations use same-composed-commit local `workflow_call` references.
- [ ] Fence/drain old release writers and demonstrate one canary, concurrent
  control repair, repeated promotion and rollback preserving current control.
- [ ] Restore/verify any separately authorized temporary policy adjustment
  before resuming ordinary publication; no new bypass actor or broad credential.

### Phase 4 - Independent consolidation gate

- [ ] Record ownership completion separately before pruning duplicate validation.
- [ ] Establish one exact-input validation contract and compact generated
  candidate gate; preserve distinct heavy/platform/installation coverage.
- [ ] Remove redundant promotion-PR/main-push validations only after replacement
  gates are required and proven; migrate old contexts/callers before deletion.
- [ ] Reconcile repair ownership with #6142 and existing remediation effort;
  disabled repair automation must escalate to a named owner.

### Phase 5 - Observe and complete

- [ ] Observe two release cycles, supersession, controlled failure/recovery,
  independent main control repair and rejected untrusted admission.
- [ ] Update as-is docs/patterns, audit final policies and record measured
  redundancy reduction with unchanged required coverage.
- [ ] Resolve every validation item before completion and archival.

## Validation Plan

- [ ] Unit: exact allowlist, invalid/case-equivalent paths, traversal, symlinks,
  executable modes, submodules, missing files and collisions fail closed.
- [ ] Real Git integration: materialization, preservation, rename/deletion,
  no-op/repeated release, provenance and rollback at pinned commits.
- [ ] Negative admission: outsider/admin mixed source-plus-control PR,
  unapproved metadata, forged check/artifact and self-authorizing allowlist fail.
- [ ] Final composition: incompatible wrapper/helper, altered recipes and
  missing/skipped/cancelled/mismatched evidence block publication.
- [ ] Race: main advances after candidate creation; stale publication cannot
  overwrite control repair; demonstrate enforced freshness, not polling alone.
- [ ] Clean-room: generated runtime/install behavior on a fresh environment;
  state concrete lane and any unavailable-tier justification per slice.
- [ ] Live Actions: required contexts report for admitted shapes, actual
  merged tree/tag match evidence, and an independent repair survives promotion.
- [ ] Migration: old gates remain until new ones pass; release writer exclusive;
  rollback and policy restoration are demonstrated.
- [ ] Consolidation: distinct stress/Windows/heavy coverage retained and
  failure routing assigned; no unowned required failure.

## Proposal

See [design.md](design.md). This is proposed behavior, not an as-is claim.
Plan approval authorizes implementation through review gates, not unbounded
credential changes, broad policy weakening or unreviewed releases.

## Journal

### 2026-10-12 - Ownership and reconciliation

- Operator appointed the campaign driver and explicitly restricted direct-main
  contributions to critical workflow/metadata, including admins.
- Reconciled the other planning agent's #6237 handoff: publish one canonical
  effort, use same-composed-commit implementations and preserve exact paths.
- Current migration has made no workflow, secret, environment or policy changes.
