# Task Verification Gate (SUBMITTED -> COMPLETED via evaluator)

- **Slug:** `task-verification-gate`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-29
- **Status:** Draft
- **Vision:** agent-dispatch vision's *verify-the-completion-claim*
- **Umbrella issue:** #4666
- **Sub-issues:** _TBD, one per Plan phase once filed_

## Guiding Intent

The 2026-09-25/2026-09-29 `SUBMITTED`/`COMPLETED` rename (see
`plugins/agent-dispatch/docs/status-rename-migration-2026-09-29.md`)
established that a worker's own completion claim (`SUBMITTED`) is provisional
until something corroborates it (`COMPLETED`, via `confirm()`). But today
**nothing automatically drives that corroboration for most producers** --
Intelligence Dampener's own `dispatch_review.py` hand-rolls its own
poll-and-confirm loop, and any other producer's tasks just pile up at
`SUBMITTED` forever with no evaluator ever looking at them (confirmed live:
1,414 of 1,623 `SUBMITTED` rows on the `lambda-core` coordinator are
`pr-review`/`intelligence-dampener-review` tasks up to ~32 days old with zero
active process left to revisit them). This effort makes verification a
first-class, opt-in, generic mechanism instead of a bespoke per-producer
poll loop, and drives a real evaluator for Intelligence Dampener's own
backlog through it.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions (this repo) | `require_verification` flag, state-machine gating, generalized evaluator invocation (script/command evaluator kind + new `Abandon` decision), agent-worktrees Tasks pivot manual override | worktree PRs against `dev` |
| aperture-labs (Intelligence Dampener) | Registers a real pr-review evaluator (checks live Gitea PR state: merged -> confirm, closed-without-merge -> abandon, open+stale 30d -> abandon), sets `require_verification=true` on every task it creates, retroactive backfill sweep of the existing 1,414-task backlog | linked effort in aperture-labs, tracked separately; see `Coordination` |

## Coordination

- **Topology:** independent per-repo PRs; this repo's Plan phases land first
  (the mechanism), aperture-labs' linked effort consumes them once merged +
  promoted + deployed.
