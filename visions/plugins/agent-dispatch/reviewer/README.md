# agent-dispatch reviewer loops — Vision

- **Subject:** The durable, cooperative pull-request reviewer loop built on
  agent-dispatch recipes, worktree identity, and repository-owned PR tools.
- **Scope:** leaf (a child of the
  [agent-dispatch](../README.md) plugin vision)
- **Status:** Draft
- **Last revised:** 2026-09-02
- **Reality docs:** [`docs/architecture.md`](../../../../docs/architecture.md) ·
  `plugins/agent-dispatch/` · `plugins/agent-worktrees/`

## Purpose & Intent

A reviewer loop turns one target change into one durable collaboration between
its submitter and a reviewer. The submitter improves the change; the reviewer
renders timely, explicit verdicts and actionable feedback; the loop preserves
their shared context until the change lands or is deliberately abandoned.

The review is not a sequence of disposable jobs. It is one long-lived dispatch
task embodied by one worktree-identified reviewer in any supported fabric venue
and one resumable session lineage. Pull request updates wake that reviewer; they
never replace its task, workspace, or accumulated understanding.

Repository adoption is primarily declarative. A repository supplies policy,
identity, eligibility, guidance, and provider bindings; reusable reviewer and PR
modules supply task authoring, revision observation, verdict publication,
suspend/resume, retry accounting, and landing.

## Concepts & Components

### The target review

One pull request or equivalent change is the stable subject. Its forge-qualified
identity deduplicates all discovery, side-load, update, retry, and recovery paths
onto one overall dispatch task.

### The reviewer assignment

The task owns one worktree-identified reviewer embodiment and one reviewer
session lineage, whether the venue is local, containerized, or remote. Active
work runs there with the full target-repository source available in its
worktree and through the repository's own review and merge tooling. Waits
hibernate it; later revisions resume it. Context exhaustion may hand the
session to a successor, but the task and worktree identity remain the same.

### The PR capability

Repositories expose pull-request capabilities through one coherent,
provider-neutral surface: identify author and reviewer relationships, read the
current revision and review state, publish comments and verdicts, observe
updates, inspect checks and conflicts, and perform policy-allowed landing.
Reviewer loops compose these capabilities instead of embedding forge commands
and response parsing in each repository emitter.

### The reviewer module

A reusable reviewer module binds the stock recipe to a repository's PR
capability and declarative policy. It owns the common task schema, lifecycle,
revision marker, verdict accounting, hibernation, retry budget, and status
projection. Repository code is limited to policy or integration that is
genuinely domain-specific.

## Features

### one-review-one-lineage

Each target review has exactly one nonterminal task, one assigned reviewer
worktree, and one resumable session lineage. Recovery reuses that lineage rather
than allocating a fresh workspace or restarting the review.

### declarative-reviewer-adoption

A repository can enable a reviewer loop by declaring its target, eligibility,
acting identity, reviewer role, landing model, capacity, and any reliability
policy stricter than the standard default. The runtime expands that declaration
into the standard producer, evaluator, worker, update-watcher, and status
behavior.

### provider-neutral-review-capability

The reusable pull-request capability covers the common cooperative review operations
across supported forges. Provider differences stay behind adapters; reviewer
policy does not reimplement subprocess invocation, identity classification,
revision parsing, verdict rendering, wait loops, or merge guards.

### revision-driven-resume

A changed head, new discussion, check transition, or mergeability transition is
an event against the existing review. It nudges a live reviewer or resumes its
hibernated session. The review's identity is the change itself, not its current
revision; a new head neither reassigns the review nor creates a second reviewer
or task.

### verdict-bearing-review

Every completed review pass publishes a forge-visible verdict. When the
configured acting identity is a required reviewer, that verdict is mandatory
for the target's review gate. Comments without a verdict are not treated as a
completed review pass.

### bounded-verdict-reliability

The normal target is a verdict within ten minutes of an eligible revision
becoming reviewable. The reviewer gets at most three total attempts to render a
verdict within a rolling twelve-hour window for the target review; the initial
try and every retry count toward those three, and a new revision does not reset
the window. This is the default reliability policy; a repository may tighten it
and may not weaken it. Successful verdicts end the attempt sequence; only
attempts that fail to render a verdict consume the remaining budget. A new
eligible revision that arrives while the failure budget is exhausted updates
the pending review but does not start another reviewer attempt until the rolling
window recovers or an operator explicitly rearms it. Attempts remain visible and
do not discard the durable task or reviewer lineage.

