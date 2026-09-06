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

### Phase 7 - Harden trusted-tool/credential resolution against environment drift

Discovered live (2026-09-05) operating a downstream reviewer deployment --
the current, most heavily-exercised consumer of this vision's reviewer-loop
shape -- across a multi-hour production incident on a single pull request and
several related ones. Six distinct failures, none
in the review *logic* itself, compounded to keep a reviewer from ever
rendering a verdict:

1. The trusted reviewer tool's `#!/usr/bin/env python3` shebang resolved
   through the *calling process's* ambient `PATH` rather than a pinned,
   verified interpreter. A long-lived `agent-mcp serve` daemon had
   inherited an unrelated tool's venv `bin/` directory ahead of `/usr/bin`
   on its own captured environment (from whatever spawned/restarted it),
   silently redirecting every tool spawn to a Python missing the reviewer's
   real dependencies. 134 retries over 8+ hours, all failing identically,
   before this was traced by hand-replaying the daemon's exact captured
   environment. Fixed downstream by pinning an explicit `PATH` in the
   consumer's bridge config,
   but the *generic* lesson is structural: a trusted tool invoked by
   subprocess exec must not depend on whatever `PATH`/environment a
   long-lived host process happens to have accumulated. It should either
   receive an explicitly pinned environment from the declaration, or
   self-verify its own runtime (interpreter identity, required imports)
   before doing any real work and fail with a specific, actionable
   diagnostic rather than a generic import error that looks identical
   across unrelated causes.
2. A checked-out trusted script's executable bit is not guaranteed to
   survive every checkout/worktree-provisioning path (observed: a mode-only
   git commit landed correctly on the default branch, but every already-materialized
   worktree checkout of the reviewer's trusted-tool directory still carried
   the old `644` mode, because Git only applies a tracked mode change on the
   *next* checkout of that path, not retroactively to an already-checked-out
   worktree). A trusted tool the reviewer directly executes should verify
   (and where safe, restore) its own required file mode at the point of
   invocation rather than assuming a git-tracked mode change alone is
   sufficient. Fixed downstream in the consumer's own repository
   (also switched the invocation itself off a `python <cwd-relative-path>`
   shebang-adjacent pattern that additionally depended on the session's
   working directory).
