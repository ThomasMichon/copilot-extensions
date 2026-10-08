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

- [ ] An unscoped call discovers and merges every project's per-worktree
      journal files -- including the archived location Phase 6 introduces
      (a pure filesystem read of a known path convention, not a call into
      `agent-logger`; see `phase-6-archival.md`) -- globally time-ordered,
      matching today's output shape (same fields, same sort) -- preserving
      existing UX/back-compat. This remains an on-demand diagnostic read
      only (the `activity` CLI verb, never a hot path), so the extra
      discovery I/O is acceptable there.

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
- [ ] Test: two different projects each with a worktree of the same id;
      confirm a live write from each lands only in its own project's file,
      never cross-contaminating the other.
- [ ] Test: a "neutral" daemon-level caller (one with no naturally
      resolvable project context) -- confirm its event is held unresolved
      rather than misfiled.

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
    exclusively for genuinely pre-resolution events (`boot_trace` and any
    future event in the same class), distinct from (and much smaller than)
    the retired general-purpose global log -- same age-based rolling
    retention discipline, just scoped to this one narrow event class; or
  - **(b) Deliberately retire `boot_trace` telemetry entirely**, removing
    its writers and their tests in the same change, if it's judged no
    longer worth the machine-scoped exception.
- [ ] Whichever is chosen, update `docs/patterns/lifecycle-activity-logging.md`
  and `plugins/agent-worktrees/docs/cli-reference.md` to describe the new
  destination (or the explicit retirement) rather than leaving them
  describing a sink that no longer exists.
- [ ] Add a test proving the chosen behavior: either boot-trace events are
  still captured and readable post-migration (option a), or that their
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
- [ ] The project-routing and `boot_trace` tests named inline above.

Back to the main plan: [`README.md`](README.md).
