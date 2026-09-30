# Task Verification Gate (SUBMITTED -> COMPLETED via a whole-goal evaluator)

- **Slug:** `task-verification-gate`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-29
- **Status:** In review
- **Vision:** agent-dispatch vision's *verify-the-completion-claim* (this
  effort's Phase 1 also revises that vision section's own wording -- see
  Plan)
- **Umbrella issue:** #4666
- **Sub-issues:** _TBD, one per Plan phase once filed_
- **Full design exchange:** `inception-transcript.md` (the multi-round
  producer/evaluator design-hardening conversation that settled the
  architecture below, preserved per the operator's own request; the
  operator's own messages are kept verbatim, this agent's responses are
  curated gists -- not a raw session log)

## Guiding Intent

*verify-the-completion-claim* already establishes that a worker's completion
is "a claim to verify, not a fact to trust on faith," and that for a
**self-tracked** task (no evaluator) "the caller tracking the task *is* the
verifier." But today that verifier role has no first-class way to say *I
require independent corroboration, not just my own self-attestation* at
task-creation time, and no generic mechanism exists for a consumer to plug in
its own corroboration logic beyond the existing purely-declarative
`SpecEvaluator` (which can only match on the task's own labels/status and
either emit a follow-up or unconditionally confirm -- it cannot consult any
external state to actually judge whether a goal was met). A consumer that
needs real external corroboration (e.g. a **reviewer**-recipe loop checking
whether its target change actually merged, closed unmerged, or went stale)
has no choice but to hand-roll its own poll-and-confirm loop outside the
queue entirely. This effort makes verification a first-class, opt-in,
generic mechanism instead.

