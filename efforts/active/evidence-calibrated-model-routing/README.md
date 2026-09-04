# Evidence-Calibrated Model Routing

- **Slug:** `evidence-calibrated-model-routing`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice worktrees and pull requests
- **Created:** 2026-09-04
- **Status:** Draft
- **Vision:** [`visions/harness-guidance`](../../../visions/harness-guidance/README.md)
  - `evidence-calibrated-model-routing`
  - `purpose-to-model-grounding`
  - `routing-policy-before-delegation`
  - `least-expensive-demonstrated-choice`
  - `accepted-outcome-economics`
  - `independent-promotion`
- **Umbrella issue:** [#2014](https://github.com/ThomasMichon/copilot-extensions/issues/2014)
- **Foundation:** [#1267](https://github.com/ThomasMichon/copilot-extensions/issues/1267)
  (coordinator-first delegation guidance)

## Guiding Intent

Let a Task-capable coordinator select the least-expensive available model that
has demonstrated the capability, tools, context, and reliability required for a
delegated purpose. Keep the routing strategy portable and model-neutral while
repositories and operators supply current model eligibility as inert,
reviewable data.

Judge routing by accepted work products rather than isolated call prices.
Trials must remain explicit and contained, ordinary product gates stay
authoritative, and the agents being evaluated cannot promote themselves into
the default routing policy.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Public effort host | Owns the issue, plan, serial PRs, and final synthesis | managed worktree |
| Bounded evidence worker | Inventories one explicitly assigned source surface and returns cited findings | native sub-agent |
| Restricted implementation worker | Executes an approved code slice without ambient credentials or authority | agent-containers restricted venue |
| Independent reviewer/steward | Reviews product changes and separately approves eligibility promotions | repository review workflow |

## Coordination

- **Topology:** serial per-slice PRs; one open implementation PR at a time.
- **Host (owns PRs):** public effort host.
- **Delegates:** evidence and implementation workers receive non-overlapping,
  bounded scopes; no worker owns final synthesis, publication, or promotion.
- **Handoff:** each slice records its merged outcome and unresolved decisions in
  this effort before the next slice begins.

## Context

Issue #1267 and the merged coordinator-first implementation established
model-neutral direct-versus-delegate guidance: bounded lookups remain direct,
broad separable evidence is delegated before source ingestion, delegate
contracts are bounded, and the coordinator retains the goal and synthesis.

Issue #2014 identifies the next delta. The current guidance does not tell a
coordinator which available model is appropriate for an evidence, coding,
testing, review, or domain-tool role. A cheap but unsuitable worker can create
more total cost through retries, discarded output, coordinator repair, and
review findings than a more capable choice.

The harness-guidance vision now states that model routing is
evidence-calibrated, purpose-aware, delivered before delegation, independently
promoted, and subordinate to product correctness. The implementation design is
captured in [design.md](design.md).

Current reusable surfaces:

- `delegation-guidance` owns direct/delegate strategy, the concise context
  kernel, and the detailed delegation skill;
- `context-injection` owns attributable first-turn composition, progressive
  disclosure, trust gating, and fail-open delivery;
- `config-migrate` provides versioned inert configuration migration;
- agent-worktrees profile assignment provides prior art for deterministic
  selection and terminal assignment state;
- agent-dispatch owns durable task/session provenance;
- agent-containers owns restricted execution and credential boundaries.

## Request

Public formulation from issue #2014:

> Add a model-neutral strategy and configurable purpose-to-model registry so a
> coordinator can select the least-expensive available model demonstrated for a
> delegated role and execution surface. Candidate models should run only in
> explicit trials; substitutions, escalations, outcomes, and promotion decisions
> should remain attributable. Deliver the compact policy before a Task-capable
> agent's first delegation decision without forcing non-delegating workers to
> ingest the complete registry.

## Plan

### Phase 0 - Reviewed intent and implementation plan

- [x] Claim public issue #2014 after searching for an existing owner.
- [x] Revise and merge the harness-guidance vision through
  [PR #2025](https://github.com/ThomasMichon/copilot-extensions/pull/2025).
- [x] Run a bounded coordinator/evidence-worker planning pass against the
  current plugin, configuration, dispatch, and restricted-container surfaces.
- [ ] Land this effort through the repository's review gate before changing
  plugin code.

### Phase 1 - Registry and on-demand strategy (`delegation-guidance`)

- [ ] Define and register a versioned inert purpose-to-model configuration
  schema with demonstrated, candidate, held, and failed eligibility.
- [ ] Define plugin, repository, and operator configuration layers without
  allowing a consumer to seize ownership of portable strategy or safety policy.
- [ ] Ship no real model defaults. Examples remain obvious placeholders; current
  model IDs arrive only through configuration and reviewed evidence.
- [ ] Extend the delegation skill with task classification, selection,
  availability fallback, explicit-trial, escalation, and result-integration
  guidance.
- [ ] Add one compact first-turn decision cue that tells a coordinator to load
  the detailed routing grounding before delegating.
- [ ] Keep malformed, unavailable, or inapplicable routing configuration
  startup-nonblocking; unproven candidates still fail closed at eligibility.

### Phase 2 - Deterministic selection helper

- [ ] Add a pure resolver from purpose, required capabilities, execution
  surface, model availability, and layered registry to an ordered routing
  decision.
- [ ] Prefer the least-expensive demonstrated eligible choice; fall through to
  another demonstrated choice when the preferred one is unavailable.
- [ ] Require an explicit armed-trial mark before returning a candidate.
- [ ] Return a structured reason for ordinary choice, substitution, trial,
  escalation, hold, or no-eligible-model.
- [ ] Keep the direct-versus-delegate decision ahead of model selection so cheap
  workers do not cause unnecessary task fragmentation.

### Phase 3 - Outcome provenance and trial admission

- [ ] Decide and document the minimal provenance owner before implementation:
  agent-dispatch task/session state, an assignment record derived from
  agent-worktrees prior art, or a deliberately shared contract with one writer.
- [ ] Record purpose, selected model, eligibility state, execution surface,
  assignment reason, parent/worker identity, and terminal disposition without
  storing raw prompts or source bodies.
- [ ] Emit attribution that external accounting systems can join to unique
  provider billing events; do not build a billing or promotion-analytics ledger
  inside this plugin suite.
- [ ] Add an enforceable admission check at the dispatch/restricted-container
  launch seam for demonstrated choices and explicitly armed candidate trials.
- [ ] Bind writable trial authority to a declared worktree, containment profile,
  path/tool/network boundary, and prior reviewed decision.

### Phase 4 - Integration, delivery, and adoption

- [ ] Preserve the current context-injection contributor contract and kernel
  budget; defer audience-gating changes unless behavioral evidence proves the
  on-demand pointer insufficient.
- [ ] Prove a Task-capable coordinator receives the compact cue before its first
  delegation and a non-delegating worker does not load the complete registry.
- [ ] Document configuration, diagnostics, trial arming, fallback, and safe
  examples.
- [ ] Keep marketplace, manifest, package, and runtime versions aligned for
  every touched plugin.
- [ ] Install through the normal update path and prove source/deployed payload
  identity before behavioral evaluation.

### Phase 5 - Evidence and independent promotion

- [ ] Run bounded real-work trials through ordinary product review gates.
- [ ] Join routing provenance to accepted, rejected, repaired, retried,
  abandoned, or superseded outcomes without double-counting billed events.
- [ ] Require a separate reviewed registry change to promote a candidate; the
  evaluated coordinator, worker, pair, or PR author cannot validate itself.
- [ ] Preserve task-class, execution-surface, and uncertainty boundaries instead
  of generalizing one successful outcome to every role.
- [ ] Record measured findings, close #2014 when the implementation delta is
  complete, and archive this effort.

## Validation Plan

- [ ] The strategy, kernel, skills, and vision contain no hardcoded preferred
  model or provider.
- [ ] The registry schema is versioned, strictly parsed as inert data, and
  rejects newer unsupported schema versions without executing content.
- [ ] Layer precedence is deterministic and preserves policy ownership.
- [ ] Missing/malformed configuration never blocks session startup.
- [ ] Unproven candidates are never returned as ordinary demonstrated choices.
- [ ] Availability fallback selects only another eligible demonstrated model
  unless an explicit trial is armed.
- [ ] Selection reasons and applicability boundaries are deterministic and
  machine-readable.
- [ ] Direct bounded work remains direct; model availability cannot force
  unnecessary delegation.
- [ ] The concise Bash and PowerShell kernels remain equivalent and inside the
  declared context budget.
- [ ] A Task-capable coordinator loads routing grounding before delegation,
  while a non-delegating worker avoids the complete registry cost.
- [ ] Outcome provenance contains no raw prompt, source body, credential, or
  repository-private context.
- [ ] Every billed event can be joined at most once by an external ledger; this
  repository does not manufacture or duplicate monetary cost.
- [ ] Restricted trial admission denies unapproved models, authority levels,
  paths, tools, network surfaces, credentials, and writable venues.
- [ ] Product correctness, security, review, and publication gates remain
  unchanged and outrank routing cost.
- [ ] Promotion is a separate reviewed action based on durable evidence and
  cannot be authored or approved solely by the evaluated pair.
- [ ] Plugin-specific guards, tests, docs consistency, install contracts, and
  cross-platform behavior pass without invoking the repository-wide exhaustive
  portfolio.

## Proposal

See [design.md](design.md).

## Journal

### 2026-09-04 - Kickoff

- Public issue #2014 claimed the implementation delta after #1267 completed the
  model-neutral coordinator-first foundation.
- The harness-guidance vision revision merged in PR #2025 after a blind
  generativity pass and a reality-aware review. It added evidence-calibrated
  model routing, accepted-outcome economics, independent promotion,
  startup-nonblocking delivery, and inert-configuration intent.
- A read-only planning pair selected one bounded evidence worker and found the
  existing ownership seams sufficient: extend `delegation-guidance`, reuse
  context-injection and config-migrate, and keep enforcement/provenance thin at
  dispatch/container boundaries.
- This PR carries only the reviewed implementation plan. Plugin changes begin
  after it merges.

