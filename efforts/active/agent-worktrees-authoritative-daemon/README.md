# agent-worktrees — Authoritative Write-Through Daemon

- **Slug:** `agent-worktrees-authoritative-daemon`
- **Repo:** copilot-extensions (plugin home; PR-gated `dev`)
- **Branch(es):** independent per-phase worktrees (land each phase's PR before
  starting the next)
- **Created:** 2026-09-26
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
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

### Phase 2 — Daemon mutation-verb plumbing ✅ INFRA DONE 2026-09-26 (no call site migrated yet)
- [x] Confirmed `module-componentization-discipline`'s `tracking.py` split is
      at a stable resting point before starting: `tracking.py` sits exactly
      at its 4009-line grandfathered ceiling with no journal activity since
      2026-09-22 (4 days idle at the time this phase started) — a safe
      resting point, not a moving target.
  - [x] Added the `tracking_write` wire kind (`tracking_write.py`), mirroring
      `classify_daemon.py`/`worktree_status_daemon.py`'s structure
      (rendezvous fields, `start_server`, a `write_with_boot` client
      helper), landed additively in `cmd_status_monitor` alongside the
      existing `classify`/`worktree_status`/`mux_link` kinds — none of
      those were touched or restructured.
- [x] **Revised, not yet built:** a full persistent in-memory record store
      (warm-restored from YAML on daemon boot, `worktree_status_cache.py`-
      style) remains explicitly **deferred**, not part of this phase's
      landed slice — see the granularity finding below for why forcing it
      in now would have been premature. Today, a registered verb still does
      its own fresh lock/load/mutate/save per call, whether invoked from the
      daemon or directly; the correctness win landed is "every write
      funnels through one process when reachable," not yet "every read is
      served from a warm in-memory copy."
- [x] The daemon-unreachable fallback: `tracking_write.run_direct(verb,
      args, reason=...)` — calls the *same* function `tracking_write.
      compute` would have called (via the shared `_VERBS` registry, see
      `register_verb`), always logging the bypass first. `tracking_write.
      dispatch()` is the single public entry point a migrated call site
      uses in place of calling its function directly, minting a fresh,
      unique coalescing key per call so two concurrent writes are never
      merged into one execution (a write-specific difference from
      `classify`/`worktree_status`'s read-safe coalescing).
- [x] **Real finding, not yet acted on for the first verb:** inspecting
      actual call sites (`register_session` in
      `tracking_session_registry.py`, `mark_resumed` in `resolve_cli.py`/
      `resolve_launch_cli.py`) found every one composes **several**
      `tracking.*` mutation calls under one `_RecordLock` before **one**
      `save_record` — never a single bare field-setter call in isolation.
      **Corrects this phase's own earlier plan wording** ("named
      operations... as request verbs," implying one verb per
      `tracking.py` function): a verb must map to a call site's whole
      guarded transaction, not to an individual setter, or a migration
      would either multiply round trips per real operation or break the
      atomicity that one lock + one save currently guarantees. See
      `tracking_write.py`'s own module docstring ("Verb granularity note")
      for the durable, code-level record of this correction.
      _(agent-recommended — found via code inspection this session, not
      operator-specified; flagged here per this skill's own demarcation
      requirement.)_
- [ ] **First migrated verb, still open:** given the granularity finding
      above, `register_session`/`mark_resumed` (this phase's originally
      named candidates) are each a multi-step guarded transaction, not a
      quick, low-risk first proof to retrofit blind in one pass against a
      hot, widely-used facility path (`register_session` specifically backs
      the sessionStart hook). Deferred to Phase 3 rather than rushed here;
      Phase 2's own infra is proven end-to-end instead via 14 new unit
      tests (`test_tracking_write.py`) exercising the registry, dispatch,
      logged fallback, and the unique-key-never-coalesces guarantee
      directly, without touching a live production call site.
