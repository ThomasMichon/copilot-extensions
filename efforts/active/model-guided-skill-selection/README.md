---
visions:
  - visions/harness-guidance
---

# Model-Guided Skill Selection

- **Slug:** `model-guided-skill-selection`
- **Repo:** copilot-extensions
- **Branch(es):** One coordinator-owned, `dev`-based worktree; incremental reviewed PRs
- **Created:** 2026-10-31
- **Status:** Draft
- **Vision:** [Harness Guidance](../../../visions/harness-guidance/README.md): authoritative ownership, concise context, reusable current discovery and evidence-backed routing; vision-closing
- **Umbrella issue:** [#9809](https://github.com/ThomasMichon/copilot-extensions/issues/9809)

## Guiding Intent

Help an agent choose appropriate registered skills and supporting guidance by
intent, approach or review purpose without relying on incomplete inline
descriptions or preparing a large hand-authored briefing.

Expose a model-guided selection tool backed by a specialized agent. Keep the
native session catalog authoritative, recommendations bounded and execution
authority with the caller. Establish thoroughness and complete-system cost
before preferring a context-delivery strategy.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Source design, integration, PR stewardship and completion | Managed source worktree with public codename `creaky-gizmo`; issue #9809 and its PRs |
| Native SDK runner | Owned, isolated transport and context witnesses | Planned owned SDK test harness and explicit native host selection |
| Independent evaluator | Preregistered guidance-quality judgment | Explicit evaluator binding before real comparative arms |

## Coordination

- **Topology:** One exclusive source worktree; no concurrent git writers.
  Review the proposal before prototypes, then publish incremental implementation
  and evidence through ordinary `dev` PRs.
- **Host (owns PRs):** Coordinator drives each PR through its real review,
  required checks and merge; a merged phase is not effort completion.
- **Delegates:** Bounded evidence, implementation or evaluation scopes only.
  The selector itself is a leaf and cannot execute or delegate the chosen work.
- **Handoff:** Preserve this effort, current source revision, live obligations,
  model/host choices and remaining gates through the existing continuity owner.
  Adopters may maintain private evidence with one-way links to this public
  canonical effort, not competing implementation plans.

## Context

The SDK exposes native skill descriptors independently of their inline
advertisement. Existing customization tooling also reads metadata, but its
broader capture inventory is not a purpose-query API or a replacement catalog.

A joined extension can start a model-selectable specialized task. Its supplied
prompt can be bounded, but the task contract does not promise the complete
assembled child context excludes parent history. Native history forking is
server-scoped; a joined session does not itself expose the required client.
An existing-context token estimate is not a prospective task-capacity guarantee.

These are supported-interface observations, not native behavioral qualification.
The spike must distinguish supplied text, inherited context, initialization,
compaction, actual model selection and total preparation cost.

## Request

Issue #9809 requests an exposed model-guided skill-selection extension backed
by a specialist, using the existing catalog and declared supporting guides.
Primary agents should prefer that flow to guessing when selecting unfamiliar
guidance for execution, planning or review.

Compare whole-context/event-boundary forks, automatically acquired bounded
context and explicit briefs. A fork can avoid caller summarization while
exceeding another model's capacity; a small prompt can still inherit a large
context. Tune completeness and efficiency rather than assuming either strategy
is better.

## Scope Approvals

Source definition and bounded spike work are admitted through issue #9809 and
normal repository review. This does not authorize shared rollout, global
settings changes, instruction-budget overrides, authentication changes or
publication of real conversation payloads.

Before real comparative arms, bind the exact candidate model, supported
execution surface, matched configuration and independent evaluator. An example
model family is not a production pin or demonstrated role qualification.
Preserve applicable operator limits without inventing budget headroom from
cached or unavailable quota evidence.

## Plan

### Phase 1 - Reviewed contract and reusable source shape

- [ ] Land this proposal and reconcile the final contract with its owning
  customization, native catalog, SDK and model-policy interfaces.
- [ ] Specify one exposed tool, one canonical specialist and concise preference
  guidance in the existing customization payload; no additional service,
  catalog authority or general discovery crawler.
- [ ] Define current-catalog binding, bounded context collection, declared-guide
  resolution, result validation, source freshness and explicit unavailable,
  ambiguity, no-match and insufficient-context outcomes.
- [ ] Preserve mandatory known-skill procedures and caller restrictions.
  Preference applies to selection, not to executing recommendations or replacing
  an independent review.

### Phase 2 - Native transport, context and lifecycle spike

- [ ] Observe the actual requesting host's catalog, available agent identifiers,
  model capabilities and accounting surfaces without model inference or global
  changes; distinguish declarations from working host support.
- [ ] Use an owned isolated SDK runtime and synthetic fixtures to witness task
  context inclusion/order, effective tools, result attribution and model
  fallback. Interception is process-global and must not attach to production.
- [ ] Exercise full/event-boundary forks only through a supported host-owned
  server client. Record absent ordinary-extension access explicitly; never use
  private connection fields or silently substitute another context strategy.
- [ ] Compare bounded automatic context and explicit-brief controls, including
  complete catalog/tool overhead, initialization and compaction. Do not infer
  prospective fit from existing-parent estimates or prompt byte size.
- [ ] Qualify cancellation, stale/late output, missed events and replacement
  boundaries. Settle only owned work; cancellation acknowledgement is not exit
  proof, and a session-wide task drain is not a child waiter.

### Phase 3 - Preregistered model-guided comparison

- [ ] Freeze the task/context boundary and catalog for representative execution,
  planning and review cases, plus ambiguity, no-match and misleading metadata.
  Keep public fixtures generic and real adopter data out of this repository.
- [ ] Bind a supported candidate and independent evaluator; compare the same
  cases/configuration across the three context strategies where representable.
  Classify unsupported configurations separately rather than manufacturing a pass.
- [ ] Measure applicable-guidance recall, precision, omissions, abstention,
  declared-guide provenance and actual resolved model/effort/tier.
- [ ] Include context acquisition, summarization where used, SDK startup,
  retries, inference and settlement in latency and usage. Label cost quantities
  and unavailable evidence; neither a model name nor synthetic usage proves savings.
- [ ] Select or revise the strategy from observed quality and complete-system
  cost. Record sample-size limits; a small demonstration does not promote a
  model or override the existing routing steward.

### Phase 4 - Tool, specialist, preference and source completion

- [ ] Implement the qualified path through public supported SDK interfaces.
  Avoid primary-model, agent-selection or global subagent-setting changes.
- [ ] Read native descriptors, not the truncated advertisement. Preserve native
  identity, enabled state and source metadata; do not invent eligibility from
  `userInvocable`, path presence or enumeration order.
- [ ] Resolve only necessary declared guides within trusted canonical source
  roots. Legitimate relative references may remain contained; pathless skills
  retain usable advisory metadata and honest guide-unavailability results.
- [ ] Return compact catalog-validated recommendations, uncertainty and
  configuration evidence. Reject invented identifiers, unsafe references,
  malformed results and silent fallback; keep unavailable acquisition distinct
  from a successful no-match decision.
- [ ] Add precise on-demand usage and troubleshooting guidance, canonical agent
  declaration, main preference and focused regression coverage. Preserve
  source-of-truth ownership rather than duplicating prompts or hoists.
- [ ] Drive source PRs, required checks and release through the normal flow;
  record native qualification and any separately owned rollout gates. Resolve
  every Plan/Validation item before declaring this effort Done.

## Validation Plan

- [ ] Native catalog scope and complete descriptor coverage are demonstrated,
  including entries omitted from inline rendering and explicit unavailable data.
- [ ] Each supported strategy has witnessed effective child input and actual
  configuration, not merely a supplied-prompt or requested-model assertion.
- [ ] The specialist has the intended read-only tool boundary and does not
  execute, approve, recursively select itself or bypass invocation restrictions.
- [ ] Parent model/configuration/history/custody remain unchanged except the
  deliberate tool call/result and documented native fork bookkeeping.
- [ ] Disabled entries, aliases, pathless providers, invalid references, stale
  data, malformed output, ambiguity and no-match have focused negative controls.
- [ ] Cancellation and extension lifecycle tests prove owned settlement and
  rejection of late output without affecting unrelated tasks or the foreground.
- [ ] Independent quality judgment and full acquisition/inference accounting
  support the chosen strategy; synthetic checks are not reported as model quality.
- [ ] Preference remains useful with mandatory procedures and ordinary failure
  recovery; no missing helper becomes permission for a guessed or unsafe action.
- [ ] Existing bounded tests, lint, documentation and relevant clean-room/native
  checks pass; no exhaustive effectful matrix is added to required unit-only CI.
- [ ] Public artifacts are neutral and self-contained; no real transcripts,
  private identifiers, credentials or account-specific rate evidence are published.

## Proposal

Start with the existing customization payload and native descriptor registry.
Collect bounded attributed context automatically, dispatch one specialist and
validate its own terminal result. Do not preload every skill body or create a
central inference/indexing service.

Keep full forks as a capability-qualified comparison, not an assumed extension
primitive. If task inheritance defeats the intended budget, evaluate a supported
owned-session alternative or name the SDK dependency before adopting it.

## Journal

### 2026-10-31 - Source coordination and contract evidence

- Claimed issue #9809 after public duplicate checks. Reused the coordinator-owned
  source worktree rather than allocating another serial checkout.
- Static SDK, catalog/guide and evaluation evidence identified reusable native
  interfaces and unresolved inheritance, capacity and lifecycle witnesses.
- This proposal precedes prototype and model trials. No source implementation,
  default model change, instruction-budget override or shared rollout is claimed.
