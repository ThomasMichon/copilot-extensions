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
- **Public coordination token:**
  [ThomasMichon/copilot-extensions#2357](https://github.com/ThomasMichon/copilot-extensions/issues/2357)
  (Phase 9 implementation). Slices land as separate PRs referencing this
  issue.

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

**Note (2026-09-10):** Phases 1-3's state list, idempotency, and steering
items are superseded in scope by
[Phase 9](phase-9-state-machine-architecture.md)'s formal three-machine
model -- their unchecked items are re-validated and folded into Phase 9's
declarations rather than implemented standalone. Left in place as the
original record.

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
several related ones. Six distinct failures, none in the review *logic*
itself, compounded to keep a reviewer from ever rendering a verdict:

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
2. A checked-out trusted script's executable bit is not guaranteed to be
   current across every independent worktree a reviewer-loop deployment
   maintains (observed: a mode-only fix landed correctly on the default
   branch, but every pre-existing worktree still pinned to an older commit
   -- i.e. never re-synced past the fix -- kept serving the stale `644` mode
   from its own checked-out tree). A trusted tool the reviewer directly executes should verify
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

### Phase 8 - Contribution candidates from a mature downstream deployment

A downstream reviewer-loop deployment has run this recipe's shape at
sustained, high-frequency production volume for several weeks (many
reviews/day, sustained incident load), and has patched its own local
lifecycle/scheduler/pool implementation in parallel rather than adopting
the generic runtime end-to-end. A structured, file-by-file comparison of
that deployment's patched behavior against this plugin's registrar, recipe
driver/registry, and worktree/claim substrate found a set of concrete
behaviors present downstream and absent (or weaker) here. This phase
records those as candidate contributions -- **evidence and candidates
only; no implementation is proposed or begun by this phase**, consistent
with this effort's own coordination gate.

**Candidate contributions (downstream ahead; each is a real, patched
behavior, not a hypothesis):**

- [ ] A rolling, windowed review-attempt budget reserved through a single
  choke point that every non-verdict exit path -- including a failure to
  even start or suspend the reviewer process -- passes through, with an
  explicit provisional-reserve/commit/cancel lifecycle so a failed launch
  never silently consumes or bypasses the budget. This is the generalized
  form of the gap Phase 7's item 4 already names for this effort's own
  `bounded-verdict-reliability` feature; the downstream implementation is a
  working reference for the choke-point shape, not a new requirement.
- [ ] An append-only, per-review-round event ledger (candidate arrival,
  claim, dispatch, retry/redrive, suspend/resume, recovery, verdict
  completion, merge, close, cleanup) keyed by a stable round identity, with
  reason-code classification on cancellation/abandonment outcomes (the
  downstream deployment found and closed a large unlabeled-cancellation gap
  in its own reason-code coverage by adding this classification -- worth
  generalizing rather than re-discovering per consumer).
- [ ] Base-only/same-head/unchanged-substance change detection: a
  rebase-stable content fingerprint that lets the scheduler distinguish "the
  base moved but the submitter's actual diff did not" from a genuine
  substantive revision, so a base-only refresh does not re-trigger a full
  review or consume a fresh attempt.
- [ ] Stale-approval-versus-current-head classification as a first-class
  scheduling input: an approval recorded against an older head is not
  merge authority for a newer one, independent of and prior to any provider
  merge-readiness check.
- [ ] A separate merge-authority lane for an official/provider-native
  approval versus a recipe-internal, candidate-fenced approval marker --
  today's `land=self`/`land=author` split is an ownership mode, not an
  approval-*source* distinction, and the two are conflated in the generic
  contract.
- [ ] WIP/draft/hold and unresolved-blocking-thread gating evaluated before
  a review or merge action is taken, not left entirely to the consumer.
- [ ] Worktree-pool reuse: force-clean/reset semantics (verify-and-restore,
  not merely detect) applied to a candidate worktree before reuse, and
  explicit tolerance for a dirty-status result that carries zero real
  content difference (a mode-only permission-bit change with no line
  changes was recently observed making this pool-reuse tooling refuse a
  cleanly-mergeable worktree downstream -- worth a normalization rule here
  rather than per-consumer workarounds).
- [ ] A long-running relay/host-substrate liveness and health-fencing
  pattern (bind-address/loopback-scope validation, an explicit health/live
  endpoint, refuse-unsafe-startup) for any generic runtime component meant
  to run continuously across restarts -- currently no equivalent exists in
  this plugin family for a component playing that role.

**Open design question -- not resolved by this phase:**

- [ ] **Conflict handling has two incompatible designs in active production
  use**, and this phase does not pick a winner. This plugin's existing
  conflict-resolution recipe rebases and force-pushes the pull request's
  own branch to resolve a conflict. The downstream deployment's patched
  scheduler deliberately does the opposite: on a confirmed conflict it
  creates **no** conflict-resolution worker at all, hands the blocker back
  to the submitter, and reviews only the submitter's own corrected head.
  Both are real, intentional, currently-deployed safety postures (the
  downstream rationale: never mutate a contributor's branch on their
  behalf, even to fix it) rather than one being an unfinished version of
  the other. Reconciling this is a maintainer decision, not a
  comparison-agent one -- surfaced here so it isn't silently decided by
  whichever side happens to land a PR first.

**Explicitly out of scope for this phase (found during the comparison,
not actionable as a port):**

- Composite-cursor pagination over the review-round ledger and durable
  protection against one contributor's pull request being closed/replaced
  by a different, competing pull request targeting the same lineage were
  both found **absent on both sides** of the comparison (the latter is
  Phase 6's own still-open structural-guard question above). Neither is a
  "downstream is ahead" contribution candidate; both are genuine shared
  gaps worth their own future phase or issue, not folded into this one.

### Phase 9 - Foundational state-machine architecture

Full design in
[`phase-9-state-machine-architecture.md`](phase-9-state-machine-architecture.md).
Phases 1-8 accumulated real hardening by patching each incident as it was
found (Phases 5-7) and by comparing against a mature downstream
deployment's own independent patches (Phase 8). That comparison confirms a
pattern: the recurring failure classes share one root cause -- no single,
explicit model of what state a review/task/session is in and what the
valid next move is. This phase defines three coupled state machines
(provider/PR-target, dispatch task, bridge/session), a declarative
per-provider capability model (approval authority, notification fidelity,
conflict policy), the board-game ownership contract (supervisor
creates/resumes/suspends/ends; the agent plays its task's current state
and requests the next; evaluators/emitters actually move it), a
recovery-mode taxonomy per transition (self-recovering / safe-retry /
self-repair), and a deterministic simulation/test track for the gnarliest
interleavings. It re-seats every Phase 8 candidate as a behavior of one of
the three machines rather than an independent patch, and resolves Phase
8's open conflict-handling question as a policy-gated default
(hand-back unless a provider/repository's declared policy explicitly
permits branch mutation).

- [ ] See the sub-doc's own Plan checklist; this phase's design must clear
  its own review gate before any implementation begins.

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
- [ ] Phase 9's full simulation/test track
  ([`phase-9-state-machine-architecture.md`](phase-9-state-machine-architecture.md)
  § Simulation and test track) passes: every listed interleaving reaches
  the correct next state regardless of ordering, replays idempotently, and
  takes the recovery mode its transition is classified under.

## Proposal

Build the provider-neutral durable lifecycle first, then adapt existing review
drivers and prove reliability with deterministic interruption and duplication
scenarios.

## Journal

### 2026-09-10 - Phase 9: second slice landed (provider/PR-target machine)

- Landed the second implementation slice: the provider/PR-target state
  machine declared as checkable data tables
  (`plugins/agent-dispatch/src/agent_dispatch/provider_state_machine.py`).
  Approval status and mergeability are declared as two independent
  dimensions rather than one flattened enum -- they move on separate
  schedules and combining them would make a reachability check assert
  nothing but "the cross product was declared." Hold (draft/WIP/blocking
  threads) is a flag set plus a pure `merge_blocked_by_hold` predicate, not
  a third state dimension, since holds co-occur and clear independently of
  both machines. Both dimensions reuse the task machine's `RecoveryMode`
  taxonomy rather than redeclaring it.
- Declared the per-provider capability table (GitHub, Azure DevOps, Gitea)
  covering automated-identity approval eligibility, per-event-type
  notification fidelity, and conflict policy -- every provider defaults to
  Phase 9's resolved **hand-back** conflict policy; branch-mutating
  (rebase + force-push) is only reachable via an explicit
  `(provider, repo)` entry in `REPOSITORY_OVERRIDES`. `capability_for`
  resolves provider defaults with repo overrides applied, raising on an
  undeclared provider rather than silently defaulting.
- Added structural tests
  (`plugins/agent-dispatch/tests/test_provider_state_machine.py`, 31
  passing) proving both dimensions fully reachable and exit-checked, every
  provider declaring fidelity for every event type, every provider
  defaulting to hand-back, and the override/no-override capability
  resolution paths. Deterministic, in-process fixtures -- no live
  provider, no adapter code yet, per this phase's declared slice order.
- Next slice: the bridge/session state machine, coordinating with the
  companion agent-bridge vision's verb-vocabulary work rather than
  re-declaring bridge verbs independently.

### 2026-09-10 - Phase 9: opened the implementation coordination issue; first slice landed

- Opened
  [ThomasMichon/copilot-extensions#2357](https://github.com/ThomasMichon/copilot-extensions/issues/2357)
  as the effort's coordination-gate "dedicated issue," replacing the
  temporary reviewed-plan-PR token now that Phase 9's design has merged.
  Implementation slices reference this issue instead of proceeding under
  the temporary token.
- Landed the first implementation slice: the dispatch task state machine
  declared as a checkable data table
  (`plugins/agent-dispatch/src/agent_dispatch/task_state_machine.py`),
  reconciled against the real, already-implemented
  `agent_dispatch.queue.Status` states (not a fresh prototype) -- Phase
  1's reviewer-flavored state list turns out to be a specific consumer's
  projection of this same eight-state machine, not a separate design.
  Added structural tests
  (`plugins/agent-dispatch/tests/test_task_state_machine.py`) proving the
  table itself is sound: every state reachable from `proposed`, no
  non-terminal state lacks a declared exit, no terminal state has one, and
  every transition carries exactly one recovery-taxonomy tag. These are
  deterministic, in-process fixtures with no live provider or
  infrastructure dependency -- the intentional first slice, ahead of any
  scenario/interleaving fixture or live-provider validation.
- Next slices: the provider/PR-target and bridge/session machines, the
  control-flow coupling rules, then the scenario-level simulation track
  from the sub-doc, each as its own reviewed PR against #2357.

### 2026-09-10 - Phase 9: fold in a corrected bridge-machine liveness model

- A downstream deployment's operator design conversation (2026-09-10) found
  a load-bearing bug class in exactly the bridge/session machine Phase 9
  already scopes: a resume path 404'd whenever a target-liveness cache
  (a discovery index) had no entry for the target, even though a live,
  authoritative existence/liveness check further down the same code path
  would have classified it correctly. The cache was accidentally load-
  bearing as the *authority* on target existence, rather than the
  performance shortcut it was meant to be.
- Folded the corrected model into Phase 9's bridge/session machine
  section: liveness is always a **live, three-tier read** (hot / warm /
  cold), never gated by cache membership; a cache miss or stale entry
  falls through to a live check instead of failing. Re-seated the
  resulting verb shape into the machine spec: one universal "resume"
  keyed by worktree or repo/agent identity, returning hot/warm/cold as an
  *observed result* rather than a caller precondition; a narrower
  exact-session-id resume; a declared error for "create fresh" against a
  single-head target instead of a separate reclaim escape hatch; and a
  distinct "discard and roll forward" handoff gesture.
- Added two simulation-track fixtures (stale/empty liveness cache;
  already-hot target refusing silent double-attach) and a self-repair
  taxonomy example for the cache-vs-live-check gap.
- This is design-only, same as the rest of Phase 9; it also matches the
  companion agent-bridge vision's own concurrent revision on this point,
  so this effort's bridge-machine section and that vision stay in sync
  rather than diverging.

### 2026-09-10 - Phase 9: architecture-first redirect before acting on Phase 8's candidates

- Operator direction, following Phase 8's landing: do not start implementing
  Phase 8's candidates piecemeal. Do the state-machine design work first --
  agent-dispatch and its consumers (a downstream reviewer deployment among
  them) keep hitting weird, un-resumable states because there is no
  enforced model of what state a review/task/session is in and what the
  expected next move is, across a distributed system with no built-in
  transaction (PR updates and verdicts arrive separately, the base moves
  constantly, providers offer inconsistent notification fidelity, and
  different providers grant different actors different rights).
- Captured as Phase 9 (own sub-doc, linked above): three coupled state
  machines (provider/PR-target, dispatch task, bridge/session) with
  explicit control-flow coupling between them; a declarative
  per-provider/per-repository capability model (approval authority,
  notification fidelity, conflict-handling policy); the board-game
  contract (an agent asks "where am I" / "what are my valid next moves";
  the dispatch supervisor owns spawn/resume/suspend/end via the bridge; the
  agent plays its task's current state and requests transitions;
  evaluators/emitters actually move the task, including mid-flight
  steering); a recovery-mode taxonomy (self-recovering / safe-retry /
  self-repair) applied to every declared transition; and a deterministic
  simulation/unit-test track covering the gnarliest interleavings (bridge
  mid-version-update, a detached session host, a changed discovered port, a
  supervisor end-of-lifing a runtime version, base-only vs. substantive PR
  movement, out-of-order verdict/update delivery, steer-vs-transition
  races).
- Re-seated every Phase 8 candidate as a behavior one of the three machines
  must express, rather than a parallel patch list.
- Resolved Phase 8's open conflict-handling question as a **policy-gated
  default**: hand-back (never mutate a contributor's branch) unless a
  provider/repository's declared policy explicitly permits automated
  branch mutation, in which case the existing rebase/force-push recipe
  remains available as that opted-in mode.
- This effort's bridge/session state-machine work explicitly depends on,
  and must not duplicate, the concurrent agent-bridge vision work
  clarifying that plugin's verb vocabulary; Phase 9 defers to it as the
  authoritative source once it lands.
- Phase 9 is design only; no implementation begins here, consistent with
  this effort's coordination gate. It must clear its own review before any
  Phase 1-8 item resumes under its model.

### 2026-09-10 - Phase 8: contribution-candidate comparison against a mature downstream deployment

- Added Phase 8 after a structured, file-by-file comparison between a
  downstream reviewer-loop deployment's patched lifecycle/scheduler/pool
  implementation and this plugin's registrar, recipe driver/registry, and
  worktree/claim substrate. The downstream deployment has run this recipe's
  shape at sustained high volume for several weeks and independently
  hardened several behaviors this plugin does not yet generalize:
  choke-point attempt-budget accounting, a per-round event ledger with
  reason-code classification, base-only/unchanged-substance detection,
  stale-approval-vs-current-head classification, an official-vs-candidate
  approval-authority split, WIP/hold gating, worktree-pool force-clean with
  content-aware dirty tolerance, and a relay/health-fencing pattern for a
  long-running component.
- Recorded, and deliberately did **not** resolve, a genuine two-sided design
  conflict found during the comparison: this plugin's conflict-resolution
  recipe rebases and force-pushes the pull request's own branch, while the
  downstream deployment's hardened scheduler refuses to ever do that,
  handing a conflict back to the submitter instead. Both are intentional,
  currently-deployed postures; picking one is a maintainer call, flagged as
  an open question rather than silently decided by this phase.
- Explicitly excluded two comparison findings that were absent on *both*
  sides (composite-cursor ledger pagination; durable protection against one
  contributor's pull request being closed/replaced by a competing one) --
  those are shared gaps, not downstream-ahead contribution candidates, and
  the latter duplicates Phase 6's still-open question.
- This phase is evidence and candidates only, per this effort's own
  coordination gate (no implementation begins under the current token).



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