- [x] Tests: `test_tracking_write.py` (27 tests, after PR review rounds 2-3:
      rendezvous parsing + port-bounds validation, verb registration/
      dispatch, unregistered-verb/malformed-payload rejection, `run_direct`'s
      logged bypass, daemon-reachable dispatch, both safe-fallback cases
      (no endpoint found; endpoint found but connect fails), the
      ambiguous-outcome-after-send exception, two-concurrent-writes-never-
      coalesce, the verb-module loader, a genuine two-subprocess
      process-boundary proof, and in-flight-write tracking independent of
      subscriber lease lifecycle).
      `test_status_monitor.py`'s existing daemon-lifecycle regression test
      updated (3 → 4 `CoalescingServer.close()` calls: classify +
      worktree_status + managed_mux + tracking_write) plus new
      `tracking_write_*` rendezvous-field assertions. Full suite: 5573
      passed, 26 skipped, 5 failures confirmed pre-existing/environment-
      dependent via `git stash` (unrelated to this change — `test_doctor.py`/
      `test_update_stage.py`/`test_registration_home.py`, all failing
      identically without these changes).

### Phase 3 — Migrate the first real write call site, then the rest _(not started)_
- [ ] Pick the first real call site to migrate given the corrected verb
      granularity (a whole transaction, not a bare setter) — candidates to
      evaluate: a narrower, lower-traffic disposition-assertion path before
      `register_session`'s own sessionStart-hook-critical one.
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

### 2026-09-26 — PR #3779 review round 3: 5 more real findings, all fixed
Round 3 caught five further genuine issues, all in the same "does the
ambiguity/liveness reasoning actually hold under real conditions" vein as
round 2:

- **`subscriber_count()` still wasn't the right in-flight signal.** A
  client releases its own lease the moment its own request call returns or
  times out -- which can happen well before the daemon-side compute (same
  process, a different thread) actually finishes, since `CoalescingServer`
  never cancels an accepted owner. Added `has_inflight_write()`: a simple
  module-level counter incremented/decremented around the verb call inside
  `compute()` itself, independent of any client's lease lifecycle. The
  monitor's `_tracking_write_busy()` now checks both signals.
- **The ambiguous/safe split was still wrong at the edges.** My round-2 fix
  treated "endpoint found" as the boundary for ambiguity, but `DaemonUnavailable`
  is also raised for a *pre-send* connect failure (a stale rendezvous entry
  left by a since-exited daemon) -- genuinely safe, indistinguishable in
  effect from never having dialed at all. The real boundary is "were request
  bytes ever sent," not "was an endpoint found." Fixed by writing
  `_send_tracking_write_request` -- a from-scratch client for this one wire
  action, splitting the TCP connect (failure -> safe, new `_PreSendFailure`,
  caught by `write_with_boot` and treated exactly like a pre-dial miss) from
  the send/receive phase (failure -> `AmbiguousWriteOutcome`, unchanged).
  `wcs_client.request()`'s single `except OSError` across the whole sequence
  cannot make this distinction, so this module no longer calls it for the
  write path (still uses `wcs_client.new_client_id`/`release` for the
  existing subscriber-lease bookkeeping, unchanged).
- **The coalescing key could theoretically be reused.** `write_with_boot`
  accepted a caller-supplied `key`; only `dispatch` happened to always mint
  a fresh one. Removed `key` from `write_with_boot`'s signature entirely --
  it now mints its own `uuid4().hex` internally, so the
  never-coalesce-two-writes guarantee is structural (every entry point),
  not a convention every future caller has to remember.
- **Port validation gap.** `endpoint_from_rendezvous` parsed a port as a
  bare `int()` with no range check, so `0`, a negative value, or anything
  above 65535 would be treated as a real endpoint and fail deep inside the
  socket client instead of safely returning `None` (the no-endpoint
  fallback path). Fixed with the same `0 < port < 65536` check
  `mux_link.endpoint_from_rendezvous` already applies.
- **Eager monitor-startup loading, done in round 2, was correctly credited**
  but the stale-count Low finding recurred (my round-2 edit fixed the
  Journal narrative but missed a live checklist line) -- fixed for real
  this time; both occurrences of the stale count now read the current
  count.
- 9 new/changed tests (27 total, was 18): out-of-range and valid port
  parsing for `endpoint_from_rendezvous`, the pre-send-connect-failure safe
  fallback, and three `has_inflight_write` tests (idle, running, clears on
  verb exception). Re-ran the full suite (5573 passed, same 5 pre-existing
  failures), `ruff check`, `check-module-size`, `check-install-contract`,
  `check-changefile-presence`: all clean.

