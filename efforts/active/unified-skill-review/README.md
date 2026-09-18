# Unified Skill Review

- **Slug:** `unified-skill-review`
- **Repo:** copilot-extensions
- **Branch(es):** isolated worktree; sequential proposal and implementation PRs
- **Created:** 2026-09-18
- **Status:** Draft
- **Vision:** `visions/harness-guidance/README.md`: authoritative-ownership,
  progressive-context-disclosure, navigable-on-demand-grounding
- **Umbrella issue:** #2847

## Guiding Intent

Keep skill authoring and holistic review in their existing owning plugin.
Runtime conformance should use the actual installed host, while consumer
harnesses contribute optional, explicitly located rulebooks instead of parallel
review skills. Preserve standalone use and keep runtime acceptance separate from
editorial recommendations.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Contributor | Proposal, implementation, validation, and publication | Issue #2847 and its linked PRs |

## Coordination

- **Topology:** sequential proposal and implementation PRs.
- **Host (owns PRs):** Contributor.
- **Delegates:** bounded review or testing only; no concurrent source ownership.
- **Handoff:** this README and the public issue retain the objective and unresolved checks.
- No consumer-private names, examples, paths, or references belong in this effort.

## Context

The existing `authoring-skills` and `reviewing-customizations` skills are the
natural owners. The existing scanner is a useful structural heuristic, but a
replica of YAML parsing cannot establish complete host loading behavior.
Review also needs a deliberate extension point for a harness's editorial policy.

This closes existing guidance-ownership and progressive-disclosure intent;
it does not establish a new service, plugin, or mandatory publication gate.

## Request

> Can the parsing of the skill and core review logic live upstream in
> copilot-extensions' customizing-copilot plugin?

> My intent has been to have the reviewing-customizations skill be what applies
> this holistic review, so this plugin risks introducing conflicting guidance.

## Plan

### Phase 1 - Review the ownership contract

- [ ] Land this proposal through the repository's review flow.
- [ ] Keep `reviewing-customizations` as the single holistic review entry point.
  Let `authoring-skills` point to it rather than duplicating a second rubric.
- [ ] Define optional rulebook resolution from explicit repository/harness
  instructions or supplied trusted context. No guessed global search or hard
  dependency on another plugin; missing optional guidance retains generic use.

### Phase 2 - Implement the focused review capability

- [ ] Add a standard-library CLI-backed skill validator and focused tests under
  `reviewing-customizations`; use supported CLI commands, not a native-addon ABI.
- [ ] Require exact staged-candidate discovery, version reporting, parsed-value
  expectations, isolated settings, and truthful unavailable/failure outcomes.
- [ ] Add a concise description/procedure rubric and read-only impact-preview
  guidance; distinguish runtime rejection, editorial advice, and consumer policy.
- [ ] Integrate the authoring and review instructions, update payload docs, and
  bump the plugin and catalog versions together.

### Phase 3 - Validate and finish

- [ ] Complete the validation plan and documentation-impact review.
- [ ] Land the implementation through its review/merge gate.
- [ ] Archive the completed effort and release its worktree responsibility.

## Validation Plan

- [ ] Synthetic unit fixtures cover YAML punctuation, field types, description
  length/Unicode boundaries, missing candidates despite exit zero, diagnostics,
  failure/timeout, source-byte integrity, and temporary/configuration isolation.
- [ ] A live installed-CLI smoke covers acceptance, rejection, and description
  round trips without invoking a model or executing candidate helpers/hooks.
- [ ] A copied-payload first-use check works without dependency installation.
- [ ] Review examples cover explicit rulebook, no rulebook, ambiguous/missing
  configured rulebook, and a host without marketplace discovery or CLI access.
- [ ] A read-only sample collection preview distinguishes unchanged skills,
  editorial suggestions, runtime defects, and behavior-sensitive recommendations;
  it neither rewrites skills nor claims static review proves operational behavior.
- [ ] Run targeted repository tests/guards, inspect the public artifact for
  private material, and confirm version/catalog synchronization.

## Proposal

Use one review owner and two evidence lanes: the real CLI for file acceptance,
and a focused rubric for writing/behavioral clarity. Read an explicitly supplied
harness rulebook as additional review guidance, not executable configuration or
a replacement for runtime rules and safety constraints. Keep formal evals
optional for ordinary edits. Present-tense operational docs explain the current
contract; historical rationale belongs in issue/PR discussion.

## Journal

### 2026-09-18 - Proposal

- Opened #2847 after checking for overlapping open work.
- Confirmed the target adopts efforts and this work advances existing
  guidance-ownership intent. No implementation changes precede proposal review.
