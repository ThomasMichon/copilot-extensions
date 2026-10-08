# Phase 4 — One-time migration

Extracted from the main effort README (`../per-worktree-activity-state/README.md`)
per repository convention: detailed phase design lives in a linked sibling
document, keeping the shared coordination README navigable. Link back there
for context, participants, and the overall plan.

## Design

A best-effort migration pass reads the existing global `activity.jsonl`
once and copies each record's history into the correct per-worktree file it
describes (established by Phase 3, `phase-3-journal-generalization.md`).

### Stable event identity, not content-based counting

Even multiset-by-content counting is insufficient: it cannot distinguish a
genuinely new event from Phase 3's dual-write path (one whose per-worktree
write succeeded but whose *corresponding* global write then failed, or vice
versa) from an unrelated, independently-occurring event that happens to
share identical content. A destination-only event from a partial dual-write
failure can silently "consume" the count that should have matched an older,
genuinely distinct global-only event with the same content -- causing
migration to skip real history rather than duplicate it.

- [ ] Give every event a **stable, explicit identity assigned at write
      time**, not derived after the fact from content: Phase 3's
      generalized writer (and, for the remaining transition window, the
      legacy global writer too) stamps each event with a **UUID** (e.g.
      `uuid4()`) at the moment it's created -- not a `(writer-pid,
      sequence)` pair, which operating systems can make collide across
      writer lifetimes (PIDs are reused, and each new process restarts its
      own sequence from zero, so two genuinely distinct events from
      different process generations could share an id). Migration then
      matches by this id, not by counting look-alike content -- exact, not
      probabilistic.
- [ ] Crash/retry-safe: if a writer crashes after assigning the id but
      before the write durably lands on one or both sides, the retry must
      reuse the *same* id (not mint a new one), so a later migration pass
      can still recognize and reconcile it correctly.
- [ ] Legacy, pre-Phase-3 history that was never stamped with this id has
      no better option than the content+multiset heuristic above --
      restrict that heuristic explicitly to entries that predate Phase 3's
      id-stamping, and use exact id-matching for everything written during
      or after the transition.
- [ ] Migration tests must include a case with genuinely repeated,
      identical-content events (the pre-id-stamping heuristic path), a
      case with a partial dual-write failure (per-worktree write landed,
      corresponding global write didn't, or vice versa), and a crash-retry
      case that reuses the same stamped id.

### A completed read pass is not itself a safe cutover boundary

A writer (including a Phase 3 dual-writer) can append to the global file
after this pass's read reaches EOF but before a stamp is written; if that
writer's corresponding per-worktree append then fails or the process
crashes mid-dual-write, a hard stamp would permanently prevent that
global-only straggler from ever migrating.

- [ ] Make the migration resumable by position, not one-shot-then-stamp:
      record the exact byte offset actually processed only *after* that
      segment's writes are confirmed durable in the destination, and treat
      a later run as picking up from the last confirmed offset rather than
      a full rescan or a hard "never again" stamp.
- [ ] **A saved offset is only valid against the same file generation.**
      `activity.py`'s own retention worker (`_prune()`) periodically
      rewrites and *replaces* `activity.jsonl` (a new, shorter file at the
      same path) -- a byte offset recorded against the pre-prune file can
      point at unrelated data (or past EOF) in the replacement, silently
      stranding records rather than erroring loudly. Record a
      generation fingerprint alongside the offset (e.g. a hash of the
      file's first N bytes, or its inode/creation time where available);
      if a later run finds the fingerprint no longer matches, treat it as
      a new generation and fall back to a full rescan (safe, given the
      stable-identity dedup above) rather than seeking to the stale offset.
      Alternatively, serialize migration against the prune worker directly
      (same lock) so the two can never interleave -- either approach is
      acceptable, but the plan must pick one rather than leave the race
      unaddressed.
- [ ] Validate a writer appending concurrently with (and after) a migration
      pass's own read, AND a prune/replace cycle racing an in-progress or
      resumed migration.

### A current unique project match does not prove historical provenance

Resolving a worktree id to exactly one *currently known* project does not
establish that the id meant that project *at the time the old event was
logged* -- a project can be reaped/deleted and its worktree id later reused
by a different, unrelated project, in which case "currently unique" would
silently misattribute the deleted project's history into the new one
despite the never-guess intent below.

- [ ] Require positive era-matched provenance: accept a match only when the
      event's own timestamp falls within that specific project+worktree's
      actual tracked lifetime window (its recorded start/completion
      bounds), not merely "resolves to one project today."
- [ ] An id that is unique today but fails the era check is treated the
      same as an ambiguous one -- unmigrated, reported, never guessed.
- [ ] Add a temporal-id-reuse test (same id, two different projects,
      non-overlapping eras) alongside the ambiguity test below.

### Safe handling of project-ambiguous/orphaned records

`activity.log_event()` does not persist its `project` argument, and a
worktree id is only unique *within* one project (two different projects can
legitimately share the same worktree id) -- a migrated-by-id-alone record
can land in the WRONG project's file, corrupting that worktree's history.

- [ ] The migration must resolve each record's project unambiguously before
      writing: look it up against every currently-known project's tracking
      directory (subject to the era check above); migrate only when the
      worktree id resolves to **exactly one** live (or
      archived-but-identifiable) project+worktree for that event's own era.
- [ ] An id that matches zero or multiple projects -- or fails the era
      check against its one current match -- is never guessed: it is
      retained in a clearly-labeled `activity.jsonl.unmigrated` sidecar (or
      equivalent) and reported in the migration's own summary output, never
      silently dropped or silently misfiled. **This sidecar is retained
      history, not a discard pile** -- Phase 3's unscoped `agent-worktrees
      activity` merge-discovery (`phase-3-journal-generalization.md`) must
      read it as one of its retained sources, or this content silently
      vanishes from the "full retained log" view the moment Phase 5 removes
      the global file it currently lives in.

### Wiring

- [ ] Wire it into `agent-worktrees update`/install so every existing
      machine picks it up, automatically, safe to re-run given the
      stable-identity and resumable-offset (with generation-fingerprint)
      requirements above.

## Validation (phase-specific)

- [ ] A migration test: seed a synthetic global log with mixed-worktree
      entries, run the migration, assert each per-worktree file receives
      exactly its own entries, idempotent on a second run.
- [ ] A migration-crash/dual-write test: interrupt a migration mid-run and
      rerun it (assert no duplicated entries in the destination); also seed
      the destination with events a Phase-3 dual-write already delivered
      before migration runs, and confirm migration recognizes and skips
      them rather than duplicating.
- [ ] A partial-dual-write-failure test: a per-worktree write lands but its
      corresponding global write fails (or vice versa), with an unrelated,
      independently-occurring event of identical content also present;
      confirm migration reconciles by stable id and neither skips the
      genuinely distinct unrelated event nor duplicates the partial one.
- [ ] A crash-retry-reuses-id test: a writer crashes after assigning an
      event's stable id but before the write durably lands; confirm the
      retry reuses the same id and migration reconciles correctly rather
      than treating it as two events.
- [ ] A repeated-identical-event test (legacy, pre-id-stamping history
      only): seed the source with two or more genuinely distinct events
      that happen to share identical content (same second-resolution
      timestamp, same fields), confirm migration preserves the full count
      in the destination rather than collapsing to one via set-style dedup.
- [ ] A concurrent-tail-write test: start a migration pass, append a new
      event to the source after its read reaches EOF but before the stamp,
      confirm a subsequent migration run still picks up and migrates that
      straggler rather than treating the earlier stamp as final.
- [ ] A prune-race test: trigger `activity.py`'s retention `_prune()` (file
      replacement) between two migration runs that would otherwise resume
      from a saved offset; confirm the generation-fingerprint mismatch is
      detected and the resumed run falls back to a full rescan rather than
      seeking into unrelated or past-EOF data in the replacement file (or,
      if serializing against the prune worker instead, confirm the two
      never interleave).
- [ ] A temporal-id-reuse test: seed a historical event for a worktree id
      that belonged to project A (now reaped) at one time period, with that
      same id now uniquely resolving to an unrelated project B at the
      current time; confirm migration does NOT attribute the old event to
      B (fails the era check) and routes it to the unmigrated
      sidecar/report instead.
- [ ] A migration-ambiguity test: seed the synthetic global log with a
      worktree id that exists in two different projects' tracking
      directories (and one that exists in none), confirm both are routed to
      the unmigrated sidecar/report rather than guessed into either
      project's file.

Back to the main plan: [`README.md`](README.md).
