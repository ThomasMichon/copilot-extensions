# Core lifecycle implementation plan

Canonical component of [this same Active effort](README.md). This is not a child effort, transfer or independent completion gate.

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
