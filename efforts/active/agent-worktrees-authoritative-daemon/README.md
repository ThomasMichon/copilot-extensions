# agent-worktrees — Authoritative Write-Through Daemon

- **Slug:** `agent-worktrees-authoritative-daemon`
- **Repo:** copilot-extensions (plugin home; PR-gated `dev`)
- **Branch(es):** independent per-phase worktrees (land each phase's PR before
  starting the next)
- **Created:** 2026-09-26
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
  (new concept *The resident daemon as the authoritative live-state
  database*; Features *daemon-mediated-write-authority* and
  *single-write-path-across-plugins*; Behaviors *no-writer-bypasses-the-
  daemon*, *durable-files-are-persistence-not-a-side-door*,
  *direct-read-is-a-degrade-not-a-peer* — added 2026-09-26)
  — **vision-realizing**: the direction is now asserted; this effort designs,
  then builds, the thing it describes.
- **Related:** `agent-worktrees-external-status-accelerator`
  (`efforts/active/agent-worktrees-external-status-accelerator/README.md`) —
  this effort is the deliberate long-term inversion of that one's "warmth,
  not truth" read-only accelerator into a write-through authority. Reads the
  same resident-daemon precedent (`classify_daemon.py`,
  `worktree_status_daemon.py`, `work_coalescing_singleton`) as its starting
  infrastructure rather than building a third daemon.
  `module-componentization-discipline`
  (`efforts/active/module-componentization-discipline/README.md`) — has
  already split `tracking.py`'s write surface into `tracking_claims.py` /
  `tracking_lifecycle.py` / `tracking_session_registry.py`; Phase 2's
  mutation-verb enumeration targets those modules' current boundaries (see
  Plan's resolved open questions). No fork of that split is planned here.
  `worktree-manager-control-plane`
  (`efforts/active/worktree-manager-control-plane/README.md`), Sub-slice 3
  — relocates only the resident status-monitor's **mux-presentation** half
  to a companion daemon; that effort's own text keeps "accumulating/
  tracking session status" (this effort's exact scope) in agent-worktrees.
  Complementary, not colliding — see Plan's resolved open questions for the
  landing-order note.
