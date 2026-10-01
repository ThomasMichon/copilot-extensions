# Retire task-status DEAD_LETTER: fold into ABANDONED

- **Slug:** `retire-dead-letter-status`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-09-30
- **Status:** Draft
- **Umbrella issue:** #4744
- **Sub-issues:** _none yet_

## Guiding Intent

`Status.DEAD_LETTER` was meant, per its own docstring, to be "an actionable
dead-letter end state rather than churning crash -> gone -> requeue forever"
— but no action is actually exposed for it. This effort retires it as a
distinct task status: a held task whose owner keeps going gone past the
retry cap should resolve to `ABANDONED` — the system's one true "this was
dead" endgame — not a second, dead-ended terminal with no CLI path out.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions (this repo) | State-machine retarget, dead-code removal, migration, test updates | worktree PRs against `dev` |

## Coordination

- **Topology:** a small number of sequential PRs against `dev` (the
  state-machine/GC change and its migration are one coherent unit; test
  updates ride along in the same PR given the tight coupling).
- **Host (owns PRs):** copilot-extensions worktree sessions.
- **Handoff:** standard — Journal records phase completion.

## Context

**How a task reaches `DEAD_LETTER` today** (the only path —
`task_state_machine.py`'s `dead_letter_held` transition): a `claimed`/
`started` task whose owner is confirmed gone (`queue_liveness.py`'s
`reconcile_liveness`, the liveness GC) is requeued, incrementing
`attempts`. Once `attempts >= DEFAULT_MAX_ATTEMPTS` (5), instead of
requeuing again it transitions to `DEAD_LETTER` instead of `QUEUED`.
`queue_records.py`'s own comment calls this "an actionable dead-letter end
state" — but neither `abandon` nor `reset --to proposed` accept
`dead_letter` as a source state (confirmed live: both reject it with
`HTTP 409`), so in practice it is a true dead end with zero CLI-exposed
recovery. 5 real tasks on the operator's own machine were found stuck this
way with no path out.

**Two different "dead letter" concepts share vocabulary — do not conflate
them:**
1. **This effort's subject**: the task-level `Status.DEAD_LETTER` above —
   assigned once, by the liveness GC, to an owned (claimed/started) task.
2. **A different, working mechanism** (`spawn_attempt_projection.py`): an
   informal `"dead_lettered"` diagnostic flag on a still-`QUEUED`,
   *never-claimed* task that failed to spawn a worker body 3+ times. This
   one has a real recovery verb, `agent-dispatch reservations rearm
   <task> --permit --reason <reason>` (confirmed: its own `--help` text
   says "queued, unowned task id", i.e. it never applies to a task whose
   actual `status` is `dead_letter`). **Out of scope for this effort** —
   it already works as designed and should not be touched.

**Why the retarget is safe, not just a rename** (confirmed by reading the
actual settlement code, not assumed): `Supervisor.reconcile()` already
generically settles any `RESERVING`/`SPAWNED`/`COLD` reservation whose task
reached a status in its own `_TERMINAL` set — which already includes
`ABANDONED` (and `SUBMITTED`/`COMPLETED`), just not `DEAD_LETTER` today.
Two narrow, carefully-reasoned sweeps exist *specifically because*
`DEAD_LETTER` was excluded from that generic path:
`recover_dead_lettered_cold_reservations()` (`spawn_cold_recovery.py`,
settles a `COLD` reservation) and an explicit `DEAD_LETTER` branch inside
`Supervisor.recover_gone()` (settles a `SPAWNED` reservation). **Once the
GC targets `ABANDONED` instead, both become dead code** — `reconcile()`'s
existing `_TERMINAL` check already covers the exact same reservation
states for `ABANDONED`. This change removes code, not just moves it.

## Request

Operator, after the stock-take found 5 stuck `dead_letter` tasks with no
CLI transition out:

> For dead_letter, let's dig in. That state wasn't really intended to
> exist, as agent-bridge is supposed to handle ensuring we always get an
> agent session, and ABANDONED is our "was dead" endgame.

## Plan

### Phase 1 — Retarget the state machine
- [ ] `task_state_machine.py`: change the `dead_letter_held` transition's
      `to_state` from `Status.DEAD_LETTER` to `Status.ABANDONED`; rename it
      (e.g. `abandon_held_exhausted`) so its name matches what it now does.
- [ ] `queue_liveness.py`: change `reconcile_liveness`'s cap-exceeded branch
      (`to_status = Status.DEAD_LETTER if attempts >= cap else
      Status.QUEUED`) to target `Status.ABANDONED`. Update the `UPDATE
      tasks SET ...` clause for this branch to match the shape already used
      elsewhere for `ABANDONED` (clear `owner`/`owner_session_id`, set
      `completed_at`) rather than today's bare `status = ?, updated_at = ?`
      (today's `DEAD_LETTER` branch never cleared ownership). Update the
      audit note (`"owner-gone (dead-letter: max attempts)"` ->
      `"owner-gone: abandoned (max attempts exhausted)"` or similar). Keep
      the returned counts dict's `dead_lettered` key name as-is for output
      shape stability, or rename it and update every caller — decide during
      implementation; either is fine, just be consistent and update
      `docstring`s to match.
- [ ] Tests: `test_task_state_machine.py` and `test_gc.py`/`test_queue.py`'s
      cap-exceeded scenarios assert `ABANDONED`, not `DEAD_LETTER`; a new
      test confirms ownership is actually cleared and `completed_at` is set
      on this path (a real gap in today's behavior, not merely a rename).

### Phase 2 — Remove the now-dead reservation-settlement branches
- [ ] Delete `recover_dead_lettered_cold_reservations()`
      (`spawn_cold_recovery.py`) and its call site
      (`Supervisor.recover_dead_lettered_cold_reservations`,
      `supervisor.py`) — `reconcile()`'s generic `_TERMINAL`-based
      settlement now covers this case since `ABANDONED` is already a
      member.
- [ ] Delete the explicit `if status == Status.DEAD_LETTER: ... continue`
      branch inside `Supervisor.recover_gone()` — same reasoning; the
      preceding `if status in _TERMINAL: continue` now covers it.
- [ ] Update `spawn_cold_recovery.py`'s and `supervisor.py`'s `_TERMINAL`
      set comments: remove the "DEAD_LETTER deliberately excluded... settled
      elsewhere" rationale, since nothing settles it elsewhere anymore (it
      is no longer reachable at all).
- [ ] Tests: `test_task_reservation_consistency.py`/`test_supervisor.py`'s
      coverage of the two removed branches is deleted or repurposed to
      confirm the generic `reconcile()` path now handles the equivalent
      scenario (a `COLD`/`SPAWNED` reservation whose task is `ABANDONED` via
      this specific retry-exhaustion path).

### Phase 3 — Migration
- [ ] Add a self-applying, idempotent migration (mirroring
      `plugins/agent-dispatch/docs/status-rename-migration-2026-09-29.md`'s
      exact pattern): `tasks.status`: `dead_letter -> abandoned`;
      `task_events.from_status`/`to_status`: the same swap, both columns;
      record `queue_migrations.name =
      "2026-0X-XX-retire-dead-letter-status"` so it never re-runs.
- [ ] Write the migration doc (same shape as the status-rename one):
      background, symptom, the common "just upgrade" case, the manual
      repair SQL for a restored-backup/forked-queue edge case, what NOT to
      touch (the unrelated `spawn_attempt_projection.py` "dead_lettered"
      diagnostic terminology never touched this column).
- [ ] Decide and document whether `Status.DEAD_LETTER`
      (`queue_records.py`) stays defined (read-compat for any
      not-yet-migrated row / historical audit) or is deleted outright once
      the migration ships — leaning toward **keep the constant, drop it
      from `TERMINAL`/`CONCLUDED`** so no *new* code path can ever target it
      again, while old historical references (docs, audit rows pre-dating
      the migration) still resolve the name.
- [ ] Tests: migration applies cleanly to a fixture DB with pre-rename rows;
      re-running it is a no-op; a restored-backup scenario (migration row
      present, `dead_letter` rows re-introduced) is covered by the doc's
      manual-repair SQL, mirroring the status-rename doc's own worked
      example.

### Phase 4 — Docs
- [ ] `plugins/agent-dispatch/README.md`: update the State model section's
      terminal-state list.
- [ ] Cross-link the new migration doc from
      `status-rename-migration-2026-09-29.md` (same convention that doc
      already uses to point forward).

## Validation Plan

- [ ] Full `agent-dispatch` plugin suite green
      (`test-supervisor -- python3 tools/run-plugin-tests.py agent-dispatch`).
- [ ] A simulated owner-gone-past-cap scenario resolves to `ABANDONED`
      (not `DEAD_LETTER`), with ownership cleared and `completed_at` set.
- [ ] Its `SPAWNED` and, separately, `COLD` reservation are each settled by
      the ordinary `reconcile()` path — confirmed by a test that does
      **not** call either removed bespoke sweep function (proving they are
      genuinely unnecessary, not merely unused).
- [ ] The migration converts a fixture DB's pre-existing `dead_letter` rows
      (`tasks` and `task_events`) to `abandoned`/`completed`-shaped values
      correctly, is idempotent, and the documented manual-repair SQL works
      against a restored-backup fixture.
- [ ] `agent-dispatch abandon`/`reset` are unaffected for every other
      existing source state (no regression to the broader state machine).
- [ ] Confirm `spawn_attempt_projection.py`'s `"dead_lettered"`
      diagnostic + `reservations rearm` are untouched and still pass their
      own existing tests — explicitly out of this effort's scope.

## Proposal

_Pending review._

## Journal

### 2026-09-30 — Kickoff
- Investigated 5 stuck `dead_letter` tasks found during a dotfiles-side
  queue stock-take. Traced the only assignment path
  (`queue_liveness.reconcile_liveness`'s cap-exceeded branch) and confirmed,
  by direct CLI test, that neither `abandon` nor `reset` accept
  `dead_letter` as a source state — a genuine dead end, not an oversight on
  the operator's part.
- Found and carefully distinguished a second, unrelated "dead_lettered"
  concept (`spawn_attempt_projection.py` + `reservations rearm`) that
  *does* work as designed, for a different (still-queued, never-claimed)
  scenario — explicitly scoped out to avoid conflating the two.
- Read `Supervisor.reconcile()` and confirmed it already generically
  settles `RESERVING`/`SPAWNED`/`COLD` reservations for any `_TERMINAL`
  status, which already includes `ABANDONED` — meaning the two bespoke
  `DEAD_LETTER`-specific sweeps become dead code once retargeted, not
  functionality needing porting. This materially de-risks the change from
  what it looked like at first read.
- Filed #4744 and this effort's plan; implementation not yet started,
  pending review of the plan (propose-before-you-do, given this touches
  the core liveness/reservation state machine and ~9 existing test files).