### 2026-09-26 — PR #3779 review round 2: 2 more real High findings, plus test-quality fixes
Round 2 (3 High/Medium/Low remaining + 4 new, 1 resolved from round 1) caught
two more genuine bugs beyond the cross-process registration gap:

- **Ambiguous fallback retry after a dialed-but-failed request.**
  `write_with_boot` treated *any* `DaemonUnavailable` (pre-dial miss **or**
  a request that reached the daemon and then timed out) identically, always
  running the same-code fallback. But `CoalescingServer` never cancels an
  already-accepted owner compute when a caller's socket read times out --
  so a post-dial failure is genuinely ambiguous (the daemon may already be
  executing, or have already committed, the mutation), and blindly retrying
  risks double-applying a non-idempotent write. Fixed: only a **pre-dial**
  miss (no endpoint discoverable at all) is safe to auto-fallback; a
  post-dial failure now raises a new `AmbiguousWriteOutcome` instead,
  documented in both `tracking_write.py`'s module docstring and
  `write_with_boot`/`dispatch`'s own docstrings. This refines (does not
  contradict) operator-resolved Open Question 1 -- that answer covered "no
  daemon reachable at all," not the separate ambiguous-timeout case, which
  is a real engineering distinction surfaced by review, not something the
  operator was actually asked about.
- **Monitor could shut down mid-write.** The resident monitor's own
  empty-strike idle-exit predicate checked `worktree_status_runtime`/
  `managed_mux_runtime` demand but not `tracking_write_server`'s live
  subscriber count -- an in-flight write with no other daemon activity
  could see the monitor decide "empty" and close the server out from under
  a still-waiting client. Fixed: both empty-strike branches now also check
  `tracking_write_server.subscriber_count() > 0`, mirroring how
  `has_active_demand()` already gates the same predicate for the read-side
  daemons.
- **`_ensure_verb_modules_loaded` now called eagerly at monitor startup**
  too (not just lazily inside `compute`/`run_direct`), directly addressing
  the still-open "load production verbs at the monitor's own startup/
  import path" finding at its own file/line.
- **Switched `_VERB_MODULES` to fully-qualified module names**
  (`importlib.import_module(name)`, not package-relative) -- simpler, and
  what let the process-boundary test's fixture module live outside the
  `agent_worktrees` package entirely (a standalone temp module on
  `sys.path`, exactly like a real external consumer would look).
- **Fixed both flagged test-quality gaps:** the deadline-fallback test now
  blocks the verb past the client's real socket timeout and asserts the
  new `AmbiguousWriteOutcome` (previously it could pass either way,
  proving nothing); the process-boundary test now declares the verb module
  via `_VERB_MODULES` and calls `compute`/`run_direct` directly, letting
  the *production* loader perform the import (previously it pre-imported
  the module and hand-set `_verb_modules_loaded = True`, bypassing the
  exact mechanism under test).
- Updated stale "14 tests" references to 18 (2 net new: the ambiguous-
  outcome test replaced the broken deadline test 1-for-1, plus a new
  pre-dial-miss-still-falls-back test made the safe/unsafe distinction
  explicit on both sides).
- Added the required PR-description "Documentation impact" statement
  (CONTRIBUTING.md § Documentation impact) the round-1 Low finding flagged
  as missing.
- Re-ran the full suite + `ruff check` + `check-module-size`/
  `check-install-contract` after all fixes: all clean.

### 2026-09-26 — PR #3779 review: closed a real cross-process registration gap
GitHub's Copilot code review (2 High + 1 Low findings) on the Phase 2 PR
caught a real bug before Phase 3 could build on top of it: `_VERBS` was a
plain process-local module dict, but the resident daemon and a CLI process
calling `dispatch`/`run_direct` are **separate Python processes** —
`register_verb` called in one would never populate the other's dict, so
every real verb (once Phase 3 added one) would have hit "unregistered verb"
on the daemon side and silently always fallen back to `run_direct`. The
daemon would never actually execute a write; `dispatch()` looked correct in
tests only because those tests register and serve in the same process.

