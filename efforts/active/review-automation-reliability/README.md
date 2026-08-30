# Review Automation Reliability

- **Slug:** `review-automation-reliability`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-08-29
- **Status:** Draft
- **Vision:** harness-guidance `authoritative-ownership`,
  `bounded-delegate-contracts`, `resilient-safety-boundary`, and
  `proportional-independent-review`

## Guiding Intent

Make automated review a durable, observable lifecycle rather than a
best-effort request. Each review has one owner, bounded retries, resumable
evidence, explicit human decision boundaries, and a terminal outcome that
cannot be mistaken for successful publication.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| lifecycle host | Owns review state, claims, retries, and terminal outcomes | isolated worktree |
| reviewer adapters | Translate provider-specific review behavior | independent slice PRs |
| reliability validator | Exercises interruption, duplication, and recovery | scenario harness |

## Coordination

- **Topology:** lifecycle host with adapter and validation slices.
- **Host (owns PRs):** lifecycle host.
- **Delegates:** adapters implement provider seams without owning orchestration.
- **Handoff:** every slice reports durable state transitions and evidence to the
  host contract.

## Context

Review automation crosses asynchronous systems that can delay, duplicate, lose,
or partially apply work. Reliability requires durable ownership and state,
idempotent delivery, bounded recovery, and a clear separation between analysis,
recommendation, and the human-controlled verdict.

## Request

Define and implement a provider-neutral review lifecycle that remains correct
across process restarts, delayed responses, duplicate delivery, reviewer
failure, and explicit human steering.

## Plan

### Phase 1 - Define the durable review lifecycle

- [ ] Specify requested, claimed, analyzing, awaiting-steer, ready, submitted,
  failed, and abandoned states with legal transitions.
- [ ] Bind each active review to one owner and one immutable target revision.
- [ ] Separate reviewer recommendation, comments, and evidence from the final
  verdict authority.

### Phase 2 - Make dispatch idempotent

- [ ] Deduplicate equivalent requests and reject conflicting ownership.
- [ ] Persist checkpoints before external delivery and correlate every response
  with the target revision and attempt.
- [ ] Bound retries with classified transient, permanent, and stale-target
  outcomes.

### Phase 3 - Add steering and recovery

- [ ] Resume interrupted analysis without duplicating submitted feedback.
- [ ] Present blocked decisions through an explicit steering contract and wake
  the same review after an answer.
- [ ] Revalidate the target revision before submission and return stale work to
  analysis rather than applying it blindly.

### Phase 4 - Prove end-to-end reliability

- [ ] Exercise delayed, duplicated, reordered, malformed, and lost provider
  responses.
- [ ] Exercise coordinator and reviewer restarts at every durable boundary.
- [ ] Publish concise status, attempt history, and terminal diagnostics without
  exposing review content outside its authorized sink.

## Validation Plan

- [ ] Concurrent claim attempts yield exactly one review owner.
- [ ] Duplicate request and response delivery produces one analysis and at most
  one submission.
- [ ] Restarting any process at each lifecycle boundary resumes or fails
  explicitly without losing evidence.
- [ ] A changed target revision blocks stale feedback until revalidation.
- [ ] Human-controlled verdicts are never inferred from reviewer completion or
  transport success.
- [ ] Terminal failure and abandonment remain visible and cannot be rendered as
  approval or completion.

## Proposal

Build the provider-neutral durable lifecycle first, then adapt existing review
drivers and prove reliability with deterministic interruption and duplication
scenarios.

## Journal

### 2026-08-29 - Kickoff

- Established the generic review ownership, idempotency, steering, recovery,
  and verdict-boundary campaign.
