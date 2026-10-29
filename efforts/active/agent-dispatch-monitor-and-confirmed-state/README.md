# agent-dispatch: monitor + confirmed state (manual-task reliability primitives)

- **Slug:** `agent-dispatch-monitor-and-confirmed-state`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-slice PRs off `dev`
- **Created:** 2026-09-25
- **Status:** Active - submission/output/delivery amendment proposed 2026-10-08
- **Source umbrella:** #3681; continuing coordination is this Active ROOT's
  reviewed Plan/Validation/Journal and eligible OPEN bounded-source issues
- **Sub-issues:** _none yet_

## Guiding Intent

**Current direction (2026-10-08):** extend the existing lifecycle, result
storage, evaluator, and steering machinery into a durable task conversation.
Workers only SUBMIT; independent policy-driven confirmation makes a task
COMPLETED. Manual tasks default to operator approval. The
[task outputs and review vision](../../../visions/plugins/agent-dispatch/task-outputs-and-review/README.md)
is canonical intent; [the current amendment](task-conversation-amendment.md)
contains the approved design decisions, dependencies, and validation matrix.
The older `completed`/`confirmed` terminology below records earlier phases;
new work uses `SUBMITTED`/`COMPLETED`, not a new `CONFIRMED` state.

Realize the two new core primitives the
[agent-dispatch vision](../../../visions/plugins/agent-dispatch/README.md)
declared (PR #3622/#3625): **the monitor** (a suspend's companion resolution
handler, with a default cooldown kind for round-robin time-slicing) and
**confirmed** (the true lifecycle terminal beyond a provisional `completed`).
Both close the exact reliability gap an operator observed: a much simpler
external task-queue wrapper — no suspension, force-run to completion, no
opt-in — was proving *more* reliable than this richer machinery, because it
never accumulates the failure modes a monitor-less suspend and an
unconfirmed completion both invite (a task parked with nothing watching it;
a "done" claim nobody ever actually checked).

A third, related idea from the same conversation — recasting the whole
delegation layer as **ACP with unbounded tool-call latency** (a suspend is
an ordinary tool call whose result may take days, delivered by a cold-started
process) — is the mental model this effort should keep coherent with,
particularly for Phase 3's crash-recovery behavior.

## Context

