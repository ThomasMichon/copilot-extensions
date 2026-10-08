# Per-worktree activity state (retire the global activity-log hot path)

- **Slug:** `per-worktree-activity-state`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase PRs, this effort file updated in the same worktree as whichever phase is active
- **Created:** 2026-10-07
- **Status:** Active
- **Vision:** zero-downtime / resident-daemon efficiency (status-monitor should never cost more than a bounded sweep); [resident daemon as the authoritative live-state database](../../../visions/plugins/agent-worktrees/README.md#the-resident-daemon-as-the-authoritative-live-state-database)
- **Umbrella issue:** ThomasMichon/copilot-extensions#5664

## Guiding Intent

The resident `status-monitor` daemon must never re-derive correctness-relevant
state by scanning an unbounded, shared journal. Per-worktree state belongs in
a transactionally-updated slot on that worktree's own record; a journal
(global or per-worktree) exists only for human/on-demand diagnostics ("what
just happened", a `doctor`-style flow), never as a source of truth an
automatic sweep depends on.

**Alignment with the single-write-authority vision.** The new handoff-lifecycle
slot fields (Phase 1) are not a new, independent direct-YAML-write path: they
are written through the **same `tracking_write` verb-dispatch mechanism**
already used for every other migrated mutation (e.g. `session_register` in
`tracking_session_registration_write.py`) -- dispatched to the resident
daemon when reachable, falling back to the identical in-process
implementation (never a second, drifting copy of the write logic) when it
isn't, exactly as the vision's "resident daemon as the authoritative
live-state database" section directs. This effort does not introduce a
second write authority; it adds one more piece of state to the single
authority that already exists.

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
      `retire_last_method`, **`retire_first_attempt_at`**,
      **`retire_disqualified_by_nonterminal`** (bool).
- [ ] **Also durably record pending-request metadata, not just spawn/retire
      outcomes.** `_monitor_pending_handoff_request`
      (`status_monitor_runtime.py:931-990`) needs more than spawn/retire
      state to do its job: for each live token it reads the originating
      `handoff_requested` event and pulls `session_id` (the predecessor
      session, falling back to `handoff.predecessor` when absent),
      `session_state` (a custom session-state path override, falling back
      to the default per-session-id path when absent), and
      `predecessor_pid` (used to confirm the predecessor process is still
      the same one via `locks.process_start_time`). None of that is
      captured by the spawn/retire fields above -- removing the journal
      read without adding somewhere durable for it would silently discard
      request routing/identity data, including a custom session-state
      path. Extend `SessionHandoff` with `request_predecessor_session_id`,
      `request_session_state_path` (nullable -- absence still means "use
      the default path", computed the same way at read time), and
      `request_predecessor_pid`, written once at request time (the same
      commit-ordering discipline as the crash-safe ordering item below:
      slot first, then the diagnostic log mirror). Cover these three
      fields explicitly in the Phase 2 test for this call site, including
      the custom-session-state-path and absent-pid cases.
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
- [ ] **Abandon-after-N-unrecoverable-failures: an explicit, justified
      behavior change, not a false "exact preservation" claim.** An
      earlier version of this plan claimed the new slot fields would
      preserve today's semantics "exactly" -- that claim was wrong, caught
      twice by review on this same piece of logic, and is corrected here
      rather than restated a third time. The **real** current behavior
      (`_pending_handoff_retire_requests`, `__main__.py:2896-2919`) is
      **bounded, not permanent**: it checks `all(...)` only across
      whichever retire events are still *visible* right now, and that
      visible set is itself bounded two ways --
      `activity.read_events(..., limit=64)` keeps only the newest 64
      global-log entries per (worktree, event-type), and
      `handoff_trace.append_event` for `handoff_predecessor_retire` is
      gated to `outcome == "gone"` only (`activity.py`'s
      `_HANDOFF_STAGE_GATE`), so a *failed* attempt reaches the durable
      per-worktree trace store not at all and ages out of the global
      log's own 64-event window over time. A sufficiently old
      non-terminal attempt can therefore silently stop counting today --
      the existing "disqualification" is bounded-window, self-healing,
      not permanent.

      A bounded field set cannot faithfully reproduce an *arbitrary
      journal-scan window size* (`limit=64` is an implementation detail of
      the old replay approach, not a deliberately chosen business rule) --
      doing so would require tracking a ring buffer of recent outcomes,
      reintroducing the journal-replay complexity this effort exists to
      remove. Instead, **explicitly adopt permanent, non-expiring
      disqualification as the new behavior**: once any non-terminal-class
      attempt is ever recorded for a token, abandonment never fires for it
      again, regardless of how long ago that attempt was or how many
      terminal-class attempts have happened since. This is a deliberate
      simplification, justified as strictly *safer* than the old bounded
      window (a token that has ever shown instability is never later
      silently treated as if it hadn't) -- not a bug-compatible port.
      `retire_first_attempt_at` is set once, on the first retire attempt
      ever recorded for the token, and never changes again.
      `retire_disqualified_by_nonterminal` starts `False` and is set `True`
      permanently the first time any non-terminal-class attempt occurs --
      never cleared. Abandonment then checks `not
      retire_disqualified_by_nonterminal and now - retire_first_attempt_at
      >= _RETIRE_ABANDON_GRACE_S` directly off the slot -- no journal
      replay, and an intentional, documented behavior change from today's
      bounded-window version, not a preservation of it.
- [ ] **One-time backfill for already-existing handoffs, before Phase 2
      cuts the readers over.** A handoff that was spawned/retired *before*
      this upgrade has that state recorded only in the journal -- its
      `SessionHandoff` entry's new slot fields all start unset. If Phase 2
      starts trusting those fields immediately, a pre-upgrade spawned
      handoff would look unattempted (risking a duplicate successor spawn)
      and a pre-upgrade retired one would look still-pending (risking a
      retry against an already-retired predecessor) -- a real double-action
      hazard right at the cutover moment, not a cosmetic gap. As part of
      this same phase (not deferred to Phase 2), backfill every existing
      `SessionHandoff`'s new fields from its current journal-derived state
      -- a one-time read per handoff at upgrade time (reusing the same
      merge-the-journal-sources logic the current readers already use),
      never a recurring per-sweep scan. Add tests covering a pre-upgrade
      spawned-but-not-retired handoff and a pre-upgrade spawned-and-retired
      one, confirming each backfills to the correct slot state rather than
      appearing unattempted after the cutover.
- [ ] **The backfill can race live mutations -- `_RecordLock` alone does
      not make it race-safe, because legacy journal writers don't hold
      it.** A one-time read-then-later-write backfill can lose a race
      against an in-flight handoff: a legacy (journal-only) writer that
      appends newer state *after* the backfill read but *before* it saves
      would have that newer fact silently overwritten by the backfill's
      now-stale computed value. Serializing the backfill's own
      read-and-write under `_RecordLock` is **not sufficient** to close
      this: today's legacy writers append `activity.jsonl` with no lock
      at all (`activity.py:286-350`) and append `handoff_trace.py`'s
      per-project/per-worktree trace under its own separate lock
      (`handoff_trace.py:138-144`) -- neither is serialized against
      `_RecordLock`, so a legacy append can land in the gap between the
      backfill's read and its save even while `_RecordLock` is held
      throughout the backfill's own critical section.

      The real fix needs a synchronization protocol **shared by the
      backfill and every legacy writer it races against**, not a lock
      only the backfill takes: during the one-time backfill pass, acquire
      every lock a legacy writer for that token could take -- `handoff_trace`'s
      per-project/per-worktree lock first, then `_RecordLock` -- in a
      fixed, documented order (to avoid introducing a new deadlock
      between the backfill and a legacy writer that might acquire them in
      the opposite order), perform the read-current-journal-state-and-write
      as one atomic operation while holding both, then release in reverse
      order. **This alone is still not sufficient for `activity.jsonl`'s
      append** (`log_event()`, unlocked today): holding only the trace
      lock and `_RecordLock` does not fence an in-flight `log_event()`
      call that already began before the backfill started -- it can
      append to the unlocked global file after the backfill's read, then
      separately block on the trace lock, and append the mirrored trace
      entry only *after* the backfill has already saved its now-stale
      slot. Closing this requires one of: (a) give `activity.jsonl`'s
      append its own lock too, and add it to the same fixed acquisition
      order the backfill takes (global-log lock, then trace lock, then
      `_RecordLock`) -- every writer, legacy or backfill, takes the same
      ordered set; or (b) an explicit producer fence/drain: before taking
      its snapshot, the backfill signals every legacy writer path for
      that token to pause (or waits out any already-in-flight `log_event`
      call via a generation/sequence check), takes the snapshot only once
      no writer is mid-append, then resumes writers. Pick one and specify
      it concretely -- "serialize under a lock" is not enough when one of
      the two sinks being raced has no lock participating in the first
      place. Define an explicit cutover boundary on top of that: Phase 2's
      slot-trusting readers must not go live for a given handoff until
      *after* its backfill has completed under the chosen protocol, so no
      legacy producer can still mutate journal-only truth inside the
      window where slots are already being trusted. Add a
      concurrent-write test: a legacy writer's `log_event()` call begins
      before the backfill's snapshot and completes its `activity.jsonl`
      append *after* that snapshot but *before* its trace-store append
      (the specific gap the chosen protocol must close); confirm the
      backfilled slot reflects the newer state, never the stale pre-write
      snapshot, and that no deadlock occurs under contention.

### Phase 2 — Rewire hot-path consumers onto slots
- [ ] `__main__._pending_handoff_retire_requests` -- read `record.handoffs`
      directly; stop calling `activity.read_events`/`handoff_trace.read_trace`

### Phase 2 — Rewire hot-path consumers onto slots
- [ ] `__main__._pending_handoff_retire_requests` -- read `record.handoffs`
      directly; stop calling `activity.read_events`/`handoff_trace.read_trace`
      for decision-making.
- [ ] `status_monitor_runtime._monitor_pending_handoff_request` -- read
      `record.handoffs`'s spawn/retire fields **and** the new
      `request_predecessor_session_id` / `request_session_state_path` /
      `request_predecessor_pid` fields directly; stop calling
      `activity.read_events` for `handoff_requested` lookups.
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

Full design, project-routing requirements, and the worktree-less-events
audit/decision: [`phase-3-journal-generalization.md`](phase-3-journal-generalization.md).

- [ ] Extend `handoff_trace.py`'s proven per-project/per-worktree
      JSONL-with-lock pattern to cover every `activity.log_event()` kind.
- [ ] `activity.log_event()` writes to the per-worktree file; every
      remaining on-demand reader reads it directly.
- [ ] Define the unfiltered `agent-worktrees activity` merge-discovery
      contract -- covering **every** retained sink (per-worktree journals,
      the Phase 6 archive, the unresolved-live-event holding location, any
      worktree-less machine-scoped sink, and Phase 4's unmigrated
      sidecar), not just per-worktree files.
- [ ] Define authoritative project routing for every live writer (never
      guess via ambient fallback alone).
- [ ] Audit every `activity.log_event()` call site for worktree-less
      callers (confirmed floor: `boot_trace`, `launcher_shell_reaped`,
      two `handoff_retire_guard` sites) and make an explicit
      preserve/retire/reroute decision for each.

### Phase 4 — One-time migration

Full design (stable-identity dedup, generation-safe resumable cutover,
era-matched provenance, ambiguity handling): [`phase-4-migration.md`](phase-4-migration.md).

- [ ] A migration pass copies the existing global `activity.jsonl`'s
      history into the correct per-worktree files, safely: exact-identity
      reconciliation (not content counting alone) across retries and
      dual-writes, generation-safe resumability across the log's own
      retention-prune file replacement, no misattributing a reused
      worktree id's history across projects/eras, and no guessing at an
      ambiguous or orphaned record.
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
      Phase 3's unscoped-merge decision), plus the worktree-less-events
      audit's own documentation implications (see
      `phase-3-journal-generalization.md`).

### Phase 6 — Worktree-state archival (agent-logger)

Full design (fail-open composition seam, archive key/collision avoidance,
archived-journal discovery, standalone-install retention floor):
[`phase-6-archival.md`](phase-6-archival.md).

- [ ] A fail-open, lower-tier-owned composition seam (never a direct
      `agent-worktrees` -> `agent-logger` call, and never an indefinite
      block on a hung callback -- a bounded timeout, same pattern as
      `claim_providers.py`'s existing provider-process calls) that
      archives a cleaned-up worktree's accumulated per-worktree state,
      verify-before-reclaim, correctly namespaced by project and
      incarnation (`creation_nonce`), not just repo -- a worktree id can be
      reused across projects *and* reap-and-recreated within the same one.
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
- [ ] A permanent-disqualification test (documents the **new, intentional**
      behavior -- not a preservation of the old bounded-window one): a
      single non-terminal attempt, anywhere in a token's history (even
      followed by many subsequent terminal-class failures, and even if it
      would have aged out of today's `limit=64` window), must permanently
      prevent abandonment -- confirm `retire_disqualified_by_nonterminal`
      is set `True` on the first non-terminal attempt and never clears,
      and that abandonment never fires for that token regardless of how
      much later terminal-only activity occurs. A separate token with
      every attempt terminal-class from the start must still abandon at
      exactly `_RETIRE_ABANDON_GRACE_S` past `retire_first_attempt_at`
      (Phase 1).
- [ ] A pre-upgrade backfill test: a `SessionHandoff` whose spawn/retire
      history exists only in the journal (simulating a pre-upgrade
      handoff) must backfill to the correct slot state -- one case spawned
      but not yet retired, one case spawned and retired -- confirm neither
      appears unattempted after the cutover (no duplicate spawn, no
      retry-after-already-retired) (Phase 1).
- [ ] A backfill-race test: a legacy writer appends new journal-only state
      for a token concurrently with that token's backfill; confirm the
      backfilled slot reflects the newer state (the lock-merged result),
      never a stale snapshot from before the concurrent write (Phase 1).
- [ ] Unit tests proving the 6 rewired hot-path functions never call
      `activity.read_events`/`handoff_trace.read_trace` (Phase 2) --
      e.g. a monkeypatch that raises if either is called during a sweep or
      during session registration.
- [ ] Phase 3's validation: see [`phase-3-journal-generalization.md`](phase-3-journal-generalization.md)
      (journal writer/reader concurrency, unscoped merge-discovery,
      project-routing, worktree-less-events audit).
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

### 2026-10-08 — Plan review (PR #5669), 9 rounds
- Drove the plan-only PR through 9 review rounds, resolving a long run of
  genuine migration/archival/state-model correctness gaps: a crash-safe
  slot/log commit ordering and a non-terminal-disqualification
  abandonment slot design verified against the real code (Phase 1 --
  later corrected again in round 14, see below: the new permanent
  semantics are a deliberate, safer behavior *change* from today's
  bounded-window reality, not an exact preservation of it), two
  additional automatic journal
  readers (Phase 2), an unscoped-`activity`-view contract, live-write
  project-routing, and a full worktree-less-events audit beyond just
  `boot_trace` (Phase 3), a UUID-based stable per-event identity (not a
  PID-reuse-vulnerable pair, not content counting alone), generation-safe
  resumability against the log's own retention-prune file replacement,
  temporal id-reuse/era-matched provenance, and project ambiguity (Phase 4),
  and a fail-open, bounded-timeout composition seam plus a
  standalone-install retention floor for archival (Phase 6).
- Reconciled Phase 1 with the repo's own "resident daemon as the
  authoritative live-state database" vision direction: the new slot
  fields are written through the same `tracking_write` verb-dispatch
  mechanism already used elsewhere, not a new, independent direct-YAML
  write path.
- Restructured the README per a Low finding and this repo's own
  decompose-liberally convention: extracted Phases 3/4/6's detailed
  design and validation into linked sibling docs
  (`phase-3-journal-generalization.md`, `phase-4-migration.md`,
  `phase-6-archival.md`), keeping the shared coordination README a
  concise, navigable map.

### 2026-10-08 — Plan review (PR #5669), rounds 10-15
- Fixed an archive-key collision across same-project worktree-id reuse by
  adding a `creation_nonce` incarnation identifier (Phase 6), bounded the
  retention of the new unresolved-live-event holding location (Phase 3),
  and broadened the unscoped `agent-worktrees activity` merge-discovery
  contract to cover every retained sink, not just per-worktree files.
- Added a one-time pre-upgrade `SessionHandoff` backfill requirement so
  Phase 2's cutover doesn't make pre-upgrade handoffs look unattempted,
  then (round 13) fixed that backfill's own race condition by requiring
  an atomic read-and-write, and (round 15, this round) corrected it
  again: holding `_RecordLock` alone is insufficient because today's
  legacy journal writers (`activity.jsonl` appends, the separate
  `handoff_trace` lock) aren't serialized against it at all -- the plan
  now specifies a fixed lock order across every writer the backfill
  races, plus an explicit cutover boundary.
- Added a recurring, independently bounded GC pass for Phase 6's
  retention floor (round 12), then bounded that GC pass itself (round
  13) so it can't grow unbounded with total cleanup history -- the exact
  problem this effort exists to fix, just relocated.
- **Round 14/15 -- corrected a false "exact preservation" claim.**
  Verified directly against `activity.py` that today's real
  abandon-after-N-unrecoverable-failures behavior is a **bounded,
  self-healing trailing window** (`handoff_trace` only records a retire
  outcome when it's `"gone"`; the global-log fallback read is capped at
  the newest 64 events) -- not permanent disqualification. The plan
  previously claimed its new always-sticky slot fields were an exact,
  behavior-preserving model of that; they aren't, and can't be without
  reintroducing a bounded ring-buffer replay. Corrected the plan (and
  this Journal, which round 15's review caught still calling the new
  semantics "EXACT") to explicitly adopt permanent, non-expiring
  disqualification as a deliberate, safer behavior *change* instead of a
  preservation claim, and updated the matching Validation Plan item to
  test the actual new behavior.

