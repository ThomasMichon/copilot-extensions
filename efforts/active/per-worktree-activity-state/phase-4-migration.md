# Phase 4 — One-time migration

Extracted from the main effort README (`../per-worktree-activity-state/README.md`)
per repository convention: detailed phase design lives in a linked sibling
document, keeping the shared coordination README navigable. Link back there
for context, participants, and the overall plan.

## Design

A best-effort migration pass reads the existing global `activity.jsonl`
once and copies each record's history into the correct per-worktree file it
describes (established by Phase 3, `phase-3-journal-generalization.md`).

### Multiplicity-preserving deduplication, not set-membership

A content hash alone is not a stable per-record identity: `log_event()`
timestamps only to whole seconds, so two *legitimate, independently
occurring* events can share identical fields; naive set-style dedup (hash
already seen -> skip) would silently collapse real, distinct history.

- [ ] Match and de-duplicate by **multiset** comparison instead: for a given
      identity (hash of content, or an existing unique field if one
      qualifies), count how many copies already exist in the destination
      and how many appear in the source segment being migrated, and append
      only the excess -- never collapse to a single copy.
- [ ] Migration tests must include a case with genuinely repeated,
      identical-content events.

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
- [ ] Validate a writer appending concurrently with (and after) a migration
      pass's own read.

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
      silently dropped or silently misfiled.

### Wiring

- [ ] Wire it into `agent-worktrees update`/install so every existing
      machine picks it up, automatically, safe to re-run given the
      multiplicity-preserving and resumable-offset requirements above.

## Validation (phase-specific)

- [ ] A migration test: seed a synthetic global log with mixed-worktree
      entries, run the migration, assert each per-worktree file receives
      exactly its own entries, idempotent on a second run.
- [ ] A migration-crash/dual-write test: interrupt a migration mid-run and
      rerun it (assert no duplicated entries in the destination); also seed
      the destination with events a Phase-3 dual-write already delivered
      before migration runs, and confirm migration recognizes and skips
      them rather than duplicating.
- [ ] A repeated-identical-event test: seed the source with two or more
      genuinely distinct events that happen to share identical content
      (same second-resolution timestamp, same fields), confirm migration
      preserves the full count in the destination rather than collapsing
      to one via set-style dedup.
- [ ] A concurrent-tail-write test: start a migration pass, append a new
      event to the source after its read reaches EOF but before the stamp,
      confirm a subsequent migration run still picks up and migrates that
      straggler rather than treating the earlier stamp as final.
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
