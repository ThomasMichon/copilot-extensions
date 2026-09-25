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

### Phase 1 — Declare the monitor + confirmed shape (data only, no runtime change)
_(agent-recommended: sequencing shape, following `task_state_machine.py`'s
own declare-then-wire pattern)_
- [ ] Add `Status.CONFIRMED` to `queue_records.py`. Add it to `TERMINAL`.
      **Do not remove `COMPLETED` from `TERMINAL` in this phase** — see
      Phase 2's risk note; Phase 1 only adds the new state and its declared
      transitions, it does not yet change what "terminal" means for
      existing callers.
- [ ] In `task_state_machine.py`: add `CONFIRMED` to `ALL_STATES`; add
      `confirm` (`COMPLETED -> CONFIRMED`, `SAFE_RETRY`) and
      `reopen_completed` (`COMPLETED -> QUEUED`, `SAFE_RETRY`, carrying
      forward progress/goal per *resume-the-goal-not-restart-it*) to
      `TRANSITIONS`. Run `states_without_exit()`/`terminal_states_with_exit()`
      against the updated table and fix any finding.
- [ ] Design (not yet implement) the **monitor** vocabulary: a small,
      closed enum/registry of monitor kinds (starting with exactly one:
      `cooldown`), each carrying the data needed to resolve it (for
      `cooldown`: a `not_before` timestamp) and a resolution check. Write
      this as its own small module (e.g. `monitors.py`) rather than folding
      it into `queue_lifecycle.py`, matching the codebase's existing
      one-focused-module-per-concern convention.
- [ ] Unit tests: table-shape checks (reachability, no dangling non-terminal
      state, no terminal state with an exit) extended to cover `CONFIRMED`,
      mirroring the existing Phase 9/10 tests in
      `review-automation-reliability`.

### Phase 2 — Wire `confirmed` for real (the invasive phase — needs its own risk pass)
- [ ] **Risk-audit every `Status.TERMINAL`/`Status.COMPLETED` call site**
      across `queue*.py`, `coordinator_tasks.py`, `board_cli.py`,
      `client.py`, `mcp_server.py`, `mcp_http.py`, the CLI, and tests before
      changing terminal-ness. The safest shape found so far: leave
      `COMPLETED` in `TERMINAL` (existing "done" semantics for
      emitter/evaluator-driven and liveness-GC/reclamation code paths stay
      byte-for-byte unchanged) and treat `confirm`/`reopen_completed` as
      **additional, additive** transitions available from a state that
      remains "terminal" for every pre-existing purpose. Revisit only if
      that additive framing proves incoherent once every call site is
      actually read.
- [ ] Implement `TaskQueue.confirm(task_id, ...)` (`COMPLETED -> CONFIRMED`)
      and `TaskQueue.reopen_completed(task_id, ..., steer_fields=None)`
      (`COMPLETED -> QUEUED`, carrying progress forward and, if given,
      recording an operator steer atomically with the reopen) in
      `queue_lifecycle.py`.
- [ ] Wire `verify-the-completion-claim`'s automatic half: for
      emitter/evaluator-driven (goal-bearing) tasks, the evaluator's own
      completion handler calls `confirm()` once its corroboration passes,
      instead of leaving the task at a bare `completed` no one revisits.
- [ ] Expose `confirm`/`reopen-completed` on the CLI (`agent-dispatch
      confirm <id>`, `agent-dispatch reopen <id> [--steer ...]`) alongside
      the existing `abandon`/`reset` verbs in whichever CLI module already
      owns them (`task_lifecycle_cli.py`).
- [ ] `board_cli.py`: add `confirmed` to the phase/group projection so the
      Tasks pane (see `agent-dispatch-tasks-pane-ux-overhaul` Phase 11) has
      something to read.

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

- [ ] `task_state_machine.py`'s own shape checks (`reachable_states`,
      `states_without_exit`, `terminal_states_with_exit`) pass with
      `CONFIRMED` included.
- [ ] Full `plugins/agent-dispatch` test suite green (establish the
      pre-change baseline count first; the same "confirm zero regressions,
      not just zero *new* failures" bar `agent-worktrees`' own recent fix
      was held to applies here).
- [ ] A hand-run scenario end-to-end: propose → queue → claim → start →
      suspend (no explicit monitor) → (fake-clock-advance) → auto-resume →
      complete → confirm; and the sibling reopen path: complete → reopen →
      queue → claim → ... → confirm.
- [ ] A hand-run crash scenario: suspend with a cooldown pending → kill the
      supervisor process → restart it → assert the honest recovery note
      lands, per Phase 4.
- [ ] Cite this effort's Phase 2/3/4 landings back into the parent vision's
      Provenance-adjacent reality docs once code lands (per envisioning's
      "close the loop" step) — no vision *edit* needed unless Phase 2's
      risk-audit surfaces a genuine blind spot the vision failed to state.

## Proposal

_Pending — Phase 1/2's design firms up once `monitors.py`'s shape and the
`Status.TERMINAL` risk-audit are actually done; this section will carry the
concrete API shapes then._

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
