# Progressive Context Disclosure

- **Slug:** `progressive-context-disclosure`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed vision and plan PR, then serial experiment,
  contract, guidance, reviewer, and migration PRs
- **Created:** 2026-09-01
- **Status:** Draft
- **Vision:** advances
  [`visions/harness-guidance`](../../../visions/harness-guidance/README.md)
  §Features/`progressive-context-disclosure`,
  `navigable-on-demand-grounding`, and
  `coherent-attributable-assembly`; §Behaviors/`critical-before-comprehensive`,
  `references-carry-applicability`,
  `composition-preserves-owner-boundaries`, and
  `deferral-is-evidence-calibrated`
- **Umbrella issue:** #1612
- **Sub-issues:** pending Phase 1 decomposition

## Guiding Intent

Turn session-start context from a collection of concise-but-independent plugin
fragments into an evidence-calibrated progressive-disclosure system.

Hooks should inject only the critical policy, constraints, orientation, and
decision cues required before safe action. Detailed overarching behavior and
grounding remain plugin-owned and available through attributable guide
references that an agent follows when the task requires them. The aggregate may
gain semantic structure so independently delivered fragments read as one
coherent document, but composition must preserve ownership and must not invent,
paraphrase, or silently reconcile policy.

The campaign is experiment-first. It will not standardize one link syntax,
emphasis level, deferral threshold, or hierarchical schema until literal-mode
clean-room evidence shows which forms preserve correctness while reducing
upfront context and unnecessary exploration.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Architecture driver | Owns vision, contributor semantics, decisions, issue decomposition, and PR sequence | isolated local worktrees |
| Clean-room evaluation lane | Owns synthetic corpus, prompt variants, literal-mode runs, evidence, and independent judgments | clean-room scenarios and evaluator agents |
| Authoring and review lane | Owns `customizing-copilot` guidance, scanner rules, diagnostics, and migration checks | serial plugin worktrees after experiment decisions |

## Coordination

- **Topology:** one reviewed intent/plan PR, followed by serial contract and
  guidance changes; clean-room variants may run in parallel only after their
  fixture and rubric commit is frozen.
- **Host (owns PRs):** architecture driver.
- **Delegates:** clean-room evaluators may own disjoint variants and independent
  judging. Authoring guidance begins after the experiment decision record;
  blocking reviewer enforcement begins only after the contributor contract and
  representative authority implementation are reviewed.
- **Handoff:** each phase updates this effort, its extracted design document,
  machine-readable evidence summaries, and the relevant sub-issue before the
  next phase begins.

## Context

The current context-injection pattern already requires a concise owner-marked
kernel and recommends a backtick faux-link for detailed mechanics. It also
provides deterministic composition, contributor budgets, exact ownership,
spill-to-session-state behavior, and counts-only customization review.

Those foundations answer who owns context and how independent hook results
survive. They do not yet answer:

- which content is critical enough to remain inline;
- which detailed overarching guidance should be deferred without becoming a
  skill;
- which reference representations an agent reliably follows on demand without
  eager loading;
- how wording and emphasis affect correct guide selection;
- what static evidence lets review distinguish a good kernel from a procedure
  dump or an under-specified pointer; or
- whether structured contributor metadata can produce a coherent hierarchy
  across arbitrary active plugin sets without centralizing authorship.

The experimental design is in [`experiments.md`](experiments.md). The structured
assembly investigation is in
[`structured-assembly.md`](structured-assembly.md).
Resume-generation recovery remains tracked separately by #1508; this effort
should integrate with its small durable witness without making transformed
prompts the primary context channel.

## Request

> Now that we're injecting context via hooks, we probably should come up with
> some strong guidance in customizing-copilot's authorship guides and its
> reviewing-customization skill to enforce a flow where hooks only inject
> critical upfront context, but also provide guide links to anything an agent
> should explore on-demand. This isn't quite a "skill", it's still overarching
> behavior control and grounding context, but it allows the agent to explore as
> needed without loading up context. We'll likely need a plan which involves
> clean-room testing of different levels of deferral, and different forms of the
> link formatting (markdown comments vs `./path` quoting, etc.) and different
> degrees of emphasis. We should also consider whether we should support a
> structured injection, where the final assembly attempts to produce a
> hierarchical document structure with a degree of coherency, despite being
> assembled by whatever plugins are currently active.

## Plan

### Phase 0 - Reviewed intent and vocabulary

