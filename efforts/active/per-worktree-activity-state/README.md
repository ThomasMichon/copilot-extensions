# Per-worktree activity state (retire the global activity-log hot path)

- **Slug:** `per-worktree-activity-state`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs, this effort file updated in the same worktree as whichever phase is active
- **Created:** 2026-10-07
- **Status:** Active
- **Vision:** zero-downtime / resident-daemon efficiency (status-monitor should never cost more than a bounded sweep)
- **Umbrella issue:** ThomasMichon/copilot-extensions#5664

## Guiding Intent

The resident `status-monitor` daemon must never re-derive correctness-relevant
state by scanning an unbounded, shared journal. Per-worktree state belongs in
a transactionally-updated slot on that worktree's own record; a journal
(global or per-worktree) exists only for human/on-demand diagnostics ("what
just happened", a `doctor`-style flow), never as a source of truth an
automatic sweep depends on.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|--------------|
| this operator's primary machine | Drives all phases | the originating worktree and its successors, one per phase |

## Coordination

- **Topology:** single sequential driver, one PR per phase (no parallel
  slices; each phase's PR targets `dev` and is reviewed/merged before the
  next phase's worktree is created).
- **Host (owns PRs):** this operator's primary machine (the only
  participant).
- **Delegates:** none.
- **Handoff:** not applicable today (single driver); if a future session
  picks up a later phase, resume via this README's Plan checklist and
  Journal -- the next unchecked Phase is always the next slice.

## Context

Live-diagnosed via `py-spy` stack sampling against a real resident
`status-monitor` process sustaining ~70-90% single-core CPU for 70+ minutes.
Root cause: `activity.read_events()` (a full linear scan + late `limit`
truncation of a machine-global, unbounded `activity.jsonl`, confirmed at
30+ MB / 100k+ lines on one machine) is called from the daemon's automatic
sweep, per tracked worktree, per project, every interval, to reconstruct
handoff-cutover lifecycle state (spawn-attempted / predecessor-retire-pending)
that should instead be tracked directly on the worktree's own record.

Confirmed-correct precedent already in the codebase, both of which this
effort generalizes rather than invents:

- `handoff_trace.py` -- an already-proven per-project/per-worktree
  JSONL-with-cross-process-lock pattern (lock via `fcntl`/`msvcrt`), scoped
  today only to the 13-stage handoff-cutover event subset.
- `agent_logger.sync.compact` -- an already-proven archive/verify/reclaim
  pattern (`sessions.archive_session` -> tar.gz, `verify_archive` before
  `force_rmtree`) for Copilot's own session-state folders.

Also already correct and NOT in scope to change:

- `WorktreeRecord.sessions` (per-worktree session journal) -- already exists.
- `stamp_session_state_worktree_binding()` (session-state-folder reverse
  pointer to its worktree) -- already exists, wired into `register-session`.
- The claims/follow-up ledger's existing slot-vs-journal split (disposition
  lives in the YAML; the log is history only) -- already correct; model new
  handoff-lifecycle state the same way.

## Request

(Operator, verbatim premise across the inception exchange)

