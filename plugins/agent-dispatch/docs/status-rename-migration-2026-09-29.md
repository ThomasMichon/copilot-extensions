# Migration note: the 2026-09-29 `SUBMITTED`/`COMPLETED` status rename

This is a narrow-window troubleshooting/migration reference for an agent or
operator whose `agent-dispatch` coordinator (or a downstream consumer of its
API) ended up carrying the transient `CONFIRMED` lifecycle state introduced
on 2026-09-25 and retired four days later. The expected window of impact is
small -- most coordinators either never installed a version in that window,
or already picked up the rename's own self-applying migration -- but if you
land here from a stale checkout, an old backup, or a fork, this is the
worked procedure.

## Background

- **2026-09-25 (PR #3715):** `CONFIRMED` was introduced as a new terminal
  state. `COMPLETED` was demoted to a worker's *provisional* completion
  claim -- corroborated only once something calls `confirm()`, moving it to
  `CONFIRMED`, the new true terminal.
- **2026-09-29 (PR #4597):** that naming choice was reverted, because
  `Status.COMPLETED`/`"completed"` reads as terminal to any reasonable
  consumer, and any external system built before 2026-09-25 that simply
  checked `status == "completed"` as done was now silently wrong (rather
  than erroring loudly) for the four days `CONFIRMED` existed. Instead of
  asking every such consumer to learn a new name (`confirmed`) for "done",
  the **old names were restored to their old (terminal) meaning**:
  - `Status.COMPLETED`/`"completed"` (the 2026-09-25..2026-09-29 provisional
    claim) was renamed to **`Status.SUBMITTED`/`"submitted"`**.
  - `Status.CONFIRMED`/`"confirmed"` (the 2026-09-25..2026-09-29 true
    terminal) **reclaimed the name `Status.COMPLETED`/`"completed"`**.
  - The `confirm()` transition/verb/CLI subcommand is unchanged -- only the
    state *names* moved.

As of `agent-dispatch` >= `0.7.3-dev1`, `Status` has no `CONFIRMED` member at
all; `"confirmed"` is not a value any current code path writes or expects.

## Symptom

You see one or both of:

- A `tasks.db` row, or an API/CLI response, with `status` literally equal to
  `"confirmed"` -- a value `Status` no longer defines.
- Your own (non-`agent-dispatch`) consumer code checks `status == "completed"`
  and assumed the 2026-09-25..2026-09-29 meaning (provisional, not yet
  corroborated) -- it is now silently wrong in the other direction, because
  `"completed"` means the corroborated terminal again.

## The common case: just upgrade and restart -- data migrates itself

`agent-dispatch`'s own coordinator (`queue.py`) carries a **self-applying,
idempotent** migration that runs the moment a `TaskQueue` opens the database
under the new code, in a single transaction:

- `tasks.status`: `completed` -> `submitted`, `confirmed` -> `completed`
- `task_events.from_status` / `task_events.to_status`: the same two-value
  swap (both columns, both directions)
- records `queue_migrations.name = "2026-09-29-status-rename-submitted-completed"`
  so it can never re-run against the same database

**If you simply upgrade `agent-dispatch` to >= `0.7.3-dev1` and cut the
coordinator over the normal way (`agent-dispatch deploy`, which drains and
restarts it), the data migrates itself. No manual SQL is required or
expected in the common case.** Verify after the fact:

```bash
sqlite3 /path/to/tasks.db \
  "SELECT name, datetime(applied_at, 'unixepoch') FROM queue_migrations WHERE name LIKE '%status-rename%';"
sqlite3 /path/to/tasks.db "SELECT status, COUNT(*) FROM tasks GROUP BY status;"
```
A present migration row and zero rows with `status = 'confirmed'` means
you're done.

## When you DO need to intervene manually

1. **You restored an old backup after the migration already ran.** The
   migration is keyed on the presence of one `queue_migrations` row, so
   re-introducing old-shaped data (a pre-rename backup) will **not**
   automatically re-trigger it. Symptom: the migration row is present, but
   `SELECT COUNT(*) FROM tasks WHERE status='confirmed'` is nonzero.
2. **You run a fork or vendored copy** of `agent-dispatch`'s queue code that
   picked up the renamed string literals elsewhere but never pulled
   `queue.py`'s own migration block. Same symptom as above.
3. **You want a pre-flight check** before/after a scheduled upgrade window.

For any of these, the sanctioned manual repair matches the coordinator's own
migration exactly -- stop the coordinator first (no writer should race a
manual migration), take a fresh backup, then:

```bash
sqlite3 /path/to/tasks.db <<'EOF'
BEGIN IMMEDIATE;
UPDATE tasks SET status = CASE
    WHEN status = 'completed' THEN 'submitted'
    WHEN status = 'confirmed' THEN 'completed'
    ELSE status END
  WHERE status IN ('completed', 'confirmed');
UPDATE task_events SET
    from_status = CASE
      WHEN from_status = 'completed' THEN 'submitted'
      WHEN from_status = 'confirmed' THEN 'completed'
      ELSE from_status END,
    to_status = CASE
      WHEN to_status = 'completed' THEN 'submitted'
      WHEN to_status = 'confirmed' THEN 'completed'
      ELSE to_status END
  WHERE from_status IN ('completed', 'confirmed')
     OR to_status IN ('completed', 'confirmed');
INSERT OR IGNORE INTO queue_migrations(name, applied_at)
  VALUES ('2026-09-29-status-rename-submitted-completed', strftime('%s', 'now'));
COMMIT;
EOF
```

**Why a single `CASE` expression, not two sequential `UPDATE` statements:**
old-`completed` and old-`confirmed` map into strings the *other* direction
also matches (`completed` -> `submitted`, `confirmed` -> `completed`). Two
separate statements run in the wrong order (or without an intermediate
placeholder value) will double-convert: renaming `confirmed` -> `completed`
first, then blindly renaming all `completed` -> `submitted` second, would
also catch the rows you just wrote in step one. A single `CASE` evaluates
each row's *original* value once, so both branches apply atomically per-row
with no double-conversion risk. (If you prefer two statements for some
reason, use a literal placeholder value for the intermediate step -- never
run the two real target values back-to-back.)

## What NOT to touch

- **Don't** apply this rename to any table/column outside `agent-dispatch`'s
  own `tasks.db` just because it contains the string `"completed"` or
  `"confirmed"` -- a downstream consumer's own database is a different
  namespace entirely, and this migration has no opinion on it (see the next
  section for what *does* need updating there).
- **Don't** touch `registrations.status` (worker registration state, e.g.
  `active`) -- an unrelated enum that never used `completed`/`confirmed`.
- The `CASE`/`WHERE ... IN (...)` scoping above only ever matches the two
  exact literal values -- it will not accidentally rewrite an unrelated
  column or a substring match.

## Downstream consumer code

Migrating the coordinator's *data* does not fix a consumer's own code that
still checks the old literal strings. If your service/script/agent checks
`status == "completed"` and assumed the 2026-09-25..2026-09-29 meaning, you
must also update your own code: old `"completed"` (provisional) becomes
`"submitted"`; old `"confirmed"` (terminal) becomes `"completed"`. The
`confirm()` transition/verb/CLI subcommand is unchanged.

See PR #4597 (this rename) for the full plugin-side diff, and aperture-labs
PR #7777 (`services/intelligence-dampener/.../harness/dispatch_review.py`)
for a worked real-world example of adopting the new mapping in a consumer.

## Timeline reference

| Date | Change | Ref |
|---|---|---|
| 2026-09-25 | `CONFIRMED` introduced; `COMPLETED` demoted to provisional | PR #3715 |
| 2026-09-29 | Renamed so old names keep old meanings: `COMPLETED` -> `SUBMITTED` (provisional), `CONFIRMED` reclaims `COMPLETED` (terminal). Self-applying DB migration ships in the same release. | PR #4597 |
