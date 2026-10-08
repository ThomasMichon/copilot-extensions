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
- [ ] **Crash-safe commit ordering.** The slot and the diagnostic log are
      not one atomic commit -- a YAML write under `_RecordLock` and a
      best-effort JSONL append cannot both land atomically, and the
      existing spawn marks bracket external process creation with no lock
      held at all (`handoff_cutover.py` spawn-started/spawn-result sites).
      Commit the slot update first, under its own `_RecordLock`
      transaction, and only emit the corresponding
      `activity.log_event()`/trace write after that commit succeeds. A
      crash between the two leaves the slot (authoritative) correct and
      only the diagnostic trail short one event -- never the reverse.
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
      verbs, `handoffs-check`, and `health.find_orphaned_handoffs()` (the
      on-demand maintenance audit/repair flow, called from
      `maintenance_cli.py`) remain the only journal readers, explicitly
      (diagnostic/on-demand only, per the Request's item 5) -- audit for any
      further automatic (non-on-demand) caller beyond the six above before
      declaring this phase done.

### Phase 3 — Generalize the per-worktree journal

Full design, project-routing requirements, and the `boot_trace`
machine-scoped-events decision: [`phase-3-journal-generalization.md`](phase-3-journal-generalization.md).

- [ ] Extend `handoff_trace.py`'s proven per-project/per-worktree
      JSONL-with-lock pattern to cover every `activity.log_event()` kind.
- [ ] `activity.log_event()` writes to the per-worktree file; every
      remaining on-demand reader reads it directly.
- [ ] Define the unfiltered `agent-worktrees activity` merge-discovery
      contract.
- [ ] Define authoritative project routing for every live writer (never
      guess via ambient fallback alone).
- [ ] Decide and implement the `boot_trace` machine-scoped pre-resolution
      events' destination (or their deliberate retirement).

### Phase 4 — One-time migration

Full design (multiplicity-preserving dedup, resumable-offset cutover,
era-matched provenance, ambiguity handling): [`phase-4-migration.md`](phase-4-migration.md).

- [ ] A migration pass copies the existing global `activity.jsonl`'s
      history into the correct per-worktree files, safely: no duplicate
      entries across retries or dual-writes, no stranding of a
      concurrently-appended straggler, no misattributing a reused worktree
      id's history across projects/eras, and no guessing at an ambiguous
      or orphaned record.
- [ ] Wired into `agent-worktrees update`/install, safe to re-run.

### Phase 5 — Retire the global log
- [ ] Once Phases 1-4 are live and proven (no remaining reader of the global
      file), stop writing to `activity.jsonl` entirely and remove the
      now-dead global-log code path.
- [ ] Update `activity.py`'s module docstring and any docs referencing the
      machine-global log -- explicitly including
      `docs/patterns/lifecycle-activity-logging.md` (its Tier A/B/C table
      and "Tier A is the durable record of record" framing both need
      rewriting once Tier A itself becomes per-worktree, likely folding
      into/merging with the existing Tier C description) and
      `plugins/agent-worktrees/docs/cli-reference.md`'s `activity` section
      (its documented machine-global retention/behavior, reconciled with
      Phase 3's unscoped-merge decision), plus the `boot_trace` decision's
      own documentation implications (see `phase-3-journal-generalization.md`).

### Phase 6 — Worktree-state archival (agent-logger)

Full design (fail-open composition seam, archive key/collision avoidance,
archived-journal discovery, standalone-install retention floor):
[`phase-6-archival.md`](phase-6-archival.md).

- [ ] A fail-open, lower-tier-owned composition seam (never a direct
      `agent-worktrees` -> `agent-logger` call) that archives a cleaned-up
      worktree's accumulated per-worktree state, verify-before-reclaim,
      correctly namespaced by project (not just repo).
- [ ] `agent-worktrees`' own baseline bounded-retention fallback for a
      standalone install with no archiver, so disk use never regresses to
      unbounded growth.
- [ ] The unscoped `activity` view (Phase 3) can still discover archived
      history via the fixed, agent-worktrees-known archive path.

## Validation Plan

- [ ] Unit tests for the new `SessionHandoff` fields and the rewritten
      abandon-after-N-failures logic (Phase 1).
- [ ] A crash-ordering test: simulate a failure between the slot commit and
      the diagnostic-event emission; assert the slot remains correct and
      authoritative regardless, and that the diagnostic write never
      precedes the slot commit (Phase 1).
- [ ] Unit tests proving the 6 rewired hot-path functions never call
      `activity.read_events`/`handoff_trace.read_trace` (Phase 2) --
      e.g. a monkeypatch that raises if either is called during a sweep or
      during session registration.
- [ ] Phase 3's validation: see [`phase-3-journal-generalization.md`](phase-3-journal-generalization.md)
      (journal writer/reader concurrency, unscoped merge-discovery,
      project-routing, `boot_trace`).
- [ ] Phase 4's validation: see [`phase-4-migration.md`](phase-4-migration.md)
      (idempotency, dedup multiplicity, concurrent-tail writes, temporal id
      reuse, ambiguity handling).
- [ ] Live validation on this machine: after deploying Phases 1-3, confirm
      via `py-spy` (or sustained CPU sampling) that a resident `status-monitor`
      no longer shows `read_events`/`handoff_trace.read_trace` in its hot
      sweep path, and CPU stays near-idle between sweeps.
- [ ] Phase 6's validation: see [`phase-6-archival.md`](phase-6-archival.md)
      (fail-open composition, archive-key collision, standalone retention,
      archived-journal discovery, archival round-trip).

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

### 2026-10-08 — Plan review (PR #5669), 7 rounds
- Drove the plan-only PR through 7 review rounds, resolving 1 Critical
  equivalent set of migration/archival correctness gaps (High findings):
  a crash-safe slot/log commit ordering (Phase 1), two additional
  automatic journal readers (Phase 2), an unscoped-`activity`-view
  contract and live-write project-routing requirement (Phase 3),
  migration safety across crashes/dual-writes/repeated-content/temporal
  id reuse/project ambiguity (Phase 4), and a fail-open composition seam
  plus a standalone-install retention floor for archival (Phase 6) --
  plus a `boot_trace` machine-scoped pre-resolution-event gap the global
  log's retirement would otherwise silently break.
- Restructured the README per a Low finding and this repo's own
  decompose-liberally convention: extracted Phases 3/4/6's detailed
  design and validation into linked sibling docs
  (`phase-3-journal-generalization.md`, `phase-4-migration.md`,
  `phase-6-archival.md`), keeping the shared coordination README a
  concise, navigable map.