- **Umbrella issue:** [#3761](https://github.com/ThomasMichon/copilot-extensions/issues/3761)
- **Sub-issues:** [#3751](https://github.com/ThomasMichon/copilot-extensions/issues/3751)
  (the CPU-pinning bug whose diagnosis surfaced this direction; fixed in
  #3755, unrelated to this effort's own scope but the reason it was found)

## Guiding Intent

Today, worktree-lifetime state (identity, claims, lineage, disposition,
session bindings) is durably owned by agent-worktrees' YAML tracking
records, read and written directly by `tracking.py` from dozens of call
sites across the CLI. A resident daemon exists (the accelerator effort) but
is deliberately non-authoritative: a read-only cache that never writes and
is never trusted over a fresher direct read.

This effort's guiding intent is to invert that: make the resident daemon the
**one live authority** for worktree-lifetime state — an in-memory database
every read *and* every mutation is funneled through — with the YAML record
becoming the daemon's own durable persistence (a warm-restore mechanism),
never a second write surface a CLI command or a sibling plugin could reach
around it. Direct file access remains available only as a degraded,
read-only resilience path for when the daemon is unreachable — never a
coequal write path.

The operator's own framing (2026-09-25 session, captured verbatim in
Context below): the accelerator effort was "just getting the daemon to exist
and get callers using it" — a first pass. The long-term goal named here is
"an authoritative database to manage live state, carefully wrapped by CLI
commands and a daemon," and — because agent-worktrees is not the only
plugin that touches worktree state — an explicit closing of the door on any
sibling plugin mutating tracking YAML directly, which today is prevented
only by convention, not by anything mechanical.

## Participants

Single-agent effort for Phase 1 (design). Later phases may dispatch
component-sized implementation slices; this section will be filled in once
Phase 2+ scope is broken into concrete, assignable pieces.

## Context

### How this was found

Diagnosing copilot-extensions#3751 (the resident status-monitor pinning
~80-85% CPU from an uncached fleet-wide YAML reparse) led to a live-host
survey of every sibling plugin's `agent_worktrees.tracking` usage
(agent-dispatch, agent-bridge, agent-codespaces, agent-containers,
agent-logger, agent-machines). The survey found real cross-plugin imports
(`agent-codespaces/pool.py`, `agent-containers/picker.py`,
`agent-logger/worktree_binding.py`) but **all read-only**
(`load_record_by_id`, `find_worktree_id_by_cwd`, `find_worktree_id_by_session`)
— no unauthorized direct writer exists today. That is good news, not a
false alarm: it means this effort is preventive, not remedial, and there is
no known-broken atomicity incident to reproduce first.

### Full inception exchange

The operator's own words, verbatim, from the session that proposed this
effort:

> Yes, let's start the doc to "invert" the vision. The first vision pass was
> just getting the daemon to exist and get callers using it. Let's update
> the vision to assert that long-term goal is indeed an "authoritative
> database" to manage live state, carefully wrapped by cli commands and a
> daemon.

And, from the request that originally proposed the direction (same
session, prior turn):

> There should be an active effort to overhaul agent-worktrees with an
> "accelerator", where agent-worktrees has an authoritative daemon process
> with other agent-worktrees CLI calls read/write *through*. Said DB then
> persists data via ~/.\<repo\>/worktrees/\*.yaml instead of having to
> refresh from those files as the authority constantly. all agent-worktrees
> commands should write through the centralized daemon, and all other
> agent-\* commands should only edit or report worktree state via
> `agent-worktrees`, not by writing worktree YAML files directly. We may
> need to make adjustments in other `agent-*` plugins to ensure this, as
> some might be writing their own states directly amidst worktree state,
> causing atomicity and lock issues.

### Existing infrastructure this effort builds on

`agent-worktrees-external-status-accelerator` already landed and validated
(2026-09-20/21) the resident-daemon plumbing this effort needs as its
starting point, not something to rebuild:

- A resident, single-per-host daemon (`cmd_status_monitor`) with a
  documented boot-on-demand/subscribe/linger lifecycle.
- `work_coalescing_singleton` (vendored, pure-stdlib, JSON-over-socket) —
  the transport this effort's new mutation-request `kind` would reuse,
  alongside the existing `classify`/`worktree_status` kinds.
- A durable, SQLite-backed cache layer (`worktree_status_cache.py`) proving
  the "in-memory hot path, SQLite for warm-restore only" pattern this effort
  needs for the tracking record itself, not just a derived-status
  projection.
- A documented cross-venv discovery contract (`hook_client.py` +
  `registry_root.py`) any future write-path client can reuse unchanged.

None of that plumbing needs to be reinvented; this effort's actual new work
is (a) extending the daemon with mutation verbs and (b) migrating every
existing write call site — inside agent-worktrees and, per the operator's
explicit ask, in any sibling plugin — onto them.

## Request

Captured verbatim above (Context § Full inception exchange). Summary: build
the resident daemon into the sole authoritative read/write path for
worktree-lifetime state; demote the YAML tracking record to the daemon's own
persistence; audit and, where needed, adjust sibling `agent-*` plugins so
none of them mutate worktree state by writing tracking YAML directly.

## Plan

### Phase 1 — Design (this document)
- [x] Amend the `agent-worktrees` vision to assert the long-term direction
      (new Concept, two Features, three Behaviors — see header above) before
      any code lands, mirroring how the accelerator effort's own Phase 1
      worked.
- [x] Survey sibling plugins for existing direct-write exposure (Context
      above) — confirmed none exists today, so this is a preventive design,
      not an incident-response migration.
- [ ] Confirm the proposed wire shape below with the operator/reviewer
      before Phase 2 starts.

#### Proposed design (draft — for review, not yet implemented)

**Wire `kind`:** a new `"tracking_write"` verb family alongside the
accelerator's existing `classify`/`worktree_status` kinds on the same
resident daemon (never a second daemon process — the vision's own *Not a
second background service* Non-Goal still holds).

**Mutation surface, not a raw record replace:** the daemon exposes the
same **named operations** `tracking.py` already has as functions today
(`save_record`, `register_session`, `open_handoff`, `add_resource_claim`,
`retire_record`, disposition/title setters, ...) as request verbs — never a
generic "here is the new record body, persist it" endpoint. A generic
replace-the-whole-record verb would hand every caller the ability to race
past whatever invariant a specific operation currently enforces (e.g. the
single-authorized-head-claimant guarantee); named verbs keep those
invariants inside the daemon's own request handlers, in one place, exactly
as they are enforced today inside `tracking.py`'s own functions — this
migration should move *where* that logic runs, not weaken what it enforces.

**In-memory state is the live truth; SQLite/YAML is persistence only.** The
daemon holds each open worktree's current record in memory (warm on boot
from the durable YAML, exactly as `worktree_status_cache.py` warm-restores
today) and answers every read from memory, never from disk on the request
path. Every accepted mutation updates memory first (so a caller's own
"read your write" is immediate and consistent within one process) and
schedules — synchronously before the mutation's response returns, per
*no-writer-bypasses-the-daemon* and *durable-files-are-persistence-not-a-
side-door* in the vision — a write of that worktree's own YAML file. A
worktree's persistence is exactly the same file `tracking.py` already
writes today (`~/.<project>/worktrees/<id>.yaml`); this effort does not
introduce a new on-disk format the operator or a break-glass script would
need to relearn.

