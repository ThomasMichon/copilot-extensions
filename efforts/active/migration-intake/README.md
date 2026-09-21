# Migration Intake

- **Slug:** `migration-intake`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-08-29
- **Status:** Active
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

## Intake Contract

Frozen per Phase 1. Every candidate processed by this effort is evaluated
against exactly this contract; a candidate that does not satisfy it is not
published.

1. **Disposition.** Every candidate resolves to exactly one of:
   - `routed` — accepted into an existing canonical domain plan's own Plan
     section (the domain plan is extended if its current phases do not yet
     cover the work).
   - `residual` — genuinely general-purpose but not yet classifiable into any
     canonical domain; stays intake-owned until enough is known to route it.
   - `rejected` — not portable/general-purpose (tied to one machine, one
     session, or non-reproducible context) and not published.
   - `closed-obsolete` — already satisfied by delivered scope (checked against
     the domain plan's own journal/Validation Plan, or an `agent-index-engine-daemon`
     / `uniform-runtime-resolution`-style completed reference).
   - `superseded` — a duplicate of an existing tracker entry or another
     candidate; the entry records which one it defers to.
2. **Ownership.** A candidate is publishable only once it has exactly one
   primary owner (a canonical domain plan, or this effort itself for
   `residual`). Cross-domain relevance may be noted in the ledger without
   creating a second owner.
3. **Fail-closed.** If ownership or publication safety (novelty vs. the live
   tracker, absence of environment-specific identifiers) cannot be resolved
   from available evidence, the candidate stays `residual` — it is never
   force-routed or force-published to clear a queue.

The **candidate ledger** (`ledger.md` in this directory) is the append-only
record: one dated section per pass, each with one row per candidate
recording its source, disposition, owner, and tracker outcome. A later pass
never edits a prior pass's rows in place -- it appends its own section so
the full history from initial scan to final outcome stays comparable and
auditable. A candidate leaves `residual` only when a later pass can resolve
disposition 1-3 above.

## Plan

### Phase 1 - Freeze the intake contract

- [x] Require one explicit disposition per candidate: route to a canonical
  domain effort, retain as an intake-owned residual, reject as non-portable,
  close as obsolete, or supersede as a duplicate. (See Intake Contract above.)
- [x] Require one primary owner before publication; cross-domain relevance may
  be recorded without creating joint ownership. (See Intake Contract above.)
- [x] Fail closed when ownership or publication safety is unresolved. (See
  Intake Contract above.)

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

### 2026-09-20 — Phase 3: created 3 issues for already-`routed` candidates; 2 have no live umbrella yet

