# Progressive Context Disclosure

- **Slug:** `progressive-context-disclosure`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed vision and plan PR, then serial experiment,
  contract, guidance, reviewer, and migration PRs
- **Created:** 2026-09-01
- **Status:** Active
- **Vision:** advances
  [`visions/harness-guidance`](../../../visions/harness-guidance/README.md)
  §Features/`progressive-context-disclosure`,
  `navigable-on-demand-grounding`, and
  `coherent-attributable-assembly`; §Behaviors/`critical-before-comprehensive`,
  `references-carry-applicability`,
  `composition-preserves-owner-boundaries`, and
  `deferral-is-evidence-calibrated`
- **Umbrella issue:** #1612
- **Sub-issues:** #1615 · #1616 · #1617 · #1618 · #1619

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
- [x] Submit this vision and effort plan for repository review and merge before
  implementation begins.
- [x] After merge, sync the execution worktree forward, set Status to Active,
  and file focused sub-issues for experiments, contributor contracts,
  authoring guidance, reviewer enforcement, and migration.

### Phase 1 - Baseline and representative corpus

- [x] Inventory every current suite-owned context contributor by owner,
  purpose, emitted bytes/tokens, volatility, criticality hypothesis, existing
  guide references, and task applicability.
- [x] Select a synthetic but representative corpus covering safety policy,
  contribution boundaries, continuity, routing, readiness, environment
  grounding, command discovery, and capability-specific procedures.
- [x] Define observable tasks that need no guide, exactly one guide, several
  guides, conflicting guidance, and an unavailable or unsafe guide.
- [x] Record the current full-inline and current concise-kernel baselines before
  testing new deferral variants.
- [x] Freeze a counts-only evidence schema and literal-mode rubric before
  running behavioral comparisons.

### Phase 2 - Deferral and reference experiments

- [x] Implement the experiment matrix in
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
- Merged the reviewed vision and plan in #1614, synced the planning worktree
  forward, and activated the effort.
- Filed #1615 for the clean-room experiment matrix, #1616 for structured
  assembly and contributor contracts, #1617 for authoring guidance, #1618 for
  customization-review enforcement, and #1619 for authority implementation and
  complete-stack migration.
- Next gate: begin Phase 1 under #1615 with the contributor inventory, frozen
  synthetic corpus, baseline, and evidence schema.

### 2026-09-02 - Phase 1 fixture frozen

- Inventoried all 21 declared suite context contributors across 15 plugins from
  their complete `session-context.json` contracts. Recorded counts-only
  PowerShell observations from one fixed source-worktree payload, including the
  two contributors that were legitimately inapplicable in that repository.
- Froze an eight-owner synthetic corpus and eleven observable tasks under
  `tools/clean-room/scenarios/progressive-context-disclosure-baseline/`.
  Synthetic guides carry deterministic high-entropy canary shapes; no private
  ambient context or raw run output is tracked.
- Froze the F0-F4 deferral, seven reference-representation, four emphasis, and
  four assembly axes with three-session primary and second-model replication.
- Added a counts-only evidence schema and literal-mode rubric. The Tier-P
  verifier rejects contributor drift, ordinary path escape, task/corpus drift,
  content-bearing evidence fields, and repeated baseline-render drift.
- Next gate: land this frozen Phase 1 fixture before implementing or running the
  Phase 2 behavioral matrix.

### 2026-09-02 - Phase 2 experiment runner implemented

- Added deterministic F0-F4 rendering for all seven reference
  representations, all four emphasis levels, and the two Phase 2 assembly
  alternatives: flat owner fragments and flat fragments with a generated
  index. The frozen F0 and F2 baseline wording and hashes remain authoritative
  in both stable-canary and per-run-canary modes.
- Froze the 3,080-cell Phase 2 render digest. Tier-P validation now catches
  renderer drift, duplicate negative-control stimuli, and divergence between
  deterministic and canary-mode rendering before an agent run.
- Added isolated run materialization with fresh 192-bit guide canaries,
  readable repository-relative and payload-relative guides, owner and global
  indexes, spill artifacts, a synthetic session-start context plugin, and
  byte-for-byte re-render verification outside the read-only fixture mount.
