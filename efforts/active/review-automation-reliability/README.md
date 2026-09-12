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
  (Phase 9 implementation, now complete) and
  [ThomasMichon/copilot-extensions#2423](https://github.com/ThomasMichon/copilot-extensions/issues/2423)
  (Phase 10 implementation). Slices land as separate PRs referencing the
  issue for their phase.

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

- [x] See the sub-doc's own Plan checklist; this phase's design must clear
  its own review gate before any implementation begins. All checklist
  items are now checked (design, all four declared machines/relations,
  full simulation/test track, and the Phase 8 re-validation pass). Five
  Phase 8 candidates remain intentionally deferred as conceptual-only
  follow-up design work (see the sub-doc's "Re-validation pass" table);
  this phase's own scope boundary ("designs the model... does not
  implement it end to end") is otherwise satisfied.

### Phase 10 - Wire the declared state machines into the live runtime

Full design in
[`phase-10-live-wiring.md`](phase-10-live-wiring.md).
Phase 9 deliberately declared four machines (task, provider, bridge,
spawn-reservation) as pure data plus deterministic, in-process
structural/simulation tests -- it never touched `queue.py`'s actual
runtime behavior. This phase closes that gap: it makes the declared
tables the **single source of truth** the live code executes against,
rather than a parallel model that tests merely assert still matches.
Doing this is what makes this effort's remaining Validation Plan items
(concurrent claim ownership, duplicate/reordered delivery, restart-at-
any-boundary resumption, stale-revision blocking, etc.) checkable against
*actual* running behavior instead of only the declared model.

- [ ] See the sub-doc's own Plan checklist; each wiring slice lands only
  once its structural tests (already-declared, from Phase 9) plus new
  live-behavior tests both pass, so no wiring slice can silently diverge
  from the machine it claims to implement.

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
- [x] Phase 9's full simulation/test track
  ([`phase-9-state-machine-architecture.md`](phase-9-state-machine-architecture.md)
  § Simulation and test track) passes: every listed interleaving reaches
  the correct next state regardless of ordering, replays idempotently, and
  takes the recovery mode its transition is classified under. All ten
  scenarios landed and pass
  (`plugins/agent-dispatch/tests/test_simulation.py`,
  `test_simulation_revision.py`, `test_simulation_deferred_scenarios.py`).

## Proposal

Build the provider-neutral durable lifecycle first, then adapt existing review
drivers and prove reliability with deterministic interruption and duplication
scenarios.

## Journal

### 2026-09-12 - Componentize supervisor.py: extract spawn_factories.py

- Split `plugins/agent-dispatch/src/agent_dispatch/supervisor.py` (4,169
  lines, the largest non-generated offender on
  `tools/module-size-baseline.json` after `queue.py`) by extracting the
  entire pre-`class Supervisor` block of default liveness/verdict/nudge/
  redrive/conclusion callables and the three embody-backend factories
  (`make_embody_spawn`, `make_headless_spawn`, `make_label_routed_spawn`,
  `make_redrive_sender`) into a new `spawn_factories.py`. Chosen as the
  safe first cut per the operator's standing componentization
  instruction: every function in that block is either a pure default
  closing only over its own arguments, or a factory returning a closure
  -- none of it touches `Supervisor` instance state (`self`), so nothing
  needed to change about *how* the class consumes these names.
  `supervisor.py` re-exports every moved name (types, constants,
  functions), so all existing call sites (`__main__.py`'s lazy
  `from .supervisor import make_embody_spawn, ...`) and every existing
  test that monkeypatches `agent_dispatch.supervisor.<name>` (e.g.
  `test_cli.py`'s `monkeypatch.setattr(sup_mod, "make_embody_spawn", ...)`)
  are unaffected -- Python resolves a bare name against the *current*
  module globals at call time, and monkeypatching a module attribute
  works identically whether the underlying implementation lives in that
  module or was imported into it.
- Two things this split's tests caught that the embody.py split's
  simpler case didn't have: (1) `test_supervisor.py` patches
  `supervisor_module.Path.stat` directly on the `pathlib.Path` **class**
  (not a module attribute) to simulate a stat failure inside
  `_target_directory_missing` (which moved to `spawn_factories.py` and
  imports its own `Path`) -- patching the class object affects every
  importer of `pathlib.Path` identically, so this worked unmodified once
  `Path` was re-imported into `supervisor.py` too (kept as a deliberate
  re-export, `# noqa: F401`, since tests reference it by name); (2) two
  type aliases (`LocalColdFn`, `FleetColdFn`) were miscounted during the
  initial line-range extraction and briefly missing from
  `spawn_factories.py` -- caught immediately by `ImportError` on the next
  full-suite run, fixed by adding them.
- `supervisor.py` is now 3,422 lines (still baselined -- nowhere near the
  1,000-line cap yet, `queue.py` and the rest of `supervisor.py` itself
  remain the next targets) but the baseline entry was refreshed downward
  (4,169 -> 3,422) so it never silently re-widens.
  `spawn_factories.py` is 605 lines, itself under the cap with no
  baseline entry.
- Added `plugins/agent-dispatch/tests/test_spawn_factories.py` (9 tests):
  a lightweight import-guard suite mirroring `test_embody_prompts.py`'s
  pattern -- confirms the new module's own public API (parse helpers,
  the three spawn factories, `make_redrive_sender`,
  `SpawnPreparationRetained`) is directly importable and behaves
  correctly independent of the `supervisor` facade, since the underlying
  behavior is already exhaustively covered through that facade in
  `test_supervisor.py`/`test_cli.py`/`test_fleet.py`.
- Full `agent-dispatch` suite (2,738 tests across the runner's 5
  sub-suites) passes via `tools/run-plugin-tests.py agent-dispatch`;
  zero regressions. Bumped agent-dispatch 0.1.2-dev83 -> dev84 across
  `plugin.json`, `pyproject.toml`, and `marketplace.json`.
- Phase 10 item 4's liveness-probe wiring (thread 2 of the last handoff)
  is **not** done this leg -- `supervisor.py` still has substantial
  further splitting ahead of it before its `local_body_verdict_fn`
  call sites are a low-risk place to wire in
  `bridge_liveness_probe.local_body_liveness_probe`. This leg's gate was
  satisfied by the module-split thread alone, per the prior handoff's
  explicit "either is a legitimate stopping point" allowance.

### 2026-09-11 - Phase 10 item 4: componentize embody.py to unblock call-site wiring

- Split `plugins/agent-dispatch/src/agent_dispatch/embody.py` (1,205 lines,
  over the repo's module-size cap) by extracting the two large, pure
  autopilot seed-prompt builders (`autopilot_worker_prompt`,
  `fleet_autopilot_worker_prompt`, ~250 lines together) into a new
  `embody_prompts.py`. Chosen as the safest possible first cut: both
  functions are pure string builders with no shared state, no subprocess/
  network I/O, and nothing any test mocks -- verified directly (existing
  tests monkeypatch `embody.autopilot_worker_prompt` itself, which still
  works after the move since the re-exported name is a normal binding in
  `embody.py`'s own module globals, and `embody.py`'s own callers resolve
  it via bare-name lookup against those same globals at call time).
  `embody.py` re-exports both names, so all 9 existing call sites and
  every existing test are unaffected -- zero test changes required.
- `embody.py` is now 989 lines (**below the cap without a baseline
  entry at all** -- graduated via `--refresh-baseline`, not merely
  grandfathered). `embody_prompts.py` is 263 lines.
- Added `plugins/agent-dispatch/tests/test_embody_prompts.py` (2 tests):
  a lightweight import guard confirming the new module's own public API
  is directly importable, independent of the facade (the functions'
  actual behavior is already thoroughly covered through
  `embody.autopilot_worker_prompt`/`fleet_autopilot_worker_prompt` in
  `test_embody.py`/`test_fleet.py`).
- Full `agent-dispatch` suite (769 tests) passes via
  `tools/run-plugin-tests.py agent-dispatch`; zero regressions. Bumped
  agent-dispatch's version to 0.1.2-dev83.
- This unblocks item 4's next slice: `embody.py` has headroom again for
  the call-site wiring (`bridge_liveness_probe.local_body_liveness_probe`
  into a real resume/reconciliation path) that a prior slice deferred
  specifically because `embody.py` had none. `supervisor.py` (4,169 lines)
  is a separate, much larger componentization target -- not attempted in
  this slice.

### 2026-09-11 - Phase 10 item 4: corrected the AHP misconception + first slice (liveness probe)

- **Correction:** item 4's earlier text said it was "coordinated with"
  agent-bridge's own verb-vocabulary convergence -- conflating it with the
  `agent-bridge-ahp-convergence` effort (Status: Draft), which is actually
  about exposing an *external* Agent Host Protocol surface, an unrelated
  concern. Verified directly: `embody.local_body_verdict`/
  `fleet_body_verdict` (`agent-bridge --json status <session>`) are
  already a real, in-production liveness read -- the exact same read item
  5's spawn-consistency sweep already calls. Item 4 was never actually
  blocked; the doc's framing was simply wrong. Corrected in
  `phase-10-live-wiring.md`.
- Added `plugins/agent-dispatch/src/agent_dispatch/bridge_liveness_probe.py`:
  `local_body_liveness_probe(session_id)`, a real HOT/WARM/COLD read via
  `agent-bridge --json status <session_id>` (this machine's own daemon).
  Maps agent-bridge's own `SessionStatus` values
  (`plugins/agent-bridge/src/agent_bridge/models.py`) directly, since that
  distinction already exists in agent-bridge's real session model and is
  finer than the tri-state `local_body_verdict`/`fleet_body_verdict`
  collapse to: `running` -> HOT (a turn is actively executing --
  attaching a second controller now would race it), `idle`/`created`/
  `starting` -> WARM (alive, no turn in flight, safe to reattach),
  `stopping`/`stopped`/`failed`/`ended` -> COLD. Every ambiguous case
  (not-found, transport failure, unparseable output, an unrecognized
  status value) resolves to HOT rather than being guessed as WARM or COLD
  -- refusing an unnecessary resume is always safe; wrongly resolving WARM
  or COLD risks a double-attached controller or an orphaned duplicate
  spawn. Never raises.
- Deliberately does **not** wire this probe into a real resume/
  reconciliation call site: every plausible call site (`supervisor.py`,
  `embody.py`) is already at its grandfathered module-size ceiling. Adding
  a call there means splitting one of those modules first or a deliberate,
  reviewed widening -- left as a named follow-up, not smuggled into this
  slice.
- Added `plugins/agent-dispatch/tests/test_bridge_liveness_probe.py` (21
  tests): every declared status value's mapping, every failure/ambiguity
  path (not-found exit, other non-zero exit, empty/unparseable/non-dict
  output, timeout, `OSError`, no resolvable launch prefix, empty session
  id) resolving to HOT, and a case-insensitivity/whitespace check on the
  status string.
- Full `agent-dispatch` suite passes via `tools/run-plugin-tests.py
  agent-dispatch`. `tools/check-module-size.py` passes.
- Item 4 remains open (call-site wiring + the fleet/SSH variant are
  follow-up slices), but is now unblocked and has a real, tested
  component, same shape as item 3's slices.

### 2026-09-11 - Phase 10 item 3 complete: poll loop + webhook receiver (fifth slice)

- Added `PRObservationStore.tracked_keys()`: every `(repo, number)` the
  store currently holds, the poll-fallback loop's iteration set. A PR
  starts being tracked the moment anything records its first observation;
  there is no separate watch-list to seed.
- Added `plugins/agent-dispatch/src/agent_dispatch/pr_review_poll_loop.py`:
  `run_poll_cycle(store, observe, repository_tiers, now)` -- iterates
  every tracked PR, refreshes whichever are due per
  `pr_polling_policy.poll_due`, and persists via `record_observation`.
  Never discovers new PRs on its own.
- Added `plugins/agent-dispatch/src/agent_dispatch/producers/
  github_pr_review_webhook.py`: a GitHub-specific FastAPI receiver
  (deliberately separate from the existing forge-neutral
  `producers/webhook.py`, which only handles PR-merge). Recognizes
  `pull_request`/`pull_request_review`/`pull_request_review_thread`/
  `check_suite`/`check_run` events via `extract_pr_ref`, verifies GitHub's
  own `X-Hub-Signature-256` HMAC (distinct from the bearer-token
  `inbound_token` the other webhook uses -- GitHub itself never sends a
  bearer token), and on any recognized event re-fetches full state through
  an injected `observe` callable rather than trying to reconstruct
  `reviewDecision`/etc. from the webhook body -- those are GraphQL-only
  aggregates the REST webhook payload does not carry. This also makes a
  late/duplicate/out-of-order delivery harmless: every delivery just
  triggers a fresh, idempotent re-observation.
  - Hit and fixed a real FastAPI/annotations gotcha while wiring this:
    with `from __future__ import annotations` in effect, a route handler's
    `request: Request` parameter failed to resolve (FastAPI misclassified
    it as an unknown query parameter, 422 on every request) because
    `Request` was imported *locally* inside `build_app` -- `get_type_hints`
    only sees the *module's* globals, not an enclosing function's locals.
    Fixed by moving the `fastapi` import to module level (documented
    in-line so the next slice doesn't reintroduce the same nested-import
    pattern for a `Request`-typed handler).
- Added `plugins/agent-dispatch/tests/test_pr_review_poll_loop.py` (5
  tests) and `test_github_pr_review_webhook.py` (20 tests, incl. valid/
  invalid/missing-signature and every recognized/unrecognized event shape).
- Full `agent-dispatch` suite passes via `tools/run-plugin-tests.py
  agent-dispatch`. `tools/check-module-size.py` passes (every new module
  is small). Bumped agent-dispatch's version to 0.1.2-dev81.
- **Item 3 is now complete end-to-end**: observe -> evaluate (staleness) ->
  persist, triggered by webhook and backstopped by cadence-scoped polling.
  What's left is deployment, not code: registering/running this webhook
  receiver with a real secret and scheduling the poll-cycle tick against a
  real repository -- both deployment-specific decisions left to whoever
  operates a concrete instance. Item 4 (bridge liveness) remains blocked on
  `agent-bridge-ahp-convergence` (status: Draft).

### 2026-09-11 - Phase 10 item 3: fourth slice, persistent observation store

- Added `plugins/agent-dispatch/src/agent_dispatch/pr_observation_store.py`:
  `PRObservationStore`, a small self-contained SQLite-backed store keyed by
  `(repo, number)` with `get`/`put`/`last_observed_at`, plus
  `record_observation()` -- the glue that reads the stored previous
  observation, evaluates the new one via `pr_revision_evaluator`, persists
  the result, and returns it.
- Deliberately **its own database file, not a new `queue.py` table**: this
  repo now enforces a 1,000-line module-size cap
  (`tools/check-module-size.py`, landed this session), and `queue.py` is
  already grandfathered at its current size. A PR-observation cache has no
  owner/generation/claim semantics in common with `queue.py`'s existing
  task/spawn-reservation/routing-assignment rows, so folding it in would
  be exactly the unbounded single-module growth the guard now exists to
  catch -- a small standalone module is the componentized alternative.
- Concurrency is intentionally minimal (a plain SQLite upsert, no CAS/
  generation fencing): unlike `queue.py`'s multi-worker task rows, exactly
  one process writes a given PR's observation in every deployment this
  targets today (a single coordinator's polling/webhook loop). Documented
  as a scope choice, not an oversight -- add real fencing only if a future
  slice introduces a genuinely concurrent writer.
- Added `plugins/agent-dispatch/tests/test_pr_observation_store.py` (9
  tests): round-trip of every field, overwrite-on-put, independence across
  repos and PR numbers, persistence across store instances against the
  same db path, and `record_observation`'s first-observation and
  staleness-detection behavior (reusing the evaluator's own logic through
  the store).
- Full `agent-dispatch` suite passes via `tools/run-plugin-tests.py
  agent-dispatch`. Bumped agent-dispatch's version to 0.1.2-dev80.
- Item 3 remaining: the real webhook receiver for review/check-status
  events (today's `producers/webhook.py` only handles PR-merge) and the
  loop that actually invokes this machinery on a schedule (poll on
  `pr_polling_policy.poll_due`, call `record_observation` on each
  observation). Item 4 (bridge liveness) is still blocked on
  `agent-bridge-ahp-convergence` (status: Draft).

### 2026-09-11 - Phase 10 item 3: third slice, revision-history evaluator (STALE)

- Added `plugins/agent-dispatch/src/agent_dispatch/pr_revision_evaluator.py`:
  `evaluate_observation(previous, current)` decides whether an
  `APPROVED` status should be corrected to `STALE`, by reusing the
  already-declared `APPROVAL_TRANSITIONS` table directly -- looks up the
  `revision_invalidates_approval` transition by name and checks
  `current.approval_status` against its `from_states` -- rather than
  re-deciding the staleness rule inline. Same "the declared table is the
  actual governing data" discipline Phase 10 items 1/2 already
  established for the task machine.
- A first-ever observation (`previous is None`) is returned unchanged:
  staleness is a property of *two* observations
  (`classify_revision_change`), not something a lone snapshot can
  classify.
- Never applies `revalidate_stale` itself: that recovery is simply
  whatever the provider's own `reviewDecision` already reports on the next
  observation (e.g. `REVIEW_REQUIRED` once someone re-requests review) --
  this evaluator always recomputes from the provider's current raw status,
  never from a locally cached "STALE" flag, so there is nothing to
  explicitly un-stick.
- Added `plugins/agent-dispatch/tests/test_pr_revision_evaluator.py` (9
  tests): first-observation pass-through, unchanged/base-only revision
  leaves `APPROVED` untouched, a substantive change invalidates `APPROVED`
  to `STALE`, a substantive change with an already-non-`APPROVED` current
  status is a no-op (parametrized over `NONE`/`PENDING`/`STALE`), and every
  other observation field is preserved through evaluation.
- Full `agent-dispatch` suite passes via `tools/run-plugin-tests.py
  agent-dispatch`. Bumped agent-dispatch's version to 0.1.2-dev78.
- Item 3 remains open: still needed are the persistent per-(repo, PR)
  state store (a new `queue.py` table, following the plugin's existing
  one-table-per-declared-machine convention) both this evaluator and the
  polling-cadence policy's fallback timer need to actually hold `previous`
  observations across calls, and a real webhook receiver for review/
  check-status events (today's `producers/webhook.py` only handles
  PR-merge).

### 2026-09-11 - Phase 10 item 3: second slice, polling-fallback cadence policy

- Added `plugins/agent-dispatch/src/agent_dispatch/pr_polling_policy.py`,
  resolving the trigger-mechanism design fork the first slice left open.
  Operator direction: webhooks are the primary, low-latency trigger for
  new PR observations; polling only fires as a fallback once a PR's last
  observed state (from any source) is older than a declared interval.
  That interval is scoped by a declared `RepoTier` (not one global
  number), matching the operator's own risk framing: `OWNED_PRIVATE` (5
  min -- a private, single-tenant repo with no shared rate-limit risk),
  `QUICK_COLLAB` (30 min), `PUBLIC_UNOWNED` (60 min, and the default for
  any repository with no explicit tier -- the conservative assumption for
  a repo this identity does not control).
- The repository -> tier mapping is intentionally **not** committed to
  this module: `copilot-extensions` is a public, organization-neutral
  repo, so no specific repository name belongs in its source. The
  mapping is caller-supplied config instead, the same shape
  `provider_state_machine.REPOSITORY_OVERRIDES` already uses (declared
  empty here, populated by whoever deploys it).
- `poll_due(repo, last_observed_at, now, repository_tiers)` is the pure
  decision function: a fresh observation from *any* source (webhook or
  poll) pushes the next poll out, so the fallback timer never fires while
  webhooks keep the state fresh.
- Added `plugins/agent-dispatch/tests/test_pr_polling_policy.py` (16
  tests): tier resolution (declared mapping, undeclared-repo fallback,
  explicit default override), the declared per-tier intervals, interval
  overrides, and `poll_due`'s not-yet-due / due / reset-by-fresh-
  observation / undeclared-repo / invalid-clock-order behavior.
- Full `agent-dispatch` suite (690 tests) passes via
  `tools/run-plugin-tests.py agent-dispatch`. Bumped agent-dispatch's
  version to 0.1.2-dev77.
- Item 3 remains open: still needed are the `Revision`-history evaluator
  (incl. `STALE`), a real webhook receiver for review/check-status events
  (today's `producers/webhook.py` only handles PR-merge), and the
  persistent per-(repo, PR) state store both the evaluator and this
  policy's poll-fallback timer depend on.

### 2026-09-11 - Phase 10 item 3: first slice of the GitHub provider adapter (read-only observer)

- Added `plugins/agent-dispatch/src/agent_dispatch/github_provider_adapter.py`:
  the first slice of item 3 (no prior provider-adapter code existed for
  `provider_state_machine.py` to wire against). `observe_pr_state` is a
  pure function classifying a raw GitHub GraphQL `pullRequest` node into
  the declared `ApprovalStatus` (via `reviewDecision`), `Mergeability`
  (via `mergeable` + the last commit's `statusCheckRollup.state`), and
  `HoldReason` set (`isDraft`, a WIP title/label marker, any unresolved
  review thread) -- plus the raw `Revision` fingerprints (`headRefOid`/
  `baseRefOid`). `GitHubPRAdapter` is the thin `gh`-CLI fetch wrapper,
  mirroring `repository_issue_loops.GitHubProvider`'s injectable-runner +
  identity-verification pattern (a fresh, narrow adapter rather than a
  shared base class -- issue polling and PR review state are different
  read shapes).
- Deliberately scoped as a **read model only**, following Phase 9's own
  "declare/observe first, wire later" sequencing: it does not decide
  `ApprovalStatus.STALE` (that needs a previously-recorded `Revision` an
  evaluator holds across two observations, not a single snapshot -- see
  `classify_revision_change`), does not call any declared transition,
  write anything back to GitHub, or feed a task/coordinator loop. An
  unrecognized `reviewDecision`/`mergeable`/`statusCheckRollup.state`
  value raises (`GitHubPRObservationError`) rather than guessing a state,
  per Phase 9's "never assume the safer state without evidence" rule.
- Added `plugins/agent-dispatch/tests/test_github_provider_adapter.py`
  (39 tests): pure-classification fixtures for every declared enum value
  plus unrecognized-value rejections, hold-combination tests (multiple
  holds co-occurring independently), and `gh`-CLI wrapper tests against a
  fake runner (identity/repo verification order and caching, GraphQL
  error surfacing, non-zero `gh` exit handling) -- no network in any test.
- Full `agent-dispatch` suite (674 tests) passes via
  `tools/run-plugin-tests.py agent-dispatch`. Bumped agent-dispatch's
  version to 0.1.2-dev76.
- Item 3 remains open (`phase-10-live-wiring.md`'s Plan checkbox): this
  slice is the read-only observer only. Remaining slices: an evaluator
  that holds prior `Revision` state and actually drives
  `APPROVAL_TRANSITIONS` (including `STALE`), and wiring the observer into
  a real polling- or webhook-driven loop. Item 4 (bridge liveness) is
  still blocked on `agent-bridge-ahp-convergence` (status: Draft).

### 2026-09-11 - Phase 10 item 5: wire the spawn-reservation consistency sweep into the supervisor

- Added `Supervisor.sweep_spawn_consistency()`
  (`plugins/agent-dispatch/src/agent_dispatch/supervisor.py`): a
  read-only, additive method that gathers real `ACTIVE` spawn-reservation
  rows for the pool, resolves liveness for each carrying a local body
  handle via the same `local_body_verdict_fn` the rest of this module
  already uses, and classifies each against
  `spawn_reservation_machine.classify_consistency` and
  `violating_assignment_groups`, logging any detected anomaly or
  single-assignment violation. It never mutates a reservation or task and
  changes no existing reconciliation method's behavior -- this is
  genuinely additive wiring, not a refactor of `reconcile_reserving`'s
  carefully-tuned control flow.
- Added `spawn_reservation_machine.verdict_to_bridge_state()`: translates
  this module's coarse `live`/`gone`/`unknown` liveness verdict to a
  `BridgeState` (`unknown` maps to `None`/not-applicable rather than
  guessing, per Phase 9's "never assume the safer state without
  evidence" rule).
- Added `plugins/agent-dispatch/tests/test_spawn_consistency_sweep.py`
  (6 tests) and four new tests for `verdict_to_bridge_state` in
  `test_spawn_reservation_machine.py`: a consistent live reservation
  reports zero anomalies; a `SPAWNED` reservation whose liveness read is
  `gone` is detected (the headline `reconcile_reserving`-class anomaly
  this effort exists to catch); a non-local-body or `unknown`-verdict
  reservation is skipped rather than misclassified; the sweep never
  mutates what it classifies; and a simulated single-assignment
  violation is caught (constructed directly, since `reserve_spawn`'s own
  atomic guarantee makes a real one unreachable through normal usage --
  itself a confirmation the invariant holds upstream).
- Full `agent-dispatch` suite (650 tests) passes. Bumped agent-dispatch's
  version to 0.1.2-dev72.
- Ticks item 5's Plan checkbox. Scheduling the sweep on a periodic
  cadence (vs. calling it ad hoc / from an operator command) is left as
  a follow-up decision, noted in the sub-doc.
- **Phase 10 items 1, 2, and 5 are now complete.** Remaining: item 3 (a
  real GitHub provider adapter -- the largest remaining item, expected to
  be its own sequence of slices) and item 4 (bridge liveness wiring,
  which depends on agent-bridge's own verb-vocabulary convergence).

### 2026-09-11 - Phase 10 item 2: idempotent replay for duplicate task-lifecycle requests

- Resolved the item-2 design question raised in the prior correction:
  operator confirmed extending `suspend`/`complete_with_outcome`'s
  existing idempotent-replay pattern to the other five methods, scoped
  narrowly -- a no-op fires only when the task is already sitting in the
  exact target state **and** the existing owner/generation/session
  fences still match, never as a blanket "swallow all errors" change.
  This directly serves the effort's "duplicate request and response
  delivery produces one analysis and at most one submission" Validation
  Plan item.
- Implemented as an opt-in `idempotent_replay: bool` parameter on
  `queue.py`'s `_transition` (default `False`, so any call site not
  explicitly updated keeps today's exact behavior). Enabled for
  `approve`, `start`, `release_suspended`, `abandon`, `yield_task`
  unconditionally, and for `resume` only when
  `adopt_owner_session_id is None` -- a handoff-adoption resume must
  always bump the generation and adopt the new session, so it
  deliberately still raises on a bare replay rather than silently
  dropping that effect.
- Added `plugins/agent-dispatch/tests/test_transition_idempotent_replay.py`
  (10 tests): a replay is a no-op for every enabled method; a wrong-owner
  or wrong-state replay still raises exactly as before; the
  adoption-resume path never no-ops, confirmed both across two real
  handoffs (generation correctly advances) and on a bare replay attempt
  (still raises).
- Full `agent-dispatch` suite (640 tests) passes with zero regressions in
  existing behavior. Bumped agent-dispatch's version to 0.1.2-dev71.
- Ticks item 2's Plan checkbox. Next: item 3 (a real GitHub provider
  adapter) or item 5 (spawn-reservation consistency checks wired into the
  supervisor's reconciliation loop) -- item 4 (bridge liveness) still
  depends on agent-bridge's own convergence.

### 2026-09-11 - Phase 10: declare and wire the missing `yield_task` transition

- Found, while starting item 2's investigation, an eighth `_transition`
  call site that slice 1 missed: `TaskQueue.yield_task` (a worker's own
  deliberate, voluntary give-back of a task on a recoverable snag, e.g. a
  merge conflict) moves `HELD -> QUEUED` but was never declared in
  `task_state_machine.py` at all -- distinct from `requeue_held`'s
  automatic owner-gone reconciliation, which shares the same states but a
  different actor and recovery mode.
- Declared it as its own named transition (`SAFE_RETRY`, since it is a
  deliberate worker action, not a system self-repair) and wired
  `yield_task`'s hardcoded `allowed=Status.HELD, to=Status.QUEUED` to the
  same `_task_transition_spec()` lookup slice 1 introduced. Added a
  regression test (`test_yield_task_is_sourced_from_the_declared_table`)
  matching the pattern the other seven wired methods already use.
- Full `agent-dispatch` suite (630 tests) passes. Bumped agent-dispatch's
  version to 0.1.2-dev70.
- This closes the gap before starting item 2's actual implementation
  (approved direction: extend `suspend`/`complete_with_outcome`'s
  existing idempotent-replay pattern to the other transition methods,
  scoped to exact-target-state + matching identity fences) -- item 2
  needs every real `_transition` call site accounted for first.

### 2026-09-11 - Phase 10 slice 1: wire the task machine into `queue.py`

- Replaced every hardcoded `allowed=`/`to=` pair in `queue.py`'s task-
  lifecycle methods (`approve`, `start`, `suspend`, `resume`,
  `release_suspended`, `abandon`, and `complete_with_outcome`'s local
  `allowed` set) with a lookup against a new
  `task_state_machine.TRANSITIONS_BY_NAME`, via a lazily-imported
  `_task_transition_spec()` helper (avoids a circular import, since
  `task_state_machine` itself imports `Status` from `queue`). The
  declared table is now the actual data these methods execute against,
  not a parallel description of it.
- **Wiring surfaced a real, previously-undetected discrepancy**: the
  declared `complete` transition only named `started` as a legal source,
  but `TaskQueue.complete_with_outcome` has always also allowed
  completing a `suspended` task directly (a suspended task may resolve
  while no worker process is running -- forcing a fake resume/active turn
  solely to reach the terminal state would be worse). Corrected the
  declared table to `frozenset({Status.STARTED, Status.SUSPENDED})` to
  match the real, already-working behavior, per this phase's own rule:
  fix the declared table first, never patch around it in `queue.py`.
- Added
  `plugins/agent-dispatch/tests/test_task_transition_wiring.py` (9
  tests): each monkeypatches one declared transition's `from_states`/
  `to_state` and asserts the corresponding live method's behavior changes
  to match -- proof of genuine wiring, not coincidental parity that could
  silently drift. Confirmed all 35 pre-existing `queue.py` ruff findings
  (unrelated `S101`/`S608`/`RUF100`/`B007`) predate this change via
  `git stash` isolation; none are new.
- Full `agent-dispatch` suite (628 tests) passes unchanged in behavior
  except the now-corrected `complete`-from-`suspended` path, which was
  already the real behavior all along.
- Bumped agent-dispatch's version (plugin.json, pyproject.toml,
  marketplace.json) to 0.1.2-dev68.
- Ticked wiring item 1's checkbox in `phase-10-live-wiring.md`. Next:
  item 2 (make `machine_coupling`'s CAS outcome classification explicit
  in `queue.py`'s own generation/lease fencing).

### 2026-09-11 - Phase 10 opened: wire the declared state machines into the live runtime

Per operator direction: continue driving this effort by wiring Phase 9's
four declared state machines (task, provider, bridge, spawn-reservation)
into the live `agent-dispatch` runtime, so the effort's remaining
Validation Plan items become checkable against actual running behavior
rather than only the declared model. This is genuinely new scope beyond
Phase 9's design-only boundary, not a continuation of it.

- Added a Phase 10 section to this README and the full design in
  [`phase-10-live-wiring.md`](phase-10-live-wiring.md): a five-item wiring
  order (task machine into `queue.py`'s transition call sites, the CAS
  primitive into `queue.py`'s generation fencing, a new GitHub provider
  adapter, the bridge machine into a live agent-bridge liveness read, and
  the spawn-reservation consistency checks into the supervisor's real
  reconciliation loop), ordered by risk: items 1/2/5 are refactors/
  additions against existing, already-working code (low risk, since the
  declared tables were directly confirmed to already match today's real
  transitions); items 3/4 require genuinely new adapter code or depend on
  agent-bridge's own convergence (larger, later slices).
- Opened
  [ThomasMichon/copilot-extensions#2423](https://github.com/ThomasMichon/copilot-extensions/issues/2423)
  as Phase 10's coordination token, alongside #2357 (Phase 9, now
  complete) rather than reusing it -- each phase gets its own tracking
  issue per this effort's established coordination pattern.
- Next: submit this phase's design as its own reviewed slice (matching
  Phase 9's own coordination-gate convention), then start with wiring
  item 1 (the task machine into `queue.py`'s transition call sites) as
  the first, lowest-risk implementation slice.

### 2026-09-11 - Phase 9: design/declaration work complete

- Every checkbox in the phase-9 sub-doc's own Plan checklist is now
  checked: all four declared machines/relations (task, provider, bridge,
  and the reservation/allocation-fencing layer coupled to it), the
  explicit control-flow coupling rules, the recovery taxonomy applied to
  every transition, the full ten-scenario simulation/test track, and the
  Phase 8 re-validation pass. Ticked the corresponding Phase 9 checkbox
  and the matching Validation Plan item in this README.
- **What remains genuinely open, and why it is not folded in here:**
  - Five Phase 8 candidates (attempt-budget choke point, event ledger
    with reason-code classification, official-vs-candidate
    approval-authority split, worktree-pool force-clean/dirty-tolerance,
    relay/host liveness and health-fencing) are still only a *conceptual*
    home assignment to one of the four modules -- no declared table or
    function backs any of them yet. This is real, deliberate deferral
    (see the sub-doc's "Re-validation pass" table), not an oversight.
  - This README's own Validation Plan items above the Phase 9 line
    (concurrent claim ownership, duplicate/reordered delivery, restart-
    at-any-boundary resumption, stale-revision feedback blocking,
    verdict-vs-transport-success separation, terminal-failure visibility,
    generic-contract cleanliness, registrar-schema reuse) are about the
    **live, running** review pipeline (Phases 1-4's original scope) --
    Phase 9 explicitly declared its own scope boundary as "designs the
    model... does not implement it end to end" (see the sub-doc's "Why
    this phase exists" section), and that boundary has been honored
    throughout: every module added is a declared table plus deterministic,
    in-process structural/simulation tests, never a live-queue,
    live-bridge, or live-provider integration. Wiring the four now-
    declared machines into `queue.py`/the coordinator/a real provider
    adapter so those items become checkable against live behavior is a
    materially different, larger tranche of work than any single slice
    landed so far -- it is not scoped as a Phase 9 sub-item, and opening
    that scope (a "Phase 10: wire the declared machines into the live
    runtime," or however the operator wants to frame it) is a decision
    left for explicit operator direction rather than assumed here.
- **Effort status remains `Draft`** -- Phase 9's own design-and-
  declaration work is complete, but the effort as a whole is not `Done`
  until its Validation Plan is satisfied or its remaining items are
  explicitly transferred to a named tracked objective, per this repo's
  effort-completion discipline.

### 2026-09-11 - Phase 9: tenth slice (reservation/allocation-fencing layer)

- Declared the spawn-reservation/allocation-fencing layer as a checkable
  relation coupled to the bridge machine
  (`plugins/agent-dispatch/src/agent_dispatch/spawn_reservation_machine.py`),
  following `agent_dispatch.queue.SpawnState`'s real, already-implemented
  lifecycle rather than a fresh design: `record_spawn`/`record_cold`/
  `request_spawn_release`/`fail_spawn`/`defer_spawn`/`settle_spawn`/
  `retire_spawn`/`rearm_spawn` each named as a declared transition, tagged
  with a `RecoveryMode`. Confirmed `FAILED` is releasable
  (`SpawnState.RELEASABLE`) but not machine-terminal here, since `rearm`
  is a real declared exit from it -- a distinction the terminal-state
  check now makes explicit rather than conflating the two concepts.
- `violating_assignment_groups()` makes the single-assignment invariant
  (`reserve_spawn`'s existing atomic guarantee: no second reservation
  minted while one is `ACTIVE` for the same task or exclusive-key group)
  a checkable structural property over a snapshot of reservation rows.
- `LetGoReason` (failed/deferred/settled/retired-rearm/preempted) declares
  the closed "letting go" vocabulary, each mapped to a real transition and
  the sub-doc's exact recovery-mode classification; preemption resolves
  through the graceful release path, distinct from an ordinary failure.
- `classify_consistency()` declares the three-tier (not-applicable/
  consistent/anomaly) reservation<->bridge relation as an explicit
  whitelist of consistent `(SpawnState, BridgeState)` pairings -- **any
  pairing not whitelisted defaults to anomaly**, including combinations
  the sub-doc's prose never explicitly addressed (e.g. RELEASING +
  HYDRATING), matching Phase 9's "never assume the safer state without
  evidence" rule rather than leaving a silent gap.
- Added
  `plugins/agent-dispatch/tests/test_spawn_reservation_machine.py` (51
  structural tests): lifecycle reachability/exit-checking, the
  releasable-but-not-terminal distinction for FAILED, every let-go reason
  and its exact recovery mode, the single-assignment invariant across
  both task-id and exclusive-key groupings, and every whitelisted /
  documented-anomaly / intentionally-undeclared consistency pairing.
- This ticks the phase-9 sub-doc's last open Plan checkbox for the
  declared-machine work. The launch-to-claim grace/recovery monitor
  remains explicitly out of scope (its own component, consuming this
  contract from the outside). The five still-conceptual-only Phase 8
  candidates (attempt-budget, event ledger, approval-authority split,
  worktree-pool dirty-tolerance, relay health-fencing) remain open
  follow-up design work, untouched this slice.
- Bumped agent-dispatch's version (plugin.json, pyproject.toml,
  marketplace.json) to 0.1.2-dev66.

### 2026-09-10 - Phase 9: ninth slice (implement the three deferred simulation scenarios)

- Implemented the three scenarios the prior design-review slice designed
  but left as zero landed fixtures:
  - **Bridge mid-version-update / transient port blip:**
    `bridge_state_machine.resolve_liveness_with_recovery(cache_hint,
    live_probe, discover_port, max_attempts=3)` -- a bounded-retry
    wrapper around `resolve_liveness`; `discover_port` is injected (never
    called internally), so a fixture can deterministically drive a
    stale-port-then-fresh-port sequence. Falls back to `Liveness.COLD` --
    never assumes `HOT` -- once attempts exhaust.
  - **Bridge/runtime EOL retirement safety:**
    `bridge_state_machine.eol_safe_to_retire(active_lease_count) -> bool`,
    a pure predicate true only at zero. No new bridge state; the
    supervisor's routing decision stays a dispatch policy.
  - **Steer-vs-transition race:** `task_state_machine.SteerOutcome`
    (`RESUME_WITH_WAKE` / `RELEASE_TO_QUEUED`),
    `STEER_OUTCOME_TRANSITION` (mapping each outcome to a real declared
    task transition -- `resume` / `release_suspended`),
    `resolve_steer_outcome(is_headless_reservation=...)`, and
    `suspend_blocked_by_pending_steer(has_untaken_steer=...)` -- the
    existing `awaiting_steer`/steer-inbox contract in `queue.py`'s
    `submit_steer`/`suspend` made checkable data, not new vocabulary.
- Added
  `plugins/agent-dispatch/tests/test_simulation_deferred_scenarios.py`
  (17 structural tests): the liveness-recovery wrapper's first-attempt-
  success, blip-then-recover, exhausted-attempts-falls-back-to-COLD, and
  cache-hint-ignored cases with `discover_port` verified as externally
  injected; the EOL predicate at zero/nonzero/negative lease counts; both
  steer outcomes mapping to real task transitions, and the suspend-
  blocked-while-untaken-steer invariant.
- This lands **all ten** of the phase-9 sub-doc's simulation scenarios
  (previously seven; the remaining three were design-only until this
  slice) and ticks its Plan checkbox.
- Full `agent-dispatch` suite still green (see Validation below); bumped
  agent-dispatch's version (plugin.json, pyproject.toml, marketplace.json)
  to 0.1.2-dev65.
- Remaining Plan item: the reservation/allocation-fencing layer
  (single-assignment invariant, closed "let go" vocabulary, three-tier
  reservation<->bridge consistency) -- designed in the prior slice but
  still zero fixtures landed. The five still-conceptual-only Phase 8
  candidates (attempt-budget, event ledger, approval-authority split,
  worktree-pool dirty-tolerance, relay health-fencing) remain open
  follow-up design work, not touched this slice.

### 2026-09-10 - Phase 9: eighth slice (design review -- the three deferred scenarios corrected + assignment/reservation coupling declared; docs-only, zero fixtures implemented)

A design-review/rubber-duck conversation with the operator, grounded by
reading the real `queue.py`/`client.py`/`embody.py`/`supervisor.py`
runtime (not just the declared machines), corrected two of the prior
slice's deferral reasons and produced a new declared design:

- **Steer (scenario 8) was wrong to defer as "needs a new task-machine
  transition."** Reading `queue.py` found steering is already fully real:
  `awaiting_steer` is an existing **boolean** flag orthogonal to `Status`
  (settable while `claimed`/`started`/`suspended`), `suspend` already
  refuses while an untaken steer answer exists, and a submitted steer
  already resolves to exactly one of two outcomes (resume-with-wake or
  release-to-queued) -- the same dual-outcome shape
  `TASK_TRANSITION_BRIDGE_CONFIRMATION["resume"]` already uses. The
  `SuspendReason`/`VersionedRecord`-payload design floated earlier in this
  slice's own design conversation was scaffolding for a mechanism that
  doesn't match the real system, and was dropped entirely -- not carried
  into the sub-doc.
- **Mid-version-update (scenario 1) was wrong to defer as "needs a
  version/EOL dimension on the bridge machine."** agent-bridge, not
  agent-dispatch, owns the actual session-host instances and their
  zero-downtime-deploy mechanics; agent-dispatch's job is only to notice a
  transient connection/call blip and recover by re-resolving the dynamic
  port binding before retrying. Designed as a bounded-retry wrapper with
  an **injected** `discover_port` callable (so a fixture can deterministically
  drive stale-port-then-fresh-port), composing with the already-declared
  `PORT_CHANGED` bridge event.
- **EOL (scenario 2)** confirmed as a pure `eol_safe_to_retire(active_lease_count)`
  predicate -- no new bridge state, reusing existing drain transitions.
- All three are now **designed, not implemented** -- corrected/added to the
  sub-doc's "Simulation and test track" section with the grounding
  evidence; zero fixtures exist for any of the three yet.
- **New: declared the reservation/assignment allocation-fencing layer**
  (`agent_dispatch.queue.SpawnReservation`) as a relation coupled to the
  bridge machine -- explicitly **not** a fourth top-level machine, kept in
  the same conceptual slot as `machine_coupling.py`. Covers: the
  single-assignment invariant (`reserve_spawn()` already atomically
  enforces it; declaring it as a checkable structural fixture is the
  remaining work), a closed "let go" reason vocabulary tagged with
  recovery modes (`fail_spawn`/`defer_spawn`/`settle_spawn`/`retire_spawn`/
  preemption), and a three-tier (N/A / consistent / anomaly)
  reservation<->bridge consistency relation grounded in real code paths
  (`worktree_ownership` `reused`/`inherited_worktree`, the cold-body-resume
  path in `supervisor.py`). Explicitly scoped **out**: a launch-to-claim
  grace/recovery monitor (bounded grace deadline, evidence-of-progress
  signals, a kill-and-resume-with-nudge ladder) -- left to a separate
  future component consuming this contract from the outside; its own
  internal state (timers, evidence counters, kill-attempt counts) is
  deliberately not declared here.
- **Vision update:** `visions/plugins/agent-dispatch/README.md`'s
  *nudge-before-recover* behavior was clarified to state explicitly that
  the graduated recovery ladder is scoped to bodies agent-dispatch itself
  spawned (a reservation exists) and never applies to an operator's own
  interactively-driven CLI session -- this was standing intent implied by
  the design but not previously stated, and is structural (recovery acts
  on a reservation; a human's own claim never creates one), not a policy
  check to remember.
- **Explicitly out of this slice:** a full command-surface caller
  taxonomy (external-input / external-observe / dispatched-agent /
  producer-emitter / evaluator / diagnostic, with per-command
  valid-state/role annotations) was worked out in the same conversation
  but deliberately left out of this PR -- it's reference material for the
  `agent-dispatch` plugin's own docs, not phase-9 state-machine design,
  and bundling it would make this review unreviewable. Noted for a
  follow-up doc under `plugins/agent-dispatch/docs/`.

### 2026-09-10 - Phase 9: seventh slice (Phase 8 candidate re-validation, docs-only)

- Re-validated all eight Phase 8 candidates against what the four Phase 9
  modules (`task_state_machine.py`, `provider_state_machine.py`,
  `bridge_state_machine.py`, `machine_coupling.py`) actually declare today,
  not just which machine each was conceptually assigned to. Added a
  per-candidate table to the sub-doc's "Phase 8 candidates, re-seated"
  section: three candidates (base-only detection, stale-approval
  classification, WIP/hold gating) are genuinely declared behaviors as of
  the prior two slices; five (attempt-budget choke point, event ledger,
  official-vs-candidate approval-authority split, worktree-pool
  dirty-tolerance, relay/host health-fencing) are still only a conceptual
  home assignment with no declared table or function backing them.
- Deliberately did not author new design to close that gap in this slice
  -- an advisor review flagged that a bridge version/EOL dimension and a
  task steer transition (needed for the three still-deferred simulation
  scenarios) would be new design authored inside a "build the test track"
  checklist item, contradicting this phase's own already-ticked
  coordination gate ("submit this phase's design as its own reviewed
  slice before any implementation begins"). Both gaps -- the five
  under-declared Phase 8 candidates and the three deferred simulation
  scenarios -- are left as open follow-up design work (a Phase 9
  sub-doc amendment or a new phase), not folded in ad hoc.
- Ticked the Plan's "re-validate each Phase 8 candidate" checkbox; the
  "build the simulation/test track" checkbox remains open (seven of ten
  scenarios covered, three deferred pending the design decision above).

### 2026-09-10 - Phase 9: sixth slice (simulation/test track, provider revision scenarios)

- Extended `provider_state_machine.py` with the revision/head-tracking
  vocabulary the remaining two simulation scenarios needed: `Revision`
  (a diff-hash + base-sha pair, tracked as two independent fingerprints),
  `RevisionChangeKind` (none/base-only/substantive), and
  `classify_revision_change` -- folding Phase 8's base-only detection
  candidate directly into the provider machine rather than a standalone
  feature, per the sub-doc's "Phase 8 candidates, re-seated" section.
  Also added `verdict_applies_to_current_revision`, the guard an evaluator
  consults before applying any verdict-driven approval transition.
- Extended the simulation driver (`simulation.py`) with an optional
  `provider` record on `World` and a `step_provider_approval` helper.
- Covered two more of the sub-doc's ten scenarios
  (`test_simulation_revision.py`): base-only vs. substantive provider
  revision movement, and an out-of-order verdict arriving after a
  substantive revision has already superseded it -- seven of ten
  scenarios now covered; three remain deferred (bridge version/EOL,
  task steer transition).

### 2026-09-10 - Phase 9: fifth slice (simulation/test track, first five scenarios)

- Added the simulation driver
  (`plugins/agent-dispatch/src/agent_dispatch/simulation.py`): a generic
  `World` pairing a task `VersionedRecord` with a bridge one, plus
  `step_task`/`step_bridge` helpers that resolve a named transition from
  the owning declared table and apply it through
  `machine_coupling.apply_transition`. The driver declares no new machine
  behavior -- it only drives the already-declared tables.
- Built five of the sub-doc's ten "Simulation and test track" scenarios as
  deterministic fixtures
  (`plugins/agent-dispatch/tests/test_simulation.py`): session host
  detached, port changed underneath an attached task, duplicate/
  out-of-order provider-driven task events, resume against a stale/empty/
  missing liveness cache, and resume against a genuinely hot target
  (including the force-takeover composite). Each asserts convergence
  regardless of event order, idempotent replay, and that the recorded
  recovery mode matches the declared taxonomy.
- The remaining five scenarios (bridge mid-version-update, supervisor
  EOL, base-only vs. substantive provider revision, out-of-order verdict
  vs. superseding revision, steer-vs-transition race) need a vocabulary
  extension the machines don't yet declare (revision/head tracking on the
  provider machine, version/EOL on the bridge machine, a steer transition
  on the task machine) -- deferred to a follow-up slice rather than forcing
  a fixture that doesn't actually exercise a real declared invariant.
  Marked as deferred, with the reason, directly in the sub-doc's checklist.

### 2026-09-10 - Phase 9: fourth slice (control-flow coupling rules)

- Declared the explicit coupling between all three machines
  (`plugins/agent-dispatch/src/agent_dispatch/machine_coupling.py`):
  `TASK_TRANSITION_BRIDGE_CONFIRMATION` names, per task transition needing
  a bridge action, which bridge transition(s) confirm it actually
  happened (a task transition never assumes the bridge effect occurred
  just because it was requested); `BRIDGE_EVENT_EVIDENCE` names, per
  bridge-side event, only the task transitions it is evidence *for* --
  the accessor `consider_task_transitions` is never given a task record,
  so it structurally cannot mutate one. Also declares the generic
  CAS/generation primitive (`VersionedRecord` + `apply_transition`) the
  sub-doc's "Mechanism, concretely" section describes, shared by all
  three machines rather than three separately hand-rolled CAS loops --
  its three outcomes (applied / lost-CAS-so-re-read / already-advanced-
  so-no-op) are exactly Phase 9's idempotent-replay requirement.
- Added structural tests
  (`plugins/agent-dispatch/tests/test_machine_coupling.py`, 18 passing):
  every bridge event names only real task transitions and never a direct
  application, every task transition's bridge confirmation names a real
  bridge transition (including `resume` accepting either resolved
  liveness outcome), and the CAS primitive's three outcomes plus
  non-mutation of its input record.
- This closes the phase-9 sub-doc's recovery-taxonomy checkbox too: every
  transition across all three machines already carried exactly one
  `RecoveryMode` tag as each was declared, proven incrementally by each
  module's own structural test.
- Remaining Plan items: the ten-scenario simulation/test track and the
  Phase 8 candidate re-validation pass -- both explicitly deferred past
  this slice per prior advisor guidance (the simulation fixtures drive
  against these coupling rules, so they come after, not before).

### 2026-09-10 - Phase 9: third slice (bridge/session machine)

- Declared the bridge/session state machine
  (`plugins/agent-dispatch/src/agent_dispatch/bridge_state_machine.py`):
  lifecycle states absent/hydrating/running/suspended/ended, plus the
  corrected three-tier liveness model (hot/warm/cold) folded in from the
  #6744 liveness fix -- liveness is always a live observation, never
  gated by a cache. The resume outcome (refuse/force-takeover/reattach/
  spawn-fresh-bound) is explicitly coupled to the lifecycle table rather
  than declared as an independent set: REATTACH and SPAWN_FRESH_BOUND
  each name a real transition in the lifecycle table, checked by test.
- The companion agent-bridge vision (`visions/plugins/agent-bridge/README.md`)
  does not yet declare the hot/warm/cold tiers or the cache-is-never-
  authority rule as of this slice -- it has a task-shaped verb set
  (create/identify/read/steer/wait/interrupt/stop/resume/end) and a
  "takeover" pattern, but nothing more specific. This module does not
  duplicate or compete with those verbs; it declares only what this
  effort's Phase 9 needs and should be reconciled with the vision once it
  declares its own liveness model.
- Added structural tests
  (`plugins/agent-dispatch/tests/test_bridge_state_machine.py`, 24
  passing): reachability/exit-checking of the lifecycle table, the live
  probe is always invoked regardless of a cache hint (including a
  deliberately-stale hint that disagrees with the live result), and every
  non-refusing resume outcome maps to a declared transition.
- Next slice: the explicit control-flow coupling rules between all three
  machines (task-requests-bridge-action / bridge-event-as-evidence).

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