> What we need is that when a new daemon stands up, it needs to: 1) Drop a
> registration file announcing itself (version + port discovery) 2) Find the
> port for its now-outmoded predecessor, and send it a handshake to tell it
> it's done, if a predecessor is still live 3) While handling new things,
> periodically check for "drained" responsibilities now freed up from the
> outgoing daemon. An old daemon needs to: 1) Periodically sweep for evidence
> it's been superseded... ideally once a minute, or after any reasonable
> sweep cycle 2) If pinged to stand down, immediately begin draining,
> terminate subscriptions, stop cycling, and reject new calls 3) Once
> drained, exit.
>
> [On discovering the real root cause was CPU, not the cutover handshake
> itself:] Wait, what is this activity log that we're reading? Why do we need
> to read back from an activity log for anything we're doing? The "recent
> activity" for any worktree should just be a *single slot* per worktree; in
> parallel we can update a log if needed, but it won't be read back, unless
> on-demand for diagnostics. And the "Messages" read-back action for a
> worktree is nested in a menu for a reason, to avoid needing to keep it live.
>
> The expectation was several-fold: 1) We expect there to be a file *per
> worktree* which keeps a journal of the *session ids* in that worktree. 2) We
> expect each session-state folder to receive a metadata marker tracking the
> given session back to the worktree(s) it gets initialized in (via CWD as
> determined by the sessionStart hook) 3) We expect the "activity log", if
> such exists, to be tracked alongside the *per worktree session-journal
> file*, so that each worktree has its own. A long-running worktree will
> accumulate these, but whatever. 4) All "live" state gets reported through
> agent-worktrees, which writes to its DB or appropriate per-worktree
> "slots". It may then "journal" updates to (what are essentially)
> append-only journals 5) Journals should only ever be used for on-demand
> "doctor" flows, should a worktree be identified as broken. All other status
> reporting must be done by reading the transactionally-updated assigned
> slots. 6) Computed worktree status can read from any number of assigned
> sub-status slots for a worktree, but status should *not* be computed from
> reading an unbounded journal, especially a machine-global shared one.
>
> (a) [confirming: fully generalize the per-worktree journal pattern,
> retiring the global activity.jsonl entirely, rather than merely gating
> reads]
>
> We'll need to then carefully split the global one back across worktrees,
> and come up with an archival strategy for worktree info. Might be worth
> adding a worktree-archival system to agent-logger, where we zip up
> worktree-state folders under `<repo>` and move them to the agent-logger
> session archive root upon "cleanup".

Items 1 and 2 above were confirmed already implemented
(`WorktreeRecord.sessions`, `stamp_session_state_worktree_binding`) during
investigation; this effort covers items 3-6 plus the migration and archival
follow-ons.

## Plan

### Phase 1 — Direct handoff-lifecycle slots
- [ ] Extend `SessionHandoff` (tracking.py) with: `spawn_attempted_at`,
      `predecessor_retire_state` (`pending|retired|abandoned`),
      `retire_attempts`, `retire_last_attempt_at`, `retire_last_outcome`,
      `retire_last_method`.
- [ ] Dual-write these fields at every existing `activity.log_event()` call
      site that currently emits `handoff_successor_spawn_started`,
      `handoff_cutover_spawn`, `handoff_successor_spawn_failed`,
      `handoff_predecessor_retire` -- same lock, same transaction, no new
      I/O.
- [ ] Preserve the existing abandon-after-N-unrecoverable-failures semantics
      (`_RETIRE_TERMINAL_FAILURE_METHODS`, `_RETIRE_ABANDON_GRACE_S`) exactly,
      now computed from the slot fields instead of log replay.

### Phase 2 — Rewire hot-path consumers onto slots
- [ ] `__main__._pending_handoff_retire_requests` -- read `record.handoffs`
      directly; stop calling `activity.read_events`/`handoff_trace.read_trace`
      for decision-making.
- [ ] `status_monitor_runtime._monitor_pending_handoff_request` -- same.
- [ ] `sessions_pane_retire.already_attempted_handoff_tokens` -- same.
- [ ] `handoff_cutover._maybe_emit_stage_13` -- its `retired` check (an
      unbounded, no-`limit` scan for `handoff_predecessor_retire`/`outcome
      == "gone"`) becomes a direct `handoff.predecessor_retire_state ==
      "retired"` read. Runs from both the automatic retire path and
      hook-invoked session registration -- not an on-demand diagnostic,
      must not keep scanning the log.
- [ ] `session_binding_cli.py`'s `already_retired` check inside
      `cmd_register_session` (the same `handoff_predecessor_retire`/`outcome
      == "gone"` scan, called on every session registration) -- same slot
      read as the previous item.
- [ ] `tracking_disposition_write.py`'s `status_reported` dedup -- replace
      the unbounded, no-`limit` full-file scan with a direct per-session
      dedup field (e.g. on the matching `SessionEntry`), since this one
      fires on every ordinary `status --activity` CLI call fleet-wide, not
      just the daemon sweep.
