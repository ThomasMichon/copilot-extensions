# agent-dispatch: monitor + confirmed state (manual-task reliability primitives)

- **Slug:** `agent-dispatch-monitor-and-confirmed-state`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-slice PRs off `dev`
- **Created:** 2026-09-25
- **Status:** Draft
- **Umbrella issue:** #3681
- **Sub-issues:** _none yet_

## Guiding Intent

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

### Phase 3 — The cooldown monitor (default, round-robin time-slice yield)
- [ ] Extend `queue_storage._enqueue_wake()` (or add a sibling) with an
      optional `not_before` override (default preserves today's
      immediate-delivery behavior exactly — no existing caller is
      affected).
- [ ] Add `TaskQueue.suspend(..., cooldown_seconds: float | None =
      DEFAULT_SUSPEND_COOLDOWN_SECONDS)` — a suspend with **no** more
      specific monitor gets the default cooldown automatically, so a bare
      "yield the lane" is always a real, bounded, monitored wait, never an
      unwatched idle (per *suspension-requires-a-monitor*). Route delivery
      through the **existing interactive/cold-headless split** found in
      Context above — for a cold-headless reservation this likely means
      scheduling a delayed flip of `resume_requested = 1` (a new small
      "cooldown reconciliation" pass alongside the existing liveness GC
      loop, since `resume_requested` today only gets set synchronously),
      not literally reusing `_enqueue_wake`'s interactive-only delivery
      path unchanged.
- [ ] A worker that names a **specific** wait (not the bare default) should
      be able to suspend against a longer, unbounded cooldown or state that
      no cooldown applies (an explicit "I have named my own monitor"
      escape hatch) — but starting with cooldown-only for every monitor
      kind (Phase 1's closed vocabulary of exactly one) is fine; a second
      monitor kind is out of scope here.
- [ ] CLI/API surface: `agent-dispatch suspend <id> --reason "..."
      [--cooldown-seconds N]`.
- [ ] Tests: a suspended task with no explicit cooldown resumes on its own
      after the default interval (fake clock, not a real sleep); an
      explicit `--cooldown-seconds` overrides the default; a task resumed
      by an operator steer before its cooldown fires never double-resumes
      when the stale cooldown wake later becomes due (reuse/extend the
      existing `_wake_is_current` fencing logic for whichever delivery path
      Phase 3 actually lands on).

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

## Validation Plan

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
      complete → confirm; and the sibling reopen path: complete → reopen →
      queue → claim → ... → confirm. (Deferred to Phase 3: the
      no-explicit-monitor auto-resume leg doesn't exist yet.)
- [ ] A hand-run crash scenario: suspend with a cooldown pending → kill the
      supervisor process → restart it → assert the honest recovery note
      lands, per Phase 4.
- [ ] Cite this effort's Phase 2/3/4 landings back into the parent vision's
      Provenance-adjacent reality docs once code lands (per envisioning's
      "close the loop" step) — no vision *edit* needed unless Phase 2's
      risk-audit surfaces a genuine blind spot the vision failed to state.

## Proposal

Phase 2's concrete API shapes, as landed:

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
