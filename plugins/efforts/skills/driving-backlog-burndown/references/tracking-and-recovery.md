# Tracking and recovery

Companion to [driving-backlog-burndown](../SKILL.md). Use its charter and
dashboard assets; this reference defines how their state survives a long
campaign without becoming another orchestration runtime.

## Authority and state layers

| Layer | Owns | Does not prove |
|---|---|---|
| Tracker / PR provider | Current item/revision, reviews, CI, merge/closure | Local implementation activity or installed behavior |
| Workspace / execution / resource owner | Claims, controller, native session, actual execution, leases | A provider merge or permission to take another owner's work |
| Canonical effort and dashboard | Parent objective, current decisions, per-item/owner evidence, next gates, journal | Freshness beyond each recorded observation |
| Session SQL / todos | Efficient joins, priorities, current sweep and local subtasks | A portable handoff or authority to reassign an item |
| Checkpoint / transfer brief | Immediate recovery delta, live obligations and exactly one next action | New authorization or replacement of the effort |

Resolve the adopting repository's state home and writable workspace through
`planning-efforts`. Personal/private evidence stays in its proper state
repository, not a shareable harness or public upstream. Publish only minimal,
synthetic or sanitized examples upstream. Do not store credentials or
credential-bearing commands in any layer.

The dashboard is a current projection, not a growing transcript. Keep a short
current snapshot and owner table at the top; append material history to the
effort journal. Record a new observation rather than silently overwriting a
contradicted old one. Mark old snapshots historical.

## Optional session SQL

For a small queue, the Markdown dashboard is sufficient. If the host supplies
a session SQL tool, the following cache avoids repeatedly loading large tables.
These names are campaign-specific and do not replace host task/claim tables.
Use the host's existing `todos`/`todo_deps` tables for actual local prerequisites;
never recreate them. Track every real dependency, not an implied list order.

```sql
CREATE TABLE IF NOT EXISTS burndown_items (
  item_id TEXT PRIMARY KEY,
  item_url TEXT NOT NULL,
  repository TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revision TEXT,
  owner_id TEXT,
  state TEXT NOT NULL,
  current_gate TEXT,
  next_action TEXT,
  observed_at TEXT NOT NULL,
  evidence_ref TEXT
);
CREATE TABLE IF NOT EXISTS burndown_owners (
  owner_id TEXT PRIMARY KEY,
  workspace_ref TEXT,
  session_ref TEXT,
  last_native_at TEXT,
  actual_activity TEXT,
  blocker TEXT,
  next_action TEXT,
  evidence_ref TEXT,
  external_obligations TEXT
);
CREATE TABLE IF NOT EXISTS burndown_sweeps (
  sweep_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  query_scope TEXT NOT NULL,
  open_count INTEGER,
  oldest_at TEXT,
  coverage_gaps TEXT,
  report_ref TEXT
);
```

Use qualified item IDs, for example `tracker/repository/item`, and consistent
UTC timestamps. Insert actual provider data, not synthetic stand-ins for
unavailable reads. Leave unknown metrics NULL; record incomplete enumeration
and unavailable owners in `coverage_gaps`.

Ready to inspect, oldest first:

```sql
SELECT i.item_id, i.owner_id, i.revision, i.current_gate,
       o.last_native_at, o.blocker, i.next_action
FROM burndown_items AS i
LEFT JOIN burndown_owners AS o ON o.owner_id = i.owner_id
WHERE i.state = 'open'
ORDER BY i.created_at, i.item_id;
```

This is an inspection order, not authorization or an automatically dispatchable
queue. Update each row only from the source that owns that fact; do not mark an
item merged from an owner's message or advance `last_native_at` from a delivery
receipt. An observation attempt can be fresh while the last successful native
observation remains old.

SQL survival is host-dependent. Do not assume another session can read this
database. Before an owner transfer, export the necessary current rows/schema
to the canonical dashboard or a named state artifact in the resolved effort
home, through its publication policy. Record the artifact revision and
observation times. Rebuild a cache from that durable state; refresh providers
before consequential actions rather than treating cached rows as live truth.

## Evidence and interventions

Record the actual boundary crossed:

- **Query succeeded** is not complete enumeration unless pagination/limits and
  scope were checked. A failed query is unknown, not an empty backlog.
