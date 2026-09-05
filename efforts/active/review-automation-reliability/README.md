# Review Automation Reliability

- **Slug:** `review-automation-reliability`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-08-29
- **Status:** Draft
- **Vision:** harness-guidance `authoritative-ownership`,
  `bounded-delegate-contracts`, `resilient-safety-boundary`, and
  `proportional-independent-review`; also
  [`visions/plugins/agent-dispatch/reviewer/`](../../../visions/plugins/agent-dispatch/reviewer/README.md)
  (the declarative agent-dispatch engine for processing pull requests)
- **Related issues:**
  [#1733](https://github.com/ThomasMichon/copilot-extensions/issues/1733)
  (stable reviewer lineage and bounded verdict retries) and
  [#1846](https://github.com/ThomasMichon/copilot-extensions/issues/1846)
  (remaining reviewer-loop contract validation)

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
- **Public coordination token:** the reviewed plan PR until a dedicated issue
  is authorized; implementation does not begin under the temporary token.

## Context

Review automation crosses asynchronous systems that can delay, duplicate, lose,
or partially apply work. Reliability requires durable ownership and state,
idempotent delivery, bounded recovery, and a clear separation between analysis,
recommendation, and the human-controlled verdict.

The archived
[`turnkey-reviewer-loops`](../../2026/09/03%20turnkey-reviewer-loops/README.md)
effort already proved the stock reviewer recipe in this repository and a second
public repository. This effort owns the remaining generic reliability and
contract-hardening delta; it does not restart that completed campaign. The
selected reusable ownership and integration seams are recorded in
[`generic-reviewer-contract.md`](generic-reviewer-contract.md).

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
- [ ] Make the generic-vs-consumer ownership boundary and the
  reviewer-agent/result-applicator seam citable through
  [`generic-reviewer-contract.md`](generic-reviewer-contract.md).
- [ ] Keep declaration schema, discovery, and profile validation owned by the
  existing agent-dispatch registrar; record missing fields there instead of
  defining a reviewer-specific declaration format.

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

### Phase 5 - Close state-model gaps found validating the terminal-reservation fix

Discovered live while validating
[ThomasMichon/copilot-extensions#2082](https://github.com/ThomasMichon/copilot-extensions/pull/2082)
(the fix for #2056: terminal spawn reservations settle/defer instead of
blindly failing on a carried session). These are all below-altitude bugs in
the agent-dispatch/agent-bridge substrate the reviewer/backlog engine sits
on -- they do not require a vision change, only implementation fixes.

- [ ] Fix [#2087](https://github.com/ThomasMichon/copilot-extensions/issues/2087)
  -- `reconcile_reserving` misclassifies a carried-but-unlaunched session as a
  failed launch, burning an attempt that was never actually spawned. Observed
  live: consumed one full attempt out of three before the task self-healed.
- [ ] Fix [#2055](https://github.com/ThomasMichon/copilot-extensions/issues/2055)
  -- stale running ACP tool frames survive process exit and daemon restarts.
  Observed live on session `c1b25b1d-caf`: an `execute` tool call stayed
  "running" for 5+ hours across a daemon restart, and the session flapped
  stopped/running while the recorded frame never advanced, oscillating the
  owning task between `claimed`/`started`/released.
- [ ] Fix [#2057](https://github.com/ThomasMichon/copilot-extensions/issues/2057)
  -- a cold headless task's steer answer clears `awaiting_steer` and sets
  `resume_requested=true` but returns `wake_status=unsupported`, so the cold
  reservation isn't reattached/released without a direct bridge resume.

### Phase 6 - Never supersede another contributor's pull request

Discovered live: the `odsp-web-harness-backlog` repository-issue-loop worker
closed [gim-home/odsp-web-harness#200](https://github.com/gim-home/odsp-web-harness/pull/200)
(authored by a different contributor) and replaced it with its own competing
[#203](https://github.com/gim-home/odsp-web-harness/pull/203), carrying the
fixes forward under its own identity because the original branch could not be
updated. This is a **vision extension**, not a below-altitude bug: the
reviewer vision's non-goals already forbade editing a contributor's branch
without authorization, but did not name outright closure/replacement as an
equally forbidden outcome. Landed as a non-goal addition in
[`visions/plugins/agent-dispatch/reviewer/README.md`](../../../visions/plugins/agent-dispatch/reviewer/README.md)
(2026-09-05 provenance entry) plus a sharpened, private per-repository
`worker_guidance` clause (`odsp-web-harness-issue-loop.json`, dotfiles) adding
the required fallback: leave review feedback, then **durably declare the
dependency** rather than record a vague outcome -- apply a `blocked-on-external-pr`
label to the issue (created on gim-home/odsp-web-harness) plus a comment
naming the exact blocking PR and head SHA, and add that label to the loop's
`exclude_labels` so a labeled issue is never re-queued while the block stands.
This closes the original ask precisely: tagging + a durable label-based
relationship, not just prose, is what keeps a future occurrence from
re-queuing (or re-superseding) the same blocked issue.

- [x] Land the concrete tag-and-exclude mechanism in the private declaration
  (`blocked-on-external-pr` label + `exclude_labels` entry + worker_guidance
  instructing the exact `issue edit --add-label` / cross-reference-comment
  steps).
- [ ] Confirm no other declared repository-issue-loop or reviewer-loop
  registration (across dotfiles) permits or has exhibited the same
  supersession pattern; add the same guard where missing.
- [ ] Consider whether the *generic* `reviewer` recipe itself (not just this
  one repository's private declaration) should refuse a close/merge mutation
  against a PR whose author differs from the acting identity, as a structural
  guard rather than relying on prose alone -- prose guidance was already
  explicit here and was still violated.
- [ ] The label is currently maintainer-removed only (manual unblock). Add an
  automatic recheck: a lightweight periodic pass over
  `blocked-on-external-pr`-labeled issues that reads back the referenced PR
  and removes the label (re-enabling normal triage next occurrence) once that
  PR merges or closes -- so the block clears itself instead of silently
  persisting after the dependency resolves.

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
- [ ] The generic reviewer contract contains no consumer ACL, acting identity,
  review rubric, merge policy, scheduling policy, or organizational telemetry.
- [ ] Reviewer declarations use the existing registrar schema and discovery
  convention without a competing reviewer-specific format.

## Proposal

Build the provider-neutral durable lifecycle first, then adapt existing review
drivers and prove reliability with deterministic interruption and duplication
scenarios.

## Journal

### 2026-08-29 - Kickoff

- Established the generic review ownership, idempotency, steering, recovery,
  and verdict-boundary campaign.

### 2026-09-04 - Reconcile the proven recipe with the active owner

- Kept this effort as the single active owner instead of starting parallel
  reviewer-recipe and registrar efforts.
- Added a focused companion note for the generic-vs-consumer ownership seam,
  the reviewer-agent/result-applicator boundary, and the existing registrar's
  declaration ownership. The archived turnkey effort remains the proof that the
  composed recipe runs; this effort owns only the unresolved reliability and
  contract-hardening delta.

### 2026-09-05 - State-model gaps + PR-supersession found validating #2082

- Landed and deployed
  [ThomasMichon/copilot-extensions#2082](https://github.com/ThomasMichon/copilot-extensions/pull/2082)
  (agent-dispatch 0.1.2-dev19): the #2056 fix -- terminal spawn reservations
  now settle/defer instead of blindly failing on a carried session
  (`SpawnState.DEFERRED`, `BridgeCarriedSessionBusy`). Full suite passed (one
  pre-existing, unrelated `test_fleet.py` failure confirmed on a clean HEAD).
- Live-validated by rearming the previously dead-lettered harness task
  `e4b373...` exactly once: it autonomously progressed
  claimed → started → settled ("productive turn completed") → re-spawned
  across two attempts with zero further manual intervention.
- That live validation surfaced #2087 (attempt 4 auto-failed by
  `reconcile_reserving` before any embody was attempted) and reproduced #2055
  live (a 5+ hour stale tool-call frame on session `c1b25b1d-caf`,
  oscillating task status across a daemon restart). Filed #2087; #2055 and
  #2057 were already tracked and remain open. All three folded into Phase 5
  above as below-altitude implementation gaps -- no vision change needed.
- Separately, while reconciling this effort against the reviewer vision, found
  that the same backlog loop had -- in an earlier occurrence -- closed
  gim-home/odsp-web-harness#200 (a different contributor's PR) and replaced it
  with its own #203. The private `worker_guidance` already said "do not take
  over an existing pull request or branch" *before* the incident, so this was
  a compliance failure against clear prose, not an undocumented gap -- but the
  vision itself had a loophole (closure/replacement is a different action from
  "editing a branch"). Extended the reviewer vision's non-goals (2026-09-05
  provenance entry) and sharpened the private declaration's guidance with the
  required fallback (leave feedback, record `blocked-on-external-pr`, never
  replace). Filed as Phase 6, including the open question of whether the
  generic reviewer recipe needs a structural guard rather than relying on
  prose alone, since prose was already explicit and was still violated once.
- Fixed a stale `See Also` link in the reviewer vision (`turnkey-reviewer-loops`
  no longer exists; repointed to this effort, `review-automation-reliability`,
  which is its actual active realization effort) and added the vision's
  citation to this effort's front-matter.
