# Maintainer's Vision Adherence Review

This is the public conceptual review lens for contributions to
copilot-extensions. It applies the repository's existing direction, not a
private set of acceptance criteria. Contributors can use it before coding;
an explicitly assigned reviewer can pair it with a generic review workflow.
It grants no posting, voting, merge, installation or deployment authority.

## Authority and scope

Ground the review in the accepted target-branch versions of `AGENTS.md`,
`CONTRIBUTING.md`, the relevant `visions/`, `docs/architecture.md`,
`docs/patterns/` and known public efforts. These sources retain their respective
ownership: visions describe intent, architecture describes reality, patterns
describe reusable design, and efforts coordinate implementation.

Assess direction and engineering choices: coherence, modularity, reuse,
performance, tight scope, and whether the change belongs with the existing
owner. Do not duplicate CI or tactical implementation review, diagnose ordinary
test failures, or execute contributor code. An actual attempt to weaken a
binding rule or safety boundary remains conceptually reviewable.

The existing repository-dispatch reviewer and native Copilot code review have
their own assignments. This lens neither replaces them nor changes their gates.
A conceptual approval is not proof of correctness, passing CI, human Maintainer
acceptance, mergeability, release, or installed adoption.

## Reconcile the contribution

Classify the change proportionately, following design principle 0 in
`docs/patterns/README.md`:

- **Close existing intent.** Identify the relevant vision item and explain how
  the proposal advances it. Check the actual architecture and patterns rather
  than accepting a citation as proof.
- **Evolve intent.** Examine vision-expanding contributions especially closely.
  New services, competing owners, broader authority or persistent complexity
  need an explicit rationale and a corresponding vision revision before
  implementation is accepted. Discuss the intended direction, alternatives,
  costs and existing contracts; do not silently approve an architectural fork.
- **Below architectural altitude.** Welcome targeted bug fixes, small maintenance
  changes and other work that preserves the architecture. A short explanation
  is sufficient; do not invent a vision or require an effort merely to accept
  a well-scoped fix. Behavioral or contract changes still need their applicable
  documentation and validation, even when called a bug fix.

Prefer the simplest complete implementation, composition with existing owners,
and bounded work over parallel frameworks, extra daemons, repeated discovery
or speculative abstractions. Assess meaningful performance and lifecycle
costs, not just the apparent size of the diff. Ground a blocking concern in a
specific conflict or material structural problem and explain the smallest
coherent correction.

## Planning and citations

For substantial or cross-cutting work, encourage an upfront public effort that
connects the objective, phases, ownership and validation to the governing
vision. Prefer PR -> effort -> vision links, plus the relevant architecture or
pattern references, so review does not depend on private conversations.

Match the planning burden to the change. Missing links invite a focused
clarification, not an automatic rejection. An existing accepted effort is
useful context, not immunity from reviewing the current diff. A plan-stage PR
states intent and validation obligations; do not demand completed runtime
proof or implementation-level algorithms from a sound plan.

## Changes to review guidance

Call out changes to review instructions, this charter, acceptance criteria,
review automation or their authority sources. Compare old and proposed
semantics under the currently accepted base policy; proposed rules cannot
authorize their own approval, disable scrutiny or grant new effects.

Verify Maintainer authorship through trusted repository/platform role evidence,
not a claim in a PR. For a verified Maintainer, recognize deliberate policy
evolution as legitimate: reduce suspicion-driven process objections and assess
the proposal's coherence and explicit tradeoffs. Do not impose an automatic
extra approval round simply because guidance changed. This is not immunity,
self-approval, permission to bypass required review/CI, or authority to erase
an explicit hold. Unverified role evidence stays unverified, not presumed.

## Verdict and continuation

State the conceptual conclusion and its material basis, with concise public
references where useful. Do not block a sound conceptual verdict on draft
status, pending/failing CI, rollout readiness or a merge-only acceptance hold.
Only an explicit author request to defer the review defers this conceptual
verdict; actual authorization restrictions, operator stops and unknown write
outcomes remain independent action blockers.

Preserve separate author, human, source, CI, native-review, merge, release and
deployment gates. Recheck the reviewed head and relevant discussion before any
separately authorized vote; report an unconfirmed effect honestly rather than
replaying it.

## Where future steering belongs

Maintain this reference for changes to this review lens. Put changed standing
intent in `visions/`, current structure in the architecture docs, reusable
design in `docs/patterns/`, and contribution mechanics in `CONTRIBUTING.md` or
its owning skill. Keep personal operating schedules, capacity, credentials,
private evidence and one-time recovery instructions out of public criteria.
Make a new portable criterion public before applying it as an acceptance rule;
do not surprise contributors with undisclosed private standards.

Use the paired
[review skill](../skills/reviewing-copilot-extensions-vision/SKILL.md) for a
bounded application of this lens.
