# agent-logger Aggregate Configuration

- **Slug:** `agent-logger-aggregate-configuration`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees and pull requests
- **Created:** 2026-09-03
- **Status:** Active
- **Vision:** [`repository-owned-aggregate-configuration`](../../../visions/plugins/agent-logger/README.md#repository-owned-aggregate-configuration) · [`explainable-machine-plan`](../../../visions/plugins/agent-logger/README.md#explainable-machine-plan) · [`resolve-before-side-effects`](../../../visions/plugins/agent-logger/README.md#resolve-before-side-effects) · [`reject-cross-repository-collisions`](../../../visions/plugins/agent-logger/README.md#reject-cross-repository-collisions) · [`configuration-is-authorization`](../../../visions/plugins/agent-logger/README.md#configuration-is-authorization)
- **Continuing coordination:** This Active Plan, Validation Plan and Journal;
  #1817 is historical tracking after complete reviewed intake. Independent
  implementation slices retain eligible open public issues and their owners.
- **Sub-issues:** #1831 · #1832 · #1833 · #1834 · #1835

## Guiding Intent

Make one installed agent-logger safely serve several repository owners on the
same machine. Each repository should be able to publish portable collection and
rendering intent, specialize that intent for a machine, and accept a
repository-scoped local override without gaining authority over another
repository's claims.

The runtime must compile those declarations into one deterministic,
explainable plan before it performs any collection, pruning, rendering, or
landing. Ambiguous ownership is an error to repair, never a reason to guess
which repository wins.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary contributor | Owns design, implementation slices, pull requests, and validation | Isolated copilot-extensions worktrees |
| Copilot reviewer | Reviews each proposed or implemented slice | Repository pull-request review |

## Coordination

- **Topology:** Independent per-slice pull requests, each merged when its own
  review, CI and mergeability gates pass; sequence actual dependencies only.
- **Host (owns PRs):** Primary contributor.
- **Delegates:** None initially; bounded implementation or review slices may be added here before dispatch.
- **Handoff:** The effort README, issue state, merged pull requests, and Journal are the durable relay.
- **Continuing intake:** Keep all remaining phases and validation here. Claim
  concrete overlapping implementation through an eligible open bounded source
  issue and this Journal, preserving the primary contributor and existing
  source, collection, rendering and operational custody. A closed historical
  umbrella never serves as a live claim or evidence of completed acceptance.

## Context

The revised [agent-logger vision](../../../visions/plugins/agent-logger/README.md)
defines repository-owned declarations, same-repository precedence,
collision-free cross-repository composition, an explainable normalized machine
plan, and fail-closed authorization before side effects. The continuing
Plan/Validation/Journal below owns that delta; #1817 records its historical
tracking origin, not a live implementation claim or completed contract.

Current reality is centered on one layered `Config`:

- built-in defaults;
- one machine-local `$AGENT_LOGGER_HOME/config.yaml`;
- one current-repository `.agent-logger.yaml`, intentionally restricted to
  log-presentation fields;
- selected environment overrides.

Session sync and the chronicler consume that merged object directly.
`agent-logger config` reports a summary of the one resolved object, target
implementations expose narrow doctor checks, and there is no aggregate
repository discovery, declaration provenance, normalized claim model, or
cross-repository collision analysis.

The implementation must preserve the safety boundary of the existing
repository-local log configuration while introducing a distinct,
versioned repository policy surface. It must remain useful without embedding a
particular control-repository name, machine inventory, or private topology.

## Request

> “This sounds good. Get this revision added upsream, then make an effort to start building it out.”

## Plan

### Phase 1 — [Specify the resolution contract](resolution-contract.md)

- [x] Define the versioned repository declaration and repository-scoped local-override contracts without overloading the existing log-presentation-only file.
- [x] Define a portable discovery-provider seam and the first concrete discovery path, including stable repository identity, declaration provenance, and an explicit machine-local admission registry.
- [x] Require one admitted authoritative checkout per canonical repository identity; treat declarations from unadmitted repositories and secondary worktrees as inert but diagnosable.
- [x] Define source selectors with canonical repository identities and an anchored, decidable pattern grammar; retire substring matching from ownership claims and specify how legacy substring lists map during compatibility.
- [x] Define explicit exclusions or cession, machine selectors, collection claims, rendered-log claims, targets, profiles, and landing policies.
- [x] Define deterministic same-repository precedence and reject equally specific or otherwise ambiguous machine matches.
- [x] Model the machine-default fallback as a first-class bounded claim that participates in overlap analysis rather than an implicit catch-all.
- [x] Define declaration-schema compatibility, including how unknown versions are quarantined or disabled machine-locally without granting another repository precedence.
- [x] Make aggregate resolution independent of process cwd and ordinary environment overrides; only admitted, provenance-bearing inputs may create or suppress claims.
- [x] Define canonical normalization, overlap witnesses, error taxonomy, and the versioned resolved-plan JSON contract.
- [x] Define ownership-change semantics for already-rendered records and reservation state; default to applying new ownership only to newly due work unless an explicit migration is requested.
- [x] Carve implementation slices into public sub-issues before code changes begin.

### Phase 2 — Build the aggregate compiler

- [x] Add typed declaration, provenance, selector, claim, target, and resolved-plan models.
- [x] Load committed declarations and repository-scoped machine-local overrides through the discovery seam.
- [x] Resolve each repository independently, then aggregate declarations without cross-repository precedence.
- [x] Deduplicate multiple checkouts and worktrees of one admitted repository identity against its authoritative checkout before claim analysis.
- [x] Canonicalize repository and target identity so basename aliases or path spelling cannot evade collision checks.
- [x] Detect exact, wildcard, exclusion, target, and ownership-dimension collisions with actionable witnesses.
- [x] Produce byte-stable normalized JSON for equivalent inputs independent of discovery order.

### Phase 3 — Add diagnostics and operator surfaces

- [x] Extend `agent-logger config` with an explicit resolved aggregate JSON surface while preserving compatibility for existing callers.
- [x] Add an aggregate `agent-logger doctor` command that uses the compiler result rather than reinterpreting configuration.
- [x] Report declaration provenance, selected and inactive machine clauses, overrides, normalized claims, canonical targets, sink readiness, and conflicts.
- [x] Define stable exit behavior for empty/passive, valid/authorized, invalid, unavailable, and conflicting plans.
- [x] Repoint or deprecate `agent-logger chronicle status` so it reports the same compiler result rather than a third interpretation of configuration.
- [x] Keep diagnostics read-only and safe to run while scheduled work is disabled or maintenance-gated.

### Phase 4 — Preserve compatibility and observe the delta

- [ ] Map legacy sync allowlists, denylists, chronicle routes, skips, and default sinks into explicit compatibility claims with visible provenance.
- [ ] Ship the compiler and diagnostics in observe-only mode before they control side effects.
- [ ] Report the normalized aggregate plan alongside the legacy plan and highlight any behavioral delta, ambiguity, or future collision.
- [ ] Add an explicit enforcement switch and documented rollback that restores legacy execution without discarding declarations or diagnostic evidence.
- [ ] Provide setup and migration guidance before any release can make an empty aggregate passive.

### Phase 5 — Gate collection and chronicling

- [ ] Adapt session sync to consume only authorized collection claims from the resolved plan.
- [ ] Adapt chronicler construction and routing to consume only authorized rendered-log claims and sinks.
- [ ] Refuse scheduling, copying, pruning, reservation, rendering, and landing when aggregate resolution fails.
- [ ] Ensure force or recovery flags cannot bypass an unauthorized aggregate plan.
- [ ] Preserve existing input fencing, catch-up idempotency, transaction rollback, and landing serialization guarantees.
- [ ] Preserve already-landed records across ownership changes; route only newly due work to a new owner unless an explicit migration workflow is invoked.

### Phase 6 — Adoption, migration, and deployment

- [ ] Document declaration ownership, machine specialization, local overrides, explicit wildcard exclusions, and collision repair using synthetic examples.
- [ ] Update configuration and architecture docs to distinguish legacy runtime configuration, repository log presentation, and aggregate operational policy.
- [ ] Add setup or migration guidance that never writes committed repository policy during install or update.
- [ ] Bump all required agent-logger versions with each implementation pull request.
- [ ] Deploy the merged runtime through the unified update flow and validate a passive machine before enabling real collection policy.

### Direct aggregate authorization and adoption acceptance from #1817

<!-- backlog-reconciliation:7556:transfer:1817:to:efforts/active/agent-logger-aggregate-configuration/README.md -->

This direct Plan/Validation supplement retains acceptance within the existing reviewed
Active effort, preserving its entire current contract, completed phases1-3, primary contributor,
Coordination and every current claim. It includes ALL accepted1834/1835 clauses, not a
link to closed wrappers. Source compiler/diagnostics remain credited without claiming
effecting/native/adoption completion. Only after normal review, actual merge and CURRENT
FULL public Plan/Validation/custody readback may1817 become historical; otherwise retain
it. The effort stays open until its own full completion gate is met.

#### Plan

- [ ] Preserve intent-level vision for deterministic repository defaults/machine
  selectors/scoped overrides, canonical repository/source identities, separate
  collection/rendered-log ownership, rejected cross-repository overlap and one
  normalized explainable doctor plan before effects. Do not freeze operational key names
  into the vision.
- [ ] Preserve existing admitted authoritative clean-checkout trust, inert
  unadmitted/secondary worktrees, duplicate/same-basename identity handling and bounded
  presentation-only config. Scoped overrides affect only their owner; no cwd/environment
  input may manufacture or suppress claims.
- [ ] Preserve exact/wildcard/exclusion/cession/unclassified ownership algebra,
  canonical logical-resource/target aliases, fallback collision analysis,
  machine-specific precedence/tie rejection, unknown-schema quarantine and explicit
  error/provenance. Disable/quarantine is not silent ownership transfer.
- [ ] Translate legacy sync allow/deny, chronicler routes/skips/default sinks into
  provenance-bearing compatibility claims; mark substring-derived/nondecidable claims
  unenforceable rather than silently granting capture authority.
- [ ] Ship actual observe-only legacy-versus-normalized
  routing/ownership/ambiguity/future-collision comparison before changing execution,
  without collection, scheduling, pruning or rendering through the new plan during
  comparison; preserve the documented legacy window.
- [ ] Provide explicit machine-local enforcement selection and documented rollback to
  legacy while preserving declarations, admission, overrides, diagnostics and audit.
  Installation/update cannot enable enforcement or write committed repository policy.
- [ ] Sync consumes authorized collection claims only; chronicler construction/routing
  consumes authorized rendered-log claims, sinks and landing policies only. No implicit
  cross-repository precedence or wildcard capture.
- [ ] In enforcement mode, invalidate the WHOLE plan before scheduling/dispatch,
  filesystem/network copying, pruning, reservation/journal mutation, manifest/render
  writes, commit/push/PR effects. Force/recovery/catch-up cannot bypass. Read-only
  diagnosis remains available; an empty authorized enforced plan is passive success.
- [ ] Preserve input fences, catch-up idempotency, transaction rollback and landing
  serialization. Ownership changes apply to newly due work only; historical landed logs
  and completed reservation/journal evidence stay stable unless separately authorized
  attributable/idempotent migration moves them.
- [ ] Publish synthetic examples of portable declarations, canonical
  admission/authoritative checkouts, default/machine/scoped override, exclusion/cession,
  logical resource bindings, sink readiness, collision repair and quarantine without
  credentials/private topology.
- [ ] Architecture/config docs distinguish legacy runtime, bounded log presentation and
  aggregate authorization. Document stable resolved-plan schema, provenance/errors,
  observe/enforce and rollback. Adoption/admission/migration follows repository-reviewed
  intent; install/update never edits committed policy.
- [ ] Follow current changefile/release policy, current bounded unit admission and
  explicit integration/native lanes with truthful unavailable outcomes. Historical
  full-suite descriptions are not permission to restore retired CI gates or expand
  deadlines. Validate passive behavior before separately authorized
  collection/enforcement rollout and preserve operational claims/state through
  deployment/rollback.

#### Validation

- [ ] Retain the COMPLETE existing validation matrix: discovery order/cwd/environment
  independence; canonical duplicate-worktree/same-basename/prefix identities;
  exact/wildcard/exclusion/unclassified and target-alias/fallback collisions; machine
  specificity/ambiguity and scoped overrides; independent collection/rendering
  dimensions; schema/quarantine; empty/passive plans; deterministic secret-free
  byte-stable JSON; bounded presentation/legacy compatibility; no-force bypass;
  ownership changes without historical-record mutation.
- [ ] Demonstrate no side effects for unauthorized, invalid, unavailable or conflicting
  plans at every effecting boundary, not merely a diagnostic exit or observe/enforce
  enum. Prove sync and actual chronicler consumers use their authorized dimensions
  before mutation.
- [ ] Demonstrate actual Windows/Linux path, identity, override, normalized-plan AND
  effecting-consumer parity, plus relevant disposable clean-room adoption. Source
  inspection, deterministic units and diagnostic-only execution keep their bounded tiers
  and are not a new native acceptance pass.
- [ ] Verify observe-only comparison is genuinely passive, explicit enforcement/rollback
  preserves policy/evidence and newly active ownership does not rewrite completed
  records. Preserve claimed-input, catch-up, rollback and serialized landing invariants.
- [ ] Keep the current primary contributor and every claim/hold/operational owner;
  broader intelligence/accounting composes with this policy, not a replacement. No new
  ACK/all-parent native ceremony or resource transfer is introduced; every own
  Plan/Validation item must resolve or transfer to a named objective before Done.

Sources: https://github.com/ThomasMichon/copilot-extensions/issues/1817 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1828 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1839 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1852 ;
https://github.com/ThomasMichon/copilot-extensions/pull/1857 ;
https://github.com/ThomasMichon/copilot-extensions/pull/5671 .

## Validation Plan

- [ ] Complete every directly retained #1817 Plan/Validation clause above at its original evidence tier and bounds; preserve current owners, holds and unfinished acceptance. Tracker consolidation is not implementation.

- [ ] Two disjoint repository declarations resolve identically regardless of discovery order.
- [ ] A declaration in an unadmitted checkout is inert and reported as unadmitted.
- [ ] Three worktrees of one admitted repository do not self-collide, and changes in a non-authoritative worktree do not alter the machine plan.
- [ ] An exact claim colliding with another exact claim fails with both provenance records and the overlapping source.
- [ ] A wildcard plus a specific claim fails unless an explicit exclusion or cession makes the claims disjoint.
- [ ] Prefix-overlapping repository names remain distinct and cannot cross-capture through legacy substring behavior.
- [ ] Collection and rendered-log ownership are checked independently and may resolve to different owners.
- [ ] A machine-specific clause overrides only its repository's default; a repository-scoped local override cannot affect another declaration.
- [ ] Equally specific machine clauses, malformed selectors, and unstable repository identities fail closed.
- [ ] Unknown declaration versions are quarantined with actionable diagnostics, and a machine-local disable cannot transfer the claim to another repository silently.
- [ ] Repositories with the same checkout basename but different canonical identities remain distinct.
- [ ] Canonically equivalent targets collide even when configured with different path spellings or aliases.
- [ ] The machine-default fallback cannot overlap another repository's explicit claim.
- [ ] Invalid or unavailable targets make the whole plan unauthorized before any filesystem, network, reservation, pruning, rendering, git, or scheduling side effect.
- [ ] Empty configuration leaves the runtime passive with a successful, explainable diagnostic result.
- [ ] Aggregate resolution produces the same plan from any cwd, and environment variables cannot forge or suppress repository claims.
- [ ] Resolved JSON is schema-versioned, deterministic, provenance-rich, and free of secrets.
- [ ] Existing log-presentation-only repository configuration remains bounded to log organization.
- [ ] Existing single-config sync and chronicler tests remain green through the compatibility window.
- [ ] Observe-only rollout reports legacy-versus-aggregate differences without changing collection; enforcement is explicit and reversible.
- [ ] `--force` and equivalent recovery paths still refuse an unauthorized aggregate.
- [ ] Changing ownership leaves existing landed records and reservations stable while newly due work follows the new plan.
- [ ] Linux and Windows path, identity, override, and diagnostic behavior remain equivalent.
- [ ] Equivalent declarations and machine identity produce byte-identical normalized JSON across Linux and Windows apart from explicitly machine-scoped fields.
- [ ] The agent-logger plugin suite, changed-plugin guards, install-contract checks, and relevant clean-room scenario pass before deployment.

## Proposal

Reviewed and merged in #1828. Implementation is authorized against this plan.

## Journal

### 2026-10-22 - Complete retained acceptance from #1817

- Retained the complete #1817 scope directly in the Plan and Validation,
  without declaring feature completion or transferring source/resource custody.
  Normal reviewed merge and current full readback precede administrative
  supersession; all substantive acceptance remains unfinished.

### 2026-09-03 — Kickoff

- Landed the aggregate-configuration vision revision through #1818.
- Confirmed the repository has adopted canonical target-owned efforts.
- Grounded the plan in the current `Config`, CLI, sync, chronicler factory,
  repository organization configuration, and deployment documentation.
- Created this effort against #1817; implementation remains behind the effort
  review gate.
- Incorporated a pre-publication review: added admitted authoritative
  repositories, decidable non-substring claims, multi-worktree handling,
  observe-only migration before enforcement, fallback collision treatment,
  schema quarantine, cwd independence, and stable ownership-change behavior.

### 2026-09-03 — Execution started

- The reviewed effort merged in #1828 and the implementation worktree pulled
  forward onto the merged plan.
- Carved Phase 1 through adoption into #1831, #1832, #1833, #1834, and #1835.
- Began #1831 with the linked aggregate resolution contract covering
  configuration homes, trust and admission, authoritative checkouts, stable
  identity, claim algebra, deterministic machine selection, collision
  witnesses, diagnostics, observe-only rollout, and ownership changes.

### 2026-09-03 — Compiler and diagnostics

- Merged the typed aggregate compiler, injectable admission/discovery seam,
  deterministic normalization, collision analysis, and secret-free resolved
  plan in #1852, closing #1832.
- Added read-only resolved-config, doctor, and chronicle-status surfaces for
  #1833. Diagnostics verify that admitted checkouts are clean default-branch
  anchors, preserve legacy status output, and return a structured unauthorized
  plan for malformed aggregate configuration.