- Worked through the 6 `routed` candidates left after the post-merge
  correction pass (#3, #5, #9, #14, #15, #16), confirming each target's
  domain-plan addition is already merged to `main`, i.e. domain-plan
  acceptance already happened. This pass does not change any candidate's
  disposition -- all 6 stay `routed`, per the Intake Contract's taxonomy. It
  only creates public tracker issues (tracker metadata, not a disposition)
  where a live umbrella exists to attach them under.
- Before creating an issue for #3 (worktree-manager-control-plane's cached
  worktree-status projection), found it already addressed by an open,
  not-yet-merged PR under a *different* effort
  ([#3102](https://github.com/ThomasMichon/copilot-extensions/pull/3102),
  `agent-worktrees-external-status-accelerator`) -- no new public issue
  needed unless that PR does not land; annotated the domain Plan bullet with
  a pointer to it, keeping the candidate `routed` (not `superseded`/
  `closed-obsolete`) pending its merge, per Copilot review feedback on this
  PR that flagged the original wording as prematurely treating open work as
  delivered.
- Created public issues for #9 and #16
  ([#3113](https://github.com/ThomasMichon/copilot-extensions/issues/3113)
  and [#3114](https://github.com/ThomasMichon/copilot-extensions/issues/3114))
  under `worktree-finality-and-obligations`'s live umbrella #1312.
- Created a public issue for #5
  ([#3115](https://github.com/ThomasMichon/copilot-extensions/issues/3115))
  citing `agent-machines-declarative-control-plane`'s umbrella #1418, then
  found on review that #1418 and all seven of that effort's listed
  sub-issues are closed -- there is currently no live umbrella to attach it
  under. Edited #3115 to drop the false "Part of #1418" claim, so it now
  stands alone as a real, open tracker entry; the candidate's disposition
  stays `routed` (unchanged -- a missing umbrella is tracker metadata, not a
  new taxonomy state), and the issue will be linked under a live umbrella
  once that effort opens one. Marked #1418 historical everywhere it's
  referenced in that effort's own README (header, coordination-token line,
  delegate line) and in the efforts index, removing the contradiction
  between those references and this note calling it closed. Each issue
  body is a self-contained, repository-neutral restatement of the routed
  Plan bullet with Summary/Scope/Validation sections, matching this repo's
  existing issue convention -- no reference to this ledger, any session, or
  any originating context.
- Left #14 (`account-aware-operations`) and #15
  (`review-automation-reliability`) without a created issue (unlike #5,
  which does have one): both owning efforts are still `Draft` status with
  no umbrella issue, so there is no accepting parent to attach a sub-issue
  to yet. Disposition stays `routed` for both -- this is a gap in tracker
  metadata (no issue created yet), not a fail-closed disposition change; an
  issue will be created for each once its owning effort is both `Active` and
  has a live umbrella.
- Appended a dated "Phase 3 publication" section to `ledger.md` recording the
  per-candidate outcome, alongside (not overwriting) the prior passes.

### 2026-09-20 — Post-merge correction: 2 mis-routed candidates were duplicate ownership

- Self-audit after merging the Phase 2 revalidation PR found candidates
  **#17** and **#19** were routed into `marketplace-scoped-installations`'
  Phase 7 as new backlog bullets, but each duplicates an already-existing,
  more detailed **Draft** effort's own scope: `tiered-payload-provisioning`
  (Windows `stamp` action) for #17, `vendored-installer-engine` (Phase 0
  audit) for #19. Removed both duplicate Phase 7 bullets from
  `marketplace-scoped-installations` and reclassified both candidates
  `superseded` by their real (Draft, but pre-existing) owning efforts in
  `ledger.md`.
- The other 6 routed candidates (#3, #5, #9, #14, #15, #16) were checked
  again and confirmed distinct from any existing Draft effort -- their
  targets are already-Active domain efforts with no competing dedicated
  plan for the same scope, so no further correction was needed there.
- Lesson recorded in the ledger: a Draft effort is still a real owner, not
  an absence of one -- check for a dedicated (even unactivated) effort
  before routing a candidate into a large domain effort's generic backlog.

### 2026-09-20 — Phase 2 revalidation: 8 candidates routed, 4 stale, 4 stay residual

- Revalidated all 19 raw candidates from the initial ledger pass against
  their current source-effort text and, for each proposed owner, that
  target's current Plan. Findings:
  - **4 already delivered** since the initial scan (agent-index-engine-daemon's
    accelerator selection, handoff-cutover-reload-robustness' bare-resume
    spawn, pr-attribution-codenames' attribution phase, uniform-runtime-
    resolution's link retirement) -> reclassified `closed-obsolete`, no
    tracker entry.
  - **1 mis-routed on the initial pass**: native-construct-convergence's own
    deferred delegation (candidate #12) is already tracked in that effort's
    own Phase C (#988) -- corrected from the initial "route to
    worktree-manager-control-plane" guess to "already covered by its own
    effort," no action.
  - **8 accepted `routed`**, each placed into its target's own "Reconcile
    deferred backlog" phase (the standard acceptance gate every domain plan
    in this pattern carries): `worktree-manager-control-plane` Phase 8 (1
    item), `worktree-finality-and-obligations` Phase 7 (2 items),
    `marketplace-scoped-installations` Phase 7 (2 items),
    `agent-machines-declarative-control-plane` (1 item -- added a new Phase 5
    for this, since the effort had no reconcile-backlog phase yet),
    `account-aware-operations` (1 item -- new Phase 5), and
    `review-automation-reliability` (1 item -- new Phase 11).
  - **4 stay `residual`** under this effort: no fitting canonical domain owner
    was found for daemon self-retirement semantics, dispatch worker-identity
    policy binding, pre-lifecycle history backfill, or the next
    module-componentization candidate.
  - 1 `rejected` (custom-context-aggregator-retirement's remaining work) was
    confirmed on re-read as genuinely blocked on facility-specific transport
    proof, not a portable gap.
- **No public GitHub issues created.** Phase 3's "obtain acceptance from the
  chosen domain plan" happens through each target effort's own normal PR
  review of the Plan addition just made here -- issue publication is the
  next step only after that acceptance, and only for items that still need
  a public tracker entry distinct from the domain plan's own Plan checkbox.
- Appended a dated "Phase 2 revalidation" section to `ledger.md` with final
  dispositions and outcomes, alongside (not overwriting) the Initial scan
  section from the prior pass, keeping the ledger genuinely append-only.

### 2026-09-20 — Phase 1 frozen; initial Phase 2 candidate ledger

- Froze the intake contract (Phase 1, all three items) as an explicit,
  referenceable **Intake Contract** section: a 5-way disposition taxonomy
  (`routed`/`residual`/`rejected`/`closed-obsolete`/`superseded`), a
  single-primary-owner rule, and a fail-closed rule for unresolved
  ownership/safety.
- Created `ledger.md`, the append-only candidate record the contract
  references.
- Ran an initial breadth-first scan of all ~47 `efforts/active/` directories
  for deferred/out-of-scope/future-work language in their Journal and Plan
  sections. Logged 19 raw candidates with a proposed disposition and owner
  each (see `ledger.md`) -- explicitly **not yet Phase-2-revalidated**: none
  have been checked against current code/docs/issues, so no tracker entries
  exist yet and Phase 2's checkboxes remain unticked. Six candidates are
  proposed `routed` to `worktree-manager-control-plane` alone, suggesting
  that effort may be a much larger active sink than its own Plan currently
  reflects -- worth confirming with that effort's owner before Phase 3
  acceptance.
- Data-quality finding (not a candidate): the canonical domain-plans list's
  `session-context-aggregation` entry correctly points outside
  `efforts/active/` (a completed historical reference), but its list
  placement reads as if it were an active plan -- flagged for a future
  clarifying pass, not fixed here to avoid conflating a docs nit with the
  intake campaign's own work.
- Next: Phase 2 revalidation of the 19 raw candidates (confirm against
  current code/docs/issues, dedupe, strip any residual environment
  specificity) before any Phase 3 routing/publication.

### 2026-08-29 - Kickoff

- Established the neutral intake contract and indexed the canonical domain
  plans without importing any originating context.