- Added the runnable `progressive-context-disclosure-eval` ACP scenario plus
  one-cell scenario generation, counts-only observation, eager-load separation,
  evidence writing/validation, and zero-turn `INVALID` records for setup,
  timeout, driver, model-selection, or transport jams. The runner records the
  native ACP exit code and applied model before evidence can pass.
- Fresh and spill cells are runnable. Resume and compaction cells fail closed at
  configuration time until the clean-room runner performs those actual session
  transitions; a fresh session cannot be relabeled as boundary evidence.
- No behavioral variant has been selected. This host still has no Docker engine
  and no WSL distribution, so it cannot produce Tier-E evidence. Next gate:
  run Tier-P containment in a disposable venue, then run three independent
  fresh sessions for each surviving primary cell.

### 2026-09-02 - Cross-platform Tier-P green; Tier-E blocked at agent-bridge

- Added and merged the Windows Server Core Tier-P arm in #1664. The complete
  frozen inventory and 3,080-cell render matrix passed in a cloud-hosted Windows
  container under Windows PowerShell 5.1 and Python 3.12.
- Exercising the Windows-hosted Linux Docker arm exposed three runner defects:
  absent-container cleanup terminated a fresh run, embedded in-container Python
  source and JSON were corrupted by Windows argv quoting, and helper-scoped
  native exit state was misread. The fixes merged through #1717 and #1719; the
  combined driver bug is recorded and closed as #1725.
- The Linux container then passed the same Tier-P fixture and reached the real
  Tier-E registration/drive boundary. No behavioral verdict was produced:
  agent-bridge new-session creation returned transport `INVALID` after the host
  runtime update left a live dynamic-port daemon drained behind a stale
  published route.
- The agent-bridge lifecycle defect is tracked in #1724. Stop repairing that
  shared host inline: the owning fix must make update/start converge to one
  healthy, published, accepting endpoint. Rerun the fresh ACP cell only after
  that tracked fix lands; the current `INVALID` runs do not eliminate any
  progressive-context variant.

### 2026-09-02 - Tier-E blockers fixed at source

- #1740 fixed #1724 in agent-bridge `0.4.0-dev417`: service recovery now
  discovers the exact port-0 singleton holder, verifies and undrains it under a
  routing-table compare-and-swap, atomically republishes its dynamic endpoint,
  preserves known runtime-version provenance, reconciles service markers, and
  refuses to report stop success while the dynamic holder remains live.
- The first manually routed ACP turn was not credited as behavioral evidence.
  It depended on working around the broken host route and also exposed a
  provenance defect: the runner selected model/status from a broad same-agent
  session listing.
- #1750 fixed the remaining #1725 gap in agent-bridge `0.4.0-dev418` and the
  clean-room runner. `agent-bridge create --session-id-file` now publishes the
  exact create-owned session before prompt streaming; the runners keep that ID
  and the session snapshot in host-only temporary state and bind model plus
  structured results only to that exact ID. Missing or ambiguous provenance is
  `INVALID`.
- Do not reconnect to or repair the shared cloud host inline. The next
  calibration attempt starts only after the merged versions are deployed
  through the normal machine update flow and the host reports one healthy,
  published, accepting bridge endpoint. No Tier-E behavioral result exists yet.

### 2026-09-03 - First behavioral cell rejected

- Bootstrapped a disposable Linux CodeSpace through the documented setup path,
  then ran the unified machine update. The durable acceptance check found
  agent-bridge `0.4.0-dev419` healthy, published, and accepting on Linux Docker.
- The first configured eval exposed a host-UID/umask portability defect before
  ACP. #1767 and #1768 fixed it at source: generated cells now bundle the frozen
  fixture plus the minimal current contributor inventory and normalize their
  own read-only permissions instead of depending on source-checkout traversal.
- Ran three independent fresh ACP repetitions for F2 / backtick
  repository-relative / conditional / flat fragments / one-guide. Every run
  loaded the required `runtime-diagnostics` guide, retained owner provenance,
  preserved the critical rule, and avoided path invention.
- Literal-mode judges returned one PASS and two FALSE-PASS failures.
  Repetitions 2 and 3 broadly enumerated guides and loaded the unrelated
  `command-reference` guide. The counts-only evidence is recorded under
  [`evidence/`](evidence/).
- Reject this cell rather than selecting from its one clean run. The next
  controlled comparison keeps F2, repository-relative backticks, flat
  fragments, the one-guide task, model, venue, and boundary fixed while raising
  emphasis from conditional to imperative.

