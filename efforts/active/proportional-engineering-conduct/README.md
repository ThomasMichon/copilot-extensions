# Proportional Engineering Conduct

- **Slug:** `proportional-engineering-conduct`
- **Repo:** copilot-extensions
- **Branch(es):** Plan PR against `dev`; implementation only after this plan
  is accepted and the scratch comparison passes
- **Created:** 2026-10-26
- **Status:** Draft
- **Vision:** [Harness guidance](../../../visions/harness-guidance/README.md)
  owns delivery. This effort proposes a child intent for judgment. See
  [proposal](proposal.md).
- **Umbrella issue:** [#9188](https://github.com/ThomasMichon/copilot-extensions/issues/9188)

## Guiding Intent

Agents should realize the asked change inside the architecture the owning
repository already states. They should extend that architecture only when the
ask cannot be expressed inside it, and only after the extension is accepted.
Caution is not a reason to invent a second test system, validation layer, or
framework.

The nudge belongs upstream, as a short always-on conduct kernel that points at
each repository's own engineering guidance. It is not a style patch, and it is
not a copy of any one repository's test or install rules.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| plan-owner | Plan, scratch comparison, and any later source PR | #9188; worktree codename `soldered-hinge` |

## Coordination

- **Topology:** One plan PR, then a separate implementation PR only if the
  scratch comparison passes.
- **Host (owns PRs):** plan-owner.
- **Delegates:** None for the plan. Comparison runs may be separate scratch
  worktrees; they do not edit this plan.
- **Handoff:** This README and #9188 carry source progress. A merged plan is
  not permission to project live guidance. A source merge is never a claim
  that an installation changed.

## Context

`agent-conduct-guidance` already ships short always-on kernels for delegation,
process hygiene, scratch space, secret-pattern masking, elevation, maintenance
standby, and multi-agent rules. None of them say "use the architecture you
already have."

[Harness guidance](../../../visions/harness-guidance/README.md) says plugin
policy arrives as a concise kernel, skills hold procedures, and repositories
keep their own invariants. [Test portfolio](../../../visions/test-portfolio/README.md)
and `TESTING.md` already state how this repository tests. Other repositories
state their own. Agents still invent parallel harnesses and extra checks.

[Instruction source resolution](../instruction-source-resolution/README.md)
is keeping required provenance and reviewed-fallback checks. This effort must
not weaken that. It forbids unjustified parallel machinery. It does not forbid
a proportionate safeguard that fits the existing architecture and is justified
by a concrete failure, and it does not remove a check the owning contract
already requires.

A style overlay (direct answers, simpler code, stated tradeoffs, dense
formatting, user-focused explanation) was the seed. The assessment is in
[proposal](proposal.md). Tone helps. It does not stop process over-building.

## Request

Public-safe capture of the ask. Private identifiers are omitted; the intent is
not.

Assess whether a style overlay can nudge agents toward practical engineering,
and layer the durable guidance upstream rather than in a control repository.

The overlay:

1. Direct execution and no filler. No conversational openers or closers.
   Start with the answer or the highest-value fact.
2. Anti-over-engineering. No extra classes, frameworks, or taxonomies when a
   simple change suffices. Readable, linear, immediately executable code.
3. State tradeoffs objectively. Call out an edge case without complicating
   the primary solution. No corporate safety disclaimers unless a boundary
   is actually crossed.
4. High information density. Bold only core terms. Short list items. Sharp
   headings.
5. Explain why in terms of user utility. Prefer plain language and analogies.
   Avoid jargon unless it is necessary.

Also required, beyond that overlay:

- Nudge agents toward each source repository's own engineering guidance.
- Do not over-engineer in the name of safety. Agents add installation
  receipts, ownership validation, provenance checks, redundant downloads, and
  over-eager refreshes, and they pre-plan spikes the task does not need.
- Repositories already state how to test, and they already allocate a safe
  test strategy. Do not invent another one.
- Two abstracted failure shapes: a worker spent hours designing a
  multi-part containerized test system for a defect the repository's existing
  test venue could already reproduce; a request to speed up an installer was
  answered by adding source validation, hash validation, installation
  ownership checks, redundant downloads, and extra refreshes.
- Prefer simple, practical solutions inside a pre-agreed architecture.
- If something must be built, plan it: check the vision, inspect the current
  architecture and invariants, propose an extension where necessary, have
  that extension validated by review (preferred) or by the operator, then
  build inside the accepted architecture.
- Most fixes are below the architecture. The implementation is wrong, not
  the design.
- Say in plain English what is underway and what the concern is. Keep
  chatter low. Update at a regular cadence. An existing disposition nudge is
  a good moment to also tell the operator.
- Tune in scratch worktrees before any live instruction projection, so worse
  guidance is not what ships.

## Plan

### Phase 0 — Accept the intent
- [x] Review this plan and [proposal](proposal.md) before any instruction
  file or vision file is added. Landed in #9191.
- [ ] Accept or revise the child-vision placement
  (`visions/harness-guidance/practical-realization`). _(agent-recommended)_
- [ ] Confirm the slug `proportional-engineering-conduct`.

### Phase 1 — Draft only
- [x] Assess the style overlay. See [proposal](proposal.md).
- [x] Draft the kernel text in the proposal. It is not projected.
- [x] Revise the kernel from review comments before any comparison.

### Phase 2 — Scratch comparison
- [x] Run the three task shapes in [proposal](proposal.md) with the draft
  kernel injected, and again without it. This run was an explicit prompt,
  not an always-on instruction file in a session.
- [x] Record pass/fail on behavior: used the stated test venue; did not add
  unjustified parallel machinery; allowed a proportionate in-architecture
  check for a concrete failure; proposed a real extension instead of
  building it; did not skip a real gate; did not go mute. Scorecard is in
  the proposal. Destinations matched. The open-ended speed task differed
  in process cost only.
- [x] Run one session-injection comparison on the open-ended installer-speed
  shape. The kernel was an always-on instruction file in one scratch
  session and absent from the control. Scorecard is in the proposal.
  Destinations diverged. Neither added unjustified machinery. Process cost
  did not show a clear guided win. Does not authorize projection.
- [x] Reframe the draft kernel so the subject is a bug fix or a feature
  scope, not a validation exam. See [proposal](proposal.md). Not projected.
- [ ] Repeat the comparison on a bug fix and a feature scope after that
  reframe. The installer session-injection run does not count as that test.
  Do not project before it. Hold projection.

### Phase 3 — Project only after the comparison passes
- [ ] Add the accepted kernel as an `agent-conduct-guidance` instruction
  projection, plus a paired skill only if the comparison showed the kernel
  cannot carry the altitude decision.
- [ ] Point at repository engineering guidance. Do not copy `TESTING.md` or
  the install contract into the kernel.
- [ ] Add the changefile for `agent-conduct-guidance`. Do not hand-edit
  versions.
- [ ] If the disposition nudge is the status seam, extend that reminder so
  the same moment also asks for the one-or-two-sentence operator update.
  Do not add a second channel.

## Validation Plan

- [x] Scratch comparison scorecard in [proposal](proposal.md) is recorded
  before any projection PR. It does not authorize projection.
- [ ] Guided runs do not skip confirmation, elevation, publication, or
  destructive-action gates.
- [ ] Guided runs do not treat a real invariant break as "just implement it."
- [ ] The projected kernel stays a concise fallback, not a restatement of
  repository test or install policy.
- [ ] Required provenance and reviewed-fallback checks owned by instruction
  source resolution remain intact. A proportionate in-architecture safeguard
  for a concrete failure is not treated as a kernel failure, and an existing
  required check is not removed.
- [ ] Plugin tests for the projection declaration pass, and a changefile is
  present, before the implementation PR merges.

## Proposal

See [proposal.md](proposal.md).

## Journal

### 2026-10-26 — Kickoff
- Opened #9188.
- Captured the public-safe request and the draft kernel. No instruction
  projection. No vision file yet.
- Slug `proportional-engineering-conduct` is in use pending confirmation.

### 2026-10-26 — Review narrowing
- #9191 review: a pre-existing contract sentence must not block a small
  check for a newly discovered ownership or corruption defect when that
  check fits the existing architecture. Narrowed the prohibition to
  unjustified parallel machinery, in the proposal and in this plan's
  comparison and validation wording. Existing required checks stay.
- Follow-up review: the Context paragraph still used the old ban. Aligned
  it with the proposal.

### 2026-10-26 — Prompt comparison, hold projection
- Three shapes, kernel in the prompt versus the same task without it.
- Reproduce in the stated venue: both used that venue. No new harness. No delta.
- Make an existing installer path faster: both final answers changed the
  existing path and added no new framework or unrequired checks. The guided
  run answered without a long investigation. The control reached the same
  destination after extended exploration.
- Contract cannot express the check: both proposed the extension and waited.
  Neither hacked around the invariant. No delta.
- Mixed. Destinations did not diverge. Process cost diverged on the
  open-ended speed task. The guided condition was told to follow the kernel.
  That is not evidence an always-on file would do the same. Do not project.

### 2026-10-26 — Session-injection comparison, hold projection
- One shape: make the suite installer faster. Same model, same commit.
  Guided session loaded the draft kernel as an always-on instruction file.
  Control session did not. Neither prompt said to follow the kernel.
- Guided reused an existing fingerprint path: resolve each distinct parent
  once per call, and keep the per-file checks. It reported a hashing-stage
  improvement, not a whole-installer claim. 34 model requests, about 11
  minutes.
- Control reused the existing source-snapshot copy loop: bounded parallel
  copies for larger snapshots, serial for small ones. Existing verification
  stayed. It reported a source-freezing improvement, not a whole-installer
  claim. 42 model requests, about 10 minutes.
- Neither added a parallel validation layer, chained receipts, redundant
  downloads, or extra refreshes. Neither went mute. Neither pushed.
- Both were told not to push and not to open a pull request, so this run
  does not show that the kernel preserves publication gates.
- Inconclusive. Destinations diverged, and both stayed inside an existing
  path. The control did not bloat, so this run does not show that the
  ambient file prevents that failure. Wall time did not favor the guided
  session. Do not project.
- This run did not test bug-fixing or feature scoping. Do not treat it as
  evidence for or against the kernel's actual subject.

### 2026-10-26 — Reframe the kernel around the ask
- The installer session-injection run scored extra validation. That was the
  wrong objective. The ask is ordinary bug-fixing and feature scoping.
- Draft kernel lead is now: if the existing architecture can express the bug
  fix or the feature, change the implementation. If it cannot, propose the
  smallest extension and wait.
- Receipts, hashes, ownership schemes, redundant downloads, and extra
  refreshes stay examples of a second system. They are not the subject.
- Not projected. The next comparison, when run, is a bug fix and a feature
  scope. The installer run does not count as that test.
