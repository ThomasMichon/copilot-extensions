# Evidence-Calibrated Model Routing

- **Slug:** `evidence-calibrated-model-routing`
- **Repo:** copilot-extensions
- **Branch(es):** serial per-slice worktrees and pull requests
- **Created:** 2026-09-04
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** [`visions/harness-guidance`](../../../visions/harness-guidance/README.md)
  - `evidence-calibrated-model-routing`
  - `purpose-to-model-grounding`
  - `routing-policy-before-delegation`
  - `least-expensive-demonstrated-choice`
  - `accepted-outcome-economics`
  - `independent-promotion`
- **Continuing coordination:** This Active Plan, Validation Plan and Journal;
  [#2014](https://github.com/ThomasMichon/copilot-extensions/issues/2014) is
  historical tracking only after complete reviewed intake and readback.
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
- **Continuing intake:** The same host retains remaining admission, delivery,
  accepted-outcome and independent-promotion work through this Plan and Journal.
  Claim bounded source slices on eligible open public issues under normal
  review; preserve existing workers, stewards and resource/implementation custody.
  A historical umbrella closure is neither a live claim nor an eligibility grant.
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
- the session-scoped dynamic-guidance pattern owns attributable first-turn
  delivery, progressive disclosure, trust gating, and fail-open behavior;
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
- [x] Land this effort through the repository's review gate before changing
  plugin code.

### Phase 1 - Registry and on-demand strategy (`delegation-guidance`)

- [x] Define and register a versioned inert purpose-to-model configuration
  schema with demonstrated, candidate, held, and failed eligibility.
- [x] Define plugin, repository, and operator configuration layers without
  allowing a consumer to seize ownership of portable strategy or safety policy.
- [x] Ship no real model defaults. Examples remain obvious placeholders; current
  model IDs arrive only through configuration and reviewed evidence.
- [x] Extend the delegation skill with task classification, selection,
  availability fallback, explicit-trial, escalation, and result-integration
  guidance.
- [x] Add one compact first-turn decision cue that tells a coordinator to load
  the detailed routing grounding before delegating.
- [x] Keep malformed, unavailable, or inapplicable routing configuration
  startup-nonblocking; unproven candidates still fail closed at eligibility.

### Phase 2 - Deterministic selection helper

- [x] Add a pure resolver from purpose, required capabilities, execution
  surface, model availability, and layered registry to an ordered routing
  decision.
- [x] Prefer the least-expensive demonstrated eligible choice; fall through to
  another demonstrated choice when the preferred one is unavailable.
- [x] Require an explicit armed-trial mark before returning a candidate.
- [x] Return a structured reason for ordinary choice, substitution, trial,
  escalation, hold, or no-eligible-model.
- [x] Keep the direct-versus-delegate decision ahead of model selection so cheap
  workers do not cause unnecessary task fragmentation.

### Phase 3 - Outcome provenance and trial admission

- [x] Decide and document the minimal provenance owner before implementation:
  agent-dispatch task/session state, an assignment record derived from
  agent-worktrees prior art, or a deliberately shared contract with one writer.
- [x] Record purpose, selected model, eligibility state, execution surface,
  assignment reason, parent/worker identity, and terminal disposition without
  storing raw prompts or source bodies.
- [x] Emit attribution that external accounting systems can join to unique
  provider billing events; do not build a billing or promotion-analytics ledger
  inside this plugin suite.
- [ ] Add an enforceable admission check at the dispatch/restricted-container
  launch seam for demonstrated choices and explicitly armed candidate trials.
- [ ] Bind writable trial authority to a declared worktree, containment profile,
  path/tool/network boundary, and prior reviewed decision.

### Phase 4 - Integration, delivery, and adoption

- [ ] Preserve the current exact-session guidance contract and kernel budget;
  defer audience-gating changes unless behavioral evidence proves the
  checked-in pointer insufficient.
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
- [ ] Record measured findings, resolve every implementation and Validation
  item or transfer it to an accepted named objective, and only then archive
  this effort. Administrative supersession of #2014 does not satisfy that gate.

### Full remaining evidence-calibrated routing acceptance from #2014

<!-- backlog-reconciliation:7556:transfer:2014:to:efforts/active/evidence-calibrated-model-routing/README.md -->

Before administrative archival of #2014, land/read back this whole unchecked section in
the EXISTING reviewed Active effort and update its live umbrella/claim/close-step
references to the existing host/Journal and eligible bounded-source process. Preserve
#2014 as immutable request/evidence history, not a closed live coordination dependency.
Keep the current effort host, bounded trial/evaluation/reviewer/steward owners and
source/resource claims unchanged. A proposal, resolver output or old creation merge is
not this section's typed delivery receipt.

This is research/acceptance DATA only. It does not select or change any executing
session's model, reasoning/context settings, account, credentials, spend/concurrency,
agent set or tools. All actual trials and settings changes require their existing
separate authority. Do not hardcode real model/provider preferences into portable
guidance.

- [ ] Preserve task/purpose registry role/class,
  demonstrated/candidate/held/failed-or-rejected states, execution surface,
  tool/context/reasoning constraints, fallback/escalation, dated evidence and recheck
  policy as strict inert configuration with deterministic layers/provenance. Code
  reuse/schema/availability is not proof a role was demonstrated.
- [ ] Keep direct-versus-delegate FIRST: bounded cohesive work stays direct; broad
  separable work is routed only where justified. Prefer the least-expensive AVAILABLE
  DEMONSTRATED eligible choice for the classified role/surface; candidates require
  explicit identified armed trials, and substitutions/escalations must be recorded
  rather than silent.
- [ ] Enforce demonstrated/candidate admission at actual dispatch/restricted-container
  launch, not only in the pure resolver or assignment record. Bind writable authority to
  the reviewed manifest/worktree/containment/path/tool/network/credential envelope;
  reject unauthorized requests before effects.
- [ ] Preserve the original staged real-work authority ladder: read-only planning,
  documentation, contained source changes and separately reviewed publication. Workers
  do not acquire merge/deploy/admin or ambient credential authority; use isolated
  worktrees and credential-free contained venues when their shell/tool surface warrants
  them. Product correctness/security/review/publication gates remain unchanged and
  outrank cost.
- [ ] Prove current compact routing policy reaches every applicable Task-capable
  decision-maker BEFORE its first delegation decision, while non-delegating workers
  receive only their bounded assignment rather than the full catalog. Keep current
  exact-session/per-owner delivery, kernel bounds and fail-open malformed-configuration
  startup behavior; change audience gating only through evidence, not a second
  aggregator or assumption that a file was consumed.
- [ ] Attribute each classified task, chosen role/model, evidence state, surface,
  fallback/escalation reason and integration owner to immutable assignment/outcome
  records without raw prompts/source/private context. Current dispatch provenance is
  credited but does not grant admission or independent promotion.
- [ ] Evaluate complete ACCEPTED outcomes, including retries, discarded output,
  coordinator repair, review findings and later defects, not one cheap call. Join
  parent/child unique provider billing events externally exactly once to
  accepted/rejected/repaired/retried/abandoned/superseded outcomes. Context-transfer
  volume is an efficiency measure, NOT another monetary charge atop billed input; do not
  create a duplicate in-product billing ledger.
- [ ] Run real bounded reviewed trials only under their approved manifests and normal
  product gates. Preserve uncertainty and role/surface boundaries; one successful task
  cannot validate every role or provider pairing.
- [ ] Keep product-artifact review independent from eligibility promotion. A separately
  reviewed steward registry decision based on durable evidence promotes a model OR
  coordinator/worker pair; evaluated agents cannot promote themselves or treat candidate
  usage as a validated default.
- [ ] Retain actual negative tests for authority/path/tool/network/worktree/container
  admission, unavailable/expired/held/failed entries, explicit candidate arming,
  fallback, exactly-once billing joins and audience/delivery. Preserve
  source/published/installed and real trial evidence distinctions plus original native
  gates; no universal unrelated all-parent campaign.

Registry, deterministic choice and provenance already landed are preserved. All missing
enforcement, delivery, adoption/economic and promotion outcomes remain planned. Ordinary
budget-policy adapters are related but do not substitute for capability/trial authority.
No model trial, API/usage probe, container, agent, settings change, credential action,
runtime update or promotion occurs through this consolidation.

## Validation Plan

- [ ] Complete every directly retained #2014 Plan/Validation clause above at its original evidence tier and bounds; preserve current owners, holds and accepted deferrals. Administrative supersession is not completion.

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

### 2026-10-22 - Complete retained acceptance from #2014

- Retained the full #2014 remainder directly in the Plan and Validation,
  preserving delivered partials, existing source/resource custodians and gates.
  Reviewed actual merge and current full readback precede administrative
  supersession; no capability, native trial or adoption is declared complete.

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

### 2026-09-04 - Registry and grounding code complete

- Added a strict versioned JSON schema, synthetic examples, and a stdlib-only
  resolver for trusted repository plus operator configuration. The operator
  layer wins by purpose/model identity; an invalid operator file suppresses
  lower-precedence choices rather than accidentally removing a hold.
- Added the compact first-turn routing cue and detailed on-demand guidance for
  purpose, execution surface, demonstrated/candidate state, availability
  fallback, explicit trials, evidence expiry, and integration ownership.
- Independent diff review found and corrected duplicate-key acceptance,
  schema/loader mismatches, trust spoofing through a test override, expired
  evidence eligibility, and the invalid-operator precedence defect.
- Focused delegation-guidance coverage passes with 21 tests and 2
  platform-specific skips. Bash syntax and aggregate output were checked
  separately under WSL; the compact context remains inside its declared budget.

### 2026-09-04 - Deterministic selection code complete

- Extended the inert resolver with explicit purpose, surface, availability,
  context, reasoning, constraint, and evidence-expiry inputs.
- Ordinary selection returns only the lowest-cost demonstrated eligible model.
  Unavailable choices are retained with reasons and fall through to demonstrated
  alternatives.
- Candidate selection requires an exact requested model plus a non-empty trial
  identity. Held, failed, expired, mismatched, or unconstrained entries remain
  in the considered set with machine-readable exclusion reasons.
- The direct/delegate decision remains outside and ahead of model selection;
  this helper cannot create a delegation merely because a cheap model exists.

### 2026-09-04 - Dispatch provenance code complete

- Chose agent-dispatch as the one writer for routing assignment and terminal
  outcome provenance. Agent-worktrees and agent-containers remain referenced
  authorities for worktree/session and containment facts.
- Added a versioned assignment record keyed to the existing
  `dispatch-task:<task_id>:<attempt>` reservation identity plus append-only
  lifecycle and provider-event-reference rows.
- Assignment creation is immutable and idempotent; terminal updates use
  compare-and-set. Repair/retry work receives a separate linked assignment.
- Added bounded read/export HTTP and client surfaces. Records contain literal
  configured model IDs but no prompts, source bodies, credentials, paths, or
  monetary values.
- Candidate admission remains deliberately unwired until the next reviewed
  slice. Existing tasks and reservations require no routing record.