**Settled architecture (see `inception-transcript.md` for the full
reasoning):** a task's goal is stated in full upfront and never narrowed
into a per-round instruction; an agent submits only when it believes the
*entire* goal is met, using `agent-dispatch run` to voluntarily yield for any
long wait along the way (posting a review/PR and waiting on response is a
`run`-wrapped wait, not a task-lifecycle transition). Emitters primarily
*create* tasks; a **subscribed** emitter may also append an **event note**
(a major external event -- merged, closed, bug fixed/rejected -- never a
goal rewrite) to an existing task it's monitoring, which wakes that task's
current agent (running or hibernating via `run`) to handle the ramifications
before it finalizes. Evaluators do exactly one thing: **decide whether a
`SUBMITTED` task's whole stated goal is actually, verifiably met**
(`Complete`) or a valid abandon-condition holds (`Abandon`) -- never a
per-round progress audit, never a goal rewrite. Liveness/heartbeat
concerns (an agent that went quiet, a stalled worker) are already covered by
existing orthogonal mechanisms (agent-worktrees' worktree-disposition
nudger, agent-bridge's turn/context/tool-call tracking) and are out of this
effort's scope. The one narrow exception needing new machinery: an
agent-dispatch outage while a `run` call is outstanding needs an explicit
recovery sweep (wake the affected task with an "infrastructure failure, try
again" result) -- see Phase 2b.

**Explicitly out of scope for this effort** (a separate, larger conversation
surfaced during the same design exchange, not yet its own tracked effort):
unifying every producer kind (`emitter`/`reviewer-loop`/
`repository-issue-loop`/`plugin-companion`) around a single `emitter`
primitive with an `extends:`-based registrar template model. That's a real,
independently-valuable direction (see `inception-transcript.md`'s Round 1)
but is orthogonal to the completion-verification gate this effort delivers,
and deserves its own plan/review rather than being bundled in here. Tracked
as a placeholder: #4691.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions (this repo) | `require_verification` flag, state-machine gating, a whole-goal-only evaluator invocation mechanism (script/command evaluator kind, `Complete`/`Abandon` decisions), the `run`-outage recovery sweep, agent-worktrees Tasks pivot manual override | worktree PRs against `dev` |
| A downstream consumer's own reviewer-recipe loop | Registers a real evaluator against the new mechanism for its own whole-goal check (e.g. "did the target change merge, or was it closed/abandoned"), and runs any historical-backlog reconciliation it needs | the consumer's own private effort, linked back here (not tracked in this repo) |

## Coordination

- **Topology:** independent per-repo PRs; this repo's Plan phases are the
  reusable mechanism. A consumer's own evaluator registration and any
  backlog reconciliation is entirely its own concern, tracked in its own
  (private) effort once this repo's Phase 1-2 land, promote to `main`, and
  are adopted.
- **Host (owns this repo's PRs):** copilot-extensions worktree sessions.
- **Handoff:** this effort's Journal records when Phase 1-2 are merged +
  promoted; a consumer's linked private effort starts once it has adopted
  that release.

## Context

- Background/timeline on the preceding `SUBMITTED`/`COMPLETED` naming:
  `plugins/agent-dispatch/docs/status-rename-migration-2026-09-29.md`.
- Current evaluator framework (`producers/evaluator.py`): a purely
  *declarative* `SpecEvaluator` -- rules match on `labels_any`/`labels_all`/
  `status`/`source` and either emit a follow-up task or blindly `Confirm`
  (the rule's own `when` clause is asserted to already BE the corroboration
  judgment; the framework performs no independent verification of its own).
  There is **no `Abandon` decision** today, and no way for an evaluator to
  consult external state (e.g. whether a target change actually merged) --
  exactly the gap a **reviewer**-recipe consumer (see
  `visions/plugins/agent-dispatch/README.md`'s recipe archetypes) hits, and
  why such a consumer would otherwise hand-roll its own poll-and-confirm
  loop entirely outside the queue.
- Existing, directly relevant machinery this effort builds on: `agent-dispatch
  run` (hibernate-the-wait -- a detached, cheap OS-level waiter that tears
  down the expensive agent session and wakes it only when the wait
  resolves, delivering the buffered result as the resume prompt).
  `agent-dispatch doctor` classifies hibernation from worktree/session
  evidence only (`doctor.py`) -- it does not probe the detached waiter
  process itself, and `execution_cli.py` returns the waiter's PID/argv only
  to the spawning caller, not to any durable coordinator-side record.
  `doctor` cannot distinguish "waiter still legitimately running" from
  "waiter died, PID reused by something else" for a `run` call specifically.
  Phase 2b adds its own durable waiter registration rather than assuming
  `doctor` already provides one -- see
  Phase 2b below.
- This effort was motivated by a live operational finding on a facility
  deployment: a large backlog of `SUBMITTED` reviewer-recipe tasks
  accumulated with no evaluator ever revisiting them, because nothing in
  agent-dispatch itself drives that corroboration generically -- only a
  bespoke external poll loop could. The facility-specific counts, host
  names, and remediation steps for that incident belong to that facility's
  own private effort (linked from there back to this one), not here; this
  repo's effort captures only the general-purpose mechanism the incident
  exposed as missing.
- This confirms the gate must be **opt-in** (`require_verification`), never
  a blanket sweep -- a task with no evaluator and no verification
  requirement must be left entirely alone by any new mechanism this effort
  adds.

## Request

Operator request (captured close to verbatim, generalized to remove
facility-specific detail per this repo's own public/organization-neutral
contribution boundary -- see `REVIEW.md`). The full multi-round design
exchange that refined this into the settled architecture above is preserved
in `inception-transcript.md`; this section keeps only the original ask plus
the final settled decisions, not the intervening back-and-forth.

> We should provide the following behaviors:
>
> 1. A task, when defined, should have a flag for "require verification". If
>    specified, agents may not, on their own, mark a task COMPLETED, only
>    SUBMITTED. Submitted tasks must run through an evaluator to be converted
>    to COMPLETED, or the user may use the Tasks UX to open a Task, look at
>    final state, and mark it as such. This way, we can still support legacy
>    or free-form tasks where the agent may self-report COMPLETED. We may
>    also offer a future where the evaluator can be another agent, just as we
>    permit a task itself to be assigned to a script and not an agent.
>
> 2. [Separately, motivating the above:] a consumer's reviewer-recipe loop
>    needs an evaluator which verifies that a SUBMITTED task produced a
>    merged change, or the change was abandoned or went stale. As
>    agent-dispatch runs its outstanding `SUBMITTED` (not yet
>    `COMPLETED`/`ABANDONED`) tasks through the evaluator, that evaluator
>    should see the change's final state and update the task accordingly.

Design decisions, final (settled across the multi-round exchange in
`inception-transcript.md`; supersedes an intermediate design this effort
briefly executed against and then paused -- see Journal):

1. **Flag placement:** create-time field + recipe/producer default
   inheritance (a producer can set it on every task it creates, so a caller
   doesn't need to remember the flag per-call).
2. **A task's goal is stated in full upfront and never rewritten into a
   narrower per-round instruction.** A bounded, "carefully-updated" event
   journal on the task carries nudges, prior rejection reasons, and a
   **subscribed emitter's own event notes** (a major external event --
   merged, closed, bug fixed/rejected -- appended to an existing task it
   monitors, never a goal rewrite) as status notes the agent reassesses
   against its own judgment -- never a rewritten goal it can comply with
   mechanically. This was the deciding factor against an earlier "emitter
   rewrites the task's goal in place each round" design: it produces
   exactly the "task says do X, agent says I already did X, reposts"
   failure mode observed in practice. An emitter's event note wakes the
   task's current agent (running or hibernating via `run`) so it can
   handle the ramifications before finalizing -- see Phase 2c.
3. **An agent submits only when it believes the entire stated goal is met**
   -- not per-phase, not per-round.
4. **Evaluators do exactly one thing:** decide whether a `SUBMITTED` task's
   whole goal is verifiably met (`Complete`) or a valid abandon-condition
   holds (`Abandon`). No progress-auditing, no goal-rewriting, no general
   `Requeue` decision in the evaluator's own vocabulary. Staleness
   thresholds (e.g. "abandon an open-but-inactive target after N days") are
   entirely the registered evaluator's own judgment -- this repo's mechanism
   hardcodes none.
5. **`run` is how an agent voluntarily yields** for a long wait (posting a
   review/PR and waiting on response); `BLOCKED`
   (`request-steer`/`await-steer`) is just one more `run`-wrapped wait from
   the agent's own point of view, not a distinct mechanism.
6. **The one new recovery mechanism needed:** if agent-dispatch itself goes
   down while `run` calls are outstanding, a startup sweep must wake each
   affected task with an explicit "infrastructure failure, try again"
   result once the coordinator recovers -- the sole legitimate use of a
   `Requeue`-shaped action in this design, and it belongs to `run`'s own
   crash-recovery path, not to the whole-goal evaluator.
7. **Historical backlog:** a consumer that already has an orphaned backlog
   of legacy `SUBMITTED` tasks with no `require_verification` flag needs an
   **explicit, scoped backfill operation** to opt them in -- this repo's
   mechanism itself must never silently reach into tasks that were never
   flagged for verification.
8. **Manual override surface:** the agent-worktrees Worktree Manager's
   **Tasks pivot** (which already provides steering support) is the intended
   home for a human "open this task, look at final state, mark it" action --
   _(agent-recommended: this effort's Phase 3 wires a `complete`/`abandon`
   action there; the exact UI affordance is this effort's own design, not
   independently specified by the operator beyond naming the Tasks pivot as
   the right home)_.

## Plan

### Phase 1 -- `require_verification` flag + state-machine gating
- [x] Add `require_verification BOOLEAN NOT NULL DEFAULT 0` to `tasks`
      (schema migration). **Every existing row defaults to `false`** --
      this migration alone does *not* opt any historical task into
      verification; see Phase 2's explicit backfill note.
- [x] `create`/`propose` CLI + API accept `--require-verification` /
      `require_verification` kwarg.
- [x] `complete()` behavior, reconciled with *verify-the-completion-claim*'s
      existing self-tracked-task language rather than contradicting it:
      today `complete()` always lands a task at `SUBMITTED`
      (`plugins/agent-dispatch/tests/test_queue.py`) and requires a
      separate `confirm()` to reach `COMPLETED`, for every task
      unconditionally. When `require_verification` is **false** (the
      default), `complete()` performs the assertion *and* the self-attested
      corroboration in one call -- the caller **is** the verifier, exactly
      as the vision's self-tracked-task path already describes, just made
      an explicit, single-call path instead of requiring a second manual
      `confirm()`. When `require_verification` is **true**, `complete()`
      lands at `SUBMITTED` only; an explicit `confirm()`/`abandon()`
      (evaluator or manual) is required to leave that state, and no
      auto-attestation ever happens.
- [x] **Revise `visions/plugins/agent-dispatch/README.md`'s
      *verify-the-completion-claim* section** to name this explicit
      `require_verification` flag and its two paths, rather than leaving
      the self-tracked-task path implicit prose only -- this is a
      vision-extending change (new stated intent), not a silent
      reinterpretation.
- [x] Recipe/producer-level default inheritance: a producer (e.g. a
      `repository-issue-loop`/`reviewer-loop` recipe declaration) may set a
      default `require_verification` for every task it creates, so a caller
      doesn't need to pass the flag on every dispatch.
- [x] Tests: state-machine transition tests for both flag values; schema
      migration test (confirms the default is `false` and no existing
      behavior for unflagged tasks changes); CLI flag tests.

### Phase 2a -- Whole-goal evaluator invocation
- [x] Add an **`Abandon`** decision type to `producers/evaluator.py`
      (parallel to the existing `Confirm`), wired through `apply_decisions`
      (`abandoner` callable, `client.abandon`-shaped). Retire the framing of
      a general `Requeue`/progress-audit decision -- the evaluator's
      vocabulary stays `Complete` (rename `Confirm` at the decision-name
      level if that reads clearer; the state-machine verb `confirm()`
      itself is unchanged) / `Abandon` / `NoOp` only.
- [x] Add a **command/script evaluator kind** alongside the existing
      declarative `SpecEvaluator`, matching the operator's own "a task
      itself may be assigned to a script, not an agent" precedent -- with
      an explicit safety boundary: `evaluator_ref` on a task remains an
      **opaque selector**, never a literal command string accepted from a
      task-creation caller. A script evaluator is a **separately, trustedly
      registered** entry (fixed `argv`, executed without a shell, no
      caller-supplied arguments) that a task's `evaluator_ref` merely
      *names*; the coordinator resolves the name against that trusted
      registry, never against caller input. Validate the registration shape
      and the child process's stdout against a strict schema before
      applying any decision. Design the interface so a *future*
      agent-backed evaluator is a drop-in third kind, without over-building
      that future today.
- [x] The evaluator has **three trigger paths** (a bare `task.submitted`
      event alone cannot cover backfill or a re-check when external state
      later changes):
      1. **Event-triggered** at the `task.submitted` lifecycle event -- the
         normal first-check path for a fresh submission.
      2. **Explicit backfill invocation** -- a one-time, scoped CLI/API
         call that directly re-runs the evaluator against specific,
         named historical rows a consumer opts in (never an automatic
         sweep of every `SUBMITTED` row -- see the legacy-backlog note
         below).
      3. **Re-triggered by a Phase 2c event note** -- when a subscribed
         emitter posts an event note against a task that is *already*
         `SUBMITTED` with `require_verification=true` and a registered
         `evaluator_ref` (the "still-in-flight, `NoOp`'d once already"
         case: nothing else would ever re-check it when the external state
         later resolves), the note also re-invokes the evaluator, not just
         wakes a running/hibernating agent.
      In all three cases: scoped strictly to `SUBMITTED` --
      `confirm()`/`abandon()` are illegal from `queued`/`claimed`/`started`.
      This is **not** a recurring interval/polling audit -- each of the
      three paths is triggered by a specific, real event (a submission, an
      explicit opt-in call, or an external-state event note), never a
      periodic sweep of every flagged row.
- [x] Tests: script-evaluator invocation (stdin/stdout contract, timeout,
      malformed-output handling, no-shell/fixed-argv enforcement), scoping
      (never touches non-`SUBMITTED` or unflagged tasks), `Abandon`/
      `Complete` decisions end-to-end, and each of the three trigger paths
      independently (including: a `NoOp`'d still-in-flight task later gets
      re-evaluated and resolved correctly via a Phase 2c event note, not
      left permanently unchecked).

### Phase 2b -- `run`-outage recovery sweep
- [x] **Durable waiter registration is a prerequisite this phase must add**
      (`doctor` does not already provide this): when a `run --detach`
      waiter spawns, persist its PID, host identity, and a
      process-start/identity fence (matching the same PID-reuse-safe
      pattern `doctor --check-live-sessions` already uses for shadowed
      embody sessions) in a durable coordinator-side record, not just
      returned to the spawning caller as `execution_cli.py` does today.
      **The registration needs an atomic retirement transition, not just a
      creation path:** when the waiter resolves normally and delivers its
      result, it (or the coordinator, on delivery) retires the
      registration by bumping/clearing its generation in the same
      transaction as the delivery -- otherwise a waiter that completed
      normally still *looks* PID-dead to a later startup sweep, and that
      sweep would send a bogus "infrastructure failure" wake to a task that
      already resolved correctly. The recovery sweep below must
      compare-and-set only against a still-*active* generation, so it can
      never race a normal, concurrent completion.
- [x] On coordinator startup (or a bounded post-recovery check), sweep
      tasks with a durably-registered outstanding `run` waiter and probe
      each one host-aware, by PID *and* the recorded start-identity fence
      (never bare PID alone, which risks a false "dead" on PID reuse).
      Classify: confirmed-dead (the recorded identity no longer matches any
      live process) -> eligible for recovery below; genuinely still running
      -> left alone; indeterminate/unknown (can't confirm either way, e.g.
      a cross-host waiter this coordinator can't probe) -> **left alone,
      never treated as dead** (same fail-safe posture `doctor`'s existing
      verdicts already use).
- [x] Wake each **confirmed-dead** task's owner with an explicit
      "infrastructure failure, try again" result -- the same delivery shape
      `run` already uses for a normal wake (buffered stdio + exit code),
      just carrying a synthetic failure payload instead of a real one.
- [x] Explicitly out of scope for this phase (per the settled design): a
      `run` command that itself hangs/deadlocks has no system-level
      defense -- only the submitting agent knows a reasonable bound. Also
      out of scope: "blessed" `run` requests routed to a registered emitter
      instead of an arbitrary command -- a possible future refinement noted
      in `inception-transcript.md`, not required here.
- [x] Tests: a simulated coordinator-restart scenario with an outstanding
      `run` call resolves to the explicit failure wake-up, not a silent
      stall; a genuinely-still-running `run` call is left alone; **a waiter
      that resolves normally right around the same time a startup sweep
      runs never receives a bogus failure wake** (the retirement-vs-sweep
      race above), confirmed via the compare-and-set-on-active-generation
      fence.

### Phase 2c -- Subscribed-emitter event notes wake the task's agent
- [x] A **subscribed** emitter (one actively watching its target, e.g. a
      webhook on PR events) may append an **event note** to an existing
      task it's monitoring -- a record of a major external event (merged,
      closed, bug fixed/rejected), never a goal rewrite. This is a new,
      narrow write path distinct from task creation; the task's own goal
      field is never touched by it.
- [x] Posting an event note wakes the task's current agent: if actively
      running, deliver the note as part of its next turn's context; if
      hibernating via `run`, resume it early (before the wrapped wait
      command naturally resolves). **Race, identified on review, that must
      be closed:** the original detached waiter keeps running after an
      early resume and can later finish on its own, sending a *second*,
      stale wake -- which could also release the hibernation claim out from
      under the task's now-newly-resumed attempt. Require a durable
      generation/supersession fence (bump a generation counter or
      equivalent stamped identity at early-resume time, same fencing
      pattern the state machine already uses for `owner_session_id`/
      `generation` elsewhere) so a late wake from the *superseded* waiter is
      detected and dropped rather than acted on -- an identity-safe
      cancellation of the old waiter (if one exists) is a valid alternative
      but the fence must exist either way; an early resume must never leave
      it ambiguous which attempt owns completion.
- [x] Tests: an event note posted while the task is actively running
      surfaces on the next turn; a note posted while hibernating via `run`
      triggers an early wake with the note in the resume context; a
      goal-rewrite attempt through this path is rejected (only an
      append-only note field is writable here, never the goal); **the
      stale-waiter race above** -- a late wake from a superseded (early-
      resumed-past) waiter is confirmed dropped, never double-processed and
      never able to mutate or release the newer attempt's claim.

### Phase 3 -- agent-worktrees Tasks pivot manual override
- [x] Add a `complete`/`abandon` action to the existing Tasks pivot (which
      already carries steering support) for a `require_verification` task
      sitting at `SUBMITTED` with no evaluator resolving it (or an operator
      who wants to override the evaluator's pending verdict).
- [x] Tests: pivot action wiring (existing agent-worktrees test conventions).

### Phase 4 -- Docs
- [x] `plugins/agent-dispatch/README.md`: document the flag, the two
      evaluator kinds, the `run`-outage recovery sweep, and the
      manual-override path in the State model section (alongside the
      existing rename footnote).
- [x] Cross-link this effort's outcome from
      `plugins/agent-dispatch/docs/status-rename-migration-2026-09-29.md`.

## Validation Plan

- [x] Full `agent-dispatch` plugin suite green
      (`test-supervisor -- python3 tools/run-plugin-tests.py agent-dispatch`).
- [x] A `require_verification=false` task's `complete()` still reaches
      `COMPLETED` in one call, unchanged from today's *external* behavior
      (internally it now also performs the self-attested corroboration
      step explicitly, per Phase 1's vision reconciliation).
- [x] A `require_verification=true` task's `complete()` lands at
      `SUBMITTED` only, and stays there until an evaluator or manual action
      resolves it.
- [x] A registered script evaluator is invoked, via its trusted
      registration (never via caller-supplied `evaluator_ref` content), for
      a `require_verification` `SUBMITTED` task, and its
      `Complete`/`Abandon`/`NoOp` decision is applied correctly -- with no
      progress-audit or goal-rewrite behavior anywhere in the path.
- [x] The mechanism never touches a `queued`/`claimed`/`started` task, and
      never touches a task with `require_verification=false` or no
      registered evaluator -- confirmed by a fixture covering: a target
      that merged -> `COMPLETED`; a target closed unmerged -> `ABANDONED`;
      a still-in-flight target -> left alone (`NoOp`) on first check, then
      correctly resolved on a later Phase 2c event-note re-trigger once it
      merges; an unflagged legacy task -> untouched; an explicit backfill
      invocation resolves a named historical row without touching any
      other unflagged row.
- [x] A simulated coordinator outage with an outstanding `run` call
      resolves to an explicit failure wake for a confirmed-dead waiter
      (matched by durable PID + host + start-identity, never bare PID);
      a genuinely-still-running or indeterminate waiter is left alone, not
      falsely recovered; a waiter that resolves normally concurrently with
      a sweep never receives a bogus failure wake (generation-fenced).
- [x] A subscribed emitter's event note wakes a running or `run`-hibernating
      task's agent early, without ever mutating the task's own goal field;
      a late wake from the original (superseded) waiter after an early
      resume is confirmed dropped, never double-processed.
- [x] agent-worktrees Tasks pivot manual override tested against a stuck
      `require_verification` task.

## Proposal

_Pending review._

## Journal

### 2026-09-29 -- Kickoff
- Effort created from a live operational finding (a facility deployment's
  own orphaned `SUBMITTED` backlog, tracked in that facility's private
  effort, not here) plus the operator's explicit design request for a
  generic verification gate. Captured close to verbatim above, generalized
  to remove facility-specific identifiers/counts per this repo's
  public/organization-neutral contribution boundary; demarcated
  agent-recommended additions (the `Abandon` decision type and the
  script-evaluator kind, both necessary to satisfy the request but not
  independently specified).
- Automated review (PR #4667) flagged: private facility identifiers in the
  original draft (fixed by generalizing per the above), an under-specified
  command-execution boundary for the script evaluator (fixed: opaque
  selector + trusted registration, no shell, no caller-supplied argv), a
  contradiction between "auto-confirm on `require_verification=false`" and
  the vision's existing self-tracked-task language (fixed: reconciled as an
  explicit single-call self-attestation path, plus a Plan item to revise
  the vision's own wording to name it), an unsafe backlog-sweep framing
  that would have run `confirm()`/`abandon()` against non-`SUBMITTED`
  states and silently never selected unflagged legacy rows (fixed: the
  interval is scoped strictly to `SUBMITTED` + flagged + evaluator-bound
  tasks; opting a historical row in is always a separate, explicit action),
  a validation gap conflating "still open" with "stale" outcomes (fixed:
  split into distinct fixture cases), a broken sidecar reference (removed;
  the verbatim Request above is short enough to keep inline), and a wrong
  doc path in Phase 4 (fixed).
- Execution began against this initial Phase 1-2 design (a background
  session started implementing it).

### 2026-09-30 -- Producer/evaluator redesign; execution paused and re-scoped
- The operator opened a much deeper design conversation covering the whole
  producer/evaluator/recipe/registrar model, not just the verification gate
  in isolation -- full exchange in `inception-transcript.md`. In-flight
  execution was paused (no PR opened) as soon as the redesign's direction
  became clear, specifically to avoid merging a shape that would need
  immediate rework.
- Settled outcome, in short: emitters primarily create tasks (never
  rewrite a goal in place -- an earlier "B" option considered and
  rejected); a task's goal stays durable and full upfront rather than being
  narrowed per round (the deciding factor -- goal-narrowing is what
  produces an agent rubber-stamping "I already did that" against a
  rewritten instruction); agents submit only once, when the entire goal is
  believed met; `run` (already-existing hibernate-the-wait) is how an agent
  yields for any long wait along the way, with `BLOCKED` just one more
  `run`-wrapped wait, not a distinct mechanism; evaluators do exactly one
  thing -- decide whether a
  `SUBMITTED` task's whole goal is met (`Complete`/`Abandon`), never a
  progress audit or goal rewrite; the one new mechanism needed is a narrow
  `run`-outage recovery sweep (Phase 2b), reusing `doctor`'s existing
  liveness-audit approach rather than building a second one.
- This Plan is rewritten above to match. Phase 1 is essentially unchanged by
  the redesign (it was already orthogonal). Phase 2 is split into 2a
  (evaluator invocation, now `Complete`/`Abandon`/`NoOp` only, no general
  `Requeue`) and 2b (the new `run`-outage recovery sweep). Phases 3-4 are
  unchanged in shape, retitled `complete`/`abandon` to match the settled
  vocabulary.
- The broader producer/registrar unification (`kind` -> `extends`, global
  emitter templates, schedule/webhook/websocket as emitter triggers) that
  came up in the same conversation is real and independently valuable, but
  is explicitly out of this effort's scope -- tracked as a placeholder
  (#4691) rather than bundled in here, per "propose before you do" and
  keeping this effort's own review surface bounded.
- Next: message the paused execution session with this revised Phase 1-2a
  spec and let it resume.

### 2026-09-30 (later same day) -- Correction: subscribed emitters can post event notes
- The operator corrected an overstatement in the redesign summary above:
  "emitters only ever create tasks" was too strict. A **subscribed** emitter
  (one actively watching its target) may append an event note to an
  existing task it monitors -- a major external event (merged, closed, bug
  fixed/rejected), never a goal rewrite -- and doing so wakes that task's
  current agent (running or `run`-hibernating) to handle the ramifications
  before finalizing. Full correction in `inception-transcript.md`'s Round
  5.
- Added Phase 2c for this capability; updated the Guiding Intent, Request
  decision #2, and Validation Plan to match. The core distinction (goal
  text never rewritten; only an append-only event-note field, and only from
  a genuinely subscribed emitter) is preserved -- this does not reopen the
  rejected "emitter rewrites the goal each round" design.

### 2026-09-30 (effort-revision PR #4692 review) -- four real gaps closed
Automated review on the effort-revision PR itself (before any code landed)
caught four genuine design gaps in the settled plan above, all fixed in
place:
- **Phase 2a's single `task.submitted` trigger couldn't cover the cases the
  Validation Plan promised** -- a backfilled legacy row and a `NoOp`'d
  still-in-flight task have no later event to re-check them. Fixed: the
  evaluator now has three explicit trigger paths (submission event,
  explicit scoped backfill invocation, and a Phase 2c event note
  re-triggering re-evaluation on an already-`SUBMITTED` row) -- still never
  a periodic/polling sweep.
- **Phase 2b overstated what `doctor` already provides** -- it classifies
  hibernation from worktree/session evidence only and does not durably
  track a `run` waiter's PID; `execution_cli.py` only returns that PID to
  the spawning caller. Fixed: Phase 2b now explicitly adds durable waiter
  registration (PID + host + a process-start identity fence) as its own
  prerequisite, with a fail-safe "indeterminate -> leave alone" verdict
  matching `doctor`'s own existing posture, rather than assuming a
  liveness-check mechanism doctor doesn't yet have.
- **Phase 2c's early-resume path had an unresolved race**: the original
  detached waiter keeps running after an early resume and can send a
  second, stale wake later, potentially releasing the newer attempt's
  claim. Fixed: requires a durable generation/supersession fence (or
  equivalent identity-safe cancellation) so a late wake from a superseded
  waiter is dropped, never double-processed.
- **`inception-transcript.md`'s opening claimed a full verbatim
  transcript** while several rounds were explicitly narrated/gisted.
  Fixed: reworded to accurately describe the operator's own messages as
  verbatim and this agent's responses as curated gists, not a raw session
  log.

### 2026-09-30 (second review round on PR #4692) -- three more real gaps
- **Phase 2b's durable waiter registration had no retirement path**: a
  waiter that resolves *normally* would still look PID-dead to a later
  startup sweep, risking a bogus "infrastructure failure" wake on an
  already-correctly-resolved task. Fixed: registration retirement is now an
  explicit, atomic part of normal delivery (generation
  bumped/cleared in the same transaction), and the recovery sweep does a
  compare-and-set against only an *active* generation so it can never race
  a concurrent normal completion.
- **Review-round narrative ("corrected after review") had leaked into the
  Context/Plan sections**, which should describe the current state
  timelessly -- that history belongs in the Journal alone. Fixed: reworded
  those sections to plain present-tense description.
- **The transcript's own mid-file "settled architecture" summary (Round
  4's recap) still said "emitters only create tasks"**, not yet reflecting
  Round 5's correction that a subscribed emitter may also append an
  event note. Fixed in place, with a pointer to Round 5.

### 2026-09-30 -- Phase 2a/2b/2c, Phase 3, and Phase 4 landed
- Reworked the in-flight branch onto the settled redesign after PR #4692:
  Phase 1 was retained, while the abandoned interval-style verification audit
  was replaced with the final whole-goal model.
- Phase 2a landed as three explicit evaluator trigger paths only: the
  `task.submitted` lifecycle event, an explicit scoped backfill route/CLI
  (`verify-submitted`), and a Phase 2c event-note re-trigger for already
  submitted tasks. Verification evaluators are coordinator-owned,
  repo/environment scoped, and limited to `Complete` / `Abandon` / `NoOp`.
- Phase 2b landed with durable detached-`run` waiter registration plus a
  startup recovery sweep, including the atomic active-generation retirement
  fence so a normal completion and a later outage sweep cannot both wake the
  same task.
- Phase 2c landed as append-only subscribed-emitter event notes stored in the
  task audit trail, with two wake paths: direct next-turn nudges for running
  owners and early wake/supersession for hibernating `run` waiters, so stale
  late waiter completions are dropped.
- Phase 3 added the Tasks pivot manual override affordances: submitted,
  verification-gated tasks now expose picker actions to complete or abandon
  them manually.
- Phase 4 updated `plugins/agent-dispatch/README.md` and cross-linked the
  status-rename migration note to this effort's landed verification-gate
  behavior.

### 2026-09-30 (follow-up hardening) -- shared durability abstraction clarified
- Review hardening on the implementation PR confirmed that Phase 2a's
  submitted-verification trigger and Phase 2b's detached-`run` outage
  recovery are the same architectural family even though they fence on
  different facts: both are **durable queued follow-up work with fenced
  claim/drain/recovery semantics**. The waiter path fences on process
  identity (`pid` + start token + owner/session/task generation), while the
  verification path fences on the submitted task incarnation itself
  (generation-scoped queued verification requests). This PR keeps them as
  separate queues in code for now to land safely, but the shared abstraction
  is now explicit for any future simplification pass.

### 2026-09-30 (final PR #4709 hardening) -- review gaps closed without widening the cap
- Closed the remaining substantive review findings before merge: generation-N
  claim-release wakes now stay deliverable even after generation N+1 is
  prepared, and a successful owner wake still releases the retired waiter's
  generation-scoped worktree claim even if a newer waiter supersedes it before
  the post-delivery cleanup step runs.
- Automatic submitted-task verification now fires the same handoff-claim
  release hook as the HTTP/CLI terminal mutation paths, so evaluator-driven
  `confirm` / `abandon` resolutions do not leave a handoff worktree claim
  behind.
- Evaluator registrations now require an explicit non-empty `evaluator_ref`
  instead of silently defaulting to the derived registration id, which was not
  a valid script selector and therefore produced "active" registrations that
  could never match a task.
- The submitted-verification drain now retries `TaskError`s only while the same
  submitted verification generation remains current, preventing a same-task
  CAS race from dropping the sole verification trigger and stranding a task at
  `submitted`.
- Added focused direct tests for these durability boundaries and kept
  `queue_run_waiters.py` shrink-only: no module-size baseline widen was needed
  or retained.
