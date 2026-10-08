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
- [ ] Extend `handoff_trace.py`'s proven per-project/per-worktree
      JSONL-with-lock pattern (or a sibling module reusing its primitives)
      to cover every `activity.log_event()` kind, not just the 13
      handoff-cutover stages.
- [ ] `activity.log_event()` writes to the per-worktree file instead of (or
      in addition to, during transition) the global one.
- [ ] Every remaining on-demand reader (the "Messages" menu action, CLI
      `activity`/`activity-log list` verbs) reads the per-worktree file
      directly -- no more global-file + `worktree_id` filtering.
- [ ] **Unfiltered `agent-worktrees activity` contract.** `activity.py` and
      `plugins/agent-worktrees/docs/cli-reference.md` both currently
      document a deliberately unscoped "full retained log" view (no
      `--worktree-id`/`--project` required) -- once there is no single
      global file, this needs an explicit, chosen behavior rather than
      silently breaking. An unscoped call discovers and merges every
      project's per-worktree journal files -- including the archived
      location Phase 6 introduces (a pure filesystem read of a known
      path convention, not a call into `agent-logger`; see Phase 6) --
      globally time-ordered, matching today's output shape (same fields,
      same sort) -- preserving existing UX/back-compat. This remains an
      on-demand diagnostic read only (the `activity` CLI verb, never a hot
      path), so the extra discovery I/O is acceptable there.

### Phase 4 — One-time migration
- [ ] A best-effort migration pass that reads the existing global
      `activity.jsonl` once and copies each record's history into the
      correct per-worktree file it describes.
- [ ] **Multiplicity-preserving deduplication, not set-membership.** A
      content hash alone is not a stable per-record identity: `log_event()`
      timestamps only to whole seconds, so two *legitimate, independently
      occurring* events can share identical fields; naive set-style
      dedup (hash already seen -> skip) would silently collapse real,
      distinct history. Match and de-duplicate by **multiset** comparison
      instead: for a given identity (hash of content, or an existing
      unique field if one qualifies), count how many copies already exist
      in the destination and how many appear in the source segment being
      migrated, and append only the excess -- never collapse to a single
      copy. Migration tests must include a case with genuinely repeated,
      identical-content events.
- [ ] **A completed read pass is not itself a safe cutover boundary.** A
      writer (including a Phase 3 dual-writer) can append to the global
      file after this pass's read reaches EOF but before the stamp is
      written; if that writer's corresponding per-worktree append then
      fails or the process crashes mid-dual-write, the stamp would
      permanently prevent that global-only straggler from ever migrating.
      Make the migration resumable by position, not one-shot-then-stamp:
      record the exact byte offset actually processed only *after* that
      segment's writes are confirmed durable in the destination, and treat
      a later run as picking up from the last confirmed offset rather than
      a full rescan or a hard "never again" stamp. Validate a writer
      appending concurrently with (and after) a migration pass's own read.
- [ ] **A current unique project match does not prove historical
      provenance.** Resolving a worktree id to exactly one *currently
      known* project does not establish that the id meant that project
      *at the time the old event was logged* -- a project can be
      reaped/deleted and its worktree id later reused by a different,
      unrelated project, in which case "currently unique" would silently
      misattribute the deleted project's history into the new one despite
      the never-guess intent. Require positive era-matched provenance:
      accept a match only when the event's own timestamp falls within that
      specific project+worktree's actual tracked lifetime window (its
      recorded start/completion bounds), not merely "resolves to one
      project today." An id that is unique today but fails the era check
      is treated the same as an ambiguous one -- unmigrated, reported, never
      guessed. Add a temporal-id-reuse test (same id, two different
      projects, non-overlapping eras) alongside the ambiguity test below.