3. `agent-mcp`'s `CliTransport` (the `type: cli` bridge transport a
   repository-owned trusted tool is exposed through) computed its spawn
   environment -- including an auth injector's acquired credential -- once
   and cached it for the life of the transport object, **even on a failed
   acquisition**. Under `agent-mcp serve`'s warm pool, one transport is
   reused across many calls over hours, so a single transient credential
   hiccup permanently disabled that bridge's auth injection until the
   daemon restarted, with no further retry ever attempted. Fixed upstream
   in `agent-mcp` 0.2.0-dev95
   ([ThomasMichon/copilot-extensions#2135](https://github.com/ThomasMichon/copilot-extensions/pull/2135)):
   the transport now re-applies the injector's `child_env()` on every call,
   relying on the injector's own cache/TTL/`invalidate` semantics instead of
   a second, unsafe layer of caching. The generic principle: nothing in a
   *transport* or *pool* layer should cache the outcome of a credential
   acquisition beyond what the credential's own injector already owns.
4. **This is a direct violation of this vision's own `bounded-verdict-reliability`
   feature, not merely an implementation gap.** The feature states every
   attempt -- the initial try and every retry -- counts toward the rolling
   attempt budget. The downstream consumer's idle-round evaluator
   had two early-return paths (a failed session-suspend, an incomplete
   verdict-payload read) that returned a bare retry *without* charging the
   attempt budget at all, so a structural failure (like #1 above) could
   retry unboundedly while the budget only ever protected the one failure
   mode it was least likely to see. Fixed downstream in the
   consumer's own repository: every no-verdict outcome -- including a failure to even suspend/start --
   now charges one deduped attempt. Any generic implementation of
   `bounded-verdict-reliability` must charge the budget from a single choke
   point that every non-verdict exit passes through, not from a per-branch
   opt-in.
5. The materialized-CLI-fallback fleet (the resilience path when a live MCP
   session degrades) is only refreshed when `agent-mcp`'s own runtime
   version changes, not when the bridge's declared config/tool content
   changes. A fix landing in the bridge config was therefore invisible to
   an already-materialized fallback fleet until forced with `--force` --
   meaning the *resilience path itself* silently carried the same defect as
   the primary path it exists to protect against, for as long as the outage.
   Cross-referenced against the open "change detection (schema drift)" item
   in the downstream consumer's own MCP-to-CLI migration effort; not yet
   fixed as of this writing.
6. A reviewer's own bootstrap/config fix cannot be reviewed by that same
   reviewer while the bug is live -- observed twice today in the
   downstream consumer, each requiring an ad hoc administrative
   approve-and-merge to break the deadlock. A generic reviewer-loop
   deployment should document (or better, provide) a sanctioned,
   audited self-bootstrap override authority for exactly this
   self-referential case, rather than leaving it to an improvised admin
   action each time it recurs.

- [ ] Specify that a repository-owned trusted tool exposed through the
  `type: cli` bridge transport receives its execution environment either
  fully pinned by the declaration or self-verified at invocation time
  (interpreter identity + required imports), never solely inherited from
  whatever long-lived host process happens to spawn it.
- [ ] Specify that a trusted tool's required executable bit (or equivalent
  platform permission) is verified -- and restored where safe -- at the
  point the reviewer loop invokes it, not assumed from a git-tracked mode
  alone.
- [ ] Confirm the `agent-mcp` `CliTransport` fix (dev95,
  ThomasMichon/copilot-extensions#2135) as the generic pattern: no
  transport/pool layer may cache a credential-acquisition *outcome* beyond
  what the credential's own injector already owns.
- [ ] Audit every `bounded-verdict-reliability` implementation path (current
  and any future generic recipe) for a single charge-the-attempt choke
  point that every non-verdict exit -- including a failure to start or
  suspend the reviewer process -- passes through. No branch may return an
  unproductive retry without charging the budget.
- [ ] Extend the materialized-CLI-fallback fleet's staleness detection to
  the bridge's declared config/tool content (a hash), not just the
  `agent-mcp` runtime version, so the fallback path cannot silently carry
  a fixed-upstream defect for the life of an outage. Coordinate with
  the downstream consumer's own MCP-to-CLI migration effort, which owns the
  open "change detection (schema drift)" item this closes.
- [ ] Document (or implement) a sanctioned, audited reviewer self-bootstrap
  override: an explicit authority path for landing a fix to the reviewer's
  own trusted config/tooling when the reviewer cannot review itself,
  distinct from and narrower than an ordinary human/admin override.

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

### 2026-09-05 - Phase 7: six live environment-drift failures from a downstream reviewer deployment

- Added Phase 7 after a multi-hour production incident
  (a downstream reviewer deployment, its production instance) surfaced six
  distinct trusted-tool/credential/pool failures, none in the review logic
  itself: ambient-`PATH`-dependent shebang resolution through a long-lived
  daemon's inherited environment, a checked-out executable bit not
  surviving worktree provisioning, `agent-mcp`'s `CliTransport` caching a
  transient auth failure for the life of a warm-pooled session, two
  no-verdict retry paths bypassing the `bounded-verdict-reliability` attempt
  budget entirely, and a materialized-CLI-fallback fleet whose staleness
  detection only tracks the `agent-mcp` runtime version, not the bridge's
  own declared content.
- Item 4 is the one direct vision violation (this effort's own
  `bounded-verdict-reliability` feature states every attempt counts, and
  two branches did not); the rest are below-altitude implementation
  hardening, matching Phase 5's classification pattern.
- Four of the six failures are already fixed and merged in the
  downstream consumer's own repository and in
  `agent-mcp` itself (dev95,
  ThomasMichon/copilot-extensions#2135); this phase captures the generic
  principle each fix implies for any future generalized reviewer recipe,
  plus the two items not yet addressed anywhere (fallback-fleet content
  staleness, a sanctioned reviewer self-bootstrap override).

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
