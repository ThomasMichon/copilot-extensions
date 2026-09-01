# Migration Intake

- **Slug:** `migration-intake`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-08-29
- **Status:** Draft
- **Vision:** efforts `one-canonical-effort`, `reviewed-wave-execution`, and
  `cross-repository-effort-ownership`

## Guiding Intent

Provide one public, repository-neutral intake path for deferred work that is
ready to move into canonical ownership. Specialized efforts own work in their
domains; this effort owns only classification, deduplication, routing, and the
small residual whose destination cannot be known before validation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| intake host | Owns classification, routing, and publication slices | isolated worktree |
| domain owners | Accept routed work within an existing canonical effort | linked effort plan |
| independent reviewer | Audits publication safety and ownership decisions | pull request review |

## Coordination

- **Topology:** one intake host with independent domain-owner slices.
- **Host (owns PRs):** intake host.
- **Delegates:** domain owners own implementation after routing.
- **Handoff:** a routed item leaves this effort only when its canonical effort
  and public tracker entry are explicit.
- **Public coordination token:** the reviewed plan PR until issue publication
  is authorized; implementation does not begin under the temporary token.

## Context

Deferred work can outlive the repository, plan, or vocabulary that first
described it. Publishing that work safely requires more than copying text: each
candidate must still be actionable, remain general-purpose, avoid duplicating
the public tracker, and have exactly one canonical effort owner.

The canonical domain plans are:

- [`account-aware-operations`](../account-aware-operations/README.md)
- [`agent-bridge-ahp-convergence`](../agent-bridge-ahp-convergence/README.md)
- [`agent-machines-declarative-control-plane`](../agent-machines-declarative-control-plane/README.md)
- [`marketplace-scoped-installations`](../marketplace-scoped-installations/README.md)
- [`native-construct-convergence`](../native-construct-convergence/README.md)
- [`plugin-process-hygiene`](../plugin-process-hygiene/README.md)
- [`restricted-venue-targets`](../restricted-venue-targets/README.md)
- [`review-automation-reliability`](../review-automation-reliability/README.md)
- [`session-context-aggregation`](../../2026/08/31%20session-context-aggregation/README.md)
- [`test-portfolio-rationalization`](../test-portfolio-rationalization/README.md)
- [`venue-parity`](../venue-parity/README.md)
- [`windows-launch-hardening`](../windows-launch-hardening/README.md)
- [`worktree-finality-and-obligations`](../worktree-finality-and-obligations/README.md)
- [`worktree-manager-control-plane`](../worktree-manager-control-plane/README.md)

`agent-index-engine-daemon` and `uniform-runtime-resolution` are completed
canonical references. Intake compares candidates against their delivered scope
to recognize already-satisfied work. If new implementation remains, it requires
an explicitly reviewed reactivation or successor plan before issue publication.

## Request

Establish a generic intake campaign that can validate and route deferred
general-purpose work without publishing its originating context or creating a
second owner for scope already covered by an active or completed effort.

## Plan

### Phase 1 - Freeze the intake contract

- [ ] Require one explicit disposition per candidate: route to a canonical
  domain effort, retain as an intake-owned residual, reject as non-portable,
  close as obsolete, or supersede as a duplicate.
- [ ] Require one primary owner before publication; cross-domain relevance may
  be recorded without creating joint ownership.
- [ ] Fail closed when ownership or publication safety is unresolved.

### Phase 2 - Validate and deduplicate

- [ ] Revalidate behavior and expected outcome against current public code,
  documentation, visions, efforts, and issues.
- [ ] Resolve duplicate groups before creating tracker entries and keep only
  the clearest actionable statement.
- [ ] Remove environment-specific motivation, examples, identifiers, and
  evidence while preserving a reproducible general-purpose problem.

### Phase 3 - Route and publish

- [ ] Obtain acceptance from the chosen domain plan and extend that plan when
  its current phases do not yet cover the validated work.
- [ ] Create or update a public issue only after its owner, novelty, and
  portable acceptance criteria are explicit.
- [ ] Keep genuinely unclassified general-purpose work here only until enough
  is known to route it without guesswork.

### Phase 4 - Reconcile and close

- [ ] Confirm every accepted item has one canonical effort and tracker outcome.
- [ ] Transfer implementation ownership to domain efforts and retain only the
  intake decision record.
- [ ] Close the intake campaign when no unresolved candidate or residual
  remains.

## Validation Plan

- [ ] Every processed candidate has exactly one explicit disposition and at
  most one canonical effort owner.
- [ ] Public issues are deduplicated against the live tracker and contain only
  self-contained, reproducible, general-purpose text.
- [ ] Domain effort links resolve and no domain is represented by competing
  active plans.
- [ ] Automated scanning and independent review find no environment-specific
  names, links, paths, identifiers, or unpublished evidence in new artifacts.
- [ ] Unresolved ownership blocks publication rather than silently falling back
  to the umbrella.

## Proposal

Use this effort as the public intake umbrella and fallback classification owner.
Route validated work into the existing domain plans whenever possible, and
retain only genuinely unclassified general-purpose work here.

## Journal

### 2026-08-29 - Kickoff

- Established the neutral intake contract and indexed the canonical domain
  plans without importing any originating context.