- [ ] **Safe handling of project-ambiguous/orphaned records.**
      `activity.log_event()` does not persist its `project` argument, and a
      worktree id is only unique *within* one project (two different
      projects can legitimately share the same worktree id) -- a
      migrated-by-id-alone record can land in the WRONG project's file,
      corrupting that worktree's history. The migration must resolve each
      record's project unambiguously before writing: look it up against
      every currently-known project's tracking directory (subject to the
      era check above); migrate only when the worktree id resolves to
      **exactly one** live (or archived-but-identifiable) project+worktree
      for that event's own era. An id that matches zero or multiple
      projects -- or fails the era check against its one current match --
      is never guessed: it is retained in a clearly-labeled
      `activity.jsonl.unmigrated` sidecar (or equivalent) and reported in
      the migration's own summary output, never silently dropped or
      silently misfiled.
- [ ] Wire it into `agent-worktrees update`/install so every existing
      machine picks it up, automatically, safe to re-run given the
      multiplicity-preserving and resumable-offset requirements above.

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
      Phase 3's unscoped-merge decision above).

### Phase 6 — Worktree-state archival (agent-logger)
- [ ] **Composition seam, not a direct call.** `agent-logger` is an
      optional, higher-tier plugin; `agent-worktrees` (the ground-layer
      owner of worktree lifecycle/identity) must not call upward into it
      directly, and its absence must never block or slow cleanup
      (`docs/patterns/a-la-carte-independence.md`). `agent-worktrees`
      defines and owns a lower-tier, fail-open drop-in callback point in
      its own cleanup/finalize path (e.g. a well-known, optionally-present
      hook directory or entry-point convention already used elsewhere in
      this repo for a-la-carte composition); `agent-logger`, if installed,
      registers into that seam to archive the diagnostic bundle. The
      authoritative "this worktree is gone" tombstone/identity decision
      stays entirely inside `agent-worktrees` regardless of whether the
      archival seam is present, fires, or fails.
- [ ] **Archived-journal discovery stays lower-tier-owned.** Phase 3
      promises the unscoped `activity` view keeps matching today's full
      retained history, but archiving (and reclaiming the live copy of) a
      cleaned-up worktree's journal would otherwise make it vanish from
      that view immediately -- a real regression against today's 7-day
      rolling retention, which still shows a just-cleaned-up worktree's
      recent history. Fix: the archive's on-disk location/layout is a
      plain, fixed path convention `agent-worktrees` itself knows and can
      read directly (a filesystem read, never a call into `agent-logger`)
      -- so `agent-worktrees`' own unscoped-merge discovery (Phase 3) also
      looks there as a fallback source. This works whether or not
      `agent-logger` is installed: if it never ran, that location is simply
      empty, and nothing is lost that wasn't already gone.
- [ ] New capability in `agent-logger`: when invoked through the Phase 6
      composition seam, archive the worktree's own accumulated per-worktree
      state (its activity-log file, its `handoff_trace` file, any other
      per-worktree sidecar) -- reusing the already-proven
      `sessions.archive_session` / `verify_archive` / reclaim pattern from
      `agent_logger.sync.compact` -- into the fixed path convention above,
      **keyed `<project>/<repo>/<worktree-id>`, not `<repo>/<worktree-id>`**
      -- a worktree id is only unique *within* one project (the same
      rationale as `handoff_trace.py`'s own existing namespacing), and
      multiple projects can legitimately target the same repo, so omitting
      the project segment risks two unrelated worktrees' archives
      colliding/overwriting each other at the same path. Add a collision
      test: two different projects, each with a worktree of the same id
      against the same repo, archived without interference.
- [ ] Verify-before-reclaim, exactly like session compaction: never delete
      the live per-worktree state until the archive is confirmed intact.
      A failed/absent archive step never blocks or reverses
      `agent-worktrees`' own cleanup decision (fail-open).
- [ ] **A standalone install (no `agent-logger`) must not regress into
      unbounded disk growth.** Today's global log has a 7-day rolling
      retention regardless of any plugin; if archival is the *only* path
      that ever reclaims a cleaned-up worktree's per-worktree journal, an
      install without `agent-logger` would keep every such journal forever
      once Phase 5 removes the bounded global file -- a real regression,
      not merely a missed optimization. `agent-worktrees` itself (the
      lower tier, always present) owns a baseline, bounded-retention
      fallback independent of the optional archiver: when its own
      cleanup/finalize path finds no archival callback registered (or the
      callback fails), it still applies a simple local bound on a cleaned-up
      worktree's own journal (e.g. the same age-based rolling window the
      global log used, truncating/pruning rather than deleting outright so
      the "recent history survives a while" promise holds) -- never
      "keep forever" as the silent default. Archival (when present) is
      strictly additive longevity on top of this floor, never the sole
      reclaim mechanism. Add a cleanup test with `agent-logger` absent that
      confirms the journal is still eventually bounded.

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
- [ ] Unit tests for the generalized per-worktree journal writer/reader,
      concurrent-writer-safety (mirroring `handoff_trace.py`'s own existing
      coverage) (Phase 3).