- [ ] Confirm `handoff_diagnostics.py`, `session_binding_cli.py`'s on-demand
      verbs, and `handoffs-check` remain the only journal readers, explicitly
      (diagnostic/on-demand only, per the Request's item 5) -- audit for any
      further automatic (non-on-demand) caller beyond the five above before
      declaring this phase done.

### Phase 3 — Generalize the per-worktree journal
- [ ] Extend `handoff_trace.py`'s proven per-project/per-worktree
      JSONL-with-lock pattern (or a sibling module reusing its primitives)
      to cover every `activity.log_event()` kind, not just the 13
      handoff-cutover stages.
- [ ] `activity.log_event()` writes to the per-worktree file instead of (or
      in addition to, during transition) the global one.
- [ ] Every remaining on-demand reader (the "Messages" menu action, CLI
      `activity`/`activity-log list` verbs) reads the per-worktree file
      directly -- no more global-file + `worktree_id` filtering.

### Phase 4 — One-time migration
- [ ] A best-effort, idempotent migration pass that reads the existing
      global `activity.jsonl` once and appends each record's history into
      the correct per-worktree file it describes, then marks itself done
      (a stamp file) so it never re-runs.
- [ ] Wire it into `agent-worktrees update`/install so every existing
      machine picks it up once, automatically.

### Phase 5 — Retire the global log
- [ ] Once Phases 1-4 are live and proven (no remaining reader of the global
      file), stop writing to `activity.jsonl` entirely and remove the
      now-dead global-log code path.
- [ ] Update `activity.py`'s module docstring and any docs referencing the
      machine-global log.

### Phase 6 — Worktree-state archival (agent-logger)
- [ ] New capability in `agent-logger`: on worktree cleanup (a hook from
      `agent-worktrees`' own `cleanup`/`finalize` terminal path), archive
      that worktree's own accumulated per-worktree state (its activity-log
      file, its `handoff_trace` file, any other per-worktree sidecar) --
      reusing the already-proven `sessions.archive_session` /
      `verify_archive` / reclaim pattern from `agent_logger.sync.compact` --
      into `agent-logger`'s existing local archive root
      (`cfg.compact_archive_root` or a sibling root), grouped under
      `<repo>/<worktree-id>`.
- [ ] Verify-before-reclaim, exactly like session compaction: never delete
      the live per-worktree state until the archive is confirmed intact.

## Validation Plan

- [ ] Unit tests for the new `SessionHandoff` fields and the rewritten
      abandon-after-N-failures logic (Phase 1).
- [ ] Unit tests proving the 6 rewired hot-path functions never call
      `activity.read_events`/`handoff_trace.read_trace` (Phase 2) --
      e.g. a monkeypatch that raises if either is called during a sweep or
      during session registration.
- [ ] Unit tests for the generalized per-worktree journal writer/reader,
      concurrent-writer-safety (mirroring `handoff_trace.py`'s own existing
      coverage) (Phase 3).
- [ ] A migration test: seed a synthetic global log with mixed-worktree
      entries, run the migration, assert each per-worktree file receives
      exactly its own entries, idempotent on a second run (Phase 4).
- [ ] Live validation on this machine: after deploying Phases 1-3, confirm
      via `py-spy` (or sustained CPU sampling) that a resident `status-monitor`
      no longer shows `read_events`/`handoff_trace.read_trace` in its hot
      sweep path, and CPU stays near-idle between sweeps.
- [ ] Archival round-trip test: create a worktree, accumulate some
      per-worktree state, clean it up, confirm the archive exists, verifies,
      and the live per-worktree state is gone (Phase 6).

## Proposal

_Pending review of Phase 1's PR (first reviewable slice)._

## Journal

### 2026-10-07 — Kickoff
- Live-diagnosed the real CPU-burn root cause (not the originally-assumed
  cutover-handshake race) via `py-spy` against a real pegged `status-monitor`
  process; confirmed exact call chain and file size/scan-cost.
- Confirmed items 1/2/4(partial) of the operator's architecture spec were
  already correctly implemented; scoped this effort to the remaining,
  confirmed gap (handoff-lifecycle state reconstructed from a journal
  instead of a slot) plus the operator's follow-on asks: full per-worktree
  journal generalization, a migration pass, and a new worktree-state
  archival capability in `agent-logger`.
- Filed ThomasMichon/copilot-extensions#5664 as the umbrella issue.