### 2026-09-03 - Imperative emphasis also rejected

- Ran three independently generated fresh ACP repetitions with only the
  emphasis coordinate changed from conditional to imperative.
- All three literal-mode judges returned FALSE-PASS failures. Every run loaded
  `runtime-diagnostics`, retained provenance, and avoided path invention, but
  each continued into broad compensating exploration and loaded two or three
  irrelevant guides.
- Imperative wording is worse than conditional for this one-guide cell: it
  produced zero clean runs and a higher irrelevant-read count. The validated
  counts-only records are linked from [`experiments.md`](experiments.md).
- Next gate: test safety-gated emphasis with every other coordinate fixed. If
  it also fails, do not strengthen wording further; compare the optional edge
  and then revisit deferral/reference shape without rewriting the frozen task
  around a preferred outcome.

### 2026-09-03 - Safety-gated emphasis also rejected

- Ran three independently generated fresh ACP repetitions with only the
  emphasis coordinate changed from imperative to safety-gated.
- Repetition 1 passed literally with one tool call and only the required
  `runtime-diagnostics` guide. Repetitions 2 and 3 were false passes after broad
  discovery and unrelated guide reads; repetition 2 also lost complete owner
  provenance.
- Preserved each run's generated scenario, result packet, and exact materialized
  canary root before replacing the disposable container. The bundled writer
  produced three schema-valid counts-only records.
- The counts-only observation is canary-backed. For repetition 2 it records one
  irrelevant guide while the independent transcript judge counted three direct
  irrelevant document reads, including an encountered guide whose canary was
  omitted from the final witness. The frozen evidence contract remains
  unchanged during calibration.
- Reject safety-gated emphasis for this cell. Next gate: test the optional edge
  with every other coordinate fixed, then revisit deferral or reference shape
  if optional is also unstable.

### 2026-09-03 - Optional emphasis also rejected

- Ran three independently generated fresh ACP repetitions with only the
  emphasis coordinate changed from safety-gated to optional.
- All three were literal-mode false passes. Each loaded the required
  `runtime-diagnostics` guide and preserved READY-1, but each also performed
  broad compensating discovery and loaded unrelated guidance; repetition 1
  also lost complete owner provenance.
- The frozen counts-only records report irrelevant guide counts of 1, 1, and 2.
  Independent transcript judges counted 1, 2, and 2 direct irrelevant guide
  reads. Repetition 2 malformed the encountered `command-reference` canary in
  its final witness, explaining the canary-backed undercount.
- All four F2 emphasis levels are rejected for this one-guide cell. Next gate:
  change only deferral from F2 to F3, keeping repository-relative backticks,
  safety-gated emphasis, flat fragments, task, model, ACP venue, and fresh
  boundary fixed. F3 exposes only the task-required guide reference and tests
  whether the surplus F2 references caused the broad exploration.

### 2026-09-03 - F3 safety-gated one-guide cell passes unanimously

- Ran three independently generated fresh ACP repetitions with only the
  deferral coordinate changed from F2 to F3.
- All three literal-mode judges passed. Every run loaded only the required
  `runtime-diagnostics` guide, preserved READY-1 and owner provenance, avoided
  path invention and prohibited sources, and performed no capability operation
  or compensating exploration.
- Tool-call counts were 1, 1, and 2. The third run used one exact locator glob
  before reading the same required guide; no unrelated path or guide was read.
- F3 reduced initial context from 4,379 characters / 1,095 estimated tokens to
  2,862 characters / 716 estimated tokens, a 34.6% reduction, while improving
  unanimous correctness and eliminating irrelevant reads for this cell.
- Retain F3 repository-relative safety-gated flat fragments as a surviving
  one-guide cell, not a selected standard. Next gate: run the no-guide task with
  every other coordinate fixed and require zero deferred-guide reads.

### 2026-09-03 - F3 safety-gated no-guide cell passes unanimously

- Ran three independently generated fresh ACP repetitions with the surviving F3
  coordinates and changed only the task boundary from one-guide to no-guide.
- All three literal-mode judges passed. Each session completed from the critical
  kernel in one turn with zero tool calls and zero deferred-guide reads.
- Every run retained all owner provenance, preserved READY-1 and CMD-1, avoided
  prohibited sources and path invention, and returned no guide canary.