- **Native output read** establishes the observed action and timestamp, not
  that every background process or broader parent finished.
- **Message admitted** is not consumed; consumed is not acted on; a completed
  turn is not independent proof of the requested result.
- **Tests passed** applies only to the tested source, selection, platform,
  fixture and limits. A smaller diagnostic or altered private composite is
  not acceptance of the publishable candidate.
- **PR merged** must come from the provider. Release, deployment and installed
  consumer proof are separate obligations when the charter includes them.

Give a steering message the exact item/source, observed blocker, one bounded
action, required evidence, scope exclusions and stopping condition. Reuse the
existing owner and conversation. Follow the owning executor's supported
observation/steering path; never infer absence from a missing registration,
pane, reply or failed transport. Surface the failed boundary without repairing
shared trust, services, runtimes or policy on inferred authority.

When an owner is parked on a human form, record the literal choice, age,
required decision-maker and whether it is already covered. Do not replace the
decision with another status prompt, silently choose a default or count queued
messages as progress. Escalate genuinely missing decisions through the host's
question mechanism; do not repeatedly ask for an authority already granted.

## Same-owner context rollover

When the host provides native context management:

1. Finish or record the immediate action and update canonical milestone state.
   Keep completed work, rejected approaches and the current measured failure;
   do not repeat settled investigation just to fill the checkpoint.
2. Write the checkpoint with the host's session-artifact tool. For a
   revision-bound `checkpoint.md`, read/stat the existing artifact and supply
   its expected revision when replacing it; use atomic append only for short
   deltas. Do not overwrite another participant's checkpoint.
3. Include parent completion gate, current slice, canonical paths/revisions,
   pending human choices, full owner-roster/state pointers, uncovered owners,
   live PRs/watches/shells/schedules, claims/leases and deadlines, and **exactly
   one next action**. Include the precise stopped/blocked boundary and what
   must not be retried or inferred.
4. Keep the whole checkpoint within the host's read limit. Compact growing
   history into the canonical journal or addressable state artifacts; never
   rely on an oversized artifact whose middle disappears on read.
5. Read it back and verify the critical state is complete before the terminal
   native rollover action (`new_context` where offered). This is the same
   owner/session: preserve its schedules and in-flight obligations rather than
   triggering a responsibility-transfer workflow or spawning a successor.
6. On return, read the bound checkpoint first, reload applicable guidance/tool
   catalogs, re-read the effort's current slice and execute the recorded next
   action. Consult bounded prior history only for a specifically missing fact.
   A persisted assertion that guidance was loaded is not current admission.

When those tools are absent, follow the host's supported continuation owner
instead of inventing files, a memory store or a raw session-clear command.

## Actual transfer, restart and resumption

An actual owner transfer is different from context-only rollover. Use
`context-handoff:context-handoff` when available and follow its authorization,
quiescing, synchronization, storage and pickup protocol. A standalone brief
does not transfer a worktree, claim, lease or worker by itself.

Before transferring, stop owned scheduled prompts through their owning tool
and record their IDs, exact payloads/cadences, last sweep and resumption
conditions. Capture results and settle/quiesce owned shells or explicitly
document a permitted exception. Preserve peer-owned workers. Journal and sync
the current worktree only through the repository's normal publication flow;
do not finalize an unfinished parent to make the transfer look clean.

The brief carries the canonical effort pointer and immediate delta rather than
copying its whole plan. Include the recipient/custody state, constraints,
external obligations, unsettled PRs, current decisions and exactly one next
action. Do not silently transfer claims, grant approval or release a lease.

After a process restart, distinguish dead caller-owned observers from remote
workers that may still be running. Inventory live state and use the owning
supervisor's rejoin/rearm procedure; do not seed replacements. Recreate a
schedule only after confirming it is absent and current authority still
permits it. An operator pause stops the campaign loop; do not rearm it.

## Rehearsal before relying on a campaign

Using synthetic items and no mutations, walk through: one ready item, one
active owner with a real test failure, one unreachable owner, one pending
human choice and new inflow. Confirm the dashboard retains all five facts,
unknown is not zero, independent work can proceed, gates are not weakened,
and the checkpoint reconstructs the parent, obligations and one next action.
Repeat the rehearsal for native rollover versus actual transfer: schedules
are preserved in the former and explicitly stopped/transferred in the latter.
