# Efforts — Vision

- **Subject:** The efforts plugin as the durable planning and continuity layer
  for multi-session, multi-wave work.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-08-27
- **Reality docs:** [`efforts/README.md`](../../../efforts/README.md) ·
  [`docs/harness-runbook.md`](../../../docs/harness-runbook.md) ·
  [`plugins/efforts/README.md`](../../../plugins/efforts/README.md) ·
  [`plugins/efforts/skills/planning-efforts/SKILL.md`](../../../plugins/efforts/skills/planning-efforts/SKILL.md)

## Purpose & Intent

Efforts should turn a substantial objective into a durable execution loop rather
than a disposable plan or one-session burst. Once a repository adopts efforts,
an agent should be able to discover that expectation, create or resume the
canonical effort, obtain review for its plan, execute it in waves, and carry the
same objective across session boundaries until the effort's own completion gate
is satisfied.

The effort is the shared contract around the work. Conversations, worktree
sessions, issues, and handoffs are individual views or relay legs; none of them
replaces the effort or independently proves that the objective is complete.
Continuity should therefore be record-first and context-efficient: a successor
loads the durable effort and only reconstructs the predecessor's immediate
activity when needed.

## Concepts & Components

- **Repository adoption** is an explicit, repository-owned declaration that
  effort-backed planning and continuity apply there.
- **Effort contract** is the versioned premise, plan, validation plan,
  coordination state, and journal shared by every participant.
- **Active effort focus** binds one worktree objective to one canonical effort
  without creating a repository-global "current effort."
- **Reviewed plan gate** separates proposal from execution so implementation is
  based on intent that other participants can discover and inspect.
- **Execution waves** advance bounded slices while preserving the whole effort
  as the completion boundary.
- **Session relay** uses handoff and ramp-up only to bridge the immediate gap
  between sessions; the effort carries the durable objective and remaining
  roster.
- **Repository capability** determines cross-repository placement: a target that
  has adopted compatible efforts may own a sub-effort, while a target that has
  not adopted them is coordinated by the host effort.

## Features

### repository-declared-adoption

A repository can explicitly declare that efforts are supported and enforced.
Agents can distinguish an adopting repository from one that merely happens to
contain similarly named files or directories.

### durable-effort-contract

A substantial objective has one canonical, versioned effort containing the goal,
plan, validation, coordination, and progress needed for another participant to
resume it without relying on conversation history.

### effort-scoped-session-loop

An effort-backed worktree behaves as a relay of bounded session legs that keep
advancing the same objective until the effort is complete.

### reviewed-wave-execution

The plan is reviewed before implementation, then executed in bounded waves whose
results and direction changes are reflected back into the effort at meaningful
boundaries.

### compact-effort-handoff

A handoff can identify the active effort and next slice instead of restating the
entire durable plan. Immediate predecessor activity remains recoverable through a
bounded ramp-up path.

### bounded-session-start-orientation

When reliable repository and worktree identity are available, a new session can
receive a concise pointer to the enforced effort policy, active effort, previous
session, and pending handoff without loading transcripts or duplicating the
effort.

### cross-repository-effort-ownership

Cross-repository work has one clear orchestration owner. A compatible target may
own a referenced sub-effort; otherwise the host effort retains the plan and
coordinates changes in the target.

## Behaviors

### effort-is-the-completion-gate

Completing a session, phase, handoff, pull request, or local checklist slice does
not complete the worktree objective while the active effort still has actionable
plan or validation items.

### continue-until-closed

The rightful head session for an effort-backed worktree should keep selecting
and driving the next authorized slice. A superseded session assists the head or
hands off; it does not race the same effort. The head pauses for genuine
uncertainty, requested steering, unavailable prerequisites, required review, or
required safety and administrative confirmation - not merely because one relay
leg ended. While required review blocks dependent mutation, the head may perform
only independent, non-bypassing preparation; waiting is a recorded gate, never
completion.

### explicit-release-of-responsibility

The effort releases its worktree objective only through an explicit completed
state with its plan and validation resolved. An unchecked item remains open
unless the effort deliberately records it as blocked or transferred to another
tracked objective; liveness is not inferred from prose or session activity.

### record-first-resumption

A successor first loads the active effort and resumes from its next incomplete
slice. It uses predecessor-session ramp-up as a bounded supplement for immediate
actions and observations, never as the durable source of truth.

### one-canonical-effort

An objective has one canonical effort. Host and target repositories may use
one-way references between an orchestration effort and target-owned sub-efforts,
but they do not maintain drifting peer copies or cyclic ownership.
Several worktrees may contribute distinct declared slices to that same effort,
but each worktree has one rightful head session and one explicit slice at a
time.

### target-capability-before-placement

An agent verifies explicit effort adoption in a target repository before placing
an effort there. Repository names, directory presence, or private assumptions
are not capability signals.

### bounded-and-attributable-context

Ambient effort guidance and dynamic orientation remain concise, attributable to
their owning plugin, and within the shared session-start context budget. Durable
detail stays in the effort and on-demand skills.

### fail-open-with-a-static-fallback

Missing configuration, optional worktree services, malformed dynamic state, or a
launch mode without extension hooks does not block session startup. Adopting
repositories retain a minimal plugin-owned static fallback for the completion
gate and effort-discovery rule.

## Non-Goals / Boundaries

- Effort-driven continuity does not bypass operator decisions, destructive-action
  confirmations, administrative approval, repository review policy, or external
  authorization boundaries.
- Efforts do not replace issue trackers; issues remain the discrete work and
  coordination tokens linked by the campaign.
- Adoption is not inferred from an `efforts/` directory alone, and active focus
  is not stored as one repository-global mutable value.
- Session-start orientation does not embed transcripts, full plans, or an
  unbounded worktree history.
- The efforts plugin does not require agent-worktrees, context-handoff, or a
  session logger to provide its basic planning lifecycle; those capabilities
  enrich continuity when present.
- This vision does not prescribe a configuration filename, schema, command
  grammar, worktree record shape, or hook implementation.

## See Also

- Parent vision: none
- Related cross-cutting vision:
  [`harness-guidance`](../../harness-guidance/README.md) for ownership and
  delivery of concise ambient policy.
- Child visions: none (leaf)
- Reality docs:
  [`efforts/README.md`](../../../efforts/README.md) ·
  [`docs/harness-runbook.md`](../../../docs/harness-runbook.md) ·
  [`docs/patterns/context-injection.md`](../../../docs/patterns/context-injection.md) ·
  [`plugins/efforts/README.md`](../../../plugins/efforts/README.md)

## Provenance

- **2026-08-27** - Established the standing intent for explicit repository
  adoption, reviewed wave execution, effort-scoped completion, compact handoffs,
  bounded session-start recovery, and capability-aware cross-repository effort
  ownership. This intent was carved into
  [`effort-driven-session-loops`](../../../efforts/active/effort-driven-session-loops/README.md)
  and issue [#1255](https://github.com/ThomasMichon/copilot-extensions/issues/1255).