- [ ] A test for the unscoped `agent-worktrees activity` merge-discovery
      behavior: seed per-worktree journals across 2+ projects, confirm an
      unfiltered call returns every entry, globally time-ordered, matching
      the pre-migration output shape (Phase 3).
- [ ] A migration test: seed a synthetic global log with mixed-worktree
      entries, run the migration, assert each per-worktree file receives
      exactly its own entries, idempotent on a second run (Phase 4).
- [ ] A migration-crash/dual-write test: interrupt a migration mid-run and
      rerun it (assert no duplicated entries in the destination); also seed
      the destination with events a Phase-3 dual-write already delivered
      before migration runs, and confirm migration recognizes and skips
      them rather than duplicating (Phase 4).
- [ ] A repeated-identical-event test: seed the source with two or more
      genuinely distinct events that happen to share identical content
      (same second-resolution timestamp, same fields), confirm migration
      preserves the full count in the destination rather than collapsing
      to one via set-style dedup (Phase 4).
- [ ] A concurrent-tail-write test: start a migration pass, append a new
      event to the source after its read reaches EOF but before the stamp,
      confirm a subsequent migration run still picks up and migrates that
      straggler rather than treating the earlier stamp as final (Phase 4).
- [ ] A temporal-id-reuse test: seed a historical event for a worktree id
      that belonged to project A (now reaped) at one time period, with that
      same id now uniquely resolving to an unrelated project B at the
      current time; confirm migration does NOT attribute the old event to
      B (fails the era check) and routes it to the unmigrated
      sidecar/report instead (Phase 4).
- [ ] A migration-ambiguity test: seed the synthetic global log with a
      worktree id that exists in two different projects' tracking
      directories (and one that exists in none), confirm both are routed to
      the unmigrated sidecar/report rather than guessed into either
      project's file (Phase 4).
- [ ] Live validation on this machine: after deploying Phases 1-3, confirm
      via `py-spy` (or sustained CPU sampling) that a resident `status-monitor`
      no longer shows `read_events`/`handoff_trace.read_trace` in its hot
      sweep path, and CPU stays near-idle between sweeps.
- [ ] A composition-seam test: confirm `agent-worktrees` cleanup completes
      normally and identically whether or not `agent-logger` is installed
      (fail-open), and that the archival callback firing/failing never
      changes `agent-worktrees`' own tombstone decision (Phase 6).
- [ ] An archive-key-collision test: two different projects each with a
      worktree of the same id targeting the same repo; confirm their
      archives land at distinct, non-interfering paths (Phase 6).
- [ ] A standalone-install retention test: clean up a worktree with
      `agent-logger` absent (or its callback failing), confirm
      `agent-worktrees`' own bounded-retention fallback still eventually
      prunes/bounds that worktree's journal rather than growing it forever
      (Phase 6).
- [ ] An archived-journal discovery test: clean up (archive + reclaim) a
      worktree with `agent-logger` installed, then confirm the unscoped
      `agent-worktrees activity` view still includes its recent history by
      reading the fixed archive location directly -- with no `agent-logger`
      call involved (Phase 6).
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
