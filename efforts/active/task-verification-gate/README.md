# Task Verification Gate (SUBMITTED -> COMPLETED via evaluator)

- **Slug:** `task-verification-gate`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-29
- **Status:** Draft
- **Vision:** agent-dispatch vision's *verify-the-completion-claim* (this
  effort's Phase 1 also revises that vision section's own wording -- see
  Plan)
- **Umbrella issue:** #4666
- **Sub-issues:** _TBD, one per Plan phase once filed_

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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions (this repo) | `require_verification` flag, state-machine gating, generalized evaluator invocation (script/command evaluator kind + new `Abandon` decision), agent-worktrees Tasks pivot manual override | worktree PRs against `dev` |
| A downstream consumer's own reviewer-recipe loop | Registers a real evaluator against the new mechanism for its own goal-verification needs (e.g. checking whether a target change merged, closed unmerged, or went stale), and runs any historical-backlog reconciliation it needs | the consumer's own private effort, linked back here (not tracked in this repo) |

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
  requirement must be left entirely alone by any new sweep/interval
  mechanism this effort adds.

## Request

Operator request (captured close to verbatim, generalized to remove
facility-specific detail per this repo's own public/organization-neutral
contribution boundary -- see `REVIEW.md`):

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

Design decisions locked via follow-up (2026-09-29):
1. **Flag placement:** create-time field + recipe/producer default
   inheritance (a producer can set it on every task it creates, so a caller
   doesn't need to remember the flag per-call).
2. **Staleness handling:** a consumer's own evaluator may auto-abandon an
   open-but-inactive target after a consumer-chosen threshold (a facility
   deployment locked 30 days for its own reviewer-recipe evaluator; this
   repo's mechanism does not hardcode any threshold -- that judgment belongs
   entirely to the registered evaluator).
3. **Historical backlog:** a consumer that already has an orphaned backlog
   of legacy `SUBMITTED` tasks with no `require_verification` flag needs an
   **explicit, scoped backfill operation** (see Phase 2's "necessary
   distinction from the recurring interval" below) to opt them in -- the
   recurring interval mechanism itself must never silently reach into tasks
   that were never flagged for verification.
4. **Manual override surface:** the agent-worktrees Worktree Manager's
   **Tasks pivot** (which already provides steering support) is the intended
   home for a human "open this task, look at final state, mark it" action --
   _(agent-recommended: this effort's Phase 3 wires a `confirm`/`abandon`
   action there; the exact UI affordance is this effort's own design, not
   independently specified by the operator beyond naming the Tasks pivot as
   the right home)_.

## Plan

### Phase 1 -- `require_verification` flag + state-machine gating
- [ ] Add `require_verification BOOLEAN NOT NULL DEFAULT 0` to `tasks`
      (schema migration). **Every existing row defaults to `false`** --
      this migration alone does *not* opt any historical task into
      verification; see Phase 2's explicit backfill note.
- [ ] `create`/`propose` CLI + API accept `--require-verification` /
      `require_verification` kwarg.
- [ ] `complete()` behavior, reconciled with *verify-the-completion-claim*'s
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
- [ ] **Revise `visions/plugins/agent-dispatch/README.md`'s
      *verify-the-completion-claim* section** to name this explicit
      `require_verification` flag and its two paths, rather than leaving
      the self-tracked-task path implicit prose only -- this is a
      vision-extending change (new stated intent), not a silent
      reinterpretation.
- [ ] Recipe/producer-level default inheritance: a producer (e.g. a
      `repository-issue-loop`/`reviewer-loop` recipe declaration) may set a
      default `require_verification` for every task it creates, so a caller
      doesn't need to pass the flag on every dispatch.
- [ ] Tests: state-machine transition tests for both flag values; schema
      migration test (confirms the default is `false` and no existing
      behavior for unflagged tasks changes); CLI flag tests.

### Phase 2 -- Generalized evaluator invocation
- [ ] _(agent-recommended, necessary to satisfy the request)_ Add an
      **`Abandon`** decision type to `producers/evaluator.py` (parallel to
      the existing `Confirm`), and wire it through `apply_decisions`
      (`abandoner` callable, `client.abandon`-shaped).
- [ ] _(agent-recommended)_ Add a **command/script evaluator kind**
      alongside the existing declarative `SpecEvaluator`, matching the
      operator's own "a task itself may be assigned to a script, not an
      agent" precedent -- with an explicit safety boundary: `evaluator_ref`
      on a task remains an **opaque selector**, never a literal command
      string accepted from a task-creation caller. A script evaluator is a
      **separately, trustedly registered** entry (fixed `argv`, executed
      without a shell, no caller-supplied arguments) that a task's
      `evaluator_ref` merely *names*; the coordinator resolves the name
      against that trusted registry, never against caller input. Validate
      the registration shape and the child process's stdout against a
      strict schema before applying any decision. Design the interface so a
      *future* agent-backed evaluator is a drop-in third kind, without
      over-building that future today.
- [ ] The coordinator runs every **`SUBMITTED` task that has both
      `require_verification=true` and a registered `evaluator_ref`**
      through that evaluator on a bounded interval (not just at the
      `task.submitted` transition) -- scoped strictly to `SUBMITTED`
      (`confirm()`/`abandon()` are illegal from `queued`/`claimed`/
      `started`, so the interval must never touch those). This recurring
      interval is what lets a **legacy backlog** be reconciled once a
      consumer explicitly opts specific historical rows in -- but the
      interval itself never opts anything in; that is always a separate,
      explicit, scoped action (a one-time `UPDATE`/CLI call flipping
      `require_verification=true` and setting `evaluator_ref` on the
      specific rows a consumer wants reconciled -- a consumer's own
      concern, not something this repo's mechanism performs automatically
      on any task that was never flagged).
- [ ] Tests: script-evaluator invocation (stdin/stdout contract, timeout,
      malformed-output handling, no-shell/fixed-argv enforcement), interval
      scoping (never touches non-`SUBMITTED` or unflagged tasks), `Abandon`
      decision end-to-end.

### Phase 3 -- agent-worktrees Tasks pivot manual override
- [ ] Add a `confirm`/`abandon` action to the existing Tasks pivot (which
      already carries steering support) for a `require_verification` task
      sitting at `SUBMITTED` with no evaluator resolving it (or an operator
      who wants to override the evaluator's pending verdict).
- [ ] Tests: pivot action wiring (existing agent-worktrees test conventions).

### Phase 4 -- Docs
- [ ] `plugins/agent-dispatch/README.md`: document the flag, the two
      evaluator kinds, and the manual-override path in the State model
      section (alongside the existing rename footnote).
- [ ] Cross-link this effort's outcome from
      `plugins/agent-dispatch/docs/status-rename-migration-2026-09-29.md`.

## Validation Plan

- [ ] Full `agent-dispatch` plugin suite green
      (`test-supervisor -- python3 tools/run-plugin-tests.py agent-dispatch`).
- [ ] A `require_verification=false` task's `complete()` still reaches
      `COMPLETED` in one call, unchanged from today's *external* behavior
      (internally it now also performs the self-attested corroboration
      step explicitly, per Phase 1's vision reconciliation).
- [ ] A `require_verification=true` task's `complete()` lands at
      `SUBMITTED` only, and stays there until an evaluator or manual action
      resolves it.
- [ ] A registered script evaluator is invoked, via its trusted
      registration (never via caller-supplied `evaluator_ref` content), for
      a `require_verification` `SUBMITTED` task, and its
      `Confirm`/`Abandon`/`NoOp` decision is applied correctly.
- [ ] The recurring interval never touches a `queued`/`claimed`/`started`
      task, and never touches a task with `require_verification=false` or
      no registered evaluator -- confirmed by a fixture covering all four
      cases (fresh-open target left alone; a target that merged ->
      `COMPLETED`; a target closed unmerged -> `ABANDONED`; an unflagged
      legacy task untouched).
- [ ] agent-worktrees Tasks pivot manual override tested against a stuck
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
