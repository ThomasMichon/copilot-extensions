# Background, original request and historical API proposal

Canonical component of [this same Active effort](README.md). This is not a child effort, transfer or independent completion gate.

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

The 2026-10-30 operator-directed intent is captured as a generic summary in the
[assignment/startup amendment](assignment-startup-amendment.md#requested-direction).
Its requested requirements, agent-recommended implementation means and unresolved
policy choices are separated. The November 3
[submission clarification](submission-closeout-amendment.md) settles retained
same-worker rejection and the worktree-owned task-claim lifetime.

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
