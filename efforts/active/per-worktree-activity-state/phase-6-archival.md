# Phase 6 — Worktree-state archival (agent-logger)

Extracted from the main effort README (`../per-worktree-activity-state/README.md`)
per repository convention: detailed phase design lives in a linked sibling
document, keeping the shared coordination README navigable. Link back there
for context, participants, and the overall plan.

## Design

### Composition seam, not a direct call

`agent-logger` is an optional, higher-tier plugin; `agent-worktrees` (the
ground-layer owner of worktree lifecycle/identity) must not call upward
into it directly, and its absence must never block or slow cleanup
(`docs/patterns/a-la-carte-independence.md`).

- [ ] `agent-worktrees` defines and owns a lower-tier, fail-open drop-in
      callback point in its own cleanup/finalize path (e.g. a well-known,
      optionally-present hook directory or entry-point convention already
      used elsewhere in this repo for a-la-carte composition).
- [ ] `agent-logger`, if installed, registers into that seam to archive the
      diagnostic bundle.
- [ ] The authoritative "this worktree is gone" tombstone/identity decision
      stays entirely inside `agent-worktrees` regardless of whether the
      archival seam is present, fires, or fails.
- [ ] **A failed callback is not the only non-blocking case -- a hung one
      must not be either.** Fail-open handling of an error return doesn't
      cover a provider process that never returns at all; a synchronous,
      unbounded wait on a hung archiver would still block cleanup
      indefinitely, contradicting the fail-open intent above. Invoke the
      callback the same way `claim_providers.py`'s existing
      `_run_provider_process`/`_run_legacy_command` already do for this
      exact composition-seam shape: a subprocess call with an explicit,
      finite `timeout`; on `subprocess.TimeoutExpired` (or any other
      failure), treat it identically to an ordinary failed/absent callback
      -- fail-open, never block. Add a hung-callback test (a stub provider
      that sleeps past the timeout) alongside the ordinary firing/failing
      cases.

### Archived-journal discovery stays lower-tier-owned

Phase 3 (`phase-3-journal-generalization.md`) promises the unscoped
`activity` view keeps matching today's full retained history, but archiving
(and reclaiming the live copy of) a cleaned-up worktree's journal would
otherwise make it vanish from that view immediately -- a real regression
against today's 7-day rolling retention, which still shows a just-cleaned-up
worktree's recent history.

- [ ] Fix: the archive's on-disk location/layout is a plain, fixed path
      convention `agent-worktrees` itself knows and can read directly (a
      filesystem read, never a call into `agent-logger`) -- so
      `agent-worktrees`' own unscoped-merge discovery (Phase 3) also looks
      there as a fallback source.
- [ ] This works whether or not `agent-logger` is installed: if it never
      ran, that location is simply empty, and nothing is lost that wasn't
      already gone.

### Archive capability and key

- [ ] New capability in `agent-logger`: when invoked through the composition
      seam above, archive the worktree's own accumulated per-worktree state
      (its activity-log file, its `handoff_trace` file, any other
      per-worktree sidecar) -- reusing the already-proven
      `sessions.archive_session` / `verify_archive` / reclaim pattern from
      `agent_logger.sync.compact` -- into the fixed path convention above.
