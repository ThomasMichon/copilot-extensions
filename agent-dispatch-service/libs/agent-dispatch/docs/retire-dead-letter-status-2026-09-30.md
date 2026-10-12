# Migration note: retire the task-level `DEAD_LETTER` status

`agent-dispatch` no longer creates a distinct task-level `dead_letter` state.
When liveness GC confirms that an owned task's owner is gone and the retry cap
is exhausted, it now transitions the task to `abandoned`, clears ownership and
the lease, and stamps `completed_at`. `abandoned` is the single terminal
end-state for work that will not be retried.

This does not change the unrelated `spawn_attempt_projection.py` diagnostic
field named `dead_lettered`, or the `reservations rearm` recovery path for
queued tasks that have never been claimed.

## Common case: upgrade and restart

Opening the queue with the new code applies the idempotent migration
`2026-09-30-retire-dead-letter-status` in one transaction:

- `tasks.status`: `dead_letter` -> `abandoned`
- `task_events.from_status` and `task_events.to_status`: the same mapping
- legacy rows have ownership and lease fields cleared, and receive
  `completed_at` when it was absent

The migration records its name in `queue_migrations`, so a normal restart does
not run it twice.

## Manual repair

Use this only for a restored backup or a fork that already contains the
migration marker but has reintroduced old rows. Stop the coordinator, back up
the database, then run:

```sql
BEGIN IMMEDIATE;
UPDATE tasks
SET status = 'abandoned',
    owner = NULL,
    owner_session_id = NULL,
    lease_expires_at = NULL,
    completed_at = COALESCE(completed_at, updated_at)
WHERE status = 'dead_letter';
UPDATE task_events
SET from_status = CASE WHEN from_status = 'dead_letter' THEN 'abandoned' ELSE from_status END,
    to_status = CASE WHEN to_status = 'dead_letter' THEN 'abandoned' ELSE to_status END
WHERE from_status = 'dead_letter' OR to_status = 'dead_letter';
COMMIT;
```

Do not alter the `dead_lettered` spawn-attempt diagnostic; it is a separate
concept and is intentionally out of scope.