- [x] File the public coordination issue (#1612).
- [x] Extend the harness-guidance vision with progressive disclosure,
  navigable grounding, coherent attributable assembly, and evidence-calibrated
  deferral.
- [x] Define the initial experiment and structured-assembly questions in linked
  design documents.
- [ ] Submit this vision and effort plan for repository review and merge before
  implementation begins.
- [ ] After merge, sync the execution worktree forward, set Status to Active,
  and file focused sub-issues for experiments, contributor contracts,
  authoring guidance, reviewer enforcement, and migration.

### Phase 1 - Baseline and representative corpus

- [ ] Inventory every current suite-owned context contributor by owner,
  purpose, emitted bytes/tokens, volatility, criticality hypothesis, existing
  guide references, and task applicability.
- [ ] Select a synthetic but representative corpus covering safety policy,
  contribution boundaries, continuity, routing, readiness, environment
  grounding, command discovery, and capability-specific procedures.
- [ ] Define observable tasks that need no guide, exactly one guide, several
  guides, conflicting guidance, and an unavailable or unsafe guide.
- [ ] Record the current full-inline and current concise-kernel baselines before
  testing new deferral variants.
- [ ] Freeze a counts-only evidence schema and literal-mode rubric before
  running behavioral comparisons.

### Phase 2 - Deferral and reference experiments

- [ ] Implement the experiment matrix in
  [`experiments.md`](experiments.md) without changing production authoring
  rules.
- [ ] Compare full inline, critical kernel plus one index, critical kernel plus
  per-topic references, and minimal locator variants.
- [ ] Compare real Markdown links, backtick repository-relative paths,
  backtick absolute paths, bare paths, HTML-comment locators, and one structured
  reference representation.
- [ ] Compare optional, conditional, imperative, and safety-gated emphasis.
- [ ] Run fresh, resumed, compacted, interactive, and ACP literal-mode sessions
  where supported; separate transport/setup failures from behavioral verdicts.
- [ ] Select the smallest variants that preserve first-turn correctness,
  task-appropriate guide reads, provenance, and bounded grounding latency
  without increasing irrelevant exploration.
- [ ] Publish a decision record with rejected variants and evidence rather than
  turning the winning syntax into folklore.

### Phase 3 - Structured assembly prototype

- [ ] Prototype the alternatives in
  [`structured-assembly.md`](structured-assembly.md): flat fragments with
  stronger labels, semantic zones, and a versioned structured fragment model.
- [ ] Preserve contributor-owned text exactly while allowing deterministic
  grouping, headings, ordering, budgets, and a deferred-reference index.
- [ ] Define behavior for missing sections, duplicate ids, incompatible
  schemas, conflicting owner claims, heading collisions, and over-budget zones.
- [ ] Prove that arbitrary active-plugin subsets still render a coherent
  document without assuming any plugin is present beyond the authority.
- [ ] Hold an explicit continue decision: adopt structured assembly only if it
  improves navigation and grounding enough to justify schema and migration
  cost; otherwise retain flat owner fragments and standardize only kernels and
  references.

### Phase 4 - Authoring guidance and exemplars

- [ ] Update `customizing-copilot:authoring-skills`, its session-context
  reference, and `docs/patterns/context-injection.md` with the selected
  criticality, deferral, reference, and emphasis rules.
- [ ] Define the boundary between a deferred overarching grounding guide and a
  task-triggered skill; neither should be used as a disguise for the other.
- [ ] Provide paired exemplars: unsafe procedure dump, under-specified pointer,
  compliant critical kernel, conditional guide reference, and structured
  contribution if adopted.
- [ ] Require guide ownership, applicability cues, contained resolution,
  stable identifiers, and bounded critical bytes.
- [ ] Document how resume recovery, session-state spill, and progressive
  disclosure compose without duplicating the full aggregate.

### Phase 5 - Contributor contract and authority implementation

- [ ] Version the contributor and engine contracts only after the experiment
  and structured-assembly decisions are reviewed.
- [ ] Add backward-compatible parsing so existing authority-aware contributors
  preserve behavior during partial rollout.
- [ ] Implement deterministic rendering, reference containment, budgeting, and
  diagnostics in the authority; never execute or auto-load a guide merely
  because it is declared.
- [ ] Migrate a policy contributor, a command-catalog contributor, and an
  environment/routing contributor before broad suite conversion.
- [ ] Preserve byte-identical shared output, source qualification, trust gates,
  cross-platform wrappers, and standalone fallback throughout rollout.

### Phase 6 - Reviewing-customizations enforcement

- [ ] During Phase 5, prototype advisory-only scanner inventory for candidate
  critical kernel metadata, deferred-reference metadata, declared size bounds,
  ownership, applicability, containment, and structured roles without executing
  hooks or printing content.
- [ ] After the contract and representative implementation are reviewed, add
  diagnostics for oversized or unclassified kernels, detailed procedures left
  inline, references without applicability cues, missing or escaping targets,
  ambiguous ownership, unsupported link forms, and unstructured fragments when
  a repository adopts the structured engine.
- [ ] Calibrate warning versus BLOCKING severity from Phase 2 evidence and the
  Phase 5 pilots; do not block on experimental fields or use a prose-quality
  linter that guesses criticality from keywords alone.
- [ ] Keep external marketplace findings advisory with an attributable upstream
  fix path; keep owned-suite guards stricter and machine-derived.
- [ ] Extend context-budget output to distinguish critical inline bytes,
  deferred-reference bytes, potential deferred guide bytes, and unknown dynamic
  output without reading guide contents into the report.

### Phase 7 - Complete-stack validation and migration

- [ ] Run the selected clean-room matrix against the representative pilots and
  the complete marketplace-owned stack.
- [ ] Prove that tasks needing a guide read the correct guide, tasks not needing
  one do not, critical rules remain active before exploration, and malformed or
  unsafe references fail closed.
- [ ] Prove deterministic hierarchical or flat rendering across Windows and
  POSIX, contributor order permutations, plugin subsets, resume, compaction,
  spill, and version skew.
- [ ] Convert the remaining suite-owned contributors, update architecture and
  reality documentation, and run customization, generated-file, marketplace,
  and clean-room guards.
- [ ] Close or transfer every sub-issue, mark this effort Done, and archive it.

### Phase 8 - Reconcile deferred backlog

- [ ] Accept progressive-disclosure candidates only through
  [`migration-intake`](../migration-intake/README.md)'s deduplication and
  ownership gate.
- [ ] Revalidate accepted candidates against the selected kernel, reference,
  structured-assembly, and review contracts.
- [ ] Place each accepted item in exactly one existing phase, extending the
  plan before implementation when necessary.
- [ ] Keep fixtures synthetic and evidence public-safe.

## Validation Plan

- [ ] The baseline records inline bytes/tokens, first-turn task correctness,
  grounding latency, guide-read count, irrelevant-read count, path-resolution
  failures, and attribution retention.
- [ ] Every experiment variant uses the same synthetic content and frozen task
  set; only deferral, representation, emphasis, or structure changes.
- [ ] Primary behavioral cells run in at least three independent fresh sessions.
  A variant with any critical-rule or required-grounding failure is eliminated;
  finalists repeat across a second model and the supported resume/ACP boundary
  before selection.
- [ ] A no-guide task completes without reading deferred material and without
  losing any critical constraint.
- [ ] A one-guide task selects the correct owned guide without reading unrelated
  guides.
- [ ] A multi-guide task reads the necessary set in a task-appropriate order and
  preserves owner boundaries when guidance interacts.
- [ ] Missing, malformed, untrusted, escaping, or inapplicable references fail
  closed with bounded diagnostics and no invented fallback path.
- [ ] Real Markdown links are tested for unintended automatic loading; faux
  links and comments are tested for discoverability rather than assumed safe or
  effective.
- [ ] Relative-path behavior is explicit about its resolution base; absolute
  pointers remain session/repository contained and portable where required.
- [ ] The selected emphasis level drives required reads without causing optional
  references to be loaded routinely.
- [ ] Structured assembly, if adopted, is byte-deterministic across platform,
  execution order, and plugin subset; it preserves exact contributor text and
  stable provenance.
- [ ] Context reduction is accepted only when correctness is no worse than the
  current kernel baseline and unnecessary exploration materially decreases
  relative to full-inline or over-linked variants.
- [ ] `reviewing-customizations` findings match the calibrated contract without
  executing hooks, dumping context, or flagging every subjective prose choice.
- [ ] Fresh, resume, compaction, ACP, and spill scenarios preserve critical
  guidance and the selected on-demand discovery behavior.

## Proposal

Approve progressive context disclosure as the next harness-guidance program.
Begin with a frozen synthetic corpus and literal-mode evaluation, not normative
wording or a schema change. Standardize link form, emphasis, criticality
thresholds, and structured assembly only after the experiment matrix produces a
reviewed decision record. Then update authoring guidance and reviewer
enforcement before migrating the complete contributor stack.

## Journal

### 2026-09-01 - Kickoff

- Filed #1612 as the public coordination token.
- Classified the change as vision-extending rather than a below-altitude
  authoring tweak: it changes the expected relationship between always-loaded
  context, deferred overarching guidance, and the aggregate document.
- Reused the existing context-injection authority, contributor ownership,
  session-state spill, budget, and counts-only review foundations rather than
  proposing a second context system.
- Kept link representation and hierarchical assembly intentionally undecided
  pending clean-room evidence.
- Next gate: merge this reviewed vision and plan, then activate Phase 1 and file
  the focused sub-issues.