- [ ] **Key the archive `<project>/<repo>/<worktree-id>-<incarnation>`, not
      `<repo>/<worktree-id>` or even `<project>/<repo>/<worktree-id>` alone.**
      A worktree id is only unique *within* one project (the same rationale
      as `handoff_trace.py`'s own existing namespacing), and multiple
      projects can legitimately target the same repo, so omitting the
      project segment risks two unrelated worktrees' archives
      colliding/overwriting each other at the same path. Project+repo
      alone is still not enough, though: this repository explicitly
      supports reap-and-recreate of a worktree id *within the same
      project*, distinguished by a fresh `WorktreeRecord.creation_nonce`
      (`tracking.py`) -- the exact same temporal-incarnation-reuse hazard
      Phase 4's migration already guards against for historical events.
      Include that incarnation identifier (the existing `creation_nonce`,
      or equivalent) in the archive key so a later incarnation's archive
      can never silently overwrite an earlier one's at the same nominal
      worktree id.
- [ ] Add a collision test: two different projects, each with a worktree of
      the same id against the same repo, archived without interference.
- [ ] Add an incarnation-reuse collision test: the *same* project reaps a
      worktree, then later recreates a different worktree reusing the same
      id (a new `creation_nonce`); confirm both incarnations' archives
      coexist without one overwriting the other.

### Verify-before-reclaim

- [ ] Never delete the live per-worktree state until the archive is
      confirmed intact, exactly like session compaction.
- [ ] A failed/absent archive step never blocks or reverses
      `agent-worktrees`' own cleanup decision (fail-open).

### Standalone-install retention floor

Today's global log has a 7-day rolling retention regardless of any plugin;
if archival is the *only* path that ever reclaims a cleaned-up worktree's
per-worktree journal, an install without `agent-logger` would keep every
such journal forever once Phase 5 removes the bounded global file -- a real
regression, not merely a missed optimization.

- [ ] `agent-worktrees` itself (the lower tier, always present) owns a
      baseline, bounded-retention fallback independent of the optional
      archiver: when its own cleanup/finalize path finds no archival
      callback registered (or the callback fails), it still applies a
      simple local bound on a cleaned-up worktree's own journal (e.g. the
      same age-based rolling window the global log used, truncating/
      pruning rather than deleting outright so the "recent history
      survives a while" promise holds) -- never "keep forever" as the
      silent default.
- [ ] **A one-time prune at cleanup time is not rolling retention.** An
      entry still younger than the cutoff at the moment of cleanup
      correctly survives that pass -- but once the worktree is gone,
      nothing revisits that file again later when those entries finally
      age past the window, so it would sit there forever past its own
      retention deadline. Add a **recurring** GC pass independent of any
      single worktree's cleanup event -- e.g. piggybacking on the resident
      status-monitor's own already-continuous periodic sweep (it already
      exists and already does light housekeeping), or an equivalent
      scheduled task -- that revisits every cleaned-up-but-still-retained
      journal and reaps/deletes it once it has fully aged out, not merely
      pruning it down.
- [ ] Archival (when present) is strictly additive longevity on top of this
      floor, never the sole reclaim mechanism.
- [ ] Add a cleanup test with `agent-logger` absent that confirms the
      journal is still eventually bounded, AND a separate recurring-GC test
      that advances time past the retention window after cleanup and
      confirms the journal is then actually reaped by the recurring pass,
      not left in place forever.

## Validation (phase-specific)

- [ ] A composition-seam test: confirm `agent-worktrees` cleanup completes
      normally and identically whether or not `agent-logger` is installed
      (fail-open), and that the archival callback firing/failing never
      changes `agent-worktrees`' own tombstone decision.
- [ ] A hung-callback test: a stub archival provider that sleeps past the
      configured timeout; confirm cleanup still completes promptly
      (fail-open on timeout, not an indefinite block) exactly like an
      ordinary failed/absent callback.
- [ ] An archive-key-collision test: two different projects each with a
      worktree of the same id targeting the same repo; confirm their
      archives land at distinct, non-interfering paths.
- [ ] A standalone-install retention test: clean up a worktree with
      `agent-logger` absent (or its callback failing), confirm
      `agent-worktrees`' own bounded-retention fallback still eventually
      prunes/bounds that worktree's journal rather than growing it forever.
- [ ] A recurring-GC test: clean up a worktree whose journal entries are
      still within the retention window at cleanup time (so the one-time
      prune leaves it intact); advance time past the window; confirm the
      recurring GC pass (not the one-time cleanup prune) reaps/deletes it
      rather than leaving it indefinitely.
- [ ] An archived-journal discovery test: clean up (archive + reclaim) a
      worktree with `agent-logger` installed, then confirm the unscoped
      `agent-worktrees activity` view still includes its recent history by
      reading the fixed archive location directly -- with no `agent-logger`
      call involved.
- [ ] Archival round-trip test: create a worktree, accumulate some
      per-worktree state, clean it up, confirm the archive exists, verifies,
      and the live per-worktree state is gone.

Back to the main plan: [`README.md`](README.md).
