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

<!-- backlog-reconciliation:7556:first-ten:Coordination -->

### Continuing source-specific #2847/#2853 custody

- **C2847:** Keep actual existing source/resource/native/domain custodians and current
  ROOT process. File authors and generic Participant roles do not appoint executors. Any
  new scoped receiving authority must be normally reviewed and source-specific
  restrictions preserved before a separate source judgment; no automatic transfer. Keep
  the actual namankanakiya assignment and the existing assignee-specific
  acknowledgement/closure/reassignment restriction; no same-assignee completion or
  release is established by proposal2848 or journal7590. This is not a universal ACK
  gate. Any additive receiving amendment must preserve the WHOLE existing
  Request/Status/Participants/Plan/actual global Validation/Journal, checked credit and
  first unchecked gates; no actual Done foundation is reopened.

- **C2853:** Keep actual existing source/resource/native/domain custodians and current
  ROOT process. File authors and generic Participant roles do not appoint executors. Any
  new scoped receiving authority must be normally reviewed and source-specific
  restrictions preserved before a separate source judgment; no automatic transfer. Any
  additive receiving amendment must preserve the WHOLE existing
  Request/Status/Participants/Plan/actual global Validation/Journal, checked credit and
  first unchecked gates; no actual Done foundation is reopened.

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

<!-- backlog-reconciliation:7556:first-ten:Plan -->

### Direct retained acceptance from #2847/#2853

_Agent-recommended additive tracking proposal; original source requirements,
alternatives, finite proof tiers and custody remain binding._

These rows retain work rather than certify it complete. Existing Request,
Status, Participants, Plan, global Validation, Journal, checked credit and
first unresolved gates remain intact. Review of this amendment is document
intake only, not implementation, native/installed proof, source closure,
owner reassignment or claim/resource release. A separate source judgment
must preserve each literal source-specific hold; no generic ACK, every-host
or whole-parent completion gate is added.

#### Source #2847

- [ ] **P2847-1:** reviewing-customizations is sole holistic owner; authoring-skills
  integrates/points to it; consumer non-skill rulebooks augment, not competing skills or
  generic security review.

- [ ] **P2847-2:** Actual supported installed CLI loader, tested version, exact staged
  candidate/discovery/parsed-value expectations, isolated settings/source bytes and
  truthful failure/unavailable/timeout; no YAML replica or undocumented native ABI.

- [ ] **P2847-3:** Synthetic YAML punctuation/types, description length/Unicode,
  exit-zero-but-missing candidate, diagnostics/source integrity/config isolation;
  copied-payload first use without dependency installation.

- [ ] **P2847-4:** Optional explicit trusted rulebook; no guessed global search or
  other-plugin hard dependency. Standalone/no rulebook works; ambiguous or
  configured-but-missing rulebook is reported; consumer policy, editorial advice and
  runtime rejection remain distinct.

- [ ] **P2847-5:** Concise description/procedure writing rubric AND read-only collection
  impact preview of unchanged/editorial/runtime/behavior-sensitive recommendations;
  formal evals optional ordinary edits; no service/startup/publication gate or rewrites.

- [ ] **P2847-6:** Credit actual2848 reviewed proposal/plan;2849 closed-unmerged
  candidate and33 helper/native/consumer claims not delivered conformance. Shared
  rubric/static scanner and journal-only7590 are bounded credits, not behavior
  completion.

- [ ] **P2847-7:** Actual namankanakiya assignment remains; specific existing owner
  acknowledgement/closure/reassignment condition and no same-assignee closeout survive.
  Do not expand custody from generic Contributor table or age/partial overlap.

#### Source #2853

- [ ] **P2853-1:** Generic authoring->authoring-skills, holistic
  review->reviewing-customizations and explicit optional rulebook routing is centrally
  plugin-owned; only repo-specific rulebook/pointer stays consumer-owned.

- [ ] **P2853-2:** Implement normal owned routing projection OR explicit reviewed
  requirement-preserving disposition.2849 closed-unmerged/branch clarification is
  neither implementation nor explicit rejection.

- [ ] **P2853-3:** Use owner's normal instruction-projections declaration AND matching
  template, exact .github/instructions/customizing-copilot/... destination, provenance
  and sync/scan drift protection; generic policy not copied by every consumer.

- [ ] **P2853-4:** No extra review skill, duplicate policy, startup emitter/runtime
  service or new mandatory gate; optional consumer supplement/standalone use remain.

- [ ] **P2853-5:** Preserve2847 actual assignee's separate installed-loader/rubric lane;
  do not silently appoint them to2853 or treat Draft as veto/current-source absence as
  proven.

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

<!-- backlog-reconciliation:7556:first-ten:Validation -->

### Global validation of retained #2847/#2853

#### Source #2847

- [ ] **V2847-1:** Map the already-present complete existing ROOT global validation:
  supported installed CLI acceptance/rejection/version/exact staged candidate/parsed
  values and truthful unavailable/timeout, synthetic
  schema/Unicode/missing-candidate/config/source isolation, copied-payload first use
  without dependency install.

- [ ] **V2847-2:** Retain explicit optional/no/ambiguous/missing rulebooks, standalone
  generic behavior, runtime/editorial/consumer distinctions and read-only impact preview
  without a model or candidate helper/hook execution; formal evals stay optional and
  current bounded tiers remain.

- [ ] **G2847:** Resolve every directly retained P2847/V2847 clause at its
  ORIGINAL finite alternative/venue/tier, retaining actual component credit, unknowns,
  same owners and every source-specific restriction. Paper intake is not
  implementation/native/installed proof or source/resource release. No unrelated
  whole-parent, every-host or generic ACK gate.

#### Source #2853

- [ ] **V2853-1:** Qualify the chosen routing projection OR actual reviewed
  requirement-preserving disposition. If implemented, normal owner
  declaration/template/exact destination/provenance/sync-scan protect generic
  authoring/review/explicit optional-rulebook guidance while consumer specifics remain
  local.

- [ ] **V2853-2:** Preserve standalone and missing/ambiguous configured-rulebook
  behavior, normal generated-owner contract and no extra review/startup/publication
  machinery. Do not infer completion/rejection from unmerged2849, force stronger
  content-envelope proof or silently expand2847's assigned loader/rubric scope.

- [ ] **G2853:** Resolve every directly retained P2853/V2853 clause at its
  ORIGINAL finite alternative/venue/tier, retaining actual component credit, unknowns,
  same owners and every source-specific restriction. Paper intake is not
  implementation/native/installed proof or source/resource release. No unrelated
  whole-parent, every-host or generic ACK gate.

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

<!-- backlog-reconciliation:7556:first-ten:Journal -->

### 2026-10-28 - Additive retained #2847/#2853 contract proposal

Added direct per-source Plan, actual global Validation and continuing custody rows after whole-record and complete receiving-document research. Normal review and current complete receiving readback remain publication gates; this entry records authored tracking only, not a future merge or source disposition. All incumbent finite source/design/release/native credits remain at their actual tiers, with original alternatives, unknowns, source-specific holds and existing source/resource custodians preserved. No implementation, operational effect or claim release is authorized. The campaign's receiving register records delivery and later source judgments separately.