- **Source visions:** the parent
  [agent-dispatch vision](../../../visions/plugins/agent-dispatch/README.md)
  §Concepts (*The monitor*), §Behaviors (*suspension-requires-a-monitor*,
  *self-tracked-review-is-not-a-lane*), and §Features
  (*verify-the-completion-claim*, extended); the
  [tasks-pane-ux child vision](../../../visions/plugins/agent-dispatch/tasks-pane-ux/README.md)
  §Concepts (*The New Task composer*, *The Completion Review card*). Those
  two PRs (#3622 landed to `main` in error, re-landed to `dev` as #3625) are
  the vision-level "should-be"; this effort is the reality-side delta.
- **Existing machinery this builds on (not replaces):**
  - `task_state_machine.py` — the declarative, checkable transition table.
    Its own docstring already frames itself as *declaring* a machine
    independent of the executing code, which is exactly this effort's
    Phase 1 shape.
  - `queue_lifecycle.py`'s `suspend()`/`resume()`/`complete_with_outcome()`
    and `queue_records.py`'s `Status` (`PROPOSED/QUEUED/CLAIMED/STARTED/
    SUSPENDED/COMPLETED/ABANDONED/DEAD_LETTER`, with `TERMINAL = {COMPLETED,
    ABANDONED, DEAD_LETTER}`) — `COMPLETED` is unconditionally terminal
    today; making it provisional (per the vision) is the one genuinely
    invasive piece of this effort and needs its own careful phase (see
    Phase 2's risk note).
  - `queue_storage.py`'s `_enqueue_wake()` — a durable, fenced
    (`generation`/`wake_seq`/`owner_session_id`) outbox row
    (`WakeOperation`, table `wake_outbox`) already exists, but **every
    existing call site sets `not_before = now`** (immediate delivery) — there
    is no delayed/cooldown wake today. `queue_steering.submit_steer` flips
    `SUSPENDED -> STARTED` **itself**, then optionally enqueues a wake whose
    only job is nudging an already-live interactive session; for a **cold**
    (headless, no live process) reservation it instead sets
    `resume_requested = 1` and leaves the task `SUSPENDED`, relying on the
    supervisor's own reconciliation loop to notice and re-embody. **Any new
    delayed/cooldown wake must route through this same split** (interactive
    vs. cold-headless), not invent a third path.
  - `queue_steering._wake_is_current()` requires
    `task.status == Status.STARTED` for a wake to be honored (not
    `SUSPENDED`) — confirms the wake outbox's job is *nudging a session*,
    never *performing* the SUSPENDED→STARTED transition itself. A cooldown
    wake that should actually resume a still-`SUSPENDED` task needs its own
    delivery path (most likely: set `resume_requested = 1` exactly like the
    cold-headless steer path, or call `resume()` directly with a synthetic
    message) — it cannot simply piggyback on `_enqueue_wake` unchanged.
  - `bridge.resume_steered_owner()`'s `message or <default-steer-text>`
    fallback assumes a **null message means "an operator answered your
    card"** — a cooldown wake must pass its **own** explicit message, never
    rely on that default, or a plain cooldown wake would misreport itself as
    an operator response.
- **Prior-art check (per envisioning's stability bias):** no existing
  `Status` value, transition, or module already implements "confirmed" or "a
  named, monitored wait." `declarative-dispatch-engine-generalization` (ADO
  backlog provider / worker identity / prompt shape) and
  `agent-dispatch-tasks-pane-ux-overhaul` (Tasks-pane UI, Phases 0-9) are
  the two active efforts closest in spirit; neither owns this scope. The
  UX-facing halves of the same vision delta (**New Task composer**,
  **Completion Review card**) are carved as new phases of
  `agent-dispatch-tasks-pane-ux-overhaul` instead of duplicated here — see
  its README Phases 10-11.

## Request

The 2026-10-07/08 direction is captured in
[operator-direction.md](operator-direction.md), with settled decisions and
agent-recommended implementation safeguards separated in
[task-conversation-amendment.md](task-conversation-amendment.md).

Operator (verbatim, this session):

> For suspension, the default monitor could be to just wake the task up
> after a cooldown. This basically lets agent round-robin-time-slice,
> yielding to allow other work to proceed, but never just getting stuck
> endlessly.
>
> The `agent-dispatch run` idea I hope we currently have implemented isn't
> bad, but has a flaw: if the dispatch service dies or the process is
> killed, when agent-dispatch launches again I don't know what happens.
> Hopefully we rewake the task, attaching a "Sorry, your task died due to
> infrastructure failures" note in the place of the tool result.
>
> Ultimately, `agent-dispatch` is just a *really slow ACP control* of an
> agent, where some tool calls shut down Copilot while waiting for answers,
> agent responses get pushed into persisted queues waiting for user
> attention, and Copilot may get cold-started out of the blue with a
> tool-call result that may have taken days to complete.

And, from the prior turn (already realized as vision text, restated here for
traceability): agents may Block via steering and may suspend to avoid
holding a lane while waiting on an externality, but should not bare
"end-turn"; suspension should be constrained to monitor-backed waits with
known end-states; a `monitors` system should complement `emitters`/
`evaluators`.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| Planning owner | Vision/effort amendment and its PR only | This Active ROOT's reviewed Plan/Validation/Journal |
| Backend slice owner | One approved implementation phase at a time | Claim an eligible OPEN bounded-source issue through this ROOT before implementation |
| Tasks-pane slice owner | UI consumers after backend contracts land | The linked Tasks-pane effort and its coordination record |

## Coordination

The finite #4464 producing-harness guidance contract below continues through
existing producer/task-command/guidance owners and this ROOT's reviewed
Plan, actual global Validation and Journal. Preserve backend versus
Tasks-pane custody and current SUBMITTED/COMPLETED direction. This intake
does not appoint an implementation owner, adopt a code target as a harness,
move a task, change routing defaults or release a source/resource claim.

The complete #3681 retained finite family continues through this existing Active ROOT's
reviewed Plan, actual global Validation and Journal/open bounded-source process under
the SAME declared and source-specific custodians. Preserve every incumbent alternative,
native/source/resource reservation and independent acceptance gate. Closed source
trackers are historical provenance, never required live claim tokens; no assignment,
claim expiry or new blanket ACK is created.

One canonical backend effort; the Tasks-pane effort owns only its UI consumers.
The planning owner lands this documentation amendment before implementation.
Later phase owners record distinct eligible OPEN bounded-source claims through
this ROOT and drive their own PRs
through merge, journaling results and handing unresolved work to a named
successor. The current planning slice does not adopt responsibility for
executing every historical open phase. Reconcile #5701/#5668 after merge rather
than opening competing implementations.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Continuing source-specific Transport/Bridge custody

#### Source #2885

The planning owner retains documentation only; separately approved
backend owners retain reason/lifecycle/history/output/monitor/delivery
implementation, and existing Tasks-pane, general Worktree Manager viewer
and native-session hosting owners retain their boundaries. Preserve
#2879's specific resource/complete-transfer and #2887's reviewer-policy
reservations without absorbing or expiring them. This is LONG-TERM intent,
not immediate implementation authority. #3681 is provenance, not a required
live claim token. No new blanket ACK or executor appointment is created.

## Plan

### Phase 1 — The monitor vocabulary only (data only, no `Status`/table change)
_(agent-recommended: sequencing shape, revised 2026-09-25 after actually
running `test_task_state_machine.py` — see the Journal entry below for why.)_
- [ ] Design and implement the **monitor** vocabulary as its own small,
      dependency-free module (`monitors.py`), matching the codebase's
      existing one-focused-module-per-concern convention: a small, closed
      enum/registry of monitor kinds (starting with exactly one:
      `cooldown`), each carrying the data needed to resolve it (for
      `cooldown`: a `not_before` timestamp) and a pure resolution check
      (`is_resolved(monitor, *, now) -> bool`). No wiring into `queue_*.py`
      yet — this phase is pure, fake-clock-testable data + a resolver
      function, deliberately kept independent of the state machine so it
      can be built and tested before Phase 2's harder question is settled.
- [ ] Unit tests: cooldown resolution at/before/after `not_before`; the
      default-cooldown constructor; no live clock, no DB, no subprocess.

### Phase 2 — `confirmed` requires resolving `COMPLETED`'s terminal-ness FIRST
**Finding (2026-09-25, from actually running the suite, not just reading
it):** `test_task_state_machine.py::test_no_transition_originates_from_a_
terminal_state` actively asserts no declared transition may source from a
terminal state. Since `Status.TERMINAL` already contains `COMPLETED`, a
`confirm`/`reopen_completed` transition sourced from `COMPLETED` is
**not decomposable** into "declare the transition now, resolve
terminal-ness later" the way the original Phase 1 draft assumed — the
table-shape test forces the terminal-ness question to be answered in the
SAME change that adds either transition. There is no clean, purely-additive
"Phase 1" for the state-machine table itself; Phase 2 below is now the
single phase that must land both together.

- [x] **Risk-audit every `Status.TERMINAL`/`Status.COMPLETED` call site**
      across `queue*.py`, `coordinator_tasks.py`, `board_cli.py`,
      `client.py`, `mcp_server.py`, `mcp_http.py`, the CLI, and tests
      **before** changing terminal-ness. **Done 2026-09-25** -- every
      real call site found and classified:
      - **Genuinely wants the OLD "no more agent work" meaning** (added a
        new `Status.CONCLUDED = {COMPLETED, CONFIRMED, ABANDONED,
        DEAD_LETTER}` constant for these, rather than repeating
        `in TERMINAL or == COMPLETED` everywhere):
        `queue_lifecycle.set_hold`, `queue_steering.submit_steer` (now also
        hints "use reopen_completed"), `queue_steering.save_card_draft`,
        `loop_commands.py`'s repository-issue-loop `active` filter,
        `reattach.py`'s dedup-key re-mint guard.
      - **Hand-rolled local duplicates of the OLD terminal set**, updated
        to also cover `confirmed`: `repository_issue_loops._TERMINAL`
        (backlog-batch "is this occurrence done" checks).
        `supervisor.py`'s own `_TERMINAL = {COMPLETED, ABANDONED}` (spawn
        **conclusion**/reclamation) and `_completion_detail`'s
        corroboration check are correctly scoped to the *moment of
        completion* and need no `CONFIRMED` handling -- by the time a task
        is confirmed, its spawn already concluded back at `COMPLETED`.
      - **`Status.ABANDONABLE`**: added `COMPLETED` (the Completion Review
        card's Abandon action) -- `task_state_machine.py`'s `abandon`
        transition sources from `Status.ABANDONABLE` directly, so this one
        edit updates both without drift.
      - **`queue.SWEEP_STATES`**: added `CONFIRMED` alongside `COMPLETED`
        (a confirmed task is just as real a dedup candidate).
      - **`task_query_cli.py`'s continuation-baton debounce** (`is_handoff
        and status == "completed"` / `status not in ("completed",
        "abandoned")`): extended both to also treat `confirmed` as spent/
        done, for a handoff task that somehow got reviewed and confirmed.
      - **Two byte-identical board-group duplicates**
        (`board_cli.GROUPS`/`TERMINAL`/`_group` and
        `task_query_cli._BOARD_GROUPS`/`_BOARD_TERMINAL`/`_board_group`):
        added a `Confirmed` group to both, kept in sync per their own
        "must stay in sync" comments.
      - **Confirmed NOT a task-`Status` concept at all** (a different
        domain reusing the string `"completed"`, false positives from the
        literal-string sweep): `bridge_namespace_cli.py`'s owner-resolution
        tuple (dead code for `completed` anyway -- `owner` is always
        cleared by then), `embody.py`'s worktree-tracking and ACP-session
        terminal sets, `producers/evaluator.py`'s own spec docstring
        example.
- [x] Update `task_state_machine.py`: added `CONFIRMED` to `ALL_STATES`;
      `COMPLETED` left `Status.TERMINAL` (moved to the new `CONCLUDED`
      superset), `CONFIRMED` took its place; added `confirm`
      (`COMPLETED -> CONFIRMED`, `SAFE_RETRY`) and `reopen_completed`
      (`COMPLETED -> QUEUED`, `SAFE_RETRY`) to `TRANSITIONS`. Exactly the
      one predicted test edit was needed
      (`test_all_states_match_queue_status`'s hardcoded state list); every
      other structural assertion in `test_task_state_machine.py` passed
      against the new table unmodified, confirming the design holds.
- [x] Implemented `TaskQueue.confirm(task_id, ...)` and
      `TaskQueue.reopen_completed(task_id, ..., steer_fields=None,
      sender=None)` in `queue_lifecycle.py`. `reopen_completed` clears
      `result`/`result_ref`/`completed_by`/`completed_at` (the disputed
      claim is superseded, not merely annotated) and optionally records an
      operator steer atomically with the reopen.
- [x] Wired `verify-the-completion-claim`'s automatic half -- **and found a
      cleaner integration point than planned**: `producers/evaluator.py`'s
      `SpecEvaluator`/`apply_decisions` (the real, already-working
      evaluator mechanism `supervisor.py`'s `advance_via_evaluator` already
      runs over every `COMPLETED`/`ABANDONED` task) gained a third
      `Confirm` decision alongside the existing `Emit`/`NoOp`: a rule
      declaring `"confirm": true` (instead of `"emit": {...}`) closes the
      *originating* task via `queue.confirm()`. `apply_decisions` gained
      `task_id`/`confirmer` parameters (both optional; a `Confirm` with
      neither wired is recorded as a skipped decision, never raised).
      `supervisor.py`'s evaluator pass now threads `task_id=tid,
      confirmer=self.client.confirm` through. **Deferred, not done**: this
      `Confirm` decision performs no corroboration check of its own --
      it trusts the rule's own `when` predicate as the corroboration
      judgment. Actually reusing `_completion_detail`'s existing
      goal/result-ref/progress corroboration logic (today only produces a
      descriptive log string, never gates anything) as a real precondition
      before a `Confirm` decision fires is real follow-up work, tracked
      here rather than guessed at: the two currently run as separate,
      untimed-relative-to-each-other passes over "terminal" tasks, and
      correctly composing them needs its own design pass.
- [x] Exposed `confirm`/`reopen` on the CLI
      (`task_lifecycle_cli.py`/`task_lifecycle_registration_cli.py`) and
      the coordinator HTTP API (`client.py`'s `confirm`/`reopen_completed`,
      `coordinator_tasks.py`'s `/tasks/{id}/confirm` and
      `/tasks/{id}/reopen` routes) -- the full stack, not CLI-only.
- [x] `board_cli.py` (and its `task_query_cli.py` duplicate): added
      `confirmed` to the phase/group projection.
- [x] Kept `client.py`/`queue_lifecycle.py` under their module-size caps by
      extracting the new methods into their own mixins
      (`client_completion_review.CompletionReviewMixin`,
      `queue_completion_review.QueueCompletionReviewMixin`), matching this
      codebase's existing one-mixin-per-concern componentization pattern
      (`client_registrations.py`/`client_worktree_status.py` were already
      split out the same way).
- [ ] Deferred, not started: expose `confirm`/`reopen_completed` as MCP
      tools (`mcp_server.py`/`mcp_http.py`, alongside the existing
      `dispatch_complete`/`dispatch_abandon`) -- purely additive, no
      regression risk either way, just not done this pass.
- [ ] Deferred, not started: reuse `supervisor._completion_detail`'s
      existing goal/result-ref/progress corroboration logic as a real
      precondition gating the evaluator's new `Confirm` decision (today it
      only produces a descriptive log string and never blocks anything;
      the `Confirm` decision itself performs no corroboration of its own,
      trusting the rule's `when` predicate instead). Needs its own design
      pass since the two currently run as separate, untimed-relative-to-
      each-other passes over "terminal" tasks.

### Phase 3 — The cooldown monitor (default, round-robin time-slice yield) ✅ landed
- [x] ~~Extend `queue_storage._enqueue_wake()`~~ **not needed** -- see the
      Journal entry below for why the actual design never touches the wake
      outbox at all. Instead, the monitor deadline is tracked directly on
      the task row (new `monitor_kind`/`monitor_not_before` columns).
- [x] Added `TaskQueue.suspend(..., cooldown_seconds: float | None =
      DEFAULT_SUSPEND_COOLDOWN_SECONDS)` — a suspend with no more specific
      monitor gets the default cooldown automatically (per
      *suspension-requires-a-monitor*). Extracted `suspend`/`resume` out of
      the near-ceiling `queue_lifecycle.py` into a new `queue_suspend.py`
      mixin (module-size discipline) alongside the new
      `reconcile_cooldowns()` reconciler.
- [x] The escape hatch exists: `cooldown_seconds=None` suppresses the
      monitor entirely (CLI: `--no-cooldown`). A second monitor kind
      remains out of scope.
- [x] CLI/API surface: `agent-dispatch suspend <id> --reason "..."
      [--cooldown-seconds N | --no-cooldown]`. Threaded through
      `DispatchClient.suspend` (new `client_suspend.py` mixin, same reason)
      and the coordinator's `SuspendBody`/`/tasks/{id}/suspend` route
      (`model_fields_set` distinguishes "not passed" from an explicit
      `null` over the wire).
- [x] Tests: `tests/test_queue_suspend.py` (fake-clock, no real sleep) --
      default cooldown attaches a monitor; an explicit override changes the
      deadline; `None` suppresses it; `resume()` always clears the monitor
      so a manually-resumed task never gets double-resumed by a later
      `reconcile_cooldowns()` pass; a raced/abandoned task is skipped, not
      errored. Plus coordinator-level HTTP round-trip tests and a real
      (short-real-sleep) end-to-end auto-resume test against the live
      `_gc_loop`.

### Phase 4 — Crash-recovery honesty (the "died due to infrastructure failure" note)
- [ ] Locate the current `agent-dispatch run`/supervisor startup
      reconciliation path (likely `supervisor.py` /
      `supervisor_daemon.py` / `coordinator_loops.py` — **not yet read in
      this session**; first Phase 4 task is exploration, not
      implementation) and document today's actual behavior on restart for
      a task that was mid-wait when the process died: does anything
      currently re-examine it, or does it silently sit until an operator
      notices?
- [ ] Extend whichever reconciliation pass already exists (or the
      liveness-GC loop, if that is the closer fit) so a task recovered from
      a confirmed-dead supervisor/coordinator process — one whose wait
      cannot be proven to have resolved normally — is woken with an
      explicit, honest note recorded in the exact place its awaited result
      would have gone (e.g. a synthetic wake/steer message: "This wait did
      not resolve normally; the agent-dispatch process serving it stopped
      unexpectedly and was restarted. <original wait context, if any> was
      not confirmed resolved."), rather than either hanging forever or
      silently resuming as if nothing happened.
- [ ] Tests: simulate a mid-cooldown process restart (kill the fake clock's
      backing process / reload the queue against the same DB file) and
      assert the resumed task's audit trail / delivered message contains
      the honest failure note, not a bare unlabeled resume.

### Phase 5 - Uniform submission and task-owned confirmation policy

- [ ] Resolve emitter `auto_confirm` and manual per-task review choices into
      immutable-at-creation effective policy; manual approval is the default.
- [ ] Make all worker completion surfaces submit only. Independent default
      evaluator confirmation may be immediate but must be a separate decision.
- [ ] Define migration of `require_verification` and existing tasks without
      silently auto-confirming pending reviews or breaking domain evaluators.
- [ ] Preserve no-lane/no-live-worker review and terminal COMPLETED semantics.

### Phase 6 - Contract-bearing output and durable conversation

- [ ] Extend bounded result storage with Markdown and optional structured data
      on submissions and steering requests; retain input forms separately.
- [ ] Enforce a task-owned, versioned JSON Schema at every output write boundary.
- [ ] Journal card/output revisions, related answers, and review decisions;
      preserve rejected results and fence acceptance to the reviewed revision.
- [ ] Add explicit completed-task follow-up creation with outcome/history
      lineage; never reopen the completed parent.

### Phase 7 - Fast acceptance and affirmative asynchronous delivery

- [ ] Measure Picker/CLI resolution, HTTP, DB commit, acknowledgment, bridge
      acceptance, and worker consumption; diagnose latency rather than infer it
      from outer timeout constants.
- [ ] Consume #5701's idempotent acceptance work after it lands; do not build
      a competing retry/dedup implementation.
- [ ] Acknowledge saved steering and durable delivery identity without waiting
      for startup; make continuation queued/claimed while preserving assignment
      and preventing overlap with a retiring worker.
- [ ] Separate transport acceptance from worker receipt and reconcile missing
      acknowledgments with bounded retries, fencing, and visible failures.
- [ ] Emit correlated attempt/outcome diagnostics outside conversation history;
      prevent wake-to-sleep churn without work-bearing cause.

Phases 5-7 follow the detailed
[amendment](task-conversation-amendment.md); their UI consumer is owned by
[Tasks-pane Phases 10-12](../agent-dispatch-tasks-pane-ux-overhaul/README.md).
These are planned extensions, not claims that the implementation exists.

<!-- backlog-reconciliation:7556:transfer:3681:to:efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md -->

### Direct complete manual-task reliability acceptance retained from #3681

Append to this SAME existing reviewed Active ROOT, not a child-only link. Preserve EVERY
existing checked/unchecked Plan and own Validation row, entire Journal and
task-conversation amendment. Historical CONFIRMED implementation remains credited
history; current work uses SUBMITTED/COMPLETED and never restores the old terminal enum.
Planning owns documentation only; separately approved backend phase owners and
Tasks-pane/UI/native owners retain their actual responsibilities.

#### Plan: original monitor and recovery programme

- [ ] Credit actual #3684 monitor vocabulary, #3715 historical confirmation and #3780
  wired default cooldown. Reconcile current declaration,
  suspend/resume/API/CLI/coordinator paths with default cooldown, chosen override and
  deliberate more-specific-wait/opt-out semantics. A monitor resolves its chosen
  condition; its deadline is not expiry of unrelated live/unknown/nonexpiring task
  claims.
- [ ] Retain round-robin time slicing and the existing interactive versus cold-headless
  resume split. No competing scheduler/wake mechanism, overlapping receiver or unwatched
  accidental wait is introduced.
- [ ] Qualify crash/restart honesty: preserve exact durable task/progress/owner context
  and place an explicit interrupted/unconfirmed-wait explanation at the applicable
  resumed result/steer boundary. A restarted process is not uninterrupted execution or
  proof a wait resolved; never promise the same session/process without evidence.

#### Plan: independent confirmation and current state semantics

- [ ] Resolve emitter auto-confirm and manual/default/opt-out choices onto the task at
  creation, preserving configured domain evaluation. Manual approval remains the
  default; worker completion surfaces ONLY submit. Independent default evaluation may
  immediately confirm where selected but remains a separate policy decision.
- [ ] Leave absent/failing evaluator or pending review pending. Define legacy
  require_verification/existing-task migration without silently accepting existing
  submissions or bypassing domain gates; no worker self-confidence substitution.
- [ ] Use SUBMITTED for review pending and terminal COMPLETED for acceptance. Fence
  accept/reject by relevant submission revision AND owner incarnation; preserve valid
  retry/late enrichment. Do not indiscriminately delete compatibility paths or reopen a
  truly completed parent.

#### Plan: full output, schemas, history and follow-ups

- [ ] Preserve Markdown plus optional structured JSON on submissions and intermediate
  steering outputs, BESIDE existing request_input/answer forms. Submission output is not
  automatically a request for more instructions.
- [ ] Task-owned VERSIONED JSON Schema is authoritative at EVERY governed
  output/revision write. Submission schemas govern submission revisions; intermediate
  steering-output schemas govern those outputs, not form answers. Declared-schema kinds
  require data; other kinds permit Markdown-only. Prose criteria/types do not become
  executable validators or a second schema authority.
- [ ] Keep bounded non-executable validation/rendering, declared
  dialect/local-reference/size rules, no remote-reference fetch and explicit
  diagnostics. Shape-valid but substantively wrong data still requires review; optional
  derived TypeScript types do not replace runtime authority.
- [ ] Preserve append-only output/card/answer/submission/review revisions, rejected
  output/feedback and legacy migration without invented lost history. Drafts remain
  durable unpublished drafts, separate from answer acceptance/publication/delivery.
  Current cards/results are projections, not independent competing truth.
- [ ] Rejection preserves rejected result/feedback and queued continuation under its
  valid task state. COMPLETED follow-up creates a NEW explicit idempotent linked task
  from selected accepted outcome/history and new instructions, with its own
  goal/schema/policy/routing; do not erase or implicitly reopen the completed parent.

#### Plan: fast durable acceptance, actual delivery and diagnostics

- [ ] Persist answer plus durable delivery operation atomically and acknowledge
  immediately after persistence, independently of cold start/bridge prompt/next turn.
  Retry/ambiguous HTTP acknowledgment reuses the exact operation identity through
  existing #5701 acceptance/idempotency, not a second dedup store.
- [ ] Preserve assignment/worktree while making cold continuation QUEUED or CLAIMED;
  mark STARTED only when work begins. No overlap with a still-retiring body. Nudge live
  workers or admit stopped workers through their actual pool/owner; no wake without
  answer/new work/resolved monitor cause.
- [ ] Distinguish transport/bridge acceptance from actual worker consumption, using the
  existing applicable answer receipt and exact task/owner/incarnation fences. Reconcile
  missing routes, stale delivery, lost acknowledgments, disconnects and
  delivered-but-unconsumed input with bounded retry and visible partial/failure
  outcomes, never a fabricated application.
- [ ] Attribute actual launcher/identity/endpoint/HTTP/DB/ack/delivery/worker stages
  with bounded redacted monotonic diagnostics OUTSIDE task conversation history. Retain
  attempt, outcome and unknown metrics without content/credential logs, overlapping-span
  sums or inferring cause from configured outer timeouts.
- [ ] Compose merged #5701 retry/idempotency and #5668 attention foundations as
  delivered interfaces, not still-open claims. Preserve #2057/#3002/#2453, current
  investigating UI/source/native holds and backend-versus-Tasks-pane presentation
  ownership. No competing implementation or responsibility takeover.

#### Direct Validation and the ROOT GLOBAL completion requirement

- [ ] Qualify default/overridden/suppressed cooldown and true resolution through current
  pure, queue, HTTP, CLI/MCP/library and coordinator paths, with race/abandon/repeated
  resume and original round-robin semantics. Preserve source/native/integration tier
  distinctions.
- [ ] Qualify original crash-in-wait/restart case with retained identity/progress and
  the explicit honest interruption result, not a bare recovery label.
- [ ] Manual question -> Markdown/data SUBMITTED -> independent accept/reject ->
  preserved rejected output/feedback -> new linked completed-task follow-up. Validate
  actual capacity release and unchanged accepted parent.
- [ ] Cover manual default/opt-out, emitter auto-confirm on/off,
  configured/default/missing/failing evaluator, pending-task migration and uniform
  CLI/HTTP/MCP/library behavior; shape validation is not substantive verification.
- [ ] Cover missing/invalid/schema-bearing data at EVERY revision, separate
  submission/intermediate/form contracts, Markdown-only allowed kinds,
  dialect/version/local reference/remote-reference denial/bounds and truthful errors.
- [ ] Prove restart-safe append-only card/output/answer/review history,
  draft/publication distinction, stale revision/owner review rejection, rejected-result
  retention, honest legacy backfill and bounded pagination.
- [ ] Prove duplicate/ambiguous acknowledgment records one answer/operation, immediate
  persistence receipt independently of cold/live startup, distinct worker consumption,
  route outage/restart/stale-generation reconciliation, no no-work wake churn and no
  old/new overlap.
- [ ] Preserve actual consumer latency/correlation/privacy tests and original isolated
  real CLI-to-daemon/cold-live-worker Windows/Linux, affected first-use/clean-room and
  available live-venue evidence, with explicit unavailable-tier reasons. Do not force
  effectful matrices into required unit-only CI or invent unrelated every-host gates.

#### Continuing Coordination, Journal and receiving gate

Keep this ROOT Active under the SAME planning, separately approved backend-phase,
Tasks-pane/UI/native, task/evaluator/history/steering and current source custodians.
Record later slices through eligible OPEN bounded-source claims and this ROOT
Plan/Validation/Journal; do not make a closed #3681 a required live coordination token.
A NEW reviewed receiving PR must reconcile operative ROOT/header/amendment pointers that
otherwise require #3681 live, without deleting historical references, altering claims or
assigning all implementation to the documentation planner.

Journal the actual administrative intake only after ordinary review, ACTUAL merge and
FULL CURRENT ROOT Plan AND Validation/Coordination/Journal readback proves every
original, refinement and amendment clause survives directly here, all old rows and
independent owners remain, and no genuine hold still requires the source live. Otherwise
RETAIN #3681. No task/claim/configuration/native/model/process/service/source effect or
implementation authority is created by this proposal.

### Finite producing-harness versus code-target guidance from #4464

**Agent-recommended tracking amendment.** Preserve the original guidance-only
realization and current lane-versus-target foundations, not a new resolver
or a mandatory default redesign.

- [ ] **P4464-1:** Explicitly reason about the PRODUCING harness lane whenever
  work touches or is authored from multiple repository worktrees. When CWD
  is not unambiguously that harness, pass `--repo <harness>` together with
  `--target-repo <code-target>`. Legitimate target-repository CWD does not
  adopt that repository as another operating harness.
- [ ] **P4464-2:** Put the cross-CWD warning in the existing owning skill
  and/or pick-and-claim guidance, retaining supported unambiguous defaults.
  Root-versus-delegate resolver enrichment is optional; no task movement,
  new registry/resolver, harness reconfiguration or restoration of historical
  task enum/API direction is required.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Direct retained Transport/Bridge acceptance

**Agent-recommended additive tracking amendment.** Preserve every incumbent
Request, status, checked credit, original row and first unresolved gate. The
direct rows below retain unfinished work at its original finite scope,
alternatives and evidence tiers. They do not authorize implementation,
native/installed trials, operational changes, source closure, reassignment
or claim/resource release. Normal review, actual merge and complete current
receiving readback precede any separate source-specific disposition.


#### Source #2885

- [ ] **P.2885.semantic-reason:** Every suspended task carries lifecycle-authoritative
  STRUCTURED/QUERYABLE semantic reason/category AND concise human explanation. Preserve all original
  steer/review, external/yielded wait, clarification, dependency, hold and legacy-unknown
  distinctions; example enums are examples, not a closed monitor-kind/status mandate.

- [ ] **P.2885.authoritative-source-and-incarnation:** Recipe/emitter or another actual lifecycle
  owner supplies/updates/resolves the reason with the original task/attempt/owner/incarnation and
  outstanding question/answer. Safe intent-seeded defaults must not guess categories from ordinary
  card/progress prose or treat a supplied description as semantic authority.

- [ ] **P.2885.query-age-attention:** Expose the same semantic reason to lightweight task
  query/API/CLI and Tasks-pane list AND/OR supported notification/operator-attention surface.
  Present actual current-state duration/age alongside reason and an obvious route to the outstanding
  question; aggregate churn or opening every card is not discoverability.

- [ ] **P.2885.historical-awaitingfalse-clarification:** Structurally distinguish
  explicit-instruction/clarification-needed from routine pending review, ordinary steer/approve and
  external wait, including original BOTH SUSPENDED with awaiting_steer=false shape. A single boolean
  or parsing status/title/body is not equivalent semantic classification.

- [ ] **P.2885.fresh-elicitation-and-resolution:** Any genuinely ambiguous/unresolved answer
  surfaces as a FRESH explicit question/elicitation, never guessed/treated as approval. Keep the
  question/reason/current incarnation correlated; update/clear only through accepted/resolved
  lifecycle, not transport delivery alone. Re-carding mitigates but does not prove complete reason
  visibility.

- [ ] **P.2885.hold-not-death-resource-release:** Semantic reason/presentation is separate from
  monitor/resolution/wake metadata and user Pause/hold. Adding or clearing a reason NEVER expires
  claims, frees a lane/resource, resumes/requeues without required instruction, authorizes a
  verdict, or infers death from age. Existing legitimate monitor-resume behavior remains with its
  original owner.

- [ ] **P.2885.GENERAL-session-viewer-compatibility:** Preserve original LONG-TERM general inline
  per-session Worktree Manager agent-chat/tool-call/user-elicitation viewer direction, for agent
  sessions rather than only task results. Pending steer card is a first-class event; reason/event
  interface must compose with that future view, not freeze today's bespoke card or equate
  task-output history with a general-session viewer.

- [ ] **P.2885.retained-whole-union:** Retain the complete current #2885 original, BOTH refinements
  and ALL eight adopted2878 clarification rows plus seven residual-refinement checks, original
  alternatives/venues/finite scope/history/query/presentation/custody. It remains LONG-TERM
  planning, not immediate implementation authority. Do NOT absorb2879
  nudges/resource/complete-transfer or2887 recipe/native/policy scope.

- [ ] **P.2885.r0-check-20:** Give the explicit-instruction-required/clarification-needed state a
  real structurally queryable semantic distinction from routine pending review, ordinary
  operator-steer/approve waiting and an external wait. It must work for the historical shape where
  both cases were SUSPENDED with awaiting_steer=false; a boolean awaiting_steer or parsing card
  status/title/body is not an equivalent fix.

- [ ] **P.2885.r0-check-21:** Carry a concise human-readable explanation alongside the structured
  reason, supplied by the recipe/emitter or another lifecycle-authoritative source. Preserve the
  relation to the actual outstanding question/answer and current task incarnation. A prompt,
  free-text recommendation or normal card alone is not the shared reason contract.

- [ ] **P.2885.r0-check-22:** Make clarification visibly attention-requiring through the Tasks-pane
  list and/or a supported notification, with an obvious path to the outstanding question. An
  operator must not have to open/read every card to discover that a prior answer was ambiguous. Keep
  the signal distinct from routine review waiting, and make the same semantic reason queryable by
  lightweight consumers.

- [ ] **P.2885.r0-check-23:** Preserve the short-term follow-up invariant: any genuinely unresolved
  or ambiguous answer is surfaced as a fresh explicit question/elicitation, not silently guessed,
  treated as approval, or left implicit. Re-carding is a mitigation, not proof of the broader
  structural/discoverability outcome. Do not require adopting a private reviewer charter or
  provider-specific business policy upstream.

- [ ] **P.2885.r0-check-24:** Keep clarification separate from a user hold/Pause and from task
  death. Presenting a reason must not change ownership, expire a claim, free a lane, resume work
  without the needed instruction, or authorize a review/verdict action. Update/clear the visible
  reason through the corresponding accepted/resolved lifecycle, not simply because a transport
  reports delivery.

- [ ] **P.2885.r0-check-25:** Preserve the operator's accepted longer-term shape: a general inline
  per-session agent-chat viewer within Worktree Manager, usable for agent sessions rather than only
  a task-result viewer, with a pending steer card represented as a first-class
  tool-call/user-elicitation event. Design the reason/event interface to compose into that future
  view; do not freeze today's bespoke card representation as the only supported model or claim a
  task-output history alone fulfills the general-session direction.

- [ ] **P.2885.r0-check-26:** Compose existing backend task-output/history/delivery contracts and
  Tasks-pane presentation ownership, preserving native/hosting authority. #2887 remains the separate
  reviewer-recipe convergence owner; this generic semantic/UI acceptance is not a transfer of its
  domain policy or a declaration that its native adoption gates passed.

- [ ] **P.2885.r0-check-27:** Preserve observable distinctions for ordinary pending review, ordinary
  operator steer, explicit clarification after an ambiguous answer, external-event/yielded waiting,
  dependency blocking, explicit user hold and legacy unknown reason. Check reason
  persistence/query/presentation and its resolution/repeated-follow-up behavior with the current
  accepted schema, rather than requiring an obsolete card-only method.

- [ ] **P.2885.r1-check-8:** Represent why each SUSPENDED task is dormant through structured,
  queryable semantics and short human-readable explanation. Preserve at least operator-steer,
  external-event/yielded-wait, clarification and dependency-blocked cases, with explicit
  unknown/legacy handling rather than guessed categories parsed from prose.

- [ ] **P.2885.r1-check-9:** Have the lifecycle-owning recipe/emitter supply the reason, and define
  any safe default seeded by run/create intent. Preserve attribution and current task/attempt/owner
  context; an ordinary description or current card title is not by itself a semantic classification
  contract.

- [ ] **P.2885.r1-check-10:** Make the reason available to task query/API/CLI and Tasks-pane
  presentation or another authoritative operator-attention surface. Combine the actual state
  duration/age with the actual reason so an operator need not open cards/progress logs merely to
  learn the blocker. Do not confuse aggregate churn reporting with a task's own age.

- [ ] **P.2885.r1-check-11:** Keep monitor/resolution metadata distinct from semantic suspension
  reason and from a user hold. Preserve current cooldown/external-wait behavior, explicit operator
  holds, pending input and existing custody; adding presentation must not release/requeue work or
  infer death from age.

- [ ] **P.2885.r1-check-12:** Accept the full transferred #2878 checklist before retiring its
  wrapper: structurally distinguish clarification from routine review/steer, visibly request
  attention, handle ambiguous follow-up as a fresh explicit elicitation, and clear/update the reason
  only through the appropriate lifecycle.

- [ ] **P.2885.r1-check-13:** Preserve the longer-term general Worktree Manager per-session
  agent-chat/tool-call viewer intent, including a pending card as a first-class elicitation event.
  Task-output history alone is not that general viewer. Compose the backend task-output/history
  owners and UI owners without replacing native/hosting authority or mandating today's card
  representation forever.

- [ ] **P.2885.r1-check-14:** Cover persistence/readback, repeated suspension/reason updates, legacy
  unknown reasons, each category, follow-up clarification and subsequent resolution across the
  supported producer/query/UI surfaces. Intent documents or a narrow suspend fixture do not prove
  the complete integration or deployed behavior.

## Validation Plan

- [ ] GLOBAL ROOT GATE: resolve EVERY incumbent ROOT Plan AND own Validation item, ALL
  direct acceptance above and original amendment matrix at their retained evidence tiers
  OR explicitly transfer discrete remaining work to named accepted objectives before
  Status Done/archive. Preserve already-delivered checked credit. A phase, receipt,
  source merge or administrative intake is not parent completion. This global rule does
  not make every unrelated parent item a prerequisite for implementing/qualifying a
  finite slice.

- [ ] Complete the [amendment validation matrix](task-conversation-amendment.md#validation-matrix)
      across queue, HTTP, CLI, MCP, evaluator, service restart, and real worker
      boundaries, including the manual `2 + 2` scenario.

- [x] `task_state_machine.py`'s own shape checks (`reachable_states`,
      `states_without_exit`, `terminal_states_with_exit`) pass with
      `CONFIRMED` included -- confirmed via the existing
      `test_task_state_machine.py` suite, unmodified except for the one
      predicted hardcoded-state-list assertion.
- [x] Full `plugins/agent-dispatch` test suite green: **3434 passed, 19
      skipped** (run twice, before and after the module-size mixin
      extraction), the same single pre-existing failure both times
      (confirmed via `git stash` against the unmodified baseline).
- [ ] A hand-run scenario end-to-end: propose → queue → claim → start →
      suspend (no explicit monitor) → (fake-clock-advance) → auto-resume →
      submit → confirm; and the sibling rejection path: submit → reject →
      queue → claim → ... → confirm. Re-check the shipped monitor path
      alongside the new policy; no completed task can originate rejection.
- [ ] A hand-run crash scenario: suspend with a cooldown pending → kill the
      supervisor process → restart it → assert the honest recovery note
      lands, per Phase 4.
- [ ] Cite this effort's Phase 2/3/4 landings back into the parent vision's
      Provenance-adjacent reality docs once code lands (per envisioning's
      "close the loop" step) — no vision *edit* needed unless Phase 2's
      risk-audit surfaces a genuine blind spot the vision failed to state.
- [ ] **V4464-1:** Qualify the actual supported neutral/cross-CWD and explicit
  target examples: producing `--repo` identity stays independent of
  `--target-repo` code metadata. Generic project examples or a spawn/task-
  state receipt do not substitute for the exact lane warning.
- [ ] **V4464-2:** Resolve every direct #4464 Plan clause through its original
  guidance-only option, existing routing defaults and task/source custody,
  without adoption, lane/task mutation or a competing resolver. Finite
  source/consumer evidence is separate from unrelated backend/UI/native or
  whole-parent acceptance; no generic owner ACK or task operation is added.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### Global validation of retained Transport/Bridge acceptance

#### Source #2885

- [ ] **V.2885.semantic-reason:** Qualify the COMPLETE matching original/refined clause at its
  ORIGINAL finite supported producer/query/UI/native tier and alternatives, preserving delivered
  credit and same source/resource custody: Every suspended task carries lifecycle-authoritative
  STRUCTURED/QUERYABLE semantic reason/category AND concise human explanation. Preserve all original
  steer/review, external/yielded wait, clarification, dependency, hold and legacy-unknown
  distinctions; example enums are examples, not a closed monitor-kind/status mandate.

- [ ] **V.2885.authoritative-source-and-incarnation:** Qualify the COMPLETE matching
  original/refined clause at its ORIGINAL finite supported producer/query/UI/native tier and
  alternatives, preserving delivered credit and same source/resource custody: Recipe/emitter or
  another actual lifecycle owner supplies/updates/resolves the reason with the original
  task/attempt/owner/incarnation and outstanding question/answer. Safe intent-seeded defaults must
  not guess categories from ordinary card/progress prose or treat a supplied description as semantic
  authority.

- [ ] **V.2885.query-age-attention:** Qualify the COMPLETE matching original/refined clause at its
  ORIGINAL finite supported producer/query/UI/native tier and alternatives, preserving delivered
  credit and same source/resource custody: Expose the same semantic reason to lightweight task
  query/API/CLI and Tasks-pane list AND/OR supported notification/operator-attention surface.
  Present actual current-state duration/age alongside reason and an obvious route to the outstanding
  question; aggregate churn or opening every card is not discoverability.

- [ ] **V.2885.historical-awaitingfalse-clarification:** Qualify the COMPLETE matching
  original/refined clause at its ORIGINAL finite supported producer/query/UI/native tier and
  alternatives, preserving delivered credit and same source/resource custody: Structurally
  distinguish explicit-instruction/clarification-needed from routine pending review, ordinary
  steer/approve and external wait, including original BOTH SUSPENDED with awaiting_steer=false
  shape. A single boolean or parsing status/title/body is not equivalent semantic classification.

- [ ] **V.2885.fresh-elicitation-and-resolution:** Qualify the COMPLETE matching original/refined
  clause at its ORIGINAL finite supported producer/query/UI/native tier and alternatives, preserving
  delivered credit and same source/resource custody: Any genuinely ambiguous/unresolved answer
  surfaces as a FRESH explicit question/elicitation, never guessed/treated as approval. Keep the
  question/reason/current incarnation correlated; update/clear only through accepted/resolved
  lifecycle, not transport delivery alone. Re-carding mitigates but does not prove complete reason
  visibility.

- [ ] **V.2885.hold-not-death-resource-release:** Qualify the COMPLETE matching original/refined
  clause at its ORIGINAL finite supported producer/query/UI/native tier and alternatives, preserving
  delivered credit and same source/resource custody: Semantic reason/presentation is separate from
  monitor/resolution/wake metadata and user Pause/hold. Adding or clearing a reason NEVER expires
  claims, frees a lane/resource, resumes/requeues without required instruction, authorizes a
  verdict, or infers death from age. Existing legitimate monitor-resume behavior remains with its
  original owner.

- [ ] **V.2885.GENERAL-session-viewer-compatibility:** Qualify the COMPLETE matching
  original/refined clause at its ORIGINAL finite supported producer/query/UI/native tier and
  alternatives, preserving delivered credit and same source/resource custody: Preserve original
  LONG-TERM general inline per-session Worktree Manager agent-chat/tool-call/user-elicitation viewer
  direction, for agent sessions rather than only task results. Pending steer card is a first-class
  event; reason/event interface must compose with that future view, not freeze today's bespoke card
  or equate task-output history with a general-session viewer.

- [ ] **V.2885.retained-whole-union:** Qualify the COMPLETE matching original/refined clause at its
  ORIGINAL finite supported producer/query/UI/native tier and alternatives, preserving delivered
  credit and same source/resource custody: Retain the complete current #2885 original, BOTH
  refinements and ALL eight adopted2878 clarification rows plus seven residual-refinement checks,
  original alternatives/venues/finite scope/history/query/presentation/custody. It remains LONG-TERM
  planning, not immediate implementation authority. Do NOT absorb2879
  nudges/resource/complete-transfer or2887 recipe/native/policy scope.

- [ ] **V.2885.r0-check-20:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Give the explicit-instruction-required/clarification-needed state a
  real structurally queryable semantic distinction from routine pending review, ordinary
  operator-steer/approve waiting and an external wait. It must work for the historical shape where
  both cases were SUSPENDED with awaiting_steer=false; a boolean awaiting_steer or parsing card
  status/title/body is not an equivalent fix.

- [ ] **V.2885.r0-check-21:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Carry a concise human-readable explanation alongside the structured
  reason, supplied by the recipe/emitter or another lifecycle-authoritative source. Preserve the
  relation to the actual outstanding question/answer and current task incarnation. A prompt,
  free-text recommendation or normal card alone is not the shared reason contract.

- [ ] **V.2885.r0-check-22:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Make clarification visibly attention-requiring through the
  Tasks-pane list and/or a supported notification, with an obvious path to the outstanding question.
  An operator must not have to open/read every card to discover that a prior answer was ambiguous.
  Keep the signal distinct from routine review waiting, and make the same semantic reason queryable
  by lightweight consumers.

- [ ] **V.2885.r0-check-23:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Preserve the short-term follow-up invariant: any genuinely
  unresolved or ambiguous answer is surfaced as a fresh explicit question/elicitation, not silently
  guessed, treated as approval, or left implicit. Re-carding is a mitigation, not proof of the
  broader structural/discoverability outcome. Do not require adopting a private reviewer charter or
  provider-specific business policy upstream.

- [ ] **V.2885.r0-check-24:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Keep clarification separate from a user hold/Pause and from task
  death. Presenting a reason must not change ownership, expire a claim, free a lane, resume work
  without the needed instruction, or authorize a review/verdict action. Update/clear the visible
  reason through the corresponding accepted/resolved lifecycle, not simply because a transport
  reports delivery.

- [ ] **V.2885.r0-check-25:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Preserve the operator's accepted longer-term shape: a general inline
  per-session agent-chat viewer within Worktree Manager, usable for agent sessions rather than only
  a task-result viewer, with a pending steer card represented as a first-class
  tool-call/user-elicitation event. Design the reason/event interface to compose into that future
  view; do not freeze today's bespoke card representation as the only supported model or claim a
  task-output history alone fulfills the general-session direction.

- [ ] **V.2885.r0-check-26:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Compose existing backend task-output/history/delivery contracts and
  Tasks-pane presentation ownership, preserving native/hosting authority. #2887 remains the separate
  reviewer-recipe convergence owner; this generic semantic/UI acceptance is not a transfer of its
  domain policy or a declaration that its native adoption gates passed.

- [ ] **V.2885.r0-check-27:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Preserve observable distinctions for ordinary pending review,
  ordinary operator steer, explicit clarification after an ambiguous answer, external-event/yielded
  waiting, dependency blocking, explicit user hold and legacy unknown reason. Check reason
  persistence/query/presentation and its resolution/repeated-follow-up behavior with the current
  accepted schema, rather than requiring an obsolete card-only method.

- [ ] **V.2885.r1-check-8:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Represent why each SUSPENDED task is dormant through structured,
  queryable semantics and short human-readable explanation. Preserve at least operator-steer,
  external-event/yielded-wait, clarification and dependency-blocked cases, with explicit
  unknown/legacy handling rather than guessed categories parsed from prose.

- [ ] **V.2885.r1-check-9:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Have the lifecycle-owning recipe/emitter supply the reason, and
  define any safe default seeded by run/create intent. Preserve attribution and current
  task/attempt/owner context; an ordinary description or current card title is not by itself a
  semantic classification contract.

- [ ] **V.2885.r1-check-10:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Make the reason available to task query/API/CLI and Tasks-pane
  presentation or another authoritative operator-attention surface. Combine the actual state
  duration/age with the actual reason so an operator need not open cards/progress logs merely to
  learn the blocker. Do not confuse aggregate churn reporting with a task's own age.

- [ ] **V.2885.r1-check-11:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Keep monitor/resolution metadata distinct from semantic suspension
  reason and from a user hold. Preserve current cooldown/external-wait behavior, explicit operator
  holds, pending input and existing custody; adding presentation must not release/requeue work or
  infer death from age.

- [ ] **V.2885.r1-check-12:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Accept the full transferred #2878 checklist before retiring its
  wrapper: structurally distinguish clarification from routine review/steer, visibly request
  attention, handle ambiguous follow-up as a fresh explicit elicitation, and clear/update the reason
  only through the appropriate lifecycle.

- [ ] **V.2885.r1-check-13:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Preserve the longer-term general Worktree Manager per-session
  agent-chat/tool-call viewer intent, including a pending card as a first-class elicitation event.
  Task-output history alone is not that general viewer. Compose the backend task-output/history
  owners and UI owners without replacing native/hosting authority or mandating today's card
  representation forever.

- [ ] **V.2885.r1-check-14:** Qualify the COMPLETE matching original/refined clause at its ORIGINAL
  finite supported producer/query/UI/native tier and alternatives, preserving delivered credit and
  same source/resource custody: Cover persistence/readback, repeated suspension/reason updates,
  legacy unknown reasons, each category, follow-up clarification and subsequent resolution across
  the supported producer/query/UI surfaces. Intent documents or a narrow suspend fixture do not
  prove the complete integration or deployed behavior.

- [ ] **G2885:** Resolve every directly retained #2885 Plan and Validation clause at its original
  finite evidence tier, preserving alternatives, completed credit and existing custodians.
  Administrative tracking is not technical completion or claim release.

## Proposal

Historical Phase 2 API shapes, as recorded then (current state vocabulary and
extension policy are in the 2026-10-08 amendment):

- `TaskQueue.confirm(task_id, *, actor=None, expected_status=None,
  expected_generation=None, now=None) -> Task` -- `COMPLETED -> CONFIRMED`,
  idempotent replay, no owner required.
- `TaskQueue.reopen_completed(task_id, *, reason=None, steer_fields=None,
  sender=None, expected_status=None, expected_generation=None, now=None)
  -> Task` -- `COMPLETED -> QUEUED`, clears the disputed completion claim
  (`result`/`result_ref`/`completed_by`/`completed_at`), optionally records
  an operator steer atomically.
- CLI: `agent-dispatch confirm <id> [--actor A]`,
  `agent-dispatch reopen <id> [--reason R] [--field K=V ...] [--sender S]`.
- HTTP: `POST /tasks/{id}/confirm`, `POST /tasks/{id}/reopen`.
- Evaluator: a `SpecEvaluator` rule spec gains `"confirm": true` (a
  `Confirm` decision) as a sibling to `"emit": {...}`; wired through
  `supervisor.advance_via_evaluator` via `apply_decisions(...,
  task_id=tid, confirmer=self.client.confirm)`.

Phase 3/4's shapes are still pending their own design pass.

## Journal

### 2026-10-28 - Finite producing-lane guidance intake

- Added direct #4464 Plan and actual global Validation with continuing
  coordination, retaining explicit multi-worktree-CWD lane reasoning,
  `--repo` producer versus `--target-repo` metadata and the guidance-only
  realization. Existing task-owned routing/claim/policy foundations remain
  credit, not proof the exact warning is delivered.
- Preserve every original row, checked credit, amendment and independent
  custodian. Normal reviewed actual merge and complete current readback
  precede a separate source judgment. No source audit, task move, provider
  operation, peer contact, implementation or resource release occurs here.

### 2026-10-23 - Direct retained acceptance from #3681

- Retained the complete #3681 acceptance directly in this ROOT Plan, its actual global
  Validation and continuing Coordination. Every incumbent obligation, historical
  outcome, alternative and custodian remains; neutral historical privacy/coordination
  corrections change no substantive gate. New normal review, actual merge and complete
  current readback precede source supersession. The effort stays Active; no
  implementation, tests, native result or rollout is claimed.

### 2026-09-25 — Kickoff
- Effort created directly from the same-session vision work (PR #3622/#3625)
  and the operator's cooldown-monitor / crash-recovery / ACP-framing
  follow-up. Context above already reflects real source-reading (not
  speculation) of `task_state_machine.py`, `queue_lifecycle.py`,
  `queue_storage.py`, `queue_steering.py`, and `bridge.py` — in particular
  the discovery that the existing wake outbox only nudges an
  **already-`STARTED`** task's live session and never itself performs a
  `SUSPENDED -> STARTED` transition, which materially changes Phase 3's
  design from "reuse `_enqueue_wake` as-is" to "route through the existing
  interactive/cold-headless split." Phase 4's supervisor-restart behavior
  was **not** read this session; flagged as its own exploration task rather
  than guessed at.
- Per the `planning-efforts` skill's review gate: this README is submitted
  as a PR for automated review before any Phase 1 code lands.

### 2026-09-25 — Plan PR (#3682) merged; Phase 1 execution begins, and immediately corrects itself
- Plan cleared review and merged to `dev`. Began Phase 1 as originally
  written (declare `CONFIRMED` + its transitions, keeping `COMPLETED`
  terminal) — and immediately ran the existing
  `test_task_state_machine.py` against a draft table edit rather than
  reasoning from the docstrings alone.
  `test_no_transition_originates_from_a_terminal_state` failed: it asserts,
  as a hard structural invariant, that no declared transition may source
  from a state in `TERMINAL_STATES`. Since `confirm`/`reopen_completed`
  both source from `COMPLETED`, and `COMPLETED` is in `Status.TERMINAL`
  today, there is no way to add either transition without also resolving
  `COMPLETED`'s terminal-ness in the same change — the original Phase
  1/Phase 2 split ("declare the transition now, decide terminal-ness
  later") does not survive contact with the real test suite. Re-split the
  Plan: Phase 1 is now *only* the standalone `monitors.py` module (no
  `Status`/table touch at all, so it stays genuinely safe and independent);
  the state-machine change is now entirely Phase 2, landing the
  terminal-ness resolution and the two new transitions together, in one
  reviewed change, rather than pretending the first half was decouplable.
- This is exactly the kind of finding this effort's own Validation Plan
  exists to catch, and exactly why the effort's Context section demanded
  real source-reading over speculation — the difference here is running the
  actual suite, not just reading the module.
- Proceeding to implement the corrected Phase 1 (`monitors.py`) now.

### 2026-09-25 — Phase 2 lands: `confirmed` is real, wired end to end
- Executed the corrected Phase 2 in full: `Status.CONFIRMED` added,
  `COMPLETED` moved out of `Status.TERMINAL` into the new `Status.CONCLUDED`
  superset (`{COMPLETED, CONFIRMED, ABANDONED, DEAD_LETTER}`), `confirm`/
  `reopen_completed` declared in `task_state_machine.py` and implemented in
  `queue.py`. Every call site the audit found was fixed (see Phase 2's
  checklist above for the itemized list) — including one the original audit
  missed entirely: `doctor.py` imports `TERMINAL_STATES` **directly** from
  `task_state_machine` (not via `Status.TERMINAL`), a second import path the
  Context section's literal-string grep never caught. The full test suite
  caught it immediately (`test_doctor.py` regressions) — a second concrete
  proof that running the suite, not just grepping for `Status.TERMINAL`,
  is what actually finds every real call site.
- **A genuinely nice surprise**: `producers/evaluator.py`'s `SpecEvaluator`
  turned out to already be the real, working "evaluator" the parent vision
  describes (`supervisor.advance_via_evaluator` already runs it over every
  `COMPLETED`/`ABANDONED` task). Rather than deferring
  *verify-the-completion-claim*'s automatic half as originally planned, added
  a third `Confirm` decision (alongside the existing `Emit`/`NoOp`) — a rule
  declaring `"confirm": true` closes the *originating* task. This is real,
  tested, working automatic confirmation for emitter-driven work, not a
  stub. What's still deferred (see Phase 2's checklist) is wiring the
  `Confirm` decision to `_completion_detail`'s existing corroboration
  check — today a `Confirm` decision trusts its own rule's `when` predicate
  entirely, performing no corroboration of its own.
- Kept `client.py` and `queue_lifecycle.py` under their module-size caps by
  extracting the new methods into their own mixins
  (`client_completion_review.py`, `queue_completion_review.py`), matching
  the codebase's existing componentization pattern rather than requesting a
  baseline widen.
- **Validation**: full `plugins/agent-dispatch` test suite run twice at the
  end of this session (once before, once after the `client.py`/
  `queue_lifecycle.py` mixin extraction) — both **3434 passed, 19 skipped**,
  with the *same single* pre-existing failure both times
  (`test_idle_headless_fleet_nudge_includes_remote_host`, confirmed via
  `git stash` to reproduce identically against the unmodified baseline —
  nothing to do with this change). `check-module-size.py` and syntax checks
  clean. Added tests: `test_monitors.py` (10, Phase 1), `test_queue.py`'s
  new confirm/reopen/`CONCLUDED`/`ABANDONABLE` cases (9), `test_evaluator.py`'s
  new `Confirm`-decision cases (8), `test_card_steer.py`'s HTTP roundtrip
  cases (3), `test_cli.py`'s CLI-parsing + board-group cases (5),
  `test_board_cli.py`'s group-ordering update (1).
- Phase 2 is code-complete. Phases 3 (cooldown monitor) and 4
  (crash-recovery honesty) remain open and are independent of each other
  and of Phase 2 — either may be picked up next.

### 2026-09-25/26 — Phase 3 lands: the cooldown monitor, wired end to end
- Resumed via a standalone context-handoff continuation in a fresh
  worktree. Started from the Phase 3 checklist above rather than
  re-deriving the design, but reading `resume()`'s `_transition` call more
  closely revealed the checklist's own premise was slightly off: the
  interactive/cold-headless split it flags does **not** need a new
  `not_before`-aware wake path, because `resume()` already internally
  branches correctly between the two (an interactive owner transitions
  straight to `started`; a cold-headless owner instead flips
  `resume_requested` and stays `suspended`, for the supervisor's own
  `release_resumed_cold_tasks` poll to pick up) -- see
  `queue_suspend.py`'s module docstring for the exact mechanism. This let
  the whole reconciler collapse to "track a deadline on the task row, then
  call the unmodified `resume()` once it's due" -- no wake-outbox changes,
  no new resume_requested-flip code path, and no risk of the two
  mechanisms (a delayed wake vs. a delayed resume_requested flip) racing
  each other.
- Landed: `monitors.py` gained `suspend_monitor_columns()` (pure) and
  `DEFAULT_SUSPEND_COOLDOWN_SECONDS`; new `monitor_kind`/`monitor_not_before`
  task columns (`queue_common.py`, auto-migrated via the existing
  `_COLUMNS`/`_migrate` mechanism -- no bespoke migration code needed);
  `suspend`/`resume` extracted out of the near-ceiling `queue_lifecycle.py`
  (998/1000 lines, effectively zero headroom) into a new `queue_suspend.py`
  mixin, which also gained `reconcile_cooldowns()`; `client.py` similarly
  had zero headroom against its own baselined ceiling (1175 lines), so
  `suspend` moved to a new `client_suspend.py` mixin (same pattern as
  Phase 2's `client_completion_review.py`). CLI: `--cooldown-seconds` /
  `--no-cooldown` on `suspend`. HTTP: `SuspendBody.cooldown_seconds`,
  using pydantic's `model_fields_set` to distinguish "not passed" (server
  default applies) from an explicit `null` (suppressed) over the wire --
  the client mirrors this with a private `_UNSET` sentinel.
- The reconciler itself doesn't run as its own new opt-in coordinator loop:
  it's piggybacked directly onto the existing always-on liveness-GC loop
  (`_gc_loop` in `coordinator_loops.py`), since a bare suspend's cooldown
  needs the exact same unconditional "never just sit there" guarantee
  liveness GC already gives held tasks -- no new `LoopHealth`/health-endpoint
  wiring needed, unlike the opt-in `handoff_fallback` loop.
- Tests: new `tests/test_queue_suspend.py` (9, fake-clock unit coverage of
  suspend/resume/reconcile_cooldowns incl. the race/abandon/already-resumed
  cases), 3 new coordinator HTTP round-trip cases, 1 new real
  (short-sleep) end-to-end coordinator test proving the live `_gc_loop`
  actually auto-resumes a suspended task, 2 new CLI cases (kwarg
  forwarding + the `--no-cooldown`/`--cooldown-seconds` conflict guard),
  plus `test_queue_lifecycle.py`'s guard test updated for the
  suspend/resume extraction. `check-module-size.py` clean (`client.py`
  1153/1175, `queue_lifecycle.py` 899/1000, both now with headroom instead
  of none). Full `plugins/agent-dispatch` suite run before merging (see
  Validation Plan) -- same single pre-existing flake as Phase 2, nothing
  new.
- Phase 4 (crash-recovery honesty) remains open and is next; genuinely
  unexplored per the Phase 4 checklist.

### 2026-10-08 - Task conversation direction reconciled

Extended the canonical vision and this existing effort rather than creating a
parallel campaign. Added Phases 5-7 and the linked design/validation amendment;
preserved historical checklists and journals without marking unverified work
done. Source inspection found bounded results, operator-answer history,
submitted-task confirm/reject, and a fenced wake outbox already present.
Card revisions, task-owned output schemas, policy separation, and completed-task
follow-ups remain explicit implementation deltas. #5701 owns existing
idempotent-steer work; #5668 owns the attention aggregation interface. No
runtime changes or delivery fixes are claimed by this documentation update.

<!-- backlog-reconciliation:7556:transport-bridge-receiving -->

### 2026-10-28 - Additive Transport/Bridge tracking contracts

Authored direct finite Plan and actual global Validation with continuing

custody after complete current receiving-document and source-contract

research. Normal review, actual merge and complete current readback remain

separate publication gates; no future delivery or disposition is claimed.

Credited existing monitor/review/result/history/delivery foundations while retaining the complete long-term semantic-reason, age, clarification and GENERAL per-session viewer direction.