- Initial context was 2,646 characters / 662 estimated tokens. Retain the
  no-guide boundary as additional support for F3, not as a final selection.
- Next gate: run the multi-guide task with every other coordinate fixed and
  require all three task-applicable guides with both owners preserved.

### 2026-09-03 - F3 repository-relative multi-guide cell rejected

- The frozen task requires three guides across two owners:
  `publication-checks`, `destination-matrix`, and `capability-procedure`.
- The first repetition attempt timed out at 300 seconds and was recorded as
  transport `INVALID`; a fresh independently generated retry supplied the
  behavioral repetition with new canaries.
- All three behavioral repetitions loaded every required guide but were
  literal-mode false passes after bounded-flow violations. Repetitions 1 and 3
  used broad compensating exploration; repetition 2 failed the experimental
  mode gate. Each violated CAP-1's bounded-task rule; repetitions 2 and 3 also
  loaded unrelated guides, and repetition 3 violated CMD-1 by loading
  `command-reference` without a required non-kernel command option.
- Reject the F3 repository-relative safety-gated flat variant despite its
  unanimous no-guide and one-guide cells. Per the frozen protocol, one critical
  violation eliminates the variant.
- Next gate: keep F3, safety-gated emphasis, flat fragments, multi-guide task,
  model, ACP venue, and fresh boundary fixed while changing only the reference
  representation to `structured-reference`.

### 2026-09-03 - Structured references do not recover multi-guide

- Ran three independently generated F3 safety-gated multi-guide repetitions
  with only the reference representation changed from repository-relative
  backticks to `structured-reference`.
- All three failed literal mode. Every run read all required guides but also
  loaded two or three unrelated guides, lost complete owner provenance, and
  returned the wrong blocked or do-not-proceed decision instead of the bounded
  procedure.
- Structured references increased tool use to 14-18 calls and did not prevent
  broad repository discovery. Reject this representation for the cell.
- Next gate: return to repository-relative references and change only assembly
  from flat fragments to `flat-with-index`, testing whether one explicit
  generated index can provide ordering without structured-reference verbosity.

### 2026-09-03 - Generated index does not recover multi-guide

- Ran three valid F3 repository-relative safety-gated multi-guide repetitions
  with only assembly changed from flat fragments to `flat-with-index`. A first
  repetition-3 attempt timed out and remains transport `INVALID`; an
  independently generated retry supplied the behavioral repetition.
- All three literal-mode judges failed the cell. Every run loaded the required
  guides, but independent judges counted three direct irrelevant guide reads,
  lost exact owner provenance, used 20 tool calls, and returned an incorrect
  blocked decision. Repetitions 1 and 2 also invented configuration paths and
  violated ROUTE-1 and CAP-1.
- The frozen canary-backed evidence records two irrelevant guides per run
  because one directly read guide canary was absent or malformed in each final
  witness. Preserve the independent transcript count rather than changing the
  evidence contract.
- Fast-forwarding the evaluation checkout exposed expected suite-inventory
  drift from a later contributor-order change, so that setup attempt remained
  `INVALID` before agent launch. The valid scenarios were generated from the
  frozen pre-change source and driven with the byte-identical current
  clean-room runner.
- Reject `flat-with-index`: its 4,150-character / 1,038-token context added
  overhead without restoring bounded behavior. Next gate: return to flat
  fragments and change only the reference representation to
  `backtick-absolute-contained`.

### 2026-09-03 - Absolute contained paths do not recover multi-guide

- Ran three F3 safety-gated flat multi-guide repetitions with only the
  reference representation changed to `backtick-absolute-contained`.
- All three were literal-mode false passes. Every run loaded the required
  guides and remained path-contained, but independent judges counted four,
  three, and five direct irrelevant document reads after broad compensating
  exploration. The canary-backed records count two, three, and one irrelevant
  guides.
- Repetitions 1 and 2 violated CAP-1 and returned an inapplicable blocked
  decision. Repetition 3 preserved the critical rules but lost complete
  decision-owner provenance after two broad searches and five unnecessary
  reads.
- Reject the representation: absolute locators removed path-resolution
  ambiguity but did not restore bounded selection. Next gate: change only the
  representation to `markdown-link` and explicitly inspect eager-loading
  evidence.

### 2026-09-03 - Markdown links do not recover multi-guide