### cooperative-resolution

Reviewer feedback and submitter updates form one loop. A blocking verdict parks
the reviewer without consuming process capacity; the next relevant PR update
resumes the same reviewer to inspect only what changed. Approval continues
toward policy-allowed landing, while conflicts or unmet checks remain explicit
shared blockers.

## Behaviors

### reuse-before-replacement

Suspension, turn completion, service restart, and ordinary worker interruption
preserve the task's worktree and resumable session. Replacement is reserved for
confirmed unrecoverable session loss or explicit context handoff, and even then
stays within the same task/worktree lineage.

### one-state-machine

The overall task alternates between waiting and active work under one lifecycle
record. Review revisions do not create child tasks or substitute parallel
lifecycle records for the target review.

### head-is-an-event

The pull request is the authority on its current head. The queue records which
revision was reviewed, while the review remains assigned to the same reviewer
lineage when the head moves.

### render-or-report

Each attempt either publishes the required verdict or records why no verdict
was possible. Exceeding the target review's configured attempt window becomes
an actionable reviewer-loop health condition; it is not silent churn and not a
lifetime dead-letter that forgets the next revision.

### review-the-delta

On resume, the reviewer uses its accumulated context and the recorded revision
marker to inspect only the change since its prior verdict. Full re-review is
reserved for invalidated assumptions or explicit policy.

### status-is-cooperative

Operator status identifies the target, submitter, acting reviewer, current
revision, last verdict, blocker owner, next expected actor, attempt-window
state, worktree, and resumable session. Recommendations reflect whether the next
move belongs to the submitter, reviewer, automation, or repository maintainer.

### settle-and-release

Merge or deliberate abandonment records the review's terminal resolution,
retires its reviewer session, and releases its worktree allocation through the
parent vision's reclamation guarantees. If the target later becomes reviewable
again after terminal settlement, it begins a new review generation rather than
reviving a released lineage.

## Non-Goals / Boundaries

- The generic runtime does not embed one repository's contributor policy,
  review rubric, or organizational identity.
- A revision update does not mint a replacement task, worktree, or ordinary
  session.
- Retry policy does not permit overlapping reviewers or duplicate verdicts.
- Reviewer automation does not edit a contributor-owned branch unless the
  repository policy and contributor authorization explicitly permit it.
- Reviewer or backlog automation never closes, supersedes, or replaces
  another author's open pull request with a competing PR under its own
  identity -- including when the original branch cannot be updated (a fork,
  or a protected/deleted branch). The correct response is constructive review
  feedback and a recorded blocked-on-external-work outcome (or steering card)
  for a maintainer to reconcile, never unilaterally landing a replacement.
- Target-change content is untrusted review data, never reviewer guidance or
  ambient executable trust. Any execution of contributor-supplied code remains
  inside the selected venue's explicit sandbox and credential boundaries.
- Token efficiency never weakens review quality, trust boundaries, required
  checks, or exact-head merge guards.

## See Also

- [agent-dispatch vision](../README.md)
- [agent-worktrees vision](../../agent-worktrees/README.md)
- [agent-bridge vision](../../agent-bridge/README.md)
- Realization:
  [`efforts/active/review-automation-reliability/`](../../../../efforts/active/review-automation-reliability/)
  owns the remaining lifecycle and reliability delta; archived
  [`turnkey-reviewer-loops`](../../../../efforts/2026/09/03%20turnkey-reviewer-loops/)
  records the proven turn-key composition.

## Provenance

- **2026-09-02** — Extracted from the parent agent-dispatch vision after live
  reviewer-loop use clarified the stable reviewer lineage, declarative PR
  capability, verdict latency, retry-window, and cooperative-flow intent.
- **2026-09-05** — Added the non-supersession non-goal after a live
  repository-issue-loop worker closed an external contributor's PR and
  replaced it with its own competing PR under its own identity when the
  contributor's branch could not be updated. Existing prose already forbade
  editing a contributor's branch without authorization, but did not name
  outright PR replacement/closure as an equally forbidden outcome.