Fixed with `_VERB_MODULES` (a tuple of module names, relative to this
package, that self-register their own verb(s) via a plain `register_verb`
call at their own import time) + `_ensure_verb_modules_loaded()` (idempotent,
thread-safe, imports every listed module) called at the top of both
`compute` and `run_direct` — so the daemon process and any CLI process
arrive at the identical registry independently via Python's own import
system, with no shared runtime state or wire-level registration protocol
needed. `_VERB_MODULES` stays empty until Phase 3 adds its first real verb
module; the contract is documented in `tracking_write.py`'s own module-level
docstring for that module to follow.

Added the literal process-boundary proof the review asked for: two genuinely
separate `python` subprocesses (not the test process itself) import a
throwaway verb module and invoke it — one through `compute`, one through
`run_direct` — proving the fix works across a real OS process boundary, not
just this test process's own single import cache. Plus two narrower unit
tests (the loader imports each listed module exactly once; both `compute`
and `run_direct` call the loader). 17 tests total in `test_tracking_write.py`
now (was 14).

Also addressed the review's Low finding: this Journal entry, plus the PR
description's required "Documentation impact" statement (CONTRIBUTING.md
§ Documentation impact) explaining that `tracking_write.py`'s own docstring
is this change's authoritative documentation (no repo-root doc changed) and
that the vision doesn't need a revision for a plumbing bug fix that carries
no new guarantee or behavior change visible above the daemon-internals
layer.

### 2026-09-26 — Phase 2 infra landed (wire plumbing + fallback + tests); first call-site migration deferred to Phase 3
- Verified `module-componentization-discipline`'s `tracking.py` split was a
  safe resting point (exactly at its 4009-line ceiling, no journal activity
  in 4 days) before starting, per Phase 1's resolved sequencing question.
- Built `tracking_write.py`: the `tracking_write` wire kind, a verb
  registry (`register_verb`/`compute`), `run_direct` (the logged,
  same-code daemon-unreachable fallback), and `dispatch`/`write_with_boot`
  (the public entry point, always minting a fresh coalescing key per call
  so concurrent writes never merge). Wired additively into
  `cmd_status_monitor` (`status_monitor_cli.py`): a fourth
  `CoalescingServer` alongside `classify`/`worktree_status`/`mux_link`,
  publishing `tracking_write_*` rendezvous fields, closed in the existing
  shutdown path.
- **Real finding while scoping the first migrated verb:** every actual
  `tracking.py` write call site (`register_session`, `mark_resumed`, ...)
  batches several mutation calls under one `_RecordLock` before one
  `save_record` — never a bare single-setter call. This corrects Phase 2's
  own earlier plan wording (documented in the Plan above and in
  `tracking_write.py`'s own docstring): a verb must map to a call site's
  whole guarded transaction, not an individual `tracking.py` function.
  Demarcated as agent-recommended (found via inspection, not
  operator-specified).
- Given that correction, and that both originally-named first-verb
  candidates turned out to be non-trivial, hot-path transactions
  (`register_session` specifically backs the sessionStart hook), chose
  **not** to rush a production call-site migration in the same pass as
  new infra against a live, widely-used facility tool. Instead proved the
  infra end-to-end via 14 new unit tests (`test_tracking_write.py`)
  exercising the registry, dispatch, logged fallback, and the
  never-coalesces-two-writes guarantee directly.
- Fixed a real, expected regression in `test_status_monitor.py`'s existing
  daemon-lifecycle test (mirrors the accelerator effort's own precedent,
  2026-09-20: adding a fourth `CoalescingServer` moved its
  `closed["n"] == 3` assertion to `4`) and added `tracking_write_*`
  rendezvous-field assertions alongside the existing ones.
- Full suite: 5549 passed, 26 skipped, 5 failures -- confirmed via
  `git stash` to be pre-existing/environment-dependent (`test_doctor.py`,
  `test_update_stage.py`, `test_registration_home.py`), unrelated to this
  change and failing identically without it.
- **Not yet done:** no production write call site actually goes through
  the daemon yet -- `tracking_write.dispatch()` exists and is proven
  correct in isolation, but nothing calls it outside its own tests. Phase
  3 picks the first real transaction to migrate.

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
