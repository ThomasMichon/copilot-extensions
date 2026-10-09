# Adopter Material Refresh

- **Slug:** `adopter-material-refresh`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-phase PRs to `dev`
- **Created:** 2026-10-08
- **Status:** Draft
- **Vision:** `visions/README.md` and the subject-specific visions, used as
  intent boundaries rather than evidence that a capability is shipped
- **Umbrella issue:** `ThomasMichon/copilot-extensions#5799`
- **Sub-issues:** carved after the reviewed inventory identifies discrete work

## Guiding Intent

Bring adopter-facing documentation and preview assets back into agreement with
the repository's supported, shipped capabilities. An adopter should be able to
understand what exists, follow a current workflow, and recognize the current
operator surface without translating obsolete claims or images.

This is a **separate sibling** of `vision-backport-sweep`, not a new phase of
its completion gate. Visions describe standing intent; these materials describe
what an adopter can actually use. A future feature or an effort's Done marker
is not sufficient evidence for a present-tense capability claim.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning lead | Owns the bounded proposal and review gate; does not assume material-execution ownership | managed proposal worktree |
| Execution lead (unassigned) | Claims the independent campaign before executing its reviewed phases | umbrella issue and a new managed worktree |

## Coordination

- **Topology:** serial per-phase PRs; one coherent material family per slice.
- **Host (owns PRs):** planning lead for the proposal; explicitly claimed
  execution lead for subsequent material PRs.
- **Delegates:** none assigned. A future lead may use disjoint, bounded evidence
  or asset scopes while retaining integration and publication ownership.
- **Handoff:** planning publication transfers execution to the independent
  tracked umbrella, not back into the vision sweep. A claimed execution slice
  retains responsibility through actual merge and its validation.

## Context

- Seed: the operator's original material concern in
  [archived `vision-backport-sweep`](../../2026/10/09%20vision-backport-sweep/README.md), umbrella
  `ThomasMichon/copilot-extensions#5456`.
- Decision: the operator selected a separate sibling and confirmed this slug.
  The current planning slice does not capture screenshots, rewrite a material
  family, change runtime behavior, or declare the materials current.
- Target-owned effort adoption was verified with the efforts plugin's exact
  read-only capability probe. Tracker and active-effort searches found no
  existing campaign for this combined scope.
- **Agent-recommended planning approach:** inventory before rewriting; ground
  each claim in the owning implementation and the referenced build; reuse
  existing docs/asset generation surfaces rather than introducing parallel ones.

## Request

Original material concern, verbatim:

> "The docs, preview images, and other user-facing materials are also getting out
> of date with all the new improvements, enforcements, and capabilities."

Relationship decision, verbatim:

> "Separate sibling effort"

Confirmed slug, verbatim:

> "adopter-material-refresh"

The phase structure and validation obligations below are **agent-recommended**,
not additional operator-requested product features.

## Plan

### Phase 0 — Publish the independent reviewed plan
- [x] Confirm sibling relationship and slug; verify target adoption and dedup.
- [x] Land this proposal through the repository's normal review gate before
      material execution.
- [x] Record the merged plan and leave execution independently claimable under
      the umbrella issue.

### Phase 1 — Inventory and baseline (agent-recommended)
- [ ] Inventory root/plugin README claims, adopter procedures, linked reference
      pages, Picker preview images, captions, and asset-generation sources.
      Keep the inventory in a linked sibling document when it grows.
- [ ] For each material family, identify the owning implementation, referenced
      shipped build, supported venue/mode, and current evidence.
- [ ] Dedup discrete gaps against existing documentation/feature issues and
      carve bounded material tasks without copying their implementation scope.

### Phase 2 — Correct claims and workflows (agent-recommended)
- [ ] Refresh one coherent documentation family per reviewed slice; preserve
      current installation, account, contribution, ownership, and recovery
      boundaries.
- [ ] Verify examples through the real supported consumer contract where
      practical, using public-safe fixtures and scoped identities.
- [ ] Distinguish shipped support, optional capabilities, compatibility limits,
      and standing future intent; do not make a docs-only patch claim to fix a
      runtime gap.

### Phase 3 — Refresh preview assets (agent-recommended)
- [ ] Reuse the owning public-safe asset/capture tooling, or establish the
      smallest justified reproducible fixture when none exists.
- [ ] Capture the actual supported surface against an identified build and mode;
      exclude private repositories, machine identifiers, personal activity,
      credentials, and unredacted logs.
- [ ] Update descriptions, links, and accessibility text with the asset; retire
      misleading previews without removing the capability they represented.

### Phase 4 — Cross-surface validation and closeout (agent-recommended)
- [ ] Resolve the inventory or transfer each residual item to a named tracked
      objective; retain truthful limitations where evidence is unavailable.
- [ ] Verify navigation, examples, captions, and build/mode attribution across
      the updated surfaces.
- [ ] Journal merged work, promote durable capture/generation procedures into
      owning docs, and declare Done only after Plan and Validation Plan resolve.

## Validation Plan

- [x] The proposal passes effort-structure and repository review gates before
      execution begins.
- [ ] Every changed present-tense claim has implementation/build evidence;
      aspirational vision text is not substituted for that evidence.
- [ ] Every changed example/link/asset is validated with the owning tooling or an
      explicit, tracked reason when a required lane is unavailable.
- [ ] Preview provenance identifies the actual build and mode, and all captured
      inputs/outputs are public-safe and credential-free.
- [ ] Final material families are internally consistent across README, linked
      docs, images, captions, and accessibility descriptions.
- [ ] All Plan and Validation Plan items are resolved or explicitly transferred
      before execution declares the sibling effort Done.

## Proposal

**Reviewed Draft; execution unassigned.** The operator confirmed relationship
and name, and the agent-recommended execution/validation structure cleared the
normal review gate in `#5807`. No runtime implementation, service repair,
repository creation, or private-environment capture was part of that proposal.

## Journal

### 2026-10-08 — Independent planning seed
- Captured the original material concern and both literal follow-up selections.
  Re-read them against the operator's words; no scope choice was softened into
  an open question.
- Created umbrella `#5799` after dedup and verified target effort adoption.
  Prepared this Draft proposal; execution remains unassigned and separately
  claimable, and the vision sweep's remaining audit gate is unchanged.
- Optional worktree-effort binding was refused by path-format validation, so
  this proposal continues with the standalone effort lifecycle. No tracking
  record was hand-edited to manufacture a binding.

### 2026-10-09 — Planning publication complete
- Proposal `#5807` merged after current-head Copilot approval with zero findings
  and passing required CI/PR gates. Publication was recorded on `#5799`, and
  the planning worktree was finalized after provider-confirmed merge.
- Phase 0 and the proposal-review validation are complete; execution remains
  unassigned and independently claimable under `#5799`. The effort stays Draft,
  not Done, and no documentation family or preview asset has been refreshed.
