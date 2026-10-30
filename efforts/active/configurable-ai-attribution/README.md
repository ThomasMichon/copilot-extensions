# Configurable AI Attribution

- **Slug:** `configurable-ai-attribution`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal and implementation PRs
- **Created:** 2026-10-30
- **Status:** Draft
- **Vision:** [Harness Guidance](../../../visions/harness-guidance/README.md#configurable-attribution-signatures)
- **Umbrella issue:** #9633
- **Sub-issues:** none; #2965 retains its separate owner

## Guiding Intent

Make `ai-attribution` the single reusable authority for disclosure policy and
configurable signatures. An adopting harness should express its goals through
configuration of that owner, not a competing plugin or copied decision matrix.
Shared defaults and trusted personal overrides should support concise or formal
markers without requiring every publication workflow to invent its own wording.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary driver | Proposal, canonical provider, integration and publication | Managed source worktree |
| Guidance auditors | Bounded read-only suite and selected-adopter inventories | Parent-controlled evidence tasks |
| Adopter driver | Configuration defaults and consumer reconciliation | Adopter's normal contribution flow |

## Coordination

- **Topology:** serial proposal/implementation PRs; dependent adopter changes
  follow the reviewed upstream contract.
- **Host (owns PRs):** Primary driver.
- **Delegates:** evidence only until the proposal clears review; subsequent edits
  have non-overlapping ownership and no independent git/publication authority.
- **Handoff:** return bounded evidence or diffs to the primary driver; do not
  transfer the objective merely because a phase or PR finishes.
- Existing #2965 custody and #9627 instruction-delivery work remain separate.
  Reconcile actual content overlap rather than superseding their owners.

## Context

- [#9633](https://github.com/ThomasMichon/copilot-extensions/issues/9633) is the
  public coordination token for this bounded enhancement.
- The provider already has operator configuration, a publication-safety kernel
  and exact-session guidance. Its current configuration does not express a
  sparse matrix of signature templates and placement.
- #5121 deliberately established the newer contribution-audience policy.
  #2965's older repository-audience proposal is not an instruction to restore
  blanket private-repository or ownership exemptions.
- Runtime source/commit attribution, sending approval and disclosure are
  distinct responsibilities. Required generated/offline policy delivery is not
  an independent policy fork.
- The [guidance inventory](guidance-inventory.md) records the source sweep,
  retained references/fallbacks and generic adopter reconciliation boundary.

## Request

**Public-safe summary of the operator's multi-round request:** enhance this
plugin with configurable attribution signatures, and sweep this suite and a
selected adopting harness for duplicate guidance. Reconcile the systems so the
adopter's goals are configuration of the upstream owner.

The requested model is a sparse **domain x visibility x activity** grid, with
per-item header/footer slots, intelligent defaults and interpolation. Domain
means a service/surface family. Shared templates are overridable through
trusted personal configuration. Emit the effective configuration as lightly
formatted Markdown in session guidance, leaving agents latitude to apply a
marker to the actual circumstance. The same data should support an optional
pure programmatic decorator with deduplication.

The confirmed initial scope includes the resolver, session guidance and pure
decorator, **not automatic publication/sending integration**. Adopt the upstream
decision authority rather than retaining an adopter policy fork.

Private identifiers and adopter-specific context are intentionally absent.

## Plan

### Phase 1 - Review the bounded contract
- [x] Establish the public issue and visible claim without taking over #2965.
- [x] Reconcile the guidance vision, current provider and duplicate inventories.
- [ ] Land this proposal through normal review before implementation.

### Phase 2 - Implement the canonical profile
- [ ] Add sparse surface/visibility/activity selection and header/footer
  templates, shared defaults, trusted overrides and bounded interpolation.
- [ ] Render the same effective profile into concise exact-session Markdown,
  retaining the current disclosure kernel and attributable error behavior.
- [ ] Provide the optional pure resolver/decorator consumer with idempotent
  marker insertion and no publishing, authentication or approval side effects.
- [ ] Document configuration, rendering, inheritance, supported context and
  failure behavior; preserve legacy configuration compatibility.

### Phase 3 - Reconcile consumers and duplicate guidance
- [x] Complete the source-owned attribution inventory and classify real forks,
  fixed wording, canonical references and intentional safety/provenance copies.
- [ ] Replace genuine suite duplicates with canonical-owner references or data.
- [ ] Supply the selected adopter's defaults/override seam and retire its
  competing provider through the adopter's own reviewed flow.
- [ ] Preserve independent sending authority, human-review truth and required
  static/offline delivery; cooperate with externally owned changes.

### Phase 4 - Validate and close
- [ ] Run focused unit, hook/CLI consumer and applicable native parity checks.
- [ ] Land source and consumer changes through their required review/CI gates.
- [ ] Record release versus installed/running state separately; transfer any
  unapproved rollout to a named objective rather than claiming deployment.
- [ ] Resolve every plan/validation item, journal outcomes and archive Done.

## Validation Plan

The following implementation/test mechanics are **agent-recommended** realization
of the confirmed scope, not additional operator-requested features.

- [ ] Exercise sparse inheritance, wildcard specificity, deterministic conflict
  handling, header/footer independence and explicit empty versus missing slots.
- [ ] Cover interpolation, output escaping, invalid/oversized data and review
  statements derived from actual context rather than presumed human review.
- [ ] Prove guidance and programmatic output use the same effective profile;
  configuration changes invalidate any reused session-guidance computation.
- [ ] Prove repeated decoration is idempotent, recognizes owned marker variants
  at intended slots and does not remove ordinary quoted content or another
  author's disclosure.
- [ ] Check unchanged unconfigured/legacy behavior and operator-versus-target
  configuration authority; a presentation profile must not silently waive a
  required disclosure or authorize a send.
- [ ] Exercise the actual entrypoints in disposable configuration/session roots,
  including cold/warm guidance and available native shell paths. Record what
  was exercised and any unavailable lane explicitly.
- [ ] Audit both inventories after migration: no competing selected provider,
  no new policy copies, no lost offline floor or provenance metadata.
- [ ] Pass touched Python lint, repository documentation/effort/vision guards,
  changefile checks and required exact-head CI/review. Add no exhaustive native
  matrix to the required unit-only portfolio.

## Proposal

**Agent-recommended shape:** keep the existing policy authority and legacy
configuration. Add one bounded, versioned data model and shared resolver with a
JSON-first, dependency-light consumption path. Define slot inheritance and
selector ambiguity explicitly; do not rely on config key ordering. Templates
are data, never executable expressions or additional agent instructions.

The guidance renderer and optional decorator consume that same model. Keep the
startup kernel small; a large profile must not displace required safety
guidance. The decorator changes only the supplied draft, before final
preview/approval, and never modifies a published item or grants permission.
Review-specific wording requires actual review context. Adopter defaults remain
data owned by the adopter; personal overrides remain private.

Resolve concrete configuration/entrypoint details against the existing
dependency-free emitters and guidance cache before coding. Do not add a daemon,
network dependency, parallel policy registry or mandatory sibling plugin.

## Journal

### 2026-10-30 - Kickoff and ownership reconciliation
- Operator confirmed the slug, service/surface domain axis, resolver/guidance/
  optional-helper scope and upstream decision authority.
- Created and claimed #9633. Read #2965 and retained its ownership and newer
  policy reconciliation rather than executing superseded checkboxes.
- Read-only audits are recorded in the linked inventory. No implementation or
  rollout has begun.