**Degrade path stays available, explicitly marked.** When no daemon is
reachable, a **read** still degrades to today's direct-file computation
(`load_record`/`list_records`) — `direct-read-is-a-degrade-not-a-peer`
requires that path to exist for resilience, but the response is marked
degraded so a caller doesn't quietly treat a possibly-stale direct read as
daemon-fresh. A **write** attempted with no daemon reachable is the one
sharp edge this design must resolve deliberately (see Open Questions) rather
than silently falling back to a direct file write that would violate
`no-writer-bypasses-the-daemon`.

**Migration is call-site-by-call-site, inside agent-worktrees first.**
`tracking.py`'s own write functions (`save_record` etc.) become thin clients
of the daemon's mutation verbs when a daemon is reachable, falling back per
the open question above when it is not — every one of `tracking.py`'s ~45
existing call sites keeps its own function signature, so this is a
migration of what happens *inside* those functions, not a rewrite of every
caller across the codebase.

**Sibling-plugin enforcement is a guard, not just a convention.** Once
agent-worktrees' own call sites route through the daemon, add a CI guard
(mirroring `tools/check-install-contract.py`'s existing style) that fails
if any sibling plugin imports a `tracking.py` **write** function
(`save_record`, `register_session`, `open_handoff`, ...) directly — the
existing read-only accessors (`load_record_by_id`, `find_worktree_id_by_cwd`,
`find_worktree_id_by_session`, ...) remain a sanctioned, documented surface,
consistent with today's actual (all read-only) usage found in the Context
survey above.

#### Open questions — RESOLVED 2026-09-26 (operator answers, verbatim in Journal)

- [x] **Write fallback when no daemon is reachable:** resolved as an
      **internal, import-based fallback** — a write with no daemon reachable
      (including a failed boot-on-demand attempt) runs the **exact same
      underlying write function** the daemon's own request handler would
      have called, invoked directly, in-process — never a second, forked
      implementation that could drift from what the daemon enforces. The
      bypass is **always explicitly logged**, so it is visible and
      reconcilable after the fact, never a silent side door. This resolves
      the availability-vs.-single-writer tradeoff without picking a hard
      block: a mutation is never refused merely because the daemon is
      briefly unreachable, and the log gives a durable trail of every time
      that happened.
- [x] **Multi-host / multi-machine scope:** resolved as **strictly
      per-host** — unchanged from the accelerator's existing "exactly one
      [daemon] per host" guarantee. Cross-machine reads/writes are **not** a
      cross-host daemon protocol: they route to the *other* machine's own
      `agent-worktrees` CLI (over whatever transport already reaches that
      host — e.g. the facility's own SSH/agent-bridge conventions in an
      adopting repo), which then talks to *its own* local daemon exactly as
      a same-host caller would. Authority recurses per-host; it never
      widens to a shared cross-host authority.
- [x] **Sequencing vs. other in-flight work:** resolved as **reconcile, and
      ideally consolidate unfinished work** rather than land in parallel
      unaware. Concurrent-effort survey (this session):
      - **`module-componentization-discipline`** (Active) has already split
        `tracking.py`'s write surface into `tracking_claims.py` (claim/
        follow-up/orphanage ledger), `tracking_lifecycle.py` (asserted
        head/handoff/create primitives), and `tracking_session_registry.py`
        (hook/session-registry + repo-freshness helpers), leaving
        `tracking.py` itself as "the persistence-heavy core: `WorktreeRecord`,
        YAML load/save/merge, locking/stamp-queue machinery." **No
        collision** — this is complementary, and genuinely useful prior
        work: Phase 2's named mutation verbs should enumerate against these
        already-split modules' functions, not a monolithic `tracking.py`,
        and Phase 2 should start once that module's own remaining split
        work (if any is still in flight) has landed, so the daemon wraps
        stable module boundaries rather than a moving target. Coordinate by
        reading that effort's own Journal before Phase 2 begins; do not
        fork a second `tracking.py`-splitting effort here.
      - **`worktree-manager-control-plane`** (Active), Sub-slice 3 (Step 1
        landed 2026-09-25, Steps 2-6 not yet implemented,
        [`phase-3b-substatus-monitor-relocation.md`](../worktree-manager-control-plane/phase-3b-substatus-monitor-relocation.md))
        relocates the resident status-monitor's **mux-presentation** half
        (the worktree⇄mux pane mapping, painting the status bar) into a
        companion daemon owned by Worktree Manager. Its own text is explicit
        that "agent-worktrees keeps sole ownership of accumulating/tracking
        session status" — i.e. exactly the write-authority state surface
        this effort claims. **No collision, but a real dependency worth
        watching**: both efforts touch `cmd_status_monitor`'s wiring
        (rendezvous fields, `_monitor_sweep`). Phase 2 should land its new
        `tracking_write` wire kind additively, the same way that effort's
        own Step 1 (`mux_link.py`'s `ManagedMuxCache`) landed additively
        alongside the existing `classify`/`worktree_status` kinds, and
        should check that effort's latest Journal entry immediately before
        touching `cmd_status_monitor` to avoid a stale rebase.
      - **`agent-worktrees-external-status-accelerator`** and
        **`worktrees-pivot-ux-overhaul`** — already accounted for in this
        effort's header (`Related`) and Context above; no further
        reconciliation needed (read-only consumers, unaffected by adding a
        write path).
      - No other active effort was found touching `tracking.py`'s write
        functions or the resident daemon's core sweep/publish loop.

### Phase 2 — Daemon mutation-verb plumbing _(not started; unblocked by Phase 1's resolved open questions)_
- [ ] Confirm `module-componentization-discipline`'s `tracking.py` split is
      at a stable resting point (or coordinate landing alongside it) before
      enumerating Phase 2's mutation verbs against `tracking_claims.py` /
      `tracking_lifecycle.py` / `tracking_session_registry.py` / the
      remaining `tracking.py` persistence core.
  - [ ] Add the `tracking_write` wire kind, mirroring `worktree_status_daemon.py`'s
      structure (rendezvous fields, `start_server`, a `_with_boot` client
      helper), landed additively alongside the existing `classify`/
      `worktree_status`/`mux_link` kinds — never replacing or restructuring
      those in the same change.
- [ ] In-memory record store, warm-restored from YAML on daemon boot.
- [ ] The daemon-unreachable fallback: a small shared helper each migrated
      write function calls into on daemon-unreachable — `run_direct(fn,
      *args, **kwargs)`-shaped, so the logging obligation lives in one place
      rather than being hand-repeated at every call site — invoking the
      exact function the daemon's own handler would have called, then
      logging the bypass (worktree id, function name, reason the daemon was
      unreachable, timestamp).
- [ ] First migrated verb (smallest, most contained write — likely
      `register_session` or a single disposition setter) as the end-to-end
      proof, mirroring how the accelerator's own Phase 4 proved its design
      with one in-process reference consumer before wider rollout.

### Phase 3 — Migrate remaining write call sites _(not started)_
- [ ] One call site (or a closely related cluster) at a time, each its own
      reviewable PR, per this repo's serial-single-writer convention —
      across whichever of `tracking.py` / `tracking_claims.py` /
      `tracking_lifecycle.py` / `tracking_session_registry.py` currently
      owns each function.

### Phase 4 — Sibling-plugin guard + audit _(not started)_
- [ ] Add the CI guard described above.
- [ ] Re-run the cross-plugin survey from this effort's Context to confirm
      it still finds zero direct writers, now backed by an enforced check
      instead of only a point-in-time grep.

### Phase 5 — Full cutover + validation _(not started)_
- [ ] Confirm no code path (inside agent-worktrees or any sibling plugin)
      still opens a tracking YAML file directly for a write.
      `direct-read-is-a-degrade-not-a-peer` reads remain sanctioned;
      writes do not.

## Validation Plan

- [ ] Phase 1: this design reviewed (operator + this repo's automated PR
      review) before Phase 2 code lands.
- [ ] Phase 2: unit tests proving in-memory-first consistency (a write's
      own immediately-following read reflects it without needing a fresh
      YAML parse) and warm-restore correctness (a daemon restart recovers
      the same state from the YAML persistence it last wrote).
- [ ] Phase 3: full existing `test_tracking.py` suite (227 tests as of
      #3755) continues passing after each migrated call site — the
      external function contracts do not change, only what happens inside
      them.
- [ ] Phase 4: the new CI guard fails on a deliberately reintroduced direct
      write from a sibling-plugin test fixture, then passes once removed —
      proving the guard actually catches the case it exists for.
- [ ] Phase 5: a live end-to-end host check (mirroring the accelerator
      effort's own Phase 7 audit tooling) confirming every sampled
      worktree's daemon-held state and its on-disk YAML persistence agree.

## Proposal

Phase 1's design and all three open questions are now resolved (see Plan
above). Pending: this repo's automated PR review on the resolution PR, and
confirming `module-componentization-discipline`'s `tracking.py` split has
reached a stable resting point before Phase 2 actually starts cutting code.

## Journal

### 2026-09-26 — Open questions resolved; concurrent-effort survey done
Operator answers, verbatim:

1. "Internal import-based fallback to run the correct direct-write
   (reusing same code, no fork), with log"
2. "Strictly per host. Cross-machine reads/writes go to other machine's
   CLI, which then wraps its daemon"
3. "We'll have to reconcile. Ideally consolidate unfinished work"

Resolved the design accordingly (see Plan's *Open questions — RESOLVED*
above) and amended the vision's *The resident daemon as the authoritative
live-state database* concept and *no-writer-bypasses-the-daemon* behavior
to match — the prior same-day vision entry had speculatively asserted a
read-only-only degrade for writes, which answer (1) explicitly overrides.

Ran the concurrent-effort survey answer (3) asked for: grepped
`efforts/active/*/README.md` for `tracking.py`/daemon/status-monitor
references, found 14 incidental hits, and read the real candidates in full.
Two are genuinely relevant and now cross-linked (both header `Related` and
Plan): `module-componentization-discipline` (already split `tracking.py`'s
write surface into `tracking_claims.py`/`tracking_lifecycle.py`/
`tracking_session_registry.py` — complementary, Phase 2 targets those
boundaries) and `worktree-manager-control-plane`'s Sub-slice 3 (relocates
only the mux-presentation half of the status-monitor; that effort's own
text explicitly keeps state-tracking authority in agent-worktrees). No
actual duplication found — nothing to merge, only to sequence and
cross-reference, which is now done.

### 2026-09-26 — Effort created; vision amended
- Operator direction (this session, captured verbatim in Context above):
  invert the accelerator's "warmth, not truth" stance into a long-term
  authoritative write-through daemon, and audit sibling plugins for direct
  tracking-YAML writes.
- Amended `visions/plugins/agent-worktrees/README.md`: added *The resident
  daemon as the authoritative live-state database* (Concepts & Components),
  *daemon-mediated-write-authority* and *single-write-path-across-plugins*
  (Features), and *no-writer-bypasses-the-daemon*,
  *durable-files-are-persistence-not-a-side-door*,
  *direct-read-is-a-degrade-not-a-peer* (Behaviors). Marked the existing
  *Derived status* and *external-status-consumer-contract* sections as this
  vision's already-landed Phase 1, forward-pointing to the new sections
  rather than leaving the document silently self-contradictory about
  whether the daemon writes.
- Filed the umbrella tracking issue
  ([#3761](https://github.com/ThomasMichon/copilot-extensions/issues/3761))
  per this repo's coordination convention (claim before starting a
  stretch).
- Drafted this effort's Phase 1 design (wire shape, migration order, three
  open questions genuinely requiring operator input before Phase 2 can be
  scoped concretely) rather than starting implementation — mirrors exactly
  how `agent-worktrees-external-status-accelerator` itself was run
  (design reviewed and merged first).
- **Not yet implemented.** This is the design-review checkpoint; Phase 2
  starts once Phase 1's open questions are resolved and the design clears
  review.