- **Host (owns this repo's PRs):** copilot-extensions worktree sessions.
- **Delegates:** aperture-labs' own linked effort owns the ID-side evaluator,
  the deploy, and the backfill sweep -- it depends on this effort's Phase 1-2
  landing (and promoting to `main`, then deploying to the live `lambda-core`
  coordinator) before it can register a real evaluator against a live task.
- **Handoff:** this effort's Journal records when Phase 1-2 are merged +
  promoted + deployed; the aperture-labs effort links back here and starts
  once that's confirmed live.

## Context

- Background/timeline: `plugins/agent-dispatch/docs/status-rename-migration-2026-09-29.md`.
- Current evaluator framework (`producers/evaluator.py`): a purely
  *declarative* `SpecEvaluator` -- rules match on `labels_any`/`labels_all`/
  `status`/`source` and either emit a follow-up task or blindly `Confirm`
  (the rule's own `when` clause is asserted to already BE the corroboration
  judgment; the framework performs no independent verification). There is
  **no `Abandon` decision** today, and no way for an evaluator to consult
  external state (a live Gitea PR) -- exactly what Intelligence Dampener's
  pr-review verification needs, and exactly why ID hand-rolled its own
  `dispatch_review.py` poll loop instead of using this framework.
- Live data snapshot (2026-09-29, `lambda-core` coordinator,
  `~/.agent-dispatch/tasks.db`): 1,623 `SUBMITTED` rows --
  1,414 `pr-review`/`intelligence-dampener-review` (orphaned, oldest ~32
  days, zero active `cleanup`-type task left tracking them in ID's own
  queue), 176 `["handoff"]` (correctly resting at `SUBMITTED` forever --
  no corroboratable fact exists for a consumed handoff), ~30 misc
  (log-writer, neuron-forge, test fixtures -- same story as `handoff`).
  This confirms the gate must be **opt-in** (`require_verification`), never a
  blanket sweep -- `handoff`/log-writer/etc. tasks must NOT be pulled into
  verification; they have no evaluator and none is being added for them.

## Request

Operator request, captured verbatim across the exchange that produced this
effort (full exchange: `inception-transcript.md`):

> Regarding SUBMITTED vs COMPLETED: we should provide the following
> behaviors:
>
> 1. A task, when defined, should have a flag for "require verification". If
>    specified, agents may not, on their own, mark a task COMPLETED, only
>    SUBMITTED. Submitted tasks must run through an evaluator to be converted
>    to COMPLETED, or the user may use the Tasks UX to open a Task, look at
>    final state, and mark it as such. This way, we can still support legacy
>    or free-form tasks where the agent may self-report COMPLETED. We may
>    also offer a future where the evaluator can be another agent, just as we
>    permit a task itself to be assigned to a script and not an agent.

> [Separately, re: the giant pile of `submitted` PR reviews:] Add an
> evaluator for ID which verifies that a SUBMITTED pr-review task produced a
> merged PR, or the PR was abandoned or went stale. As agent-dispatch runs
> the not-yet-COMPLETED-or-ABANDONED tasks through the evaluator, said
> evaluator should see the PR's final state, and update the task.

Design decisions locked via follow-up (2026-09-29):
1. **Flag placement:** create-time field + recipe/producer default
   inheritance (ID's own producer sets it on every pr-review task it
   creates; callers don't need to remember the flag per-call).
2. **Staleness handling:** auto-abandon an open-but-stale PR after **30
   days**.
3. **Retroactive backfill:** the new evaluator sweeps the existing
   1,414-task backlog too, not just new tasks going forward.
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
      (schema migration, default preserves today's self-report behavior).
- [ ] `create`/`propose` CLI + API accept `--require-verification` /
      `require_verification` kwarg.
- [ ] `complete()`: when `require_verification` is false (default), the
      existing `SUBMITTED` landing immediately auto-confirms in the same
      call (single request reaches `COMPLETED`, preserving legacy
      self-report semantics exactly as they behave today for producers that
      never call `confirm()`). When true, `complete()` lands at `SUBMITTED`
      only -- an explicit `confirm()`/`abandon()` (evaluator or manual) is
      required to leave that state.
- [ ] Recipe/producer-level default inheritance: a producer (e.g. a
      `repository-issue-loop`/`reviewer-loop` recipe declaration) may set a
      default `require_verification` for every task it creates, so a caller
      doesn't need to pass the flag on every dispatch.
- [ ] Tests: state-machine transition tests for both flag values; schema
      migration test; CLI flag tests.

### Phase 2 -- Generalized evaluator invocation
- [ ] _(agent-recommended, necessary to satisfy the request)_ Add an
      **`Abandon`** decision type to `producers/evaluator.py` (parallel to
      the existing `Confirm`), and wire it through `apply_decisions`
      (`abandoner` callable, `client.abandon`-shaped).
- [ ] _(agent-recommended)_ Add a **command/script evaluator kind**
      alongside the existing declarative `SpecEvaluator`: `evaluator_ref`
      may name an external command; the coordinator invokes it per
      `require_verification` task lifecycle event (event JSON on stdin,
      a `Decision` JSON on stdout), matching the existing "a task itself may
      be assigned to a script, not an agent" precedent the operator cited.
      Design the interface so a *future* agent-backed evaluator is a drop-in
      third kind, without over-building that future today.
- [ ] The coordinator runs **every** non-terminal `require_verification`
      task with a registered evaluator through it on a bounded interval
      (not just at the `task.submitted` transition) -- this is what lets a
      backlog/retroactive sweep (aperture-labs' Phase, once this lands)
      re-evaluate old rows without a fresh event ever firing for them again.
- [ ] Tests: script-evaluator invocation (stdin/stdout contract, timeout,
      malformed-output handling), sweep-interval coverage, `Abandon` decision
      end-to-end.

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
      `docs/status-rename-migration-2026-09-29.md`.

## Validation Plan

- [ ] Full `agent-dispatch` plugin suite green
      (`test-supervisor -- python3 tools/run-plugin-tests.py agent-dispatch`).
- [ ] A `require_verification=false` task's `complete()` still reaches
      `COMPLETED` in one call, unchanged from today.
- [ ] A `require_verification=true` task's `complete()` lands at `SUBMITTED`
      only, and stays there until an evaluator or manual action resolves it.
- [ ] A registered script evaluator is invoked for a `require_verification`
      task and its `Confirm`/`Abandon`/`NoOp` decision is applied correctly.
- [ ] agent-worktrees Tasks pivot manual override tested against a stuck
      `require_verification` task.
- [ ] (Downstream, tracked in the linked aperture-labs effort, not this
      repo's own gate): the ID evaluator correctly resolves a sample of the
      live 1,414-task backlog (merged -> `COMPLETED`, closed-without-merge
      -> `ABANDONED`, still-open -> left alone) without touching the
      176 `handoff` / ~30 misc `SUBMITTED` rows that have no evaluator.

## Proposal

_Pending review._

## Journal

### 2026-09-29 -- Kickoff
- Effort created from a live operational finding (the 1,414-task orphaned
  `SUBMITTED` backlog surfaced while investigating the 2026-09-25/09-29
  rename) plus the operator's explicit design request for a generic
  verification gate. Captured verbatim above; demarcated agent-recommended
  additions (the `Abandon` decision type and the script-evaluator kind,
  both necessary to satisfy the request but not independently specified).