- Ran three F3 safety-gated flat multi-guide repetitions with only the
  reference representation changed to `markdown-link`.
- No guide body was auto-loaded in ACP. Every observed canary followed an
  explicit agent read, so real links passed the eager-load boundary in this
  venue.
- All three runs still failed literal mode. Repetitions 1 and 3 enumerated the
  repository and loaded two irrelevant canary-bearing guides; repetition 2
  loaded only the required guides but performed four undeclared
  configuration-location reads. Tool-call counts were 12, 7, and 7.
- Reject the representation for this cell: discoverability was sufficient, but
  the links did not enforce task-applicable bounded flow. Next gate: change
  only the representation to `backtick-payload-relative`.

### 2026-09-03 - Payload-relative locators expose delivery machinery

- Ran three F3 safety-gated flat multi-guide repetitions with only the
  reference representation changed to `backtick-payload-relative`.
- All three were literal-mode false passes and explicitly reread the prohibited
  generated payload `context.md`. No guide body auto-loaded; every observed
  canary followed an agent-initiated read.
- The runs loaded two or three irrelevant guides, used 17, 15, and 20 tool
  calls, and violated CAP-1. Repetition 2 manually invoked the session-start
  hook with an unset payload root and manufactured a
  `/scripts/emit-context.py` blocker; repetition 3 began with an invented guide
  glob.
- Reject the representation: exposing the payload resolution base encouraged
  inspection of delivery machinery instead of bounded guide selection. Next
  gate: change only the representation to `bare-labeled-path`.

### 2026-09-03 - Bare labeled paths do not bound discovery

- Ran three F3 safety-gated flat multi-guide repetitions with only the
  reference representation changed to `bare-labeled-path`.
- All three failed literal mode after broad guide, repository, or configuration
  discovery. The canary-backed records count three, four, and two irrelevant
  guides; tool-call counts were 9, 17, and 15.
- Repetition 1 preserved the critical rules but returned an unsupported
  settings-based blocker. Repetition 2 failed PUB-1, ROUTE-1, and CAP-1;
  repetition 3 failed CAP-1 and continued through five broad exploration
  actions after the first unresolved gate.
- Reject the representation. Next gate: change only the representation to
  `html-comment-locator`, completing the frozen reference-form sweep for this
  multi-guide coordinate.

### 2026-09-03 - Comment locators complete the rejected F3 reference sweep

- Ran three F3 safety-gated flat multi-guide repetitions with only the
  reference representation changed to `html-comment-locator`.
- Comment locators were discoverable and no guide body auto-loaded, but every
  run broadened into guide, repository, or configuration discovery and loaded
  one to three irrelevant guides. Tool-call counts were 17, 9, and 16.
- Repetitions 1 and 3 preserved the critical rules but continued beyond the
  first unresolved gate; repetition 2 violated CAP-1 and substituted an
  unrelated readiness rule.
- Reject the representation. All seven frozen reference forms are now rejected
  for the F3 multi-guide coordinate. Next gate: return to repository-relative
  references and change only deferral from F3 to F1.

### 2026-09-03 - F0 exposes an unsatisfiable multi-guide stimulus

- Ran the F0 full-inline control after the F3 reference sweep. Even with every
  guide body already inline, the task could not reach its expected execution:
  the materialized world had no affirmative READY signal, scoped destination
  identity, review-gate state, or attributable bounded command.
- Reclassified every freeze-epoch-1 multi-guide elimination as confounded. Raw
  read counts, Markdown no-eager-load behavior, payload generated-context
  rereads, and index overhead remain useful decision-independent observations;
  no representation, assembly, or deferral is eliminated by those runs.
- Closed #1868 without merge rather than publishing the confounded F1
  conclusion. Earlier merged multi-guide records remain as historical
  counts-only artifacts, with [`experiments.md`](experiments.md) now carrying
  the superseding classification.
- Refroze the task as epoch 2 without changing required guides, applicability
  cues, critical rules, or expected decision. Execution tasks now materialize
  owner-declared READY, scoped destination/review-gate grounding, and an exact
  synthetic bounded command; Tier-P validation rejects an unsatisfiable
  execution task before behavioral runs.
- Next gate: land the reviewed fixture correction, then require the corrected
  F0 multi-guide control to pass before rerunning any deferred variant.
