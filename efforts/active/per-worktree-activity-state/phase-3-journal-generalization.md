# Phase 3 — Generalize the per-worktree journal

Extracted from the main effort README (`../per-worktree-activity-state/README.md`)
per repository convention: detailed phase design lives in a linked sibling
document, keeping the shared coordination README navigable. Link back there
for context, participants, and the overall plan.

## Design

- [ ] Extend `handoff_trace.py`'s proven per-project/per-worktree
      JSONL-with-lock pattern (or a sibling module reusing its primitives)
      to cover every `activity.log_event()` kind, not just the 13
      handoff-cutover stages.
- [ ] `activity.log_event()` writes to the per-worktree file instead of (or
      in addition to, during transition) the global one.
- [ ] Every remaining on-demand reader (the "Messages" menu action, CLI
      `activity`/`activity-log list` verbs) reads the per-worktree file
      directly -- no more global-file + `worktree_id` filtering.

### Unfiltered `agent-worktrees activity` contract

`activity.py` and `plugins/agent-worktrees/docs/cli-reference.md` both
currently document a deliberately unscoped "full retained log" view (no
`--worktree-id`/`--project` required) -- once there is no single global
file, this needs an explicit, chosen behavior rather than silently breaking.

- [ ] An unscoped call discovers and merges **every retained sink this
      effort introduces**, not just per-worktree journal files -- "full
      retained log" means every record that is still kept somewhere, and
      each of the following is a place genuine, retained history can now
      live:
  - every project's per-worktree journal files;
  - the archived location Phase 6 introduces (a pure filesystem read of a
    known path convention, not a call into `agent-logger`; see
    `phase-6-archival.md`);
  - the unresolved-live-event holding location this same phase introduces
    below (a write that genuinely couldn't resolve its owning project);
  - the machine-scoped sink for worktree-less events, if that's the chosen
    outcome of the audit below (not applicable if those event kinds are
    instead deliberately retired);
  - Phase 4's `activity.jsonl.unmigrated` sidecar (`phase-4-migration.md`)
    -- an ambiguous/orphaned historical record is still retained history,
    and omitting it from the unscoped view would make it silently vanish
    the moment Phase 5 removes the global file it currently lives in.
- [ ] All of the above, globally time-ordered, matching today's output
      shape (same fields, same sort) -- preserving existing UX/back-compat.
      This remains an on-demand diagnostic read only (the `activity` CLI
      verb, never a hot path), so the extra discovery I/O across several
      sinks is acceptable there.
- [ ] Test: seed at least one record in each retained sink above (including
      the unmigrated sidecar and the unresolved-live-event holding
      location), confirm an unscoped `activity` call surfaces all of them,
      not only the per-worktree-journal subset.

### Authoritative project routing for live writes

The same project-ambiguity hazard the migration section (`phase-4-migration.md`)
addresses for *historical* events also applies to **new, live** writes once
this phase generalizes the journal: `activity.log_event()` currently accepts
an optional `project` and falls back to ambient context, while some
resident-monitor emitters (e.g. `_monitor_claim_handoff_cutover`) pass only a
worktree id -- and a worktree id is only unique *within* one project. A live
write that can't actually resolve its owning project correctly risks
misfiling into the wrong project's per-worktree file, the live-write mirror
of the exact hazard the migration's own ambiguity handling guards against.

- [ ] Every writer must propagate or resolve its owning project explicitly
      at the call site -- not rely on ambient fallback alone when the
      caller actually knows (or can cheaply determine) it.
- [ ] A writer that genuinely cannot resolve its project (ambient context
      unset, and the caller has no explicit value) retains the event
      unresolved (a small, clearly-labeled holding location) rather than
      guessing via "the only project with a matching worktree id right
      now" -- the same never-guess discipline as the migration.
- [ ] **This holding location needs a bounded retention policy of its own,
      not an implicit "keep forever."** Unlike a per-worktree journal (one
      file per worktree, naturally small), repeated neutral-context writes
      accumulating in one shared holding location would, left unbounded,
      silently recreate exactly the unbounded-machine-global-diagnostic-sink
      problem this whole effort exists to eliminate. Apply the same
      age-based rolling-retention discipline the global log used (or an
      equivalent bound) directly to this location.
- [ ] Test: two different projects each with a worktree of the same id;
      confirm a live write from each lands only in its own project's file,
      never cross-contaminating the other.
- [ ] Test: a "neutral" daemon-level caller (one with no naturally
      resolvable project context) -- confirm its event is held unresolved
      rather than misfiled.
- [ ] Test: repeated neutral-context writes over time; confirm the holding
      location's retention bound actually prunes/ages out old entries
      rather than growing without limit.

### Worktree-less events -- a full audit, not just `boot_trace`

`boot_trace` is not the only event that can run without a resolvable
worktree id. Confirmed by direct source inspection, at least three
production call sites emit without one:

- `boot_trace` -- written directly by the shell/PowerShell resolver and
  dispatcher (`scripts/invoke-payload-runtime.sh`, `scripts/resolve-runtime.sh`,
  and their Windows equivalents), bypassing `activity.log_event()` entirely,
  **before** a project or worktree is known at all.
- `launcher_shell_reaped` (`reap_cli.py`) -- a machine-scoped reap sweep
  describing a reaped shell process, not any one worktree.
- `handoff_retire_guard` -- at least two call sites
  (`pane_lifecycle.py`, `sessions_pane_retire.py`) currently omit a
  worktree id.

Once the global file is retired, none of these has anywhere to land if
every remaining sink is strictly per-worktree -- silently losing them, or
leaving them unresolved forever, is not an acceptable default.

- [ ] **Audit every `activity.log_event()` call site** (not just the three
      above) for any path that can run without a resolvable worktree id --
      this list is a confirmed floor, not necessarily the ceiling.
- [ ] For each one found, make an **explicit** preserve/retire/reroute
      decision (the same three options as `boot_trace` below), not a
      silent default -- machine-scoped events that are still wanted need a
      home; events judged no longer worth keeping are retired outright,
      writers and tests together, in the same change.
- [ ] Add a test enumerating these call sites (or their equivalent audit
      surface) and asserting each has a recorded decision, so a future new
      worktree-less call site can't silently slip through unresolved.

### Machine-scoped pre-resolution events (`boot_trace`)

`boot_trace` records are **not** like other `activity.log_event()` events:
they are written directly by the shell/PowerShell resolver and dispatcher
(`scripts/invoke-payload-runtime.sh`, `scripts/resolve-runtime.sh`, and their
Windows equivalents) **before** a project or worktree is known at all --
they bypass `activity.log_event()` entirely. Once the global file is
retired, there is no single place left for them, and they cannot be routed
to a per-worktree file because no worktree is resolved yet at the point
they're written.

- [ ] Decide and implement one of:
  - **(a) Keep a small, separate, still-bounded machine-scoped sink**
    exclusively for genuinely pre-resolution/worktree-less events
    (`boot_trace`, `launcher_shell_reaped`, the `handoff_retire_guard`
    sites above, and any future event in the same class), distinct from
    (and much smaller than) the retired general-purpose global log --
    same age-based rolling retention discipline, just scoped to this one
    narrow class; or
  - **(b) Deliberately retire the telemetry entirely**, per-event-kind,
    removing its writers and their tests in the same change, for any of
    the above judged no longer worth the machine-scoped exception.
- [ ] Whichever is chosen (independently, per event kind), update
  `docs/patterns/lifecycle-activity-logging.md`
  and `plugins/agent-worktrees/docs/cli-reference.md` to describe the new
  destination (or the explicit retirement) rather than leaving them
  describing a sink that no longer exists.
- [ ] Add a test proving the chosen behavior per event kind: either it's
  still captured and readable post-migration (option a), or that its
  writers/tests are fully and consistently removed with no dangling
  references (option b).

## Validation (phase-specific)

- [ ] Unit tests for the generalized per-worktree journal writer/reader,
      concurrent-writer-safety (mirroring `handoff_trace.py`'s own existing
      coverage).
- [ ] A test for the unscoped `agent-worktrees activity` merge-discovery
      behavior: seed per-worktree journals across 2+ projects, confirm an
      unfiltered call returns every entry, globally time-ordered, matching
      the pre-migration output shape.
- [ ] The project-routing and worktree-less-events tests named inline above.

Back to the main plan: [`README.md`](README.md).
