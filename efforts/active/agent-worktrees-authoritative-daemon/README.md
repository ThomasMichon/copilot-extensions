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
  [`ThomasMichon/copilot-extensions#5555`](https://github.com/ThomasMichon/copilot-extensions/issues/5555)
  — names a consolidation gap this effort would be a natural home for:
  `classify_daemon` and `worktree_status_daemon` today independently compute
  overlapping git-state facts for the same worktree via two separate compute
  paths (`worktree_status_compute.py` only assembles a read-only bundle;
  `picker_support/data_local.py` is the path that actually stamps
  `WorktreeRecord.git_state`). #5555 proposes consolidating those two compute
  paths into one. **Now formally in scope as Phase 6** (added 2026-10-06,
  operator-directed) — see the Plan below. **Formal follow-on from
  `pivot-streaming-transport`:** that effort's own Phase 3 note directs
  whoever closes it out to continue directly into this effort's Phase 6
  rather than stopping — the four-layer "#5555" direction and this effort's
  own Worktrees-specific consolidation are sequenced, not parallel, work.
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
  #3755, unrelated to this effort's own scope but the reason it was found),
  [#3798](https://github.com/ThomasMichon/copilot-extensions/issues/3798)
  (a residual accept-to-handler-dispatch drain race in the shared
  `work_coalescing_singleton` library, found during #3779's review round 7;
  resolved in Phase 3's PR #3807), [#3812](https://github.com/ThomasMichon/copilot-extensions/issues/3812)
  (an unsupported-verb response during a daemon rolling upgrade
  misclassified as `AmbiguousWriteOutcome`, found during #3807's review;
  deferred, tracked for a future Phase 3 slice)

## Guiding Intent

**Read-side bound (operator clarification, 2026-10-12):** ordinary reporting of
daemon-cached worktree status is O(state-schema), not O(journal, ledger, list,
claims graph, or fleet). The authoritative writer pushes claims-derived facts
and monitoring membership into independently readable current-state
projections; readers select the current handoff head directly rather than
walking succession history. The exact request and proposed acceptance live in
the coupled [per-worktree read contract](../per-worktree-activity-state/schema-bounded-read-contract.md).
This strengthens the existing authority direction; an in-memory cache of a
growing record alone does not satisfy it.

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

| Participant | Scope | Coordination |
|-------------|-------|--------------|
| Streaming recovery owner | Phase 6d.3a client startup/reconnect acceptance only | #6832; independent, sequential plan and implementation PRs |

This participant does not take ownership of the remaining daemon phases,
singleton transitions, central fleet exporter, or other pivots.

## Coordination

The complete direct #2558/#2706/#7097 finite cases continue through the SAME Active
ROOT's existing guarded transaction, explicit catalog/inspection and repair
responsibilities, actual global Validation and full Journal under the current #3761
integration/source owners. Preserve EVERY incumbent row, completed phase, #4120 intake,
#229/#1919/#3751 coordination acceptance, #5664 lifecycle/enrollment, #6086
exact-selection enrichment, #6832 client and current exporter/native/resource claims.
#3585 remains independently OPEN in its existing dedicated head-succession Phase2; its
full ambient/root-discovery contract below is REFERENCE ONLY, not received custody. No
second daemon/registry, new generic ACK, implementation assignment, operational
permission or parent-completion waiver is created.

The direct retained #4120 acceptance continues through this SAME existing Active ROOT's
Plan, own and actual global Validation, and Journal under the incumbent domain, source,
native and resource custodians. Every prior phase, alternative, hold and responsibility
remains. Administrative supersession retires only duplicate tracking; a closed source is
historical provenance, not a required live claim token. This intake grants no
implementation, rollout, process action, reassignment or new blanket approval gate.

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
  alongside the existing `classify`/`worktree_status`/`mux_link` kinds.
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
accelerator's existing `classify`/`worktree_status`/`mux_link` kinds on the same
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

**In-memory state is the live truth; durable storage is persistence only.**
The daemon holds current state in memory and serves ordinary status through
an independently addressable O(state-schema) projection, not by copying its
whole historical record. Every accepted mutation updates memory first (so a
caller's own "read your write" is immediate and consistent within one process)
and schedules — synchronously before the mutation's response returns, per
*no-writer-bypasses-the-daemon* and *durable-files-are-persistence-not-a-
side-door* in the vision — durable publication of the affected current-state
projection and its generation/readiness. Existing worktree YAML remains a
compatibility/history persistence surface; its growing collections cannot
be the ordinary status lookup or restoration source. The persistence
design must support bounded point reads of durable current state. Recovery
that needs older records belongs to the owner-controlled restoration/repair
path, with unready state explicit until bounded serving is available.

**Degrade path stays available, explicitly marked.** When no daemon is
reachable, an **ordinary status read** may return only an independently
addressable bounded durable projection, marked degraded/advisory rather
than daemon-fresh, or explicit unavailable/unready state. It must not fall
back to `load_record`/`list_records`, journal replay or graph reconstruction.
`direct-read-is-a-degrade-not-a-peer` preserves resilience without exempting
that read from the schema-bound contract; explicit inspection/repair may
still read historical files. A **write** attempted with no daemon reachable is the one
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
        alongside the existing `classify`/`worktree_status`/`mux_link` kinds, and
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
      Phase 2's own infra is proven end-to-end instead via 27 unit tests
      (`test_tracking_write.py`, after PR review rounds 2-4) exercising the
      registry, dispatch, logged fallback, the unique-key-never-coalesces
      guarantee, the ambiguous/safe outcome boundary, and in-flight-write
      tracking, directly, without touching a live production call site.
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
      `tracking_write_*` rendezvous-field assertions and the
      `TestWaitForTrackingWriteIdle` class added in review round 4. Full
      suite (current, as of round 6): 5576 passed, 26 skipped, 5 failures
      confirmed pre-existing/environment-dependent via `git stash`
      (unrelated to this change — `test_doctor.py`/`test_update_stage.py`/
      `test_registration_home.py`, all failing identically without these
      changes).

### Phase 3 — Migrate the first real write call site, then the rest _(in progress)_
- [x] Resolved [#3798](https://github.com/ThomasMichon/copilot-extensions/issues/3798)
      (the accept-to-handler-dispatch drain race in the shared
      `work_coalescing_singleton` library) — `CoalescingServer` now exposes
      `active_handler_count()`, incremented at `accept()` time (before the
      handler thread is even spawned) and decremented once that handler
      fully returns (including a failed-dispatch case, e.g. `Thread.start()`
      raising), and `status_monitor_cli._tracking_write_busy()` ORs it in
      alongside `subscriber_count()`/`has_inflight_write()`. Landed in PR
      [#3807](https://github.com/ThomasMichon/copilot-extensions/pull/3807)
      (see below), synced to both vendored copies (agent-worktrees +
      worktree-manager) with a version bump per
      `check-vendored-libs-sync.py`.
- [x] Picked and migrated the first real call site: `status`'s write mode
      (`_cmd_status_write` / the `status_disposition_write` verb, a new
      `tracking_disposition_write.py` module) — the "narrower, lower-traffic
      disposition-assertion path" this Plan named, not `register_session`
      (sessionStart-hook-critical) or `mark_resumed`. Landed in PR
      [#3807](https://github.com/ThomasMichon/copilot-extensions/pull/3807)
      (8 review rounds — cross-project scoping for the disposition-history
      sidecar AND the `status_reported` durable trace, both explicitly
      threaded through rather than trusting a resident daemon's own ambient
      `cfg.active_project()`; the #3798 fix above; a counter-leak-on-failed-
      dispatch fix; a worktree-manager payload version bump for the
      vendored library change). One residual finding — an unsupported-verb
      response during a daemon rolling upgrade misclassified as
      `AmbiguousWriteOutcome` — deliberately deferred as
      [#3812](https://github.com/ThomasMichon/copilot-extensions/issues/3812)
      rather than expanding this PR's scope, same rationale as #3798's own
      original deferral.
- [x] Migrated the second call-site cluster: the itemized follow-up
      ledger's three write transactions (`follow-ups add`/`resolve`/
      `dismiss` — `follow_up_add`/`follow_up_resolve`/`follow_up_dismiss`
      verbs, a new `tracking_followup_write.py` module), landed together
      as one PR since they are a closely related cluster (same
      `tracking._RecordLock` -> mutate -> `save_record` ->
      `activity.log_event` shape). Landed in PR
      [#3868](https://github.com/ThomasMichon/copilot-extensions/pull/3868)
      — only a single low-severity finding this round (a missing
      Documentation-impact PR-description statement); none of this
      cluster's three events are stage-mapped in
      `activity.HANDOFF_STAGE_MAP`, so unlike `status_disposition_write`
      there was no cross-project durable-trace scoping fix needed here.
- [x] Migrated the third call-site cluster: the outbound resource-claim
      ledger's three single-worktree write transactions (`claims add`/
      `release`/`settle` — `claim_add`/`claim_release`/`claim_settle`
      verbs, a new `tracking_claim_write.py` module), mirroring
      `tracking_followup_write.py`'s own shape. Landed in PR
      [#3876](https://github.com/ThomasMichon/copilot-extensions/pull/3876)
      — deliberately excludes `claims sweep`/`claims reconcile-at-rest`
      (multi-worktree batch operations) and the cross-machine `owner_ref`
      resolution/deferral path (a CLI-level concern resolved before a
      local `yaml_path` is even known). One real finding this round: the
      verb's own frozen-state pre-check only covered
      `finalizing`/`orphaned`, missing that `tracking.add_resource_claim`
      itself also rejects a completed `system`/`bridge` record (and a
      reservation-conflict) via `ValueError` — uncaught, that exception
      would have been silently swallowed by `CoalescingServer` when
      dispatched via the daemon, surfacing as `AmbiguousWriteOutcome`
      instead of a deterministic rejection. Fixed by catching `ValueError`
      generically around the call (never duplicating
      `add_resource_claim`'s own guard predicate) — a lesson worth
      carrying into every future verb: prefer catching the wrapped
      function's own validation exceptions generically over re-deriving
      which cases it covers.
- [x] Migrated the fourth call-site cluster: the two dedicated ground-layer
      session-lifecycle write commands (`conclude-session`/
      `link-succession` — `session_conclude`/`session_link_succession`
      verbs, a new `tracking_session_lifecycle_write.py` module). Landed
      in PR [#3886](https://github.com/ThomasMichon/copilot-extensions/pull/3886)
      with zero findings — the simplest cluster yet (single-lock,
      project-agnostic, no `activity.log_event`). A design survey (done
      BEFORE implementation, per this session's approach) found most of
      `tracking_lifecycle.py`'s remaining write functions are NOT
      standalone call sites and stay deliberately deferred:
      - `open_handoff`/`link_handoff` are invoked from inside
        `register_session`'s own sessionStart-hook-critical path — the
        same risk class this effort's original guidance told it to avoid
        for an early verb.
      - `handoff_cutover.py`'s confirmed-retire repairs
        (`_conclude_retired_predecessor`/`_settle_predecessor_session_claim`)
        are best-effort, silently-swallowed-exception call sites reachable
        from the live handoff-cutover choreography — could plausibly
        REUSE the now-landed `session_conclude`/`claim_settle` verbs
        rather than inventing new ones, but need care around their
        existing silent-no-op contract before wiring that in.
      - `terminal_conclusion.py`'s `_save_session_conclusion` is one step
        inside the disposable-worktree conclusion cascade's own single
        lock, not a standalone transaction of its own.
- [x] Migrated the fifth call-site cluster: `handoff_cutover.py`'s two
      confirmed-retire repairs (`_settle_predecessor_session_claim`/
      `_conclude_retired_predecessor`) — reusing the already-landed
      `claim_settle`/`session_conclude` verbs via new opt-in no-op guard
      args (`skip_if_released`/`only_if_active`, checked inside the verb's
      own locked transaction) rather than inventing new verbs. Landed in
      PR [#3911](https://github.com/ThomasMichon/copilot-extensions/pull/3911)
      — 4 review rounds, the last three all on the same underlying
      question (what lock policy the repair's write should use): a
      `require_sidecar=False` guard was added, then found incomplete
      (`tracking.save_record`'s own nested lock still hardcoded
      `require_sidecar=True`), then found unsafe once fully threaded
      through (writing without cross-process exclusion on contention could
      resurrect an already-released claim from a stale snapshot) — deeper
      analysis showed the pre-migration transaction's own net effect on
      contention was ALREADY to fail closed (its outer lock degraded, but
      `save_record` always hard-required the sidecar), so the correct fix
      was reverting to `apply_claim_settle`'s original `require_sidecar=True`
      throughout, matching old behavior exactly rather than genuinely
      degrading. A lesson for future verb migrations: tracing a
      pre-migration transaction's own *net* lock-contention behavior
      (not just its outermost lock's stated policy) before assuming what
      "faithful to the old code" requires.
- [x] Migrated the sixth call-site cluster: `finalize.py`'s
      `_settle_current_session_claim` (settles the invoking session's own
      outbound `session` claim to at-rest before finalize's obligation
      gate runs) — reusing the same `claim_settle` verb's
      `skip_if_released` guard via `tracking_write.dispatch`, instead of a
      bespoke locked transaction duplicating the guard PR #3911 already
      landed. Unlike `terminal_conclusion.py`'s `_save_session_conclusion`
      (still deferred — embedded in a bigger single-lock cascade with two
      separate `save_record` calls around it), this function already
      opened its own SELF-CONTAINED `_RecordLock`, never sharing a lock
      scope with `finalize()`'s surrounding flow — confirmed via
      inspection of its only call site before migrating. Landed in PR
      [#4064](https://github.com/ThomasMichon/copilot-extensions/pull/4064)
      — one review round: the existing tests' autouse fixture disables
      the resident monitor, so they only exercised the direct fallback;
      added a live-`CoalescingServer` dispatch test and a
      dispatch-failure-keeps-the-stale-record test to cover the daemon
      path and the `except Exception: pass` contract explicitly.
- [x] Migrated the seventh call-site cluster: `register_session` itself —
      the sessionStart-hook-critical transaction Phase 2's own plan and
      Phase 3's fourth-cluster design survey both deliberately avoided.
      Operator-directed (2026-09-27): rather than extracting only the
      internal `link_handoff` call (one conditional branch inside a
      single, deeply interdependent transaction — pulling it out alone
      would split one atomic write into two, a real regression, not a
      simplification), verb-ified the WHOLE transaction as one atomic
      `session_register` verb, keeping the atomicity guarantee intact.
      Landed in PR
      [#4159](https://github.com/ThomasMichon/copilot-extensions/pull/4159)
      — 3 review rounds, all correctly focused on this cluster's unique
      stakes (a sessionStart hook blocks the interactive session, unlike
      every prior CLI-command cluster):
      1. **Latency mitigation** (operator-anticipated in the same
         direction that authorized this cluster): `register_session`'s
         own dispatch passes `boot_wait_s=0` (never spin-wait for a cold
         daemon boot -- `ensure_monitor()` still fires the non-blocking
         spawn, warming the daemon for the NEXT session, but this one
         falls straight through with no added wait) and a short
         `_SESSION_REGISTER_REQUEST_DEADLINE_S` (1.5s, vs the module's
         default 8s) bounding even a reachable-but-stalled daemon.
      2. **Rolling-upgrade version skew, fixed at the source**
         (copilot-extensions#3812, previously deferred as narrow/rare for
         a lower-stakes verb -- now genuinely fixed, since it is far
         higher-stakes on this hot path): `compute()` now returns
         `{"unsupported_verb": True}` instead of raising for an
         unregistered verb (defense-in-depth), but the REAL fix is
         capability-aware endpoint selection -- `rendezvous_fields()`
         publishes this process's own registered verb names, and
         `endpoint_from_rendezvous()`/`write_with_boot()` refuse to even
         dial an endpoint whose own published capability list doesn't
         cover the requested verb (including data with no such field at
         all, i.e. a daemon process that predates the whole concept) --
         treated identically to "no endpoint found," which safely
         triggers the normal boot-wait/fallback path instead of a doomed
         connection attempt.
- [x] Migrated the eighth call-site cluster: `deregister_session` itself
      — the symmetric sessionEnd counterpart, second cluster taken from a
      hot hook path. Much simpler than its sibling: a single
      find-the-matching-entry loop, no handoff-token/candidate-token
      branching. Landed in PR
      [#4238](https://github.com/ThomasMichon/copilot-extensions/pull/4238)
      applying the exact latency checklist PR #4159 established
      (`boot_wait_s=0` + a short request deadline); rolling-upgrade safety
      came for free from `tracking_write.py`'s own capability-aware
      endpoint selection. **2 review rounds on the SAME real finding**,
      worth recording as its own lesson: a first attempt moved
      `stop_fsmonitor_daemon` (a `git fsmonitor--daemon stop` subprocess)
      OUTSIDE the `_RecordLock`, reasoning it was a scope-discipline
      improvement (`_RecordLock`'s own docstring discourages holding it
      across git I/O) — review correctly found this ordering was
      load-bearing, not incidental: holding the SAME lock
      `register_session` also needs is what prevents a concurrent session
      start from adding a new open session in the window between the
      "no open sessions" check and the actual stop; without it, a session
      starting moments later could have its own fsmonitor killed out from
      under it. Reverted to the exact pre-migration in-lock ordering,
      with the module's own docstring now recording why. **Lesson for
      future migrations touching a lock-scope "improvement": an
      established `_RecordLock` scope-discipline guideline is a strong
      default, not an absolute rule — verify what invariant the EXISTING
      ordering protects (often by asking "what runs concurrently against
      the SAME lock this call site also uses?") before assuming a
      textbook scope reduction is safe.** (Directly parallel to PR #3911's
      own `require_sidecar` lesson: trace what an existing pattern's *net*
      effect actually guarantees before changing it, not just what its
      stated policy/guideline suggests.)
- [x] **Read-side companion (2026-09-27, not itself a Phase-3 write-migration
      cluster): daemon writes push straight into the in-process read
      cache.** Reframed from an initial "verb-ify `terminal_conclusion.py`'s
      `_save_session_conclusion`" plan once the operator redirected scope:
      the actual ask was narrower and squarely serves this effort's own
      Guiding Intent ("every read *and* every mutation... funneled
      through") without requiring the daemon to become the sole
      authoritative writer first — a read/write-broker daemon gets the
      same consistency guarantee. `record_cache.store()` seeds/refreshes
      the existing per-process `(mtime_ns, size)`-keyed cache
      (`copilot-extensions#3751`) with a record this process just wrote;
      `tracking._save_record_unlocked` — not only `tracking.save_record`,
      since several callers (the execution-leg CLI's own nested mutation
      lock, `tracking_lifecycle.create_new_record_if_absent`) call
      `_save_record_unlocked` directly to avoid re-acquiring a lock they
      already hold, bypassing `save_record` entirely — calls it right
      after its atomic write, inside whichever lock the caller already
      holds. `tracking.load_record()` now routes through
      `record_cache.cached_load()`, giving every one of its ~140 call
      sites the same benefit without touching each one. Landed in PR
      [#4265](https://github.com/ThomasMichon/copilot-extensions/pull/4265)
      (2 review rounds; see Journal). This does NOT close
      `terminal_conclusion.py`'s `_save_session_conclusion` holdout as a
      write-migration candidate -- it still does not dispatch through a
      daemon verb, and remains listed below as genuinely open for that.
      What this pass DOES confirm: that holdout was always correctness-
      safe (the cache self-invalidates on ANY writer's stat change, verb-
      mediated or direct) and needs no further work to get this same
      read-consistency benefit -- the two are separate axes.
- [ ] One call site (or a closely related cluster) at a time, each its own
      reviewable PR, per this repo's serial-single-writer convention —
      across whichever of `tracking.py` / `tracking_claims.py` /
      `tracking_lifecycle.py` / `tracking_session_registry.py` currently
      owns each function. `status_disposition_write` is the template to
      follow for the next call site (a whole guarded transaction becomes a
      registered verb in its own module; the call site dispatches and
      handles `AmbiguousWriteOutcome`; any ambient-context read inside the
      transaction — project, tracking dir, env vars — must be resolved by
      the CALLER and passed as an explicit arg, never read inside the verb
      itself). **Do design-survey-before-implementation** for any
      remaining candidate touching `tracking_lifecycle.py`/
      `tracking_session_registry.py` — several of their write functions
      are embedded in bigger orchestrated flows or hot hook paths, not
      standalone CLI transactions; confirm a call site is genuinely
      self-contained (one dedicated CLI command, one lock, no shared
      choreography) before wrapping it as a verb, UNLESS the whole
      enclosing transaction is verb-ified together (as `register_session`/
      `deregister_session` now demonstrate) -- narrower extraction from
      inside a bigger transaction is the actual hazard, not verb-ifying a
      hot path per se, and don't assume a lock-scope "improvement" is
      free without checking what the existing ordering protects.
      Remaining candidates: the one still-deferred EMBEDDED call site
      (`terminal_conclusion.py`'s `_save_session_conclusion`, one step
      inside a bigger single-lock cascade with two separate `save_record`
      calls around it; `register_session`'s own internal `link_handoff`
      call is now moot -- it moved into the `session_register` verb
      itself, per the option above). Still genuinely open for THIS
      Phase's write-migration purpose (it does not yet dispatch through a
      daemon verb) -- but the separate read-side cache-push work above
      already covers it for read-consistency, since that cache-push is
      universal at `_save_record_unlocked` regardless of which caller
      reaches it, verb-mediated or still-direct.

### Phase 4 — Sibling-plugin guard + audit _(in progress)_
- [x] Add the CI guard described above:
      `tools/check-no-sibling-tracking-writes.py` (mirrors
      `check-no-agent-machines-packages.py`'s structural-invariant style),
      an AST-based scan of every `plugins/*` sibling plugin (excluding
      `agent-worktrees` itself) for a direct import/call of a
      hand-curated denylist covering every tracking-record write function
      across 12 modules (55 functions total: `tracking.py` /
      `tracking_lifecycle.py` / `tracking_claims.py` /
      `tracking_session_registry.py` / `tracking_controller_relations.py`
      / `tracking_write.py` / the 6 `tracking_*_write.py` verb-handler
      modules). Twenty review rounds found real gaps each time (see Journal
      for the full blow-by-blow: a missing re-export module, a
      package-root-alias attribute chain never tracked at all, an
      incomplete denylist, a wildcard-import escape hatch, the daemon's
      own verb handlers being an equally-importable write surface, a
      fail-open exception path on unreadable/unparseable files, two
      private read-modify-write helpers, `tracking_write`'s own
      generic direct-execution APIs, a trivial-reassignment alias
      evasion, and two documentation-accuracy findings) -- every one
      fixed with a dedicated regression test, none assumed away. Wired
      into CI alongside `test_check_no_sibling_tracking_writes.py` (28
      tests). **Scoped to every `plugins/*` directory except
      `agent-worktrees` itself** (not filtered by an `agent-*` name
      prefix -- this repo ships several non-`agent-*`-named plugins too)
      -- deliberately does NOT cover
      `worktree-manager/` (a separate, non-plugin, out-of-plugin
      control-plane app), which already imports several of these same
      write functions directly today via its own in-process
      `_engine_runtime` bridge
      (`stamp_bound_live`/`stamp_mux_live`/`stamp_session_state` in
      `production_picker/picker_tui/data_local.py`) -- a KNOWN,
      already-tracked gap (see `efforts/active/worktree-manager-control-
      plane/README.md` Phase 3d's own "Design the `data_local.py` hot
      path" TODO, explicitly sequenced after that effort's Phase 3c), not
      an oversight this guard should silently flag or fold in without that
      separate effort's own coordination.
- [ ] Re-run the cross-plugin survey from this effort's Context to confirm
      it still finds zero direct writers, now backed by an enforced check
      instead of only a point-in-time grep.

### Phase 5 — Full cutover + validation _(not started)_
- [ ] Confirm no code path (inside agent-worktrees or any sibling plugin)
      still opens a tracking YAML file directly for a write.
      `direct-read-is-a-degrade-not-a-peer` reads remain sanctioned;
      writes do not.

### Phase 6 — Consolidate `classify_daemon` + `worktree_status_compute` into one compute path _(in progress — 6b/6c merged; 6d open)_
_(Added 2026-10-06, operator-directed formal follow-on from
`pivot-streaming-transport`. Addresses the Worktrees-pivot half of
[`ThomasMichon/copilot-extensions#5555`](https://github.com/ThomasMichon/copilot-extensions/issues/5555):
`_classify_records`'s resident daemon fast path (`classify_daemon.py`,
no-fetch, coalesced) and this effort's own `worktree_status_daemon.py`
(`worktree_status_compute.py`, fetch-confirmed, TTL-cached) independently
compute overlapping git-state facts for the same worktree. The **production**
Worktrees-pivot route is `worktree-manager`'s own
`production_picker/picker_tui/data_local.py` →
`engine_client.list_worktree_rows()` → `agent-worktrees`' `list_cli.py`,
which *does* pass `daemon_filters` into `_classify_records` (`list_cli.py`
line ~344) and stamps the result — not
`plugins/agent-worktrees/picker_support/data_local.py`'s own
`_classify_records(records, session_ctx)` call (no `daemon_filters`, line
~359), which is a *different*, non-daemon caller. `worktree_status_compute`'s
`git_state` fact is consumed separately, by
`agent-dispatch/worktree_status_relay.py`. Whether/how these two consumers'
answers can actually diverge against the same worktree — and whether that
is the row-oscillation issue #5555 describes, or a distinct risk — is not
yet traced; 6a establishes that before any consolidation work starts.

**Additional concrete findings for 6a's own trace (2026-10-07, operator
report of the live oscillation symptom — `MERGED` → `WIP`/`ACTIVE` →
`MERGED`)**, incorporated into 6a's checklist below but not yet
investigated/resolved, found while confirming the symptom was real rather
than hypothetical:
- `worktree_manager.engine_client.current_worktree_status()`'s own
  docstring self-reports as **"the status bar's own non-daemon classify
  pass"** — a third, separate compute path beyond the two 6a already names,
  worth tracing alongside them for whether any Worktrees-pivot row render
  (not only the status bar this docstring names) ever reaches it while the
  daemon is reachable.
- `worktree_manager.production_picker.picker_tui.data_local._overlay_cached_state()`
  has the shape below, but repository usage shows it is called **only by
  tests** — the actual production cache-only path is
  `engine_client.list_worktree_rows(..., cache_only=True)` →
  `agent_worktrees.list_cli` →
  **`agent_worktrees.picker_support.data_local._overlay_cached_state()`**
  (a second, separate function of the same name in a different module —
  trace this one, not the unused `worktree_manager` copy, or 6a could
  incorrectly rule out a live override that's actually running). It trusts
  `rec.git_state` (the daemon-stamped value) and then
  **unconditionally overrides it** — `if live: raw["state"] = "active"` —
  whenever a *different* fact (`session_bound_live`/a live
  `inuse.<pid>.lock`) is true, independent of whether git state actually
  changed. Its own docstring documents this as intentional design for the
  cache-only **fast pass** specifically ("a live bound Copilot -> ACTIVE,
  authoritative in the fast pass -- wins over a cached terminal/unknown
  state") — i.e. a deliberate precedence rule for one render phase, not an
  oversight; whether that rule itself is what produces the *visible*
  oscillation (reconciling against the subsequent `classify=True` pass's
  own value) is exactly what 6a's trace needs to confirm or rule out. This
  does not substitute for 6a-6c's own consolidation outcome even if it
  turns out to be the dominant visible cause** — #5555 and this phase
  require one authoritative compute path, not merely a quieter symptom; a
  narrow overlay fix may legitimately land first (it addresses a real bug
  regardless), but it closes neither 6a's own trace nor this phase's
  closing claim on #5555 on its own.)_
- [x] **6a — Design sub-pass (do this first, in its own PR per this effort's
      own Phase 1 precedent):** first, trace both consumers' actual
      dataflow — the **production** Worktrees-pivot path
      (`worktree-manager/production_picker/picker_tui/data_local.py` →
      `engine_client.list_worktree_rows()` → `list_cli.py`'s
      `daemon_filters`-passing call, confirmed above), not the non-daemon
      `plugins/agent-worktrees/picker_support/data_local.py` caller; and
      `agent-dispatch/worktree_status_relay.py`'s status-bundle consumer —
      to establish concretely whether/when they can disagree for the same
      worktree, rather than assuming it. **Also trace the two additional
      findings above** (`current_worktree_status()`'s non-daemon pass, and
      `_overlay_cached_state()`'s unconditional `live` override) as
      candidate direct mechanisms for the reported oscillation — confirm
      or rule out before assuming the full classify/compute consolidation
      is the only fix needed. Then enumerate every fact
      `classify_daemon` computes (`git_ops.WorktreeStateInfo`: `state`,
      `dirty`, `behind`, `ahead`, etc.) against every fact
      `worktree_status_compute.compute()` assembles, and classify each as
      (a) identical/overlapping — a single source should serve both call
      sites — or (b) distinct — stays source-specific. Decide and document
      the consolidation shape: either (i) `classify_daemon` becomes a thin
      no-fetch/fast-mode view onto a generalized `worktree_status_compute`
      (add a `fetch: bool` parameter so the existing fast, coalesced,
      no-fetch call path keeps its latency profile while sharing one
      implementation), or (ii) the reverse — `worktree_status_compute`'s
      fetch-confirmed facts become an opt-in enrichment layered on top of
      `classify_daemon`'s existing fast path. Do not start 6b until this
      design is written down and reviewed (this effort's own repo PR
      review gate) — the two existing call sites have different
      latency/freshness contracts and a wrong default risks silently
      slowing down the Picker's default classify path.

      **Done — see `phase-6-audit.md`.** Traced all three candidate
      compute paths (`classify_daemon`/`_classify_records`;
      `worktree_status_compute`/agent-dispatch's relay; `status-segment`'s
      own inline classify). Confirmed `worktree_status_compute` and
      `status-segment` are uninvolved in the Worktrees-pivot row render (a
      different consumer, and an `active_paths=None` path respectively) —
      the oscillation is not two daemons disagreeing. The classify pass's
      own `active_paths` (built by `_build_active_paths`) already forces
      `ACTIVE` for a genuinely live worktree, so a classify re-run does
      not unconditionally revert to stale git state while a session stays
      live, nor does overwriting `state` in `_overlay_cached_state` matter
      on its own for the lock/bound-live signals — `derive.py`'s
      `_state()` already prioritizes those marker fields over `state`.
      The classify pass's own write-back (`_stamp_from_raw`) is only
      *attempted* exactly once before the subprocess exits (its queue's
      `atexit`-registered flush), not guaranteed to succeed:
      `_apply_session_state_stamp` acquires its per-path record lock
      **non-blocking** and silently skips the write (no retry) on
      contention — a fully code-confirmed, self-contained mechanism on
      its own: a classify pass can compute the correct state and still
      never persist it on an ordinary clean exit, leaving the next
      cache-only paint reading stale data. The cache-only (`--cache-only`)
      payload is separately confirmed to never carry `mux_session`/
      `mux_attached` at all (it calls `_worktree_to_dict` with no
      `mux_info`/`session_ctx`), a secondary, real field-coverage gap
      versus the classify payload. Pinning the *frequency* of the
      contended-write skip in a real Picker refresh cycle (to fully
      confirm the reported recurrence cadence) remains open, requiring
      live reproduction rather than further static tracing — but the
      mechanism itself needs no further confirmation. Decided the
      consolidation shape for 6b: both paths already call the shared leaf
      (`git_ops.classify_worktree`); the genuinely new, narrow seam is one
      level up — `_classify_one_record`'s full wrapper (`active_paths`,
      the classify call, tracking-override closure refinement, and
      session-turn `CONVO` refinement), factored into one function both
      `_classify_daemon_compute` and `worktree_status_compute.compute()`
      call. Neither existing `work_coalescing_singleton` server/rendezvous
      is retired by this shape — the two request granularities (whole-
      project batch vs. single-worktree bundle) remain genuinely distinct
      consumers; only the shared wrapper is new. Scoped a second, separate
      fix (narrow, not part of 6b/6c/6d, gated on confirming the timing
      trigger) to close the mux-visibility gap in the cache-only payload
      by surfacing the tracking record's existing cached `mux_live` hint
      — see the audit doc's Recommendation section — required before this
      phase's closing claim on #5555, per 6d's own "don't let a silent gap
      stand" requirement.
- [x] **6a follow-on — Bound background cache-stamp lock acquisition**
      _(agent-recommended, reviewed in #5659)_: background stamps wait up to
      0.5s per lock layer, fail closed on exhaustion, and report timeout/write
      failures. Synchronous best-effort callers remain nonblocking. Regression
      coverage reproduces cross-process lost stamps, checks foreground-field
      preservation, newer queued stamps, timeout recovery, and actual
      interpreter-exit drainage. This closes the dropped-stamp failure mode,
      not #5555's full repeated-oscillation or daemon-authority acceptance gate.
- [x] **6b — Implement the chosen consolidation**, keeping both existing
      external call-site contracts (`_classify_records`'s `daemon_filters`
      path; `session_tracking_cli`'s `worktree-status` bundle command)
      unchanged in shape — only the shared internal compute path changes.
      **Neither `work_coalescing_singleton` server is retired** (6a found
      the two serve genuinely different request shapes — a whole-project
      batch vs. a single-worktree bundle — so both `classify_daemon`'s and
      `worktree_status_daemon`'s own `CoalescingServer`/rendezvous fields
      in the lock file stay live). The new shared seam is
      `_classify_one_record`'s full wrapper (`active_paths` handling, the
      `classify_worktree` call, `_apply_tracking_override`'s FINAL/MERGED
      closure refinement, and `refine_state_with_session`'s `CONVO`
      refinement), factored into one `fetch: bool`-parametrized function
      that both `_classify_daemon_compute` and `worktree_status_compute
      .compute()`'s own `facts["git_state"]` assembly call — not merely
      sharing the already-shared leaf `git_ops.classify_worktree` call,
      which both paths invoke today with no new seam at all. **Path B
      must keep passing `active_paths=None`** to the shared helper: for a
      live worktree, `classify_worktree`'s `active_paths` short-circuits
      to a zero-valued `ACTIVE` *before* its requested fetch even runs,
      which would silently replace `compute()`'s actual git disposition
      fact with a placeholder — `compute()`'s bundle already has its own
      separate `facts["liveness"]` fact for this, so the shared helper's
      `active_paths` parameter stays an opt-in Path A only passes, not a
      value both callers share.

      Implemented in `worktree_git_facts.compute`: both callers share checkout
      existence, git classification, closure override, and CONVO refinement.
      Lists retain their targeted session scan; bundles use durable
      `session_turns` without adding session/liveness probes to the git fact.
      The root tracking-override adapter deliberately preserves existing
      monkeypatch compatibility. Source hosting is now verified:
      `status_monitor_cli` starts both servers in the same resident process;
      distinct request shapes do not imply independent daemon processes.
- [x] **6c — Structural delegation test proving one shared compute seam,**
      not a same-answer coincidence test: a same-repository-state agreement
      check cannot distinguish "two implementations that happen to agree on
      an unchanged repo" from "one shared compute path" — and would wrongly
      fail after a legitimate freshness divergence (the no-fetch classify
      result's `behind` can genuinely differ from the fetch-confirmed
      bundle's right after a remote ref changes, per 6a's own freshness-
      contract requirement). Instead: once 6a selects the shared seam
      (i or ii above), write a test that patches/mocks *that specific
      shared function* and asserts **both** entry points
      (`_classify_records(daemon_filters=...)` and `session_tracking_cli`'s
      `worktree-status` bundle fetch) invoke it — proving structural
      delegation, not output coincidence. Cover mode-specific freshness
      behavior (the no-fetch vs. fetch-confirmed `behind` divergence case
      above) as a **separate**, explicitly-expected-to-differ assertion,
      not folded into the delegation test.

      `test_worktree_git_facts.py` patches the new wrapper (not the previously
      shared leaf) and reaches both public entry points through real isolated
      coalescing servers and a real bundle cache, forbidding caller fallback.
      A separate local bare-remote scenario advances a ref and proves that
      no-fetch `behind=0` and fetch-requesting `behind=1` are both correct.
- [ ] **6d — Close the remaining snapshot/stream gap, or formally defer it:**
      6b as scoped only shares a compute implementation between the two
      existing *polling* entry points — it does not give the Picker's
      Worktrees pivot the daemon-only initial-snapshot-plus-incremental-
      update stream #5555 asks for (today `_classify_records` still falls
      back to caller-side git computation whenever the daemon is
      unavailable, and `list --json --classify` remains a polled call, not
      `stream`/`subscribe` per `pivot-streaming-transport`'s own contract).
      Either scope and schedule that cutover as a 6d sub-phase here, or
      explicitly narrow this phase's closing claim on #5555 and open a
      tracked follow-on issue for the snapshot/stream cutover — do not let
      6a-6c's completion read as "the Worktrees-pivot half of #5555 is
      done" if this gap is left open silently.

      The chosen continuation is the cutover, not a silent narrowing:
      [Phase 6d execution plan](phase-6d-authority.md) scopes resident list
      authority, a coherent snapshot/held feed, and actual consumer
      row-stability acceptance. It explicitly covers whole-payload cache,
      caller-side session/mux enrichment, and Group C reconcile overlays.
      The execution detail is agent-recommended and must clear review before
      implementation; live-daemon errors never authorize direct computation.
      The plan cleared review and merged as #5748; that planning milestone
      did not complete the authority/feed/consumer-acceptance checklists.
- [ ] **Schema-bounded read-side acceptance:** integrate the coupled
      [current-state contract](../per-worktree-activity-state/schema-bounded-read-contract.md)
      into 6d. Push claims-derived state through the existing write authority;
      no ordinary reader or advisory fallback reconstructs it from ledgers,
      historical arrays, journals, or a fleet scan. Preserve independent
      explicit graph/repair APIs and the staged lifecycle producer gate.
- [x] Update `classify_daemon.py`'s and `worktree_status_compute.py`'s own
      module docstrings to describe the consolidated architecture, so a
      future reader doesn't rediscover this effort's own "two daemons, one
      fact" history as a live bug.

### Phase 6d.3a - Recover slow consumer feed startup

The [consumer recovery plan](phase-6d-consumer-recovery.md) addresses #6832
within the already-requested Phase 6d.3 real-consumer acceptance.

- [x] Review the bounded client startup/reconnect plan before implementation.
- [x] Repair the client deadline/recovery policy and add real-process regressions.
- [ ] Verify promoted, installed default streaming through the real Picker;
      remove the temporary opt-out only after that proof.
- [ ] Journal the result and return the remaining parent acceptance to #3761;
      completing this narrow repair does not complete the whole effort.

<!-- backlog-reconciliation:7556:transfer:4120:to:efforts/active/agent-worktrees-authoritative-daemon/README.md -->

### Direct retained #4120 acceptance: canonical PR state and precomputed Picker consumption

Append this COMPLETE unchecked intake without removing or rewriting any incumbent ROOT
Plan, completed delivery, Validation, Participants, Coordination, dependencies or
append-only Journal. This is finite PR-state tracker consolidation, NOT capability
completion, source reassignment or rollout. Preserve the original and full current
refinement: https://github.com/ThomasMichon/copilot-extensions/issues/4120 and
https://github.com/ThomasMichon/copilot-extensions/issues/4120#issuecomment-6320365714 .

#### Plan

- [ ] P1. Establish one authoritative PR-state meaning across the relevant
  tracking/serialization/read/write surfaces. Classify each apparent duplicate as
  canonical history, compatibility accessor, materialized current projection or
  genuinely competing authority BEFORE migration. Preserve legitimate multiple-PR
  history, active-PR selection, publication/branch/source identity and compatibility; do
  not delete lineage to force a literal single scalar.
- [ ] P2. Route the original relevant create/open/terminal/reconcile/interrupted PR
  lifecycle updates through the existing owning guarded transaction. Bind updates to the
  actual current publication identity, merge provider observations onto fresh state
  under the existing lock, publish atomically and preserve concurrent unrelated changes.
  Unknown/unavailable provider evidence cannot fabricate terminal success or release
  review/PR/resource custody.
- [ ] P3. Reconcile legacy fields and all original relevant writers/readers into that
  single interpretation without a second PR-status store/service. Preserve the owner's
  schema-bounded current projection, per-fact revision/freshness/readiness,
  current-versus-advisory semantics, logged same-code fallback and ambiguous-write
  safety. Compatibility/history access is not a rival ordinary-status authority.
- [ ] P4. Wire the ACTUAL Picker source/loader and PR formatter to owner-precomputed PR
  state without per-worktree provider/query-time process spawning or rival render-time
  computation. Preserve supported legacy compatibility, explicit owner reconciliation
  and user-requested lifecycle actions as separate paths, not hidden render work. Credit
  the existing formatter and resident/held-feed delivery rather than reopening them as
  wholly absent.
- [ ] P5. Preserve every current ROOT/source/PR/review/claim/native/resource owner and
  hold. Keep the streaming-recovery participant's bounded lane, #6086 exact-selection
  reservation and adjacent efforts unchanged. This finite source does not require all
  daemon phases, every pivot, all hosts or unrelated lifecycle redesign to complete.
- [ ] P6. Journal exact source/containing-release/installed-consumer acceptance
  separately. Deliver this COMPLETE direct intake and the actual global Validation entry
  below through a NEW ordinary reviewed PR, obtain actual merge, then read back the
  WHOLE current ROOT before superseding #4120. Old plan creation, title/link/child/Draft
  or existing projection source is not intake delivery; missing clause/custody means
  retain.

#### Validation

- [ ] V1. Verify the real canonical PR representation, active selection, legitimate
  multi-PR history, compatibility fields and current materialized view across the
  original supported consumers. Demonstrate one authority rather than merely removing
  similarly named fields.
- [ ] V2. Exercise the original relevant PR lifecycle and provider reconciliation
  writers, including concurrent foreground updates, partial/interrupted publication,
  numberless/stale legacy state and provider unknown/error. Assert fresh identity-bound
  atomic updates, preserved unrelated fields and truthful source/review/claim
  obligations, not only a single successful locked write.
- [ ] V3. Verify legacy/current read and write compatibility, current projection
  publication and owner generation/freshness/readiness, with uncertain post-send writes
  not silently replayed or transformed into direct competing authority.
- [ ] V4. Through actual Picker source/loader/PR rendering, verify supplied precomputed
  state is consumed and ordinary row formatting/refresh does not spawn per-worktree
  provider processes or independently recompute PR status. Test/update the existing
  accepted consumer contract rather than add a competing poller/materializer.
- [ ] V5. Preserve current, advisory, stale, unknown and unavailable distinctions plus
  owner-directed explicit reconciliation. A cheap snapshot or fetched row cannot grant
  PR mutation, review completion, claim release or cleanup authority. Record exact
  source, release, installed/native subject and evidence limits where applicable.
- [ ] V6. Verify all P1-P6, V1-V6 and C1-C3, the actual global Validation entry, and the
  entire incumbent ROOT remain directly present after NEW reviewed actual merge and
  WHOLE current readback. Resolve only this finite original at its accepted bounds;
  unrelated whole-parent/all-host/native-program completion is not a new gate.

#### Continuing Coordination

- [ ] C1. The existing authoritative-daemon/write-path driver retains integration/PR
  stewardship. Current PR publication/provider owners retain their facts and
  review/claim custody; current Manager/streaming participants retain only their
  admitted consumer/recovery lanes. No claimant, source, resource or native owner is
  transferred by this text.
- [ ] C2. Preserve all current ROOT Plan/Validation/Journal, completed phases, open
  implementation/review/native holds, #3761 and #6086 coordination and existing related
  reservations. New actual assignment or textual hold before publication must be
  preserved/reconciled by its owner, never expired from age, inactivity or an empty
  assignee field.
- [ ] C3. The integration owner obtains NEW normal reviewed actual intake merge and
  WHOLE current readback before source supersession. Product acceptance stays unchecked
  here; no new blanket ACK is added and no original source-specific consent or evidence
  tier is weakened. Missing any direct clause/global validation/custody keeps #4120
  OPEN.

Public delivery foundations:
https://github.com/ThomasMichon/copilot-extensions/pull/3170 ;
https://github.com/ThomasMichon/copilot-extensions/pull/6448 ;
https://github.com/ThomasMichon/copilot-extensions/pull/6595 .

### Direct finite tracking-integrity cases - existing transaction/catalog interfaces

*The following contribution was assisted using Copilot.*

These complete retained source cases extend the SAME Active ROOT, not a new umbrella or
writer. Existing Phases3/6 interfaces and ordinary schema-bounded current/advisory read
guarantees apply. No implementation or operational work is performed by this intake.

#### #2558 - Unreadable record is UNKNOWN, not absent

<!-- backlog-reconciliation:7556:transfer:2558:to:efforts/active/agent-worktrees-authoritative-daemon/README.md -->

Source: https://github.com/ThomasMichon/copilot-extensions/issues/2558. Full original
and eligible refinements remain binding; administrative tracking intake is not
implementation completion.

Credit/qualification: 2521 positively absent allocation handling and current
resolver-error None are credited; supplier unreadability is explicitly excluded from
that delivery.

##### Plan

- [ ] **2558-P1** Expose optional skipped-record signal/count OR an equivalent honest
  exact-ID unreadable/unknown result through list_records -> actual listing/resolver ->
  affected Dispatch presence, live-worktree/orphan and cleanup consumers. Distinguish
  genuine empty/absent from malformed/partial/permission-denied exact record; no silent
  skip into absence.
- [ ] **2558-P2** Unknown never retires a reservation, releases an exclusive key,
  abandons responsibility or authorizes replacement embodiment. Preserve exact
  installation/machine/project/worktree scope and ordinary current/advisory read
  boundaries; a parser-only helper is not end-to-end safety.
- [ ] **2558-P3** Keep assigned1918 attempts and its explicit ground-reader dependency
  separate; the supplier implementation is not taken over by that assignment. Draft
  terminal-reclamation, context-budget5896 and generic settings/plugin-registry absence
  work are not a substitute canonical for this exact contract.

##### Validation

- [ ] **2558-V1** Malformed/partial/unreadable/permission/concurrent record state,
  exact-ID and neutral-CWD/cross-platform filtering preserve attributable UNKNOWN versus
  confirmed absence through actual owning consumers.
- [ ] **2558-V2** Actual downstream typed unknown/hold versus positive absence controls
  leave live/unknown body/task/claim capacity intact without a live probe or destructive
  fixture.
- [ ] **2558-V3** Finite source/read consumer evidence can qualify independently while
  ROOT all-mutator/native/cutover and assigned allocator completion stay separate. No
  damaged-record, presence, claim or process experiment occurs during intake.

##### Continuing coordination

- [ ] Preserve existing source/consumer authorities, claims and obligations; the
  receiving tracker does not reassign implementation/native/resource custody. Complete
  direct receiving acceptance and current readback precede any administrative
  supersession; unresolved fit or custody keeps #2558 OPEN.
- [ ] Retain this case's literal alternatives, qualifications and finite evidence tier.
  This is not a new blanket ACK, all-host/native gate, whole-parent veto or operational
  permission.

#### #2706 - Complete cross-project protective path coverage

<!-- backlog-reconciliation:7556:transfer:2706:to:efforts/active/agent-worktrees-authoritative-daemon/README.md -->

Source: https://github.com/ThomasMichon/copilot-extensions/issues/2706. Full original
and eligible refinements remain binding; administrative tracking intake is not
implementation completion.

Credit/qualification: 2697 same-cell invocation and fail-closed Logger hub protection,
plus6595 bounded per-project current feeds are delivered foundations, not whole-machine
coverage.

##### Plan

- [ ] **2706-P1** Support an explicit complete machine-wide cross-project query OR a
  considered Logger caller-side project enumeration/resolution contract from neutral
  scheduled CWD, with installation/machine/project attribution. A single active/default
  project, status/platform inclusion or correct peer executable cannot substitute for
  all relevant adopted projects.
- [ ] **2706-P2** Aggregate scope/results honestly and keep hub compaction skipping
  unresolved protection. Empty one-project list, unavailable peer or unreadable store
  cannot prove untracked everywhere or enable archive. Do not borrow arbitrary project
  CWD or conflate service-context2701 and semantic-archive5942 work.
- [ ] **2706-P3** Make the multi-project protective inventory an EXPLICIT named consumer
  operation with bounded per-row authority and honest completeness, not hidden
  fleet/graph/history work in ordinary point/advisory status. Preserve
  current-role/project boundaries and existing owning query/Logger interfaces; no
  automatic cross-host registry or new store.

##### Validation

- [ ] **2706-V1** Neutral-CWD legacy and same-cell compaction paths, more than one
  relevant project, explicit scope and missing/unknown/unreadable/partial project
  coverage versus genuine complete results.
- [ ] **2706-V2** Complete protected tracked paths versus confirmed untracked, partial
  coverage/unreadable stores and legitimate no-peer/unavailable results retain correct
  skip/hold and no archive permission.
- [ ] **2706-V3** Actual chosen query OR enumeration consumer shape, source/installation
  isolation and unknown propagation at original finite tier; no full parent
  installation/rollout/native programme becomes its prerequisite.

##### Continuing coordination

- [ ] Preserve existing source/consumer authorities, claims and obligations; the
  receiving tracker does not reassign implementation/native/resource custody. Complete
  direct receiving acceptance and current readback precede any administrative
  supersession; unresolved fit or custody keeps #2706 OPEN.
- [ ] Retain this case's literal alternatives, qualifications and finite evidence tier.
  This is not a new blanket ACK, all-host/native gate, whole-parent veto or operational
  permission.

#### #7097 - Approved exact misplaced-store integrity repair

<!-- backlog-reconciliation:7556:transfer:7097:to:efforts/active/agent-worktrees-authoritative-daemon/README.md -->

Source: https://github.com/ThomasMichon/copilot-extensions/issues/7097. Full original
and eligible refinements remain binding; administrative tracking intake is not
implementation completion.

Credit/qualification: 7068 label repair's receiving-project refusal and existing
uniquely recoverable pair copy retaining legacy record are valid partials, not duplicate
quarantine or orphan disposition.

##### Plan

- [ ] **7097-P1** Report-first select exact source receiving store and record ID;
  resolve actual repository via canonical registration and report duplicate versus
  orphan, authority, references, conflicting metadata and minimum missing provenance
  before any effect.
- [ ] **7097-P2** Only for PROVEN duplicate offer an explicit authoritative
  METADATA-ONLY quarantine/retirement plan with ORIGINAL-BYTE backup and audit. Preserve
  canonical counterpart, checkout, branches, sessions, head, claims/lifecycle and all
  divergent/unsettled responsibility.
- [ ] **7097-P3** Orphan/removed-checkout/missing-parent cases report exact missing
  provenance and minimum reconciliation obligation; never silently
  relocate/delete/fabricate parent or weaken project/platform/valid historical alias
  fences. No broad doctor-fix, lifecycle cleanup or manual YAML removal is
  deduplication.
- [ ] **7097-P4** Bind approval to observed exact inputs, then revalidate under normal
  locks/publication authority; reject changed/ambiguous state. Provide JSON and public
  CURRENT-state readback/invalidation so stale duplicate leaves the correct receiving
  roster, not a runtime patch or service destruction.
- [ ] **7097-P5** Use existing explicit owner-controlled repair/transaction path;
  ordinary O(schema) reads do not gain historical scans or write authority. Keep
  machine-label7057 and paired-copy/handoff owners separate; no second owner-migration
  API or permission for live repair is created.

##### Validation

- [ ] **7097-V1** Proven duplicate, divergent duplicate, removed checkout, missing
  parent and wrong/ambiguous canonical binding remain distinct.
- [ ] **7097-V2** Exact approved duplicate plan, unchanged canonical/physical/source
  state, protected divergent/held records and backup/audit refusal on failure.
- [ ] **7097-V3** Unknown/orphan and foreign receiving-project cases remain
  held/actionable; legitimate verified same-machine aliases and label-repair refusal
  preserved.
- [ ] **7097-V4** Concurrent change, idempotency, approved versus altered plan, exact
  readback/roster invalidation and untouched unrelated records.
- [ ] **7097-V5** Contained exact-record source/consumer regression at original tier; no
  deployment, live record mutation, source/resource handoff or whole-parent/all-host
  gate is introduced by this intake.

##### Continuing coordination

- [ ] Preserve existing source/consumer authorities, claims and obligations; the
  receiving tracker does not reassign implementation/native/resource custody. Complete
  direct receiving acceptance and current readback precede any administrative
  supersession; unresolved fit or custody keeps #7097 OPEN.
- [ ] Retain this case's literal alternatives, qualifications and finite evidence tier.
  This is not a new blanket ACK, all-host/native gate, whole-parent veto or operational
  permission.

#### Retained independent dependency #3585 - NOT received custody

FULL directly required current waypoint names3585 as the existing dedicated
head-succession Phase2, with its own driving participant/coordination and validation.
The chosen authoritative-daemon ROOT is a data supplier, not established acceptance of
the ENTIRE proactive ambient/root-discovery scope. No exact administrative rehoming
authority is evidenced. Retain3585; this is a concrete scope/owner boundary, not a new
universal ACK. 3688 typed direct local holders, bounded ancestry helpers, manual tracing
and same-repo exact effort/slice duplicate checks remain partials. No dependency row
below is a new daemon completion gate. Existing source-specific coupled authority/read
contracts remain.

- **3585-D1 - retained under3585:** Keep3585 responsible for routine cheap pre-work
  duplicate-effort/objective checking in worktree instructions before substantial
  requests, not just an on-demand lookup; refer to existing driver without suppressing
  legitimate concurrently declared slices. **Existing-owner qualification:** Actual
  ambient workflow and existing-driver discovery, not mere command presence.
- **3585-D2 - retained under3585:** Report the actual CURRENT root responsibility rather
  than nearest/recent leaf or historical creator after accepted transfer.
  **Existing-owner qualification:** Current responsibility and root-versus-direct
  distinction through admitted scope.
- **3585-D3 - retained under3585:** Typed kind/ref or supported equivalent finds ALL
  qualified matching direct holders; report direct and root separately. **Existing-owner
  qualification:** Positive direct-owner and multiple matching-holder cases.
- **3585-D4 - retained under3585:** Bounded inspectable creator/responsibility chain
  with machine/repo/worktree/session qualification, current held/transferred
  claim/effort evidence; ancestry alone is not current custody. **Existing-owner
  qualification:** Multi-hop and transferred responsibility, source/identity boundaries.
- **3585-D5 - retained under3585:**
  Multiple/cycle/depth/conflicting/unreadable/skipped/remote-unavailable/out-of-band
  cases remain incomplete/ambiguous; empty local lookup is not global owner absence and
  recency/bare-ID cannot fabricate root. **Existing-owner qualification:** Unknown
  record, cycle, same-ID/different-scope and bounded incomplete cases.
- **3585-D6 - retained under3585:** Reuse actual registry/provider/claimant-graph scope
  with coverage named; inspection does not impersonate remote authorization or bypass
  cell/project/source restrictions. **Existing-owner qualification:** Scope and actual
  public-interface boundaries, not another ledger.
- **3585-D7 - retained under3585:** Surface root, chain/evidence and coordination route.
  Root/leaf liveness is separate observation, not a grant; no age expiry, reclaim,
  release or #912 reservation change. **Existing-owner qualification:** Readable
  qualified report without ownership/liveness conflation.
- **3585-D8 - retained under3585:** Keep original
  direct/root/multi-hop/multiple/transferred/unknown/cycle/same-ID consumer regressions
  under3585's existing owner when authorized. Daemon may supply data but no receiving or
  implementation custody is transferred here. **Existing-owner qualification:** Original
  finite report/guidance test tier and explicit graph inspection, never reconstruction
  hidden in ordinary row status or a new universal native trial.

#### Continuing coordination for these finite interfaces

- Retain the SAME #3761 driver, current guarded-writer/catalog/inspection/repair and
  affected Dispatch/Logger consumers. Preserve schema-bounded ordinary reads,
  source/project/installation authority, durable-before-ACK, same-handler
  confirmed-pre-send fallback and no replay after ambiguous execution.
- Keep ALL incumbent ROOT bytes, 29 checked and32 unchecked rows,
  Participants/Coordination/Plan/ACTUAL level-two global Validation, full inline and
  linked historical Journal, #4120 direct intake and current #3761 inherited
  durable-state/catalog/CPU acceptance. Existing source/native/adopter/producer/exporter
  reservations retain exact scope.
- Before any accepted-source supersession obtain NEW normal reviewed ACTUAL merge of the
  COMPLETE direct local Plan/local Validation/continuing Coordination, EACH actual ROOT
  global entry and dated Journal. Then fully read back the WHOLE immutable/current ROOT,
  ALL linked history, exact source and #3761 current
  author/state/lock/assignees/eligible discussion/actual holds. Old
  creation/title/link/child-only text or this proposal is not receipt.
- If current Active fit, exact repair/read boundary, any unique clause/tier or actual
  custody cannot survive complete receiving acceptance, RETAIN the source. Finite case
  qualification does not wait for unrelated all-mutator/native/whole-parent completion;
  the parent still keeps its own entire completion gate.


## Validation Plan

### Direct finite tracking-integrity acceptance

- [ ] **GLOBAL-2558** Qualify ALL direct #2558 Plan/local Validation/continuing
  Coordination clauses above through their actual owning interfaces:
  Malformed/partial/unreadable/permission/concurrent record state, exact-ID and
  neutral-CWD/cross-platform filtering preserve attributable UNKNOWN versus confirmed
  absence through actual owning consumers. Actual downstream typed unknown/hold versus
  positive absence controls leave live/unknown body/task/claim capacity intact without a
  live probe or destructive fixture. Finite source/read consumer evidence can qualify
  independently while ROOT all-mutator/native/cutover and assigned allocator completion
  stay separate. No damaged-record, presence, claim or process experiment occurs during
  intake. Preserve literal alternatives, current source/custody and finite evidence
  tiers; no unrelated whole-parent/native prerequisite. Partial fixtures/source or
  historical provider credits are not whole completion.
- [ ] **GLOBAL-2706** Qualify ALL direct #2706 Plan/local Validation/continuing
  Coordination clauses above through their actual owning interfaces: Neutral-CWD legacy
  and same-cell compaction paths, more than one relevant project, explicit scope and
  missing/unknown/unreadable/partial project coverage versus genuine complete results.
  Complete protected tracked paths versus confirmed untracked, partial
  coverage/unreadable stores and legitimate no-peer/unavailable results retain correct
  skip/hold and no archive permission. Actual chosen query OR enumeration consumer
  shape, source/installation isolation and unknown propagation at original finite tier;
  no full parent installation/rollout/native programme becomes its prerequisite.
  Preserve literal alternatives, current source/custody and finite evidence tiers; no
  unrelated whole-parent/native prerequisite. Partial fixtures/source or historical
  provider credits are not whole completion.
- [ ] **GLOBAL-7097** Qualify ALL direct #7097 Plan/local Validation/continuing
  Coordination clauses above through their actual owning interfaces: Proven duplicate,
  divergent duplicate, removed checkout, missing parent and wrong/ambiguous canonical
  binding remain distinct. Exact approved duplicate plan, unchanged
  canonical/physical/source state, protected divergent/held records and backup/audit
  refusal on failure. Unknown/orphan and foreign receiving-project cases remain
  held/actionable; legitimate verified same-machine aliases and label-repair refusal
  preserved. Concurrent change, idempotency, approved versus altered plan, exact
  readback/roster invalidation and untouched unrelated records. Contained exact-record
  source/consumer regression at original tier; no deployment, live record mutation,
  source/resource handoff or whole-parent/all-host gate is introduced by this intake.
  Preserve literal alternatives, current source/custody and finite evidence tiers; no
  unrelated whole-parent/native prerequisite. Partial fixtures/source or historical
  provider credits are not whole completion.

#3585 remains an independently OPEN existing discovery/guidance responsibility, not an accepted-source or new daemon completion gate. All incumbent global Validation rows remain unchanged.

- [ ] Complete EVERY directly retained #4120 P1-P6, V1-V6 and C1-C3 clause above at its
  original finite scope/evidence tiers, preserving incumbent Plan/Validation/Journal,
  compatible PR history/current semantics and all owners/claims/holds. Tracker
  consolidation is not implementation or installed/native acceptance; NEW normally
  reviewed actual merge and WHOLE current ROOT readback precede source supersession.


- [ ] Meet the coupled [O(state-schema) validation](../per-worktree-activity-state/schema-bounded-read-contract.md#validation-plan)
      while ledger, history, graph and fleet cardinalities grow independently.
      A shared compute implementation, TTL or small returned payload alone is
      not a pass for this read-side requirement.
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
- [ ] Phase 6: 6c's structural delegation test passes (both entry points
      provably invoke the one shared compute seam, with mode-specific
      freshness divergence covered as a separate expected-difference
      assertion); 6d's gap is either closed (daemon-only snapshot/stream
      cutover shipped and verified) or formally narrowed/deferred to a
      tracked follow-on issue, never left silently open; and — for whatever
      scope 6d actually closes — the Picker's rendered Worktrees-pivot row
      for a sampled worktree does not visibly flip `state` across repeated
      refreshes with no real underlying git change — the literal acceptance
      bullet from `ThomasMichon/copilot-extensions#5555` ("No pivot's row
      state can visibly oscillate between two different values for the same
      underlying fact with no real change having occurred"), checked here
      for the Worktrees pivot specifically.

## Proposal

Phase 1's design and all three open questions are now resolved (see Plan
above). Pending: this repo's automated PR review on the resolution PR, and
confirming `module-componentization-discipline`'s `tracking.py` split has
reached a stable resting point before Phase 2 actually starts cutting code.

## Journal

### 2026-10-24 - Direct finite tracking-integrity intake

- Plan the COMPLETE #2558 unreadable-versus-absence, #2706 cross-project
  protective-query and #7097 exact-record integrity cases directly in this SAME ROOT
  Plan, local Validation/continuing Coordination and its ACTUAL global Validation.
  Preserve every incumbent byte, checkbox state, historical result, #4120 family,
  current #3761 inheritances and actual owner/claim.
- #3585 and ALL inherited #3558 ambient/direct-to-root discovery remain independently
  OPEN in the existing dedicated head-succession Phase2. Their complete dependency
  reference does not rehome custody or satisfy receiving acceptance.
- This is proposed administrative tracking intake, not implementation, native/product
  completion or permission to query/repair operator state. NEW normal review, ACTUAL
  merge and FULL CURRENT ROOT/history/source coordination readback precede any
  accepted-source supersession; record the real receipt only after it exists.
- Existing domain/source/native/resource custodians continue the next unfinished
  admitted Plan item. The ROOT remains Active; no unrelated whole-parent prerequisite or
  waiver is introduced.

- [2026-10-24 - Direct retained acceptance from #4120](<journal/2026/10.24 backlog-intake-85-86.md>)

### 2026-10-15 - Phase 6d.3a plan reviewed; client repair acceptance in progress

Plan #6834 merged as `d959de8511ccba374af02e2e804d4b52cc113ecd` after
current-head Copilot approval with zero findings and successful required CI.
The bounded #6832 repair uses the existing source-startup budget, starts the
unchanged transaction deadline only on admitted publication barriers, and
retries transport watchdog expiry through the existing cancellable backoff.
Explicit terminal peer errors remain terminal; no polling authority is added.

Five real-child regressions failed against the old loader; the corrected
consumer selection passes 165 tests with one platform skip, including a
separate incomplete-initial-publication guard. Touched Python lint and the
installation-contract guard pass. A native Windows production-Picker probe
against the selected installed engine admitted current rows under a held
feed with no local source error. The candidate Manager source was explicitly
identified; this is source-consumer evidence, not promoted installed acceptance.
Implementation review, normal promotion and authorized installed acceptance
remain open in the [bounded recovery plan](phase-6d-consumer-recovery.md).
The remaining parent phases, resident reports and other pivots are unchanged.

Review exposed an additional partial-line boundary: terminating a stalled
child could make `readline` return an unterminated fragment before the
retryable watchdog outcome was examined. A new real-child regression
reproduced that terminal misclassification. The loader now checks cancellation
and expiry immediately after the blocking read, preserving malformed-output
refusal when no watchdog expired. Recovery, genuine malformed output and
partial-line cancellation all pass in the updated selection above.

### 2026-10-14 - Phase 6d promoted; signed publication and consumer acceptance next

PR #6595 merged as `b4dadce47c97adb0c4bdd1a5236cfdeaf83016f1` after
current-head Copilot review `5513584244` reported zero findings, all eleven
threads were resolved, and required CI `38516933011` passed. The bounded
resident projection, held CLI feed, mandatory local/SSH consumer path and
review corrections below are now landed source, not installed acceptance.
The containing main promotion recorded below completed separately.
Successful signed-artifact publication and installed acceptance remain open.

Re-read the Phase 6d and coupled read-side gates after merge. Source
implementation is no longer the next stretch: continue Phase 6d.3's real
generation/row-stability, platform, adoption and consumer proof. The parent
6d, schema-bounded acceptance and whole-effort completion boxes remain open;
separately owned Phases 1-5 and other pivots are not closed by this PR.
Natural dev CI `38520558555` was superseded; successor `38520905507`
passed on `d8ede62eed83a603af51a1af8ddfb1fd1224b20f`, one commit ahead of
the merge above. Promotion `38519804724` independently checked out the exact
merge for its successful full Manager validation. Release #6642 then merged
as `f327cf44793c60321bc205eddadccaeda3d5ee43`; its post-merge signed
publication failed at release-state removal in the older trusted workflow
checkout. The reviewed publisher repair is now on main, but successful hosted
publication remains required; neither the merged release nor the
workflow-carrier SHA alone proves that gate.

### 2026-10-14 - Phase 6d publication, ordering and adoption review corrections

Review of #6595 exposed three availability/provenance gaps: the early mux
failure path bypassed producer-failure notification, cold publication failure
hid its actionable reason, and the Manager admitted contradictory advisory
availability on current-quality rows. The corrected paths mark published and
demanded projects explicitly unavailable/stale and enforce quality/status
agreement. A later review found ordinary creation and yielded-head scalar
fields missing from the bounded schema; both are now explicitly typed rather
than dropping current identity or resumability semantics.

Successor promotion now refuses adoption if current-roster activation fails;
the unpublished successor retains retry state and cannot sweep before activation.
Held updates carry committed identity order in bounded 128-ID frames. Both
receivers require the exact final identity permutation at commit, preserving
reorder-only publications and front insertion without resending unchanged rows.
The real TCP relay covers 300-row reorder/insertion, while the actual local
and SSH loader children assert the same committed ordering.

Current evidence: 44 publication/producer/schema cases pass; the affected
resident/feed matrix has 231 passes and one platform skip, with its strict
1100-row CLI child missing the unchanged two-second first-load budget on two
attempts and passing its isolated retry. Failure diagnostics now retain the
bounded child error/exit state instead of reporting only an empty queue.
The final held-feed/adoption rerun passes all 29 cases, including the unchanged
strict load assertion. The activation/adoption subset passes 13 cases, and the Manager current-feed,
current-roster and SSH-source subset passes 156 with one platform skip.
The timing variability is not a live performance pass or a reason to raise
the budget. Normal promotion, deployment, installed full-roster timing,
rendering and CPU/preservation evidence remain required.

The next review caught the typed non-Picker consumer rejecting integer dirty
counts greater than one. Its Boolean indicator now accepts nonnegative counts
while preserving the exact count in the raw current row and rejecting negatives.
Provider-exec claim inspection now negotiates a supported read-only capability:
the Manager registry admits `claims`, and the container source descriptor
advertises it. Existing live instance, target, lease and transport checks still
gate the explicit read; mutation capabilities remain disabled. The changefile
also covers that provider payload. Manager typed-client, source and detail
contracts pass 206 with one platform skip; provider SSH contracts pass 28 with
two platform skips. The older README and CLI descriptions now consistently
state the mandatory current-authority/upgrade contract.

A subsequent review alleged that the second of two owned read-only hardlinks
fails against a stale cached count. The exact shared-cache callback sequence
does not reproduce that claim: the last link has `st_nlink == 1` and skips the
multi-link comparison. Native complete cleanup also passes with two, three
and sixteen read-only hardlinks across separate directories; chmod clears
the shared file's read-only attribute. The runner contract suite passes 62
with one platform skip. Added these regressions without weakening ownership
checks or inventing a counter change for an unobserved failure.

The next review found a genuine machine-SSH argv corruption: the encoding-aware
transform visits every argument, so appending `--follow` modified the executable,
SSH options and alias as well as the remote command. It now replaces the
existing `--stream` flag inside the remote command, including encoded PowerShell.
Both Bash and PowerShell regressions assert that every executable/option/alias
argument remains byte-for-byte unchanged and that the decoded remote command
adds exactly one held-feed flag. The full current-feed/current-roster/SSH-source
selection passes 158 with one platform skip; whole-tree Ruff and all 29 changed
source-module ceilings pass. This is source evidence, not deployed SSH proof.

The producer failure boundary now marks every ordinary exception stale before
re-raising to the monitor's logging boundary, including SQLite operational and
lookup failures formerly outside its explicit exception tuple. Real owner
serialization/publication tests retain the last-good row and notify held
readers of the same failure; 54 producer/runtime/projection cases pass.
The detail card no longer promises a provider error after closing when the
quiet background contract cannot deliver one. Its existing visible failure
state remains explicit; 50 detail/current-roster/feed cases pass.

Snapshot admission now explicitly rejects non-object decoded frames before
field access. Scalar/array cases before and inside a publication barrier, plus
the actual one-shot loader error path, verify a visible failed source rather
than an uncaught thread exception. The current-roster/feed/SSH selection passes
171 cases with one platform skip.

Authority startup now always attempts elected activation: absence of the active
route is a failure, not permission to skip activation and acknowledge adoption.
Both missing-election and SQLite-failure regressions prove refusal followed by
successful retry. Existing monitor fixtures now provide an explicit elected
route and bounded retirement; all 205 monitor cases pass with one platform skip.
Held commits advance the presentation clock and refresh relative age, last-used
and pulse fields even when facts are unchanged. The shared normalizer uses the
same helper, while unchanged factual/asset presentation is still reused. A
controlled-clock regression covers reused and changed rows without full
renormalization of the former; 175 current-feed/source/pulse/recency cases pass
with one platform skip.

The relay now preserves structured reconnect policy across its CLI error
boundary. Disconnect/unready failures remain retryable; live-peer timeout,
malformed transaction, incompatible capability and authentication errors stop
automatic Manager child respawn. The Manager validates the Boolean policy and
treats its own watchdog as terminal. Real child tests assert a single launch,
visible failure and complete owned-process cleanup for terminal frames;
existing graceful/crash reconnect cases still pass. The relay/client selection
passes 55 cases and the Manager current-roster/feed/source selection passes
178 with one platform skip. No daemon-recovery or deadline policy was relaxed.

### 2026-10-13 - Phase 6d.2 held feed and actual loader enrollment

The existing authenticated status-monitor control endpoint now offers the
versioned held feed. Subscriber admission precedes initial publication capture;
monotonic project-revision cursors and complete snapshot/update barriers fence
incremental upserts and deletions. A subscriber retains only one notification
stamp and its last committed roster, so bursts coalesce against the latest
complete publication rather than queueing historical payloads. Admission is
bounded to 32 feeds. Heartbeats do not decode unchanged rosters. Producer
availability changes are explicit and preserve current rows.

Planned drain, shutdown and supersession emit a reasoned going-away notice
before release. Feed demand and response lifetime participate in monitor
accounting. Independent pre-submit review found two consequential lifecycle
gaps: a retained crashed endpoint did not invoke bounded recovery, and ordinary
point responses were not included in drain. Both are corrected and covered by
regressions; slow/malformed live peers still cannot trigger recovery or compute.
The existing installer/activation path owns cutover, with no new daemon or
deadline/lease increase.

The CLI's additive `--follow` negotiates the feed capability and validates full
transactions before relaying stdout. Default local and machine-SSH Manager
loads now hold that CLI boundary, keep stale last-good rows during reconnect,
and atomically admit a complete replacement generation. Complete updates
reuse unchanged presentation rows instead of re-normalizing all facts.
Periodic polling/reconcile cannot overwrite an active feed. Explicit one-shot
compatibility remains current-authority-only. Demo and legacy test fixtures
now honor the same complete envelope/barrier or explicitly choose one-shot
mode; unsupported held capability remains an upgrade error.

Windows contained evidence: the final affected engine matrix passes 498 cases
with two platform skips after the named review fixes; the corrected runtime/feed
matrix passes 39, including a real CLI child loading all 1100 published rows
under two seconds, receiving one changed-row commit and a cutover notice.
Focused Manager contracts pass 148 with one platform skip; all 12 coupled
full-suite failures pass after updating their authoritative fixtures/demos.
The full Manager run had 2040 passes, 13 skips and 13 failures, not a pass:
its remaining independent tar-symlink fixture mismatch was explicitly cleared
by that owner and repaired as a native Path comparison at the fixture seam.
The complete Manager portfolio now passes 2054 cases with 13 platform skips
across two complementary contained selections, without omissions or raising
the existing per-run budget. The runner's Windows temporary Git-object cleanup
refusal needed a separate bounded correction: restore write permission only
for regular read-only files within the owned sandbox, with every hardlink
accounted for there. Symlinks, outside targets and permanent failures do not
gain a permissive fallback. The cleanup's 15-second sharing retry is unchanged.
Its contained contracts pass 46 cases with one platform skip, and the exact
native Git-object failure plus feed/fixture regressions pass 19 with clean
runner exit. An initial abrupt-disconnect fixture incorrectly slept instead
of crashing; it now actually exits and proves that separate reconnect path.

Documentation impact: the engine/Picker contract and Manager README now
describe held barriers, bounds, explicit recovery/availability and lifecycle;
the testing guide records bounded cleanup and complete-portfolio partitioning.
The existing changefile covers both payloads. This is realization of the
reviewed Phase 6d direction, not a new intent revision. Publication, platform/
external coverage, live daemon generation and consumer stability, promotion,
unified rollout and actual latency/CPU acceptance remain open. No installed
or live-roster success is asserted by these source fixtures.

### 2026-10-13 - Phase 6d.1 resident serving and ordinary reader enrollment

The current roster now runs inside the existing resident monitor. Its
authenticated control surface advertises a versioned capability, reads only
published rows, and reports unready, draining, incompatible or unavailable
state explicitly. Election is rechecked inside the ownership transaction;
an unelected late initial activation cannot steal a newer publisher, and one
bounded CAS retry handles a genuinely raced predecessor commit. Monitor
publication reuses the existing serializer and git/session/mux reductions,
captures its project/repository explicitly, and reports failed publications
while retaining labeled last-good rows. Unchanged identity/filter indexes
are neither rebuilt nor rewritten for fact-only publications.

Ordinary production JSON and one-shot stream reads select that authority.
The Manager requires `--current` locally and remotely; optional flag retries
cannot remove it, and point refresh cannot backfill or enumerate the fleet.
Local Group C reconcile no longer overwrites current rows. The CLI uses
existing owner authentication without minting a token, enforces a total
request deadline and bounded point response, and permits only independently
indexed advisory state after failure. The wire preserves UTF-8 byte bounds
rather than expanding valid rows past them through ASCII escaping.

Both ordinary envelopes and one-shot streams carry selected-row counts.
The Manager admits only a complete publication barrier, rejects mixed
revisions, duplicates, historical collections and truncated responses, and
labels advisory/stale rows without changing their stored state. This is
not a claim that the held incremental feed is implemented.

Validation: 474 focused engine cases passed with two platform-dependent
skips; the remaining new producer fixture needed an explicitly configured
project and passes separately after that correction. The focused Manager
suite passes 236 cases with one platform-dependent skip. Current-component
Ruff, changed Python syntax lint, staged module ceilings, whitespace and all
13 install contracts pass. These are Windows contained/transport checks,
not installed, live-roster, Linux/macOS or external-venue acceptance.
Documentation impact: the engine/Picker contract records the authority,
availability, bounded-row and one-shot barrier changes; the existing
changefile covers both runtime payloads. This realizes the already-reviewed
authoritative-daemon direction, without adding a daemon or changing installer
ownership. Held-feed drain/notice, live stability, promotion, rollout and
latency/CPU acceptance remain open; continue the parent rather than treating
this reader enrollment as completion.

### 2026-10-13 - Phase 6d.1 current claim aggregates and explicit detail inspection

The owner-side adapter now reuses the engine serializer in a current-only
mode: full resource/PR/profile histories stay out, while the active PR and exact
counts remain. Held-claim type/count aggregates preserve the Manager's existing
active/at-rest presentation; ranked links have an explicit total/completeness
marker and bounded preview. Current materialization surfaces registry/ranking
errors rather than silently publishing an empty claim summary. Exercising the
real serializer, including 1,000 held claims and git dirty-file counts, exposed
and corrected the foundation's mistaken boolean type for `dirty`.

The Manager accepts those aggregates without visiting a ledger. View details
opens immediately with the held count and loads the full list through its
existing background/Inbox path only on explicit demand; failures stay visible,
and the full list never flows back into ordinary cached rows. Local reads use
the selected provider and exact worktree identity. Remote reads retain the
source incarnation/assignment/transport checks, sharing the existing session
detail-query admission path rather than introducing a second transport.
The detail screen and query helpers were split at their natural seams to
preserve the repository's source-module ceilings.

Validation: 50 engine cases pass (projection, current materialization and
existing serializer semantics); 137 Manager cases pass, with one existing
platform-dependent skip (source routing, asset presentation and native detail
loading/error paths). The strict 1,100-row native viewport cases still pass
all eight unchanged 200 ms gates, with a maximum of 149.54 ms including GC.
These are source/contained checks, not installed or live-roster acceptance.
The resident producer, authenticated projection serving and ordinary consumer
read routing are still unwired; neither this chunk nor a repaint result closes
the load-under-2-second requirement or Phase 6d.

### 2026-10-13 - Phase 6d.1 indexed selections and publisher fencing

The storage seam now materializes status/platform/checkout-presence selections,
exact-before-unique-suffix aliases and case-sensitive codenames on publication.
Duplicate codenames still fail before selection filters, while suffix ambiguity
is evaluated inside the selected roster, matching the existing CLI semantics.
Both resident and advisory point reads decode one addressed row; durable alias
resolution uses the SQLite primary-key index, not a fleet scan. Ambiguity reports
retain the exact count with a bounded preview rather than expanding every match.
Checkout presence is an explicit producer fact; an unenrolled presence filter
is unready, not a silently shortened roster.

An explicit compare-and-swap generation handover fences the durable publisher.
Constructing another store, reading advisory rows or reusing a generation cannot
take ownership. A late prior-owner publication is refused and its resident view
is closed; monitor election, request admission and planned drain still own the
runtime transition. The contained storage/selector/fencing suite passes 33
cases, including 1,100 unrelated rows with corrupted payloads and unchanged
point decode counts. This remains unwired source foundation: no installed
latency, producer completeness, authenticated transport or consumer acceptance
is claimed. The Phase 6d and parent completion gates remain open.

### 2026-10-13 - Phase 6d.1 current-roster storage foundation

Taking the reviewed resident-projection slice under #5555, separately from
#6086's warm-pass/cross-cycle enrichment work. The initial source storage seam
accepts explicitly declared, typed and size-bounded current fields, not whole
tracking records, ledgers or session/handoff/PR history. Complete project
publications replace rows atomically, including deletes; exact point reads
decode only that independently addressed slot. The resident view is in memory;
the SQLite projection is advisory after restart and is never promoted to
current merely because it exists. Missing, invalid, oversized and incomplete
publications fail explicitly instead of fabricating an empty successful roster.

This is a storage foundation, not a completed authority or latency fix.
Producer enrollment, current claims summaries preserving the Manager's asset
presentation, owner/generation fencing, filter and suffix/codename indices,
authenticated capability-negotiated transport and consumer routing remain
open. No runtime or ordinary read uses this new seam yet. Held feed admission,
snapshot barriers and cutover stay in 6d.2. All parent validation boxes remain
unchanged; the storage tests do not establish a real Manager load budget.

### 2026-10-12 — Materialized schema-bounded read-side goal

The operator explicitly requires constant-schema current-state reads, pushed
claims-derived state and direct current handoff-head selection. The canonical
agent-worktrees vision and the coupled per-worktree-activity-state amendment
record that goal and the proposed scaling proof. Phase 6d retains authority and
consumer integration; #5664 retains handoff/lifecycle slot migration.
No implementation, new daemon, live rollout or cleanup is performed by this
documentation amendment, and neither effort is complete.

### 2026-10-08 — Phase 6d: scoped the remaining authority/stream cutover

The execution plan merged as #5748, including separate Windows/Linux/macOS
coverage, advisory durable reads distinct from fresh recomputation, bounded
recovery after a failed start, and explicit going-away notices before planned
feed closure. At this checkpoint, only the planning gate was cleared;
6d.1 implementation was next.

Source tracing after #5743 confirmed that the gap is larger than the git
fallback: normal classified reads may serve whole-payload cache, cache-only
paints use record hints, streaming computes locally, and the production local
loader overlays a separate reconcile batch. Coalescing-library `subscribe`
is lifetime accounting, not a change feed. `phase-6d-authority.md` records the
agent-recommended execution slices and validation obligations for closing
those competing read paths without changing the parent's hard requirement.
The live oscillation's actual cadence remains unverified; neither this plan
nor the already-merged repairs are a substitute for its consumer acceptance.

### 2026-10-08 — Phase 6b/6c: one shared wrapper; stamp release verified

The shared-wrapper implementation and its structural/freshness tests merged
as #5743 (merge commit `9a09837d699600b47888bf053769f8a89654752f`).
At this 2026-10-08 checkpoint, its promotion was verified in the released
source. Local unified deployment was blocked on another process's build lease
for 1.24.26-dev1: the update refused to race that builder after refreshing
payloads, while the active runtime remained 1.24.25-dev1. Retrying the unified
flow after that build finished remained an obligation; neither the new
runtime nor its activation had been verified at this checkpoint. This records
the historical observation, not the current installed generation or a new
deployment instruction.
The implementation worktree is retained for Phase 6d, not finalized merely
because this slice landed.

The cache-stamp fix merged as #5700 and is now present in the released
`main` source (the original dev CI run completed successfully; promotion
#5705 merged). Deployment inspection of agent-worktrees 1.24.25-dev1 confirms
the `lock_timeout` parameter and the background queue's `lock_timeout=0.5`
call. Unified update reported live-monitor cutover and the new monitor
publishes both classify and worktree-status generations. This verifies the
narrow stamp repair, not the original recurrence cadence.

The reviewed richer wrapper is implemented in `worktree_git_facts.compute`,
not a second wrapper around the already-shared leaf alone. It retains
explicit fetch intent, closure/dirty safeguards, and CONVO refinement.
Bundle git facts now gain the same closure/CONVO policy, using durable turn
counts and `active_paths=None`; batch lists retain their existing session
observation and no-fetch behavior. Two request servers remain inside one
resident process, with distinct request/cache contracts.

All 224 tests in the complete affected contract selection passed on native
Windows, including real coalescing transports, a local bare-remote freshness
divergence, missing/finalized checkouts, and root-override compatibility.
No live tracking record or mux session was modified by those tests.
Clean-room provisioning is not applicable to this computation-only change;
external development-venue validation is not available for this local
engine surface. Linux parity remains CI coverage.

Phase 6d remains open: caller-side fallback on a live daemon's timeout or
malformed answer is still not authorized by the parent requirement. The
whole-payload `list_cache` is another path to cover: normal classified lists
can return cached JSON without computing/stamping, while cache-only paints
read record hints directly. Its resident freshness lease can outlive the
ordinary 4s TTL. This dataflow is confirmed, but its contribution to the
reported live oscillation is not yet reproduced. Neither wrapper sharing nor
stamp deployment closes daemon-only snapshot/stream authority or row-stability
acceptance.

### 2026-10-08 — Phase 6 cache-stamp follow-on: reproduced contention; bounded the background wait

The regression failed against the prior implementation: a real separate
process held the record sidecar while the async worker attempted an ACTIVE
stamp; after queue drainage the durable record still said completed. The
new worker opts into `_RecordLock`'s bounded, sidecar-required acquisition,
with 0.5s per lock layer. It never falls through to an unlocked write.
Timeout and storage errors are reported; synchronous nonblocking callers
retain their behavior. An actual child interpreter exiting normally also
persists the contended stamp after the holder releases.

Focused validation: 81 stamp/record-lock tests and the complete affected
tracking/cache-consumer selection (400 tests) passed, including the real
cross-process and process-exit contracts. The implementation is a
below-altitude reliability repair on the transitional cache path, not a
substitute for the vision's sole-daemon authority. Remaining Phase 6 work:
shared computation, structural delegation tests, mux-cache consistency if
still necessary, and snapshot/stream plus daemon-only read authority.
No claim is made that synthetic contention reproduces the reported live
recurrence cadence.

### 2026-10-08 — Phase 6a: traced all three compute paths; oscillation mechanism confirmed (contended-write skip) after three successive hypotheses refined through review
Full trace in `phase-6-audit.md`. Confirmed `worktree_status_compute`
(agent-dispatch's Tasks-board relay) and `status-segment` (deliberately
passes `active_paths=None`) are not implicated in the reported
`MERGED`→`WIP`/`ACTIVE`→`MERGED` oscillation — it is not two daemons
disagreeing. Three successive hypotheses for the exact mechanism were
refined through this repo's own PR review with concrete code evidence:
(1) "`_overlay_cached_state`'s `live` override unconditionally sets
`state`" — disproven, since `derive.py`'s `_state()` already prioritizes
liveness marker fields over `state`; (2) "the cache-only payload's
missing `mux_session`/`mux_attached` fields alone cause a repeating flap"
— a real field-coverage gap, but insufficient alone since the classify
pass's own `active_paths` already forces `ACTIVE` while genuinely live;
(3) **confirmed as the primary mechanism**: `_apply_session_state_stamp`'s
write-back acquires its per-path record lock **non-blocking** and
silently skips the write (no retry) on contention
(`tracking.py:3708-3710`) — so a classify pass can compute the correct
state and still never persist it on an ordinary clean exit (the queue's
`atexit`-registered flush only guarantees the write is *attempted*, not
that it *succeeds*), leaving the next cache-only paint reading stale data
with no error surfaced anywhere. This is fully code-confirmed and
sufficient on its own; only the real-world *frequency* of the contention
(to match the reported recurrence cadence) remains open, needing live
reproduction rather than further static tracing. Scoped the primary fix
as a bounded retry/short block in `_StampWriteQueue._apply` on contention,
with the mux-visibility-gap fix as a secondary, independent closer.
Decided the
consolidation shape for 6b: both paths already call the shared leaf
(`git_ops.classify_worktree`); the genuinely new seam is
`_classify_one_record`'s full wrapper (`active_paths`, the classify call,
tracking-override closure refinement, `CONVO` refinement) one level up,
shared by both `_classify_daemon_compute` and `worktree_status_compute
.compute()` (which must keep passing `active_paths=None`, since it
short-circuits before the fetch and would silently replace Path B's real
git disposition fact). Neither existing `work_coalescing_singleton` server is
retired — the two request granularities (whole-project batch vs.
single-worktree bundle) remain genuinely distinct consumers. Phase 6
heading corrected from "not started" to "in progress."

### 2026-09-28 — Confirmed operational pattern: duplicate resident `status-monitor` processes are a real, correctable bug, not benign
Found while updating a Windows-native install after the two fixes above:
**two** `status-monitor` processes were running simultaneously on one host,
both on the same (already-fixed) version -- not a version-supersession race
`status-monitor-restart` would reap (that command only ever reaps a
version-*superseded* owner; a same-version duplicate is left alone, exactly
as it did here: "a current monitor already owns the host -- left as-is").
Confirmed which PID was the genuine lock owner by reading
`~/.agent-worktrees/status-monitor.lock`'s own `pid` field directly, then
killed only the non-owning duplicate by specific PID. The same host also had
a dozen stale `worktree_manager --project ...` Picker processes accumulated
across three old versions with no Picker terminal actually open -- confirmed
with the operator before killing all of them, since "no open terminals" is
exactly the kind of claim that needs the operator's own confirmation, not an
agent's assumption, before a bulk kill.

**This is now a documented, sanctioned diagnostic+remediation pattern** (not
a one-off cleanup): a duplicate resident `status-monitor` is a genuine,
potentially-blocking bug -- two instances can race applying `mux-status-v1`
writes to the same session, corrupting rendering in ways indistinguishable
from the render-freeze bugs fixed above. Added to the facility's own
error-response-discipline documentation as a cross-host/cross-OS trigger,
with a fail-closed, identity-bound recipe (three rounds of Copilot review
correctly hardened this: first flagging the initial draft's naive "kill
every non-owning PID from an earlier read" as racy, then flagging that even
a fresh command-line check before a plain PID kill is still not
reuse-safe, then flagging that the recipe still had no floor -- if the
re-read finds no live owner at all, "kill every other candidate" degenerates
into killing everything observed): **whenever an agent observes more than
one resident `status-monitor` process on a single host, re-read the lock
file (`locks.read_lock`) and check `locks.lock_is_live`** for each candidate
PID's own recorded `start_time`
(`plugins/agent-worktrees/src/agent_worktrees/locks.py`) -- **note
`lock_is_live` itself fails OPEN (treats a pid as live) when `start_time` is
absent or unreadable, so it alone does not prove a candidate is stale; use
it only to find the one entry the lock FILE claims as current owner, never
as the sole basis for judging a candidate safe to kill. If that re-read
finds no parseable lock, or `lock_is_live` is false, or the claimed owner PID
isn't present in a fresh process census, STOP -- there is no validated live
owner to exclude, so no candidate may be terminated yet; re-enumerate (or
just retry the whole check) until a real live owner is confirmed present.**
Only once a validated live owner is confirmed, **terminate every OTHER
candidate via `procs.terminate_pid_if_identity(pid, expected_start_time)`**,
never a plain `kill`/`Stop-Process` by bare PID: that helper binds the
termination itself to the verified process identity (an open pidfd + start-time check on POSIX,
a creation-time check through the same handle `TerminateProcess` uses on
Windows) and fails closed if the identity can't be proven, closing the
reuse window a check-then-kill can never fully close on its own.

### 2026-09-28 — Real bug found via CI drift: `picker-reconcile-local`'s fast-dispatch path was broken, blocking the whole dev->main promotion pipeline
Found while pushing the `_StatusSegmentCache` fix (above) through the release
pipeline: the `full - agent-worktrees` full-tree CI job -- one of the gates
`validate-and-promote.yml` requires before it will promote anything -- was
failing on `dev`, silently blocking **every** promotion attempt repo-wide
(not just this one) for hours. Two real, independent, previously-undetected
bugs, both surfaced by `test_lazy_dispatch.py`'s drift checks:
1. **`picker_reconcile_cli.add_parser`/`add_parsers` naming mismatch.** The
   shared `lazy_cli_dispatch` fast-path mechanism calls `module.add_parsers(sub)`
   by convention; `picker_reconcile_cli.py` only ever defined the singular
   `add_parser`. Its `_LAZY_DISPATCH_TABLE` entry meant
   `agent-worktrees picker-reconcile-local` crashed with `AttributeError` via
   the real fast-dispatch path in production today -- a genuine, currently-
   shipping regression, not a test artifact. Renamed to `add_parsers`
   (updating `context_cli.py`'s own nested call to it) rather than adding a
   test-only special case.
2. **`_CLUSTER_FREE_MODULES` drift**, both directions: `picker_reconcile_cli`
   needed adding (confirmed cluster-free only after fix #1 -- before it, the
   real-handler-execution test correctly caught the `AttributeError` a
   static-only scan couldn't); `context_cli` needed removing, since it now
   transitively reaches `_in_ssh_session` (a `resolve_machine_cli`-owned name
   bound only inside the deferred `_load_full_command_surface()` block) --
   not a crash (module `__getattr__` self-heals via an eager full load) but a
   silent perf regression that defeats the fast path for every context_cli-
   routed command. Removed the now-invalid `test_cluster_free_command_handler_
   body_runs_without_cluster` parametrize case for a `context_cli` command
   (`machine-context`) and added a real one for `picker-reconcile-local`
   instead, per this test suite's own stated philosophy: a scanner alone
   already missed both directions of this once.
Also fixed `test_lazy_dispatch_table_matches_regenerated_scan`'s own
regeneration helper: it hadn't accounted for a parser nested inside a
*different* module's `add_parsers()` (here, `picker_reconcile_cli`'s own
registration is ALSO invoked from inside `context_cli.add_parsers()`),
which silently dropped the entry from the simulated table. Module-size-
neutral swap in `_CLUSTER_FREE_MODULES` (one name out, one in). Full
`agent-worktrees` suite: 5813 passed (up from 5797), 26 skipped, only the
same two already-confirmed-pre-existing/environmental failures remain
(a local `gh` CLI device-id leak and a local `leaked_agent_rt_root` doctor
finding, both artifacts of this machine's own heavy use this session, not
this repo's code).

### 2026-09-27 — Real-bug fix: `_StatusSegmentCache.get()` NameError on the resident monitor's fast-dispatch path (#4341)
Found live while chasing the mux status-bar freeze (see
`worktree-manager-control-plane`'s companion fix, journaled there): after
that fix restored the render pipeline end-to-end (push succeeded,
`last_status_rendered_at` advanced), the actual `@aw_seg` value applied to
tmux was still permanently empty for every managed session. Root-caused via
a direct, in-process repro (not guessed): `agent_worktrees.__main__`'s
`_StatusSegmentCache.get()` referenced the bare module globals
`_find_record_for_path`/`_render_status_segment` directly -- but both are
only ever bound by the deferred `_load_full_command_surface()` block, which
`status-monitor`'s own fast-dispatch entry (`_LAZY_DISPATCH_TABLE`,
`status_monitor_cli` marked cluster-free) never runs. Every real call raised
`NameError`, silently swallowed by `_monitor_sweep`'s blanket
`except Exception: pass`, so the segment stayed `""` forever with zero
visible error. This test suite's own `conftest.py` unconditionally
pre-loads the full surface for every test, which is exactly why the existing
`test_segment_cache_*` tests never caught it -- they can't observe the real
unloaded-fast-path state at all. Fixed by resolving both names through the
already-established `_self_override(...)` + `from . import status_bar_cli`
pattern (mirroring `_monitor_maybe_trigger_handoff_cutover`'s own existing
Stage-D-safe call a few hundred lines above it) instead of the bare globals
-- a 3-line, self-contained diff. Added a genuine regression test that
deletes the bare globals to reproduce the real unloaded state (proven to
fail with the exact `NameError` against the pre-fix code, pass after).
Module-size-neutral: `__main__.py` sits exactly at its grandfathered
7072-line ceiling, so the fix's own comment was compacted to a single line
to land at net-zero growth rather than touching unrelated regions.
Validation: full `agent-worktrees` suite, 5797 passed, 26 skipped, plus 3
pre-existing failures confirmed unrelated (a `_CLUSTER_FREE_MODULES`/scan
drift from concurrent unrelated PRs, and a local `gh` CLI state-leak
artifact from this machine's own auth usage) -- both reproduced identically
with this fix stashed out.

### 2026-09-27 — Phase 4 CI guard landed; process-consolidation cross-link confirmed with worktree-manager-control-plane
Operator directed continuing this effort "as you go" while explicitly
checking alignment with process consolidation -- reducing rogue process
spawns from `launch_session`, the mux monitor's status sweep, and Worktree
Manager reads/refreshes/writes. A live-host process census
(`status-monitor`, `mux-daemon`) found both resident singletons healthy
(exactly one of each, no duplicates) -- the accelerator effort's
single-instance lease is holding as designed; no rogue spawn observed
right now.

Investigating the three named sources traced each to an already-tracked,
in-progress owner, none of them this effort:
- `launch_session` (`worktree-manager/bin/launch-session.{sh,ps1}`) and the
  mux monitor's own sweep are `worktree-manager-control-plane`'s Phase 3b
  (Mux status-monitor consolidation -- Done) and Phase 3d's still-open
  "design the `runner.py` process-lifecycle call sites" TODO
  (`reap_orphan_mux_sessions`/`_sweep_finished_sessions_on_cadence`/etc.).
- Worktree Manager's own reads/refreshes/writes is that same effort's
  Phase 3d "design the `data_local.py` hot path" TODO -- its Picker's
  refresh loop (`tracking.list_records`/`stamp_bound_live`/`stamp_mux_live`/
  `stamp_session_state`) already runs in-process today (an in-process
  import of agent-worktrees' own `tracking.py` via `_engine_runtime.py`,
  not yet even a subprocess), and that TODO explicitly names "one new
  atomic, batched `--json` verb (read + reconcile + stamp in a single
  agent-worktrees-owned call)" as its own prerequisite, sequenced after
  that effort's Phase 3c. This effort's `tracking_write.py` verb registry
  (dispatch/`AmbiguousWriteOutcome`/capability-aware endpoint selection) is
  exactly the substrate that future batched verb will need -- direct,
  concrete alignment, not incidental.

This also surfaced a real, previously-unsurveyed gap this effort's own
Context section's cross-plugin survey (2026-09-26) did not cover: that
survey scoped to `agent-*` PLUGINS only and found zero direct writers, but
`worktree-manager/` (a separate, non-plugin, out-of-plugin control-plane
app) already calls `tracking.stamp_bound_live`/`stamp_mux_live`/
`stamp_session_state` directly today via its own in-process bridge --
exactly the write-surface exposure this effort's `no-writer-bypasses-the-
daemon` behavior means to close, just not yet caught because it isn't a
"plugin." Confirmed this is a KNOWN gap (Phase 3d's own TODO already
names it and gates its resolution behind that effort's own Phase 3c), not
a new incident -- filing a duplicate fix here would fork ownership of one
call site across two efforts. Cross-linked instead: this effort's Phase 4
now explicitly documents the gap and points at Phase 3d/3c rather than
silently missing it or unilaterally rewriting a hot path another effort
already has an ordered plan for.

Landed the Phase 4 CI guard proper: `tools/check-no-sibling-tracking-
writes.py`, an AST-based scan of every sibling `plugins/*` (excluding
`agent-worktrees` itself) for a direct import/call of a hand-curated
denylist covering every tracking-record write function -- derived by
AST-walking each protected module for functions whose body calls
`save_record`/`_save_record_unlocked`/the async stamp queue, then
hand-verified against the remainder (the final denylist, after twenty
review rounds below, covers 55 functions across 12 modules:
`tracking.py`/`tracking_lifecycle.py`/`tracking_claims.py`/
`tracking_session_registry.py`/`tracking_controller_relations.py`/
`tracking_write.py`/the 6 `tracking_*_write.py` verb-handler modules).
Scoped deliberately to
sibling PLUGINS only, matching this effort's own original survey/Plan
wording -- NOT extended to cover `worktree-manager/`'s own already-tracked
exception (see above), since that scope decision belongs to a
coordinated cross-effort call, not something to fold in unilaterally here.

**Twenty review rounds found real gaps, none assumed away:**
(1) `tracking_controller_relations.py`'s own thin
`save_record`/`_save_record_unlocked` re-export wrappers were absent from
the protected module set, so a sibling could route through that module
instead and pass clean. (2) The attribute-chain matcher only handled a
single-level module alias (`tracking.save_record(...)`), missing the
two-level `import agent_worktrees as aw; aw.tracking.save_record(...)`
form -- a package-root alias was never tracked at all. (3) The denylist
itself was incomplete: `backfill_legacy_controller_relations`/
`set_controller_relation`/`end_controller_relation`/
`remove_controller_relation` persist records in
`tracking_controller_relations.py` AND are re-exported by `tracking.py`
itself, plus `load_or_create_anchor_record` conditionally persists via
`create_new_record` -- none were in the original list. The SECOND
round then caught that round one's own fix for gap (2) was itself
semantically wrong: `import agent_worktrees.tracking` with NO `as` binds
only the top-level `agent_worktrees` name (Python's own import semantics
-- the submodule name is never bound locally), so modeling it as a
`tracking` module alias both mis-caught an unrelated bare
`tracking.save_record(...)` name collision as if it came through this
import AND missed the actual valid `agent_worktrees.tracking.save_record
(...)` shape this import produces -- fixed by routing the unaliased
dotted-import case into the package-alias tracking instead, matching
`import agent_worktrees`'s own already-correct handling. A THIRD
review round then found a wildcard-import escape hatch entirely outside
the alias-tracking model: `from agent_worktrees import *` (or `from
agent_worktrees.tracking import *`) followed by a bare `save_record(...)`
call produces an `ImportFrom` alias literally named `*`, which neither
existing branch ever matches -- fixed by rejecting the wildcard import
itself outright (unresolvable safely against the denylist, so treated as
a violation on sight). A FOURTH review round found two more real
gaps: the module set stopped at the five original "public" tracking
modules, but the daemon's own 6 `tracking_*_write.py` verb-handler
modules (`apply_claim_add`/`apply_claim_release`/`apply_claim_settle`/
`apply_status_disposition`/`apply_follow_up_add`/
`apply_follow_up_resolve`/`apply_follow_up_dismiss`/
`apply_session_deregister`/`apply_session_conclude`/
`apply_session_link_succession`/`apply_session_register`, 11 functions)
are an EQUALLY importable write surface -- each one itself acquires
`_RecordLock`, mutates the record, and calls `tracking.save_record`,
just skipping the verb-dispatch layer on the way there; and (7) the
file-scan's own exception handling failed OPEN -- an unreadable,
non-UTF-8, or syntactically invalid sibling `.py` file was silently
treated as clean, so a file the guard simply couldn't parse was a
guaranteed way to smuggle a forbidden import past CI. Fixed by adding
the verb-handler modules/functions to the protected sets, and by turning
every file-scan failure into a reported `Violation` instead of a silent
empty list. A FIFTH review round found one more private
read-modify-write helper absent: `_stamp_liveness` (backs
`stamp_mux_live`/`stamp_bound_live`) -- underscore-prefixed, but nothing
in Python actually prevents a sibling from importing a private name
directly. Re-ran a comprehensive AST scan (every top-level function
across all 11 modules whose body calls a known writer, plus a
transitive-closure pass over the growing denylist itself) to catch any
further stragglers before a sixth round could -- found one more genuine
gap this way (`_apply_session_state_stamp`, backing
`stamp_session_state`) and one false-positive candidate the transitive
pass surfaced but manual inspection ruled out
(`reopen_finalized_owner` calls `tracking.update_status(...,
save=False)` -- mutates in-memory only, never itself persists, so it
does not belong on the denylist). A SIXTH review round found four
more real findings: **(a)** `tracking_write.py`'s own generic
direct-execution APIs -- `run_direct(verb, args, reason=...)` and
`compute(kind, payload)` -- can load and invoke ANY registered verb's
handler in-process without ever naming the handler function itself
(e.g. `tracking_write.run_direct("claim_add", {...}, reason=...)` never
mentions `apply_claim_add`), a bypass shape entirely outside the
name-matching model; fixed by adding `tracking_write` to the protected
modules and `run_direct`/`compute` to the denylist, while deliberately
leaving `dispatch`/`write_with_boot` unprotected -- those two ARE the
sanctioned daemon-mediated API. **(b)** A trivial reassignment
(`writer_module = tracking`) evaded every alias check, since aliases
were only ever populated from import nodes; fixed with a fixed-point
propagation pass over simple `Name = Name` assignments (handling
multi-hop chains like `a = tracking; b = a; c = b`). **(c)** The
docstring claimed this guard scoped to `agent-*`-named plugins, but the
scan itself has always covered every directory under `plugins/`
(including non-`agent-*`-named ones, e.g. `visions`,
`customizing-copilot`) except `agent-worktrees` -- fixed by correcting
the docstring (and the `main()` output text) to describe the actual,
broader, name-prefix-independent scope rather than narrowing the scan
to match a now-inaccurate claim. **(d)** This very Journal entry still
quoted an earlier round's stale function/module counts after two later
rounds had already changed them -- fixed by updating every remaining
count in this entry, not just the one flagged. A SEVENTH review
round found the reassignment-propagation fix from round 6 was itself
incomplete: it only handled a bare `Name = Name` RHS, missing the
equally simple `tracking_module = aw.tracking` (a two-level `Attribute`
RHS off a package alias) -- fixed by resolving that shape into the same
`module_aliases` set a direct `aw.tracking.save_record(...)` call
already populates, rather than adding a second parallel check
downstream. An EIGHTH review round found three lingering
documentation-accuracy stragglers from fix (c) above -- the CI step's
own comment in `.github/workflows/ci.yml`, the test file's own module
docstring, and this Journal's own "(25 tests)" count -- all still said
"agent-*" or a stale count after the code and the main docstring had
already been corrected; fixed by sweeping every remaining mention, not
just the one flagged each time. One of round 8's own CI runs also hit a
genuinely unrelated, pre-existing flaky test
(`test_first_use_provision_is_serialized` in
`libs/payload-invocation`/`agent-index`, nothing to do with this guard)
-- confirmed unrelated and cleared with a CI rerun, not a code change.
A NINTH review round found the round-6/7 reassignment-propagation
fix was STILL incomplete: it only handled plain `ast.Assign`, missing
the equally simple annotated form (`writer_module: object = tracking`)
-- an annotation adds no actual indirection, so `ast.AnnAssign` needed
the identical treatment; fixed by unifying both assignment forms into
one shared propagation pass rather than a third parallel branch. This
round also caught the PR description's own stale test count ("adds 8
tests") and a logically backwards claim in the
`worktree-manager-control-plane` cross-link (implying the guard would
need the `worktree-manager` call site added once it converts to the
batched verb, when conversion should REMOVE that call site entirely) --
both corrected. A TENTH review round found the deepest gap yet:
`getattr(tracking, "save_record")` and
`tracking.__dict__["save_record"]` both fetch a write function
reflectively -- neither produces an `ast.Attribute` node, so both
evaded the entire attribute-matching model regardless of how complete
the denylist or alias tracking was. Fixed by adding two narrow,
literal-string-only checks: a `getattr(<tracked-module>, "<write-fn>")`
call and a `<tracked-module>.__dict__["<write-fn>"]` subscript, both
resolved through the SAME module/package-alias tracking the ordinary
attribute check already uses. Deliberately NOT a general
reflection-proof analysis -- a non-literal name
(`getattr(tracking, some_variable)`) is genuinely undecidable statically
and is not attempted; a dedicated regression test documents that
boundary rather than asserting false safety. This round also caught two
more documentation stragglers: the guard's own maintenance note still
named only the original four modules (missing
`tracking_controller_relations`/`tracking_write`/the six
`tracking_*_write.py` modules added in later rounds), and this Journal
entry's own "(26 tests)"/"five review rounds" phrasing had gone stale
again after round 9's edits. An ELEVENTH review round found the
final escape hatch this static model had left open: a literal-string
dynamic import (`importlib.import_module("agent_worktrees.tracking")`
or `__import__("agent_worktrees.tracking")`) resolves a tracked module
just as statically as an ordinary `import` statement, but produced
neither an `Import` nor `ImportFrom` node, so it bypassed every alias
check entirely. Fixed by recognizing both call forms (literal-string
argument only, matching the same non-literal boundary already accepted
for `getattr`/`__dict__`) and feeding their resolved target into the
SAME alias-propagation machinery an ordinary import already populates --
both as an assignment RHS and as an inline, unassigned call whose result
is used directly. A TWELFTH review round found two more real gaps:
**(a)** the denylist protected `tracking.stamp_mux_live`/
`stamp_bound_live`/`stamp_session_state` but not the shared
`_STAMP_QUEUE` singleton those wrappers themselves funnel through --
`tracking._STAMP_QUEUE.submit(...)`/`.submit_mux(...)` (and its own
direct `._apply(...)` path) trigger the exact same persisted write while
bypassing those wrappers' own best-effort/throttle semantics entirely,
a THREE-level attribute chain (module -> `_STAMP_QUEUE` -> method) the
existing two-level matcher never modeled. Fixed by adding a dedicated
`STAMP_QUEUE_WRITE_METHODS` check reusing the same module/package-alias
resolution the rest of the guard already has. **(b)** The dynamic-import
detector from round 11 only recognized the literal identifier
`importlib` as the receiver, missing `import importlib as il` and
`from importlib import import_module` (calling it completely bare) --
fixed by tracking `importlib`'s own aliases and `import_module`'s own
direct bindings the same way every other name in this guard is tracked,
rather than hardcoding one spelling. A THIRTEENTH review round
found the queue-specific matcher from round 12 only recognized the
INLINE `<module>._STAMP_QUEUE.<method>` chain in one expression -- a
sibling reassigning the queue object itself first
(`queue = tracking._STAMP_QUEUE; queue.submit(...)`, or a direct
`from agent_worktrees.tracking import _STAMP_QUEUE`) evaded it entirely.
Fixed by adding a `queue_aliases` set populated the same way
`module_aliases`/`package_aliases` already are (a direct import of
`_STAMP_QUEUE` itself, or an assignment whose RHS resolves to
`<tracked-module>._STAMP_QUEUE`), then matching `<queue-alias>.<method>`
alongside the existing inline chain. A FOURTEENTH review round
found the deepest layer still uncovered: `_atomic_write` -- the
low-level function `save_record` itself calls to actually emit the
serialized YAML -- was absent from the denylist entirely. A sibling
importing it directly could write raw content straight to a
`tracking.yaml` path, bypassing every validation/locking/merge layer
above it, not merely one specific field-level wrapper. Added to
`WRITE_FUNCTIONS`. A FIFTEENTH review round found two more real
gaps at the very bottom of the stack: **(a)** `_replace_with_retry`,
the even-lower-level `os.replace(src, dst)` helper `_atomic_write`
itself calls, was still missing; **(b)** `tracking_write._VERBS` -- the
raw verb-name -> handler registry `run_direct`/`compute` themselves
dispatch through -- was entirely unprotected, so
`tracking_write._VERBS["claim_add"](args)` reaches a mutating handler
without ever naming `run_direct`, `compute`, or any `apply_*` function.
Fixed by adding `_replace_with_retry` to the denylist, and by
denylisting the `_VERBS` ATTRIBUTE NAME itself (not a specific key) so
any access to it -- subscripted immediately or bound to a variable
first -- is caught the moment `._VERBS` is touched at all. A
SIXTEENTH review round -- the checks turning green for the first time --
found four more real gaps: **(a)** `_StampWriteQueue`, the CLASS behind
`_STAMP_QUEUE`, was itself importable/instantiable separately from the
resident singleton, reaching the exact same writers via a fresh
instance; denylisted the class name itself. **(b)** The dynamic-import
detector's non-literal-target boundary was too conservative in one
specific, real-but-bounded case: `mod = "agent_worktrees.tracking";
importlib.import_module(mod)` is STATICALLY resolvable (the target is a
plain local name previously assigned a string literal), unlike a
genuinely dynamic value (a parameter, an f-string) -- resolving this
narrow shape (not every non-literal dynamic import, which would be far
too broad and noisy against the whole codebase) closed a real,
bounded gap without widening the guard's blast radius. **(c)**
`__import__` without a `fromlist` argument returns CPython's own
TOP-LEVEL package, not the deepest submodule (a real semantics bug in
round 11's original fix, which had modeled every dotted `__import__`
target as resolving straight to the submodule) -- fixed by only
treating a dotted `__import__` target as a `module` resolution when a
non-empty `fromlist` is actually present, defaulting to `package`
otherwise, matching Python's own real import machinery. **(d)** The
existing internal `_tracking()` lazy-import helper (a pattern repeated
across several `agent_worktrees` modules to dodge circular imports,
always returning the `tracking` submodule) was entirely unmodeled --
`_tracking().save_record(...)` never matched any existing receiver
shape. Fixed by tracking which local names were actually imported (by
that exact name) from one of the protected modules, and recognizing a
bare, no-argument call to one of those specific names as resolving to
`tracking` -- deliberately NOT a blanket "any function named `_tracking`
anywhere" rule, which would risk flagging an unrelated sibling's own
identically-named local helper. A SEVENTEENTH review round found
two more real gaps, both refinements of round 16's own fixes: **(a)**
`_has_fromlist` treated ANY supplied fourth-argument/keyword as
non-empty, but `fromlist=[]` and `fromlist=None` are exactly equivalent
to omitting the argument entirely (CPython still returns the top-level
package) -- the previous version reported a false violation for
`__import__("agent_worktrees.tracking", fromlist=[])` used as a bare
package. Fixed by recognizing a literal empty list/tuple or `None`
fromlist as "absent," while still conservatively treating anything else
non-literal as "present." **(b)** The reflective-access checks
(`getattr`/`__dict__`) only recognized names in `WRITE_FUNCTIONS`, but
`_STAMP_QUEUE` is tracked separately (via `STAMP_QUEUE_ATTR`), so
`getattr(tracking, "_STAMP_QUEUE")` and
`tracking.__dict__["_STAMP_QUEUE"]` both reflectively fetched the same
queue object the plain-attribute check already flags outright, while
evading both reflective checks entirely. Fixed by extending both checks
to also recognize `STAMP_QUEUE_ATTR` alongside `WRITE_FUNCTIONS`. An
EIGHTEENTH review round found two more real gaps, plus a documentation
mismatch: **(a)** round 17's `_STAMP_QUEUE` reflective fix only
recognized `getattr(<module>, "_STAMP_QUEUE")` (fetching the QUEUE
OBJECT from a module), missing the deeper chain
`getattr(tracking._STAMP_QUEUE, "submit")` (fetching a write-triggering
METHOD off an ALREADY-resolved queue expression) -- fixed by adding a
`_queue_expr_label` helper mirroring `_module_expr_label` one level
down the stack, and using it for both the plain-attribute and
reflective (`getattr`/`__dict__`) queue-method checks alike. **(b)** The
inline package-alias chain (`aw.tracking.save_record(...)`) only
recognized a bare `Name` package alias as its base, missing
`__import__("agent_worktrees.tracking").tracking.save_record(...)` --
`__import__` with no fromlist returning the package INLINE, immediately
chained with `.tracking`, never assigned to a variable at all -- fixed
by generalizing the package-base check (`_is_package_expr`) to also
accept an inline dynamic-import call that itself resolves to the
package. **(c)** This Journal's own per-round numbering (`(N)` prefixes
before each spelled-out ordinal) had drifted out of sync with the
actual round count -- a leftover artifact of numbering individual
FINDINGS rather than ROUNDS, which diverge once a round contains more
than one lettered finding. Fixed by dropping the redundant numeric
prefixes entirely, keeping only the spelled-out round ordinals (which
were themselves always accurate) as the source of truth. A NINETEENTH
review round then found two more real gaps, both the SAME shape as
earlier fixes -- an already-established recognized expression
reassigned to a new name before use, one level further out than
previously covered: **(a)** the `_tracking()` lazy-helper recognition
only matched the helper call as the IMMEDIATE receiver
(`_tracking().save_record(...)`), missing
`tracking = _tracking(); tracking.save_record(...)` -- fixed by
extending the reassignment-propagation loop to recognize a bare,
no-argument call to a tracked `_tracking` name as a module-resolving RHS,
alongside the Name/Attribute/dynamic-import forms it already handled.
**(b)** `getattr(aw, "tracking")` (a single reflective CALL doing the
package-to-module hop the plain `aw.tracking` Attribute chain already
caught) was unrecognized entirely as a receiver expression, and -- once
added -- also needed the same reassignment coverage
(`tracking = getattr(aw, "tracking")`). A TWENTIETH review round found
one more real gap, converging quickly this time (a single finding, not
several): the reflective checks (`getattr`/`__dict__`) still only
accepted an INLINE string literal for the fetched name itself, so
`fn = "save_record"; getattr(tracking, fn)(...)` and
`key = "_STAMP_QUEUE"; tracking.__dict__[key]` both evaded them even
though the value is exactly as statically resolvable as the
dynamic-import target already handles via `_string_arg_value`/
`constant_string_aliases`. Fixed by reusing that SAME resolution for
the reflective name/key argument across all four reflective branches
(write-function `getattr`, queue-method `getattr`, write-function
`__dict__`, queue-method `__dict__`), rather than requiring an inline
literal at each site independently. Every fix across all twenty rounds
has a dedicated regression test.

72 tests total (`test_check_no_sibling_tracking_writes.py`): a clean
tree, the sanctioned read-only accessors staying unflagged, three
single-level import/call shapes (module attribute call, direct
`from agent_worktrees import <fn>`, `from agent_worktrees.tracking_claims
import <fn>`), an aliased-module-import evasion case, three
package-root-alias evasion cases (aliased, unaliased bare, and unaliased
dotted), the `tracking_controller_relations` re-export case, the four
added controller-relation functions, `load_or_create_anchor_record`, two
wildcard-import rejection cases, two verb-handler-module cases, two
fail-closed file-scan-error cases (unreadable, syntactically invalid),
the two private liveness-writer helpers, `tracking_write`'s own
direct-execution APIs (one denylisted case, one confirming
`dispatch`/`write_with_boot` stay unflagged), three reassignment-evasion
cases (single-hop Name, multi-hop chained Name, and the
package-attribute RHS form), two annotated-assignment cases (plain and
package-attribute RHS), four reflective-access cases (`getattr` via a
module alias, `getattr` via a package-alias chain, a non-literal
`getattr` name confirming that boundary is not claimed safe, and the
`__dict__` subscript form), a confirmation that an unrelated module's
`getattr` is never flagged, five literal-dynamic-import cases
(`importlib.import_module` assigned then used, `__import__` of the bare
package assigned then used, an inline unassigned call, a non-literal
import-target boundary confirmation, and an unrelated module's dynamic
import never flagged), an aliased-`importlib`-module case, a bare
`import_module`-name case, six `_STAMP_QUEUE` cases (`submit`,
`submit_mux` via a package-alias chain, `_apply`, a confirmation that an
unrelated queue attribute is never flagged, the queue object reassigned
to a new name, and a direct `_STAMP_QUEUE` import), an `_atomic_write`
denylist case, a `_replace_with_retry` denylist case, two `_VERBS`
registry cases (subscripted directly, and bound to a variable first), a
constant-string-alias dynamic-import case, three `__import__`-fromlist
semantics cases (no fromlist resolving to the package, the WRONG
submodule-direct resolution confirmed NOT flagged, and a fromlist
present resolving to the submodule), a `_StampWriteQueue` class case, a
`_tracking()` lazy-helper case, a confirmation that an unrelated
identically-named local `_tracking` is never flagged, the owning-plugin's
own exemption, and a live-repo smoke test (confirmed clean: zero
violations today, matching the original survey's own finding). Wired
into `.github/workflows/ci.yml` alongside the existing
`check-no-agent-machines-packages.py` guard.

### 2026-09-27 — PR #4265: daemon writes push straight into record_cache -- the read-consistency companion to Phase 3's write migration, plus two real bugs review caught
Operator redirected scope mid-session, away from an initially-discussed
"redesign `terminal_conclusion.py`'s `_save_session_conclusion`" plan: the
actual ask was narrower and squarely serves this effort's own Guiding
Intent ("every read *and* every mutation... funneled through") without
requiring the daemon to become the sole authoritative writer first --
"whether the daemon becomes the authoritative state handler, or just a
read/write broker, the same effect is achieved: all entities reading and
writing through the daemon get a consistent state."

Investigated `record_cache.py` (an existing per-process, `(mtime_ns,
size)`-keyed memoization cache, `copilot-extensions#3751`, used only by
`tracking.list_records`) and confirmed `tracking.load_record` (the
single-record read path, ~140 call sites) did NOT use it at all -- always a
fresh re-parse. Added `record_cache.store()` (seeds/refreshes the cache
with a record this process just wrote) and routed `load_record()` itself
through `cached_load()`, giving every one of its call sites the benefit
without touching each one.

**First review round found two real bugs, both fixed, neither assumed
away:**
1. **High: recursive cache lookup in `list_records`.** The first attempt
   left `list_records` calling `record_cache.cached_load(yaml_file,
   load_record)` -- but `load_record()` now already IS a `cached_load`
   call, so every cache MISS recursed into itself until `RecursionError`,
   silently swallowed by `list_records`'s own broad `except Exception`
   (returning no records at all, not stale ones). Fixed by calling
   `load_record(yaml_file)` directly.
2. **Medium: transient projection-dirty state was cached too.**
   `copy.deepcopy(record)` also copied the runtime-only, never-serialized
   `_session_projection_dirty` / `_session_projection_initial_registration`
   / `_controller_projection_dirty` attributes `_save_record_unlocked` can
   populate while serializing; these are cleared on the CALLER's own
   object only after the cache write returns
   (`_flush_session_projections`, once the lock releases). A cache-hit
   `load_record()` could hand back a record that still looked dirty --
   unlike a fresh uncached parse, which never carries these attributes at
   all. Fixed by stripping them in `record_cache.store()`.

**Also found, while manually validating before pushing (not by review):**
a genuine regression in `test_execution_leg_cli.py` (2 failing tests) from
an initial version that pushed the cache only from `tracking.save_record`.
Several real callers -- the execution-leg CLI's own nested mutation lock,
`tracking_lifecycle.create_new_record_if_absent` -- call
`tracking._save_record_unlocked` DIRECTLY to avoid re-acquiring a lock
they already hold, bypassing `save_record` entirely. Root-caused via
`git stash` bisection (confirmed the failures were new, not
pre-existing) down to a `_loaded_from`-staleness bug: `store()` cached a
plain passthrough of the caller's `record`, whose `_loaded_from` is only
set correctly by `save_record` AFTER the cache write (to avoid a
write/stat race); a cache-hit `load_record()` handing that back to a
caller that resaves with no explicit `path` (resolving one via
`record.yaml_path`) silently wrote to the WRONG file. Fixed two ways: (a)
`store()` stamps its own copy's `_loaded_from` explicitly rather than
trusting the passthrough, and (b) moved the cache push from
`save_record` down into `_save_record_unlocked` itself -- the ACTUAL
universal physical-write chokepoint, reaching every direct caller too.
Review's own third (Low) finding independently asked for regression
coverage of exactly this direct-caller path; added
`test_a_direct_unlocked_save_caller_also_pushes_the_cache`, which would
fail if the push were ever moved back into `save_record` alone.

Never a TTL/blackout cache: any writer's STAT-CHANGING write (verb-
mediated, `save_record`, a direct `_save_record_unlocked` caller, or a
raw external rewrite whose resulting `(mtime_ns, size)` differs from
what's cached) is visible on the very next read -- this was always a
performance/consistency improvement, never a correctness fix, which is
also why `terminal_conclusion.py`'s still-open write-migration holdout
needed no further work to get this SAME read-consistency benefit (a
separate axis from write-migration, which it still awaits). Every writer
THIS effort actually cares about (any `tracking.py` write function, verb-
mediated or direct) is unaffected by the `(mtime_ns, size)` key's own
theoretical blind spot (a raw external rewrite landing on the exact same
size at the exact same nanosecond as what's cached) -- `store()` always
stats the file itself, right after its own write, so it can never miss
its own change; only a write bypassing `tracking.py` entirely could hit
that pre-existing, narrower edge case, unchanged by this PR.

Full `agent-worktrees` suite: 5733 passed before the review-fix commit's
new regression tests, 5743 passed after (10 new tests added: the two
review-caught-bug regressions plus the direct-`_save_record_unlocked`-
caller and transient-attribute-stripping tests) -- both runs showed only
the two known pre-existing failures (`test_doctor.py::
test_no_drift_when_consistent`, `test_registration_home.py`).

### 2026-09-27 — PR #4238: Phase 3's eighth migrated call-site cluster, deregister_session itself -- a lock-scope 'fix' tried and reverted twice in one review
Migrated `deregister_session` (the symmetric sessionEnd counterpart to
`register_session`, PR #4159) onto a new `session_deregister` verb.
Much simpler transaction than its sibling: a single find-the-matching-
entry loop, no handoff-token/candidate-token branching, no return value
beyond acknowledgement. Applied PR #4159's own established checklist for
a hot-hook-path verb without rediscovering it: `boot_wait_s=0` + a short
1.5s request deadline; rolling-upgrade safety came for free from
`tracking_write.py`'s own capability-aware endpoint selection (already
landed, no call-site-specific work needed).

While migrating, moved `stop_fsmonitor_daemon` (a `git fsmonitor--daemon
stop` subprocess call, best-effort with its own 5s timeout) to run AFTER
the `_RecordLock` released, reasoning it was a straightforward
scope-discipline improvement -- `_RecordLock`'s own class docstring
explicitly discourages holding the lock across git I/O, and this looked
like exactly that violation. GitHub's Copilot code review caught this
immediately, and correctly: the in-lock ordering was load-bearing, not
incidental. Holding the SAME lock `register_session` also needs for the
whole stop duration is what prevents a concurrent session start from
adding a new open session in the window between this transaction's own
"no open sessions" check and the actual stop -- without it, a session
that starts moments after this one ends could have its own fsmonitor
killed out from under it by a stale decision. Reverted to the exact
pre-migration in-lock ordering; a second review round (on a stale PR
description that still claimed the moved-outside-the-lock behavior)
caught that the PR's own narrative hadn't been updated to match the
revert -- fixed by rewriting the PR description, no further code change
needed. The module's own docstring now documents explicitly why the
ordering is intentional, so a future session doesn't rediscover the same
false "improvement."

**This is the SECOND time this exact shape of mistake has landed in this
effort** (the first: PR #3911's `require_sidecar` detour, where a
"restore the old degrade behavior" instinct was applied to the wrong
layer and had to be traced back to the transaction's *real* net
contention behavior before landing on the correct, simpler fix). The
generalizable lesson, now recorded twice: **an established scope/lock
guideline (or any stated best practice) is a strong default, not an
absolute rule for every call site** -- before applying a textbook
reduction (shrink a lock's scope, restore a degrading lock policy, etc.),
verify what invariant the EXISTING code's ordering/policy actually
protects, typically by asking "what else runs concurrently against the
SAME lock/resource this call site also touches?" A change that looks
like unambiguous hygiene at the function's own scope can silently remove
protection the function was never documented as providing, because the
guarantee lived in an interaction with a SIBLING call site, not in this
one's own visible logic.

Added `test_tracking_session_deregistration_write.py`: verb registration,
faithful-behavior unit tests, a live-`CoalescingServer` dispatch proof,
the same boot-latency guarantee test as `register_session`'s own suite,
and -- after the revert -- a test proving `stop_fsmonitor_daemon` still
runs WHILE the lock is held, via a cross-THREAD probe (the in-process
lock is per-thread reentrant, so a same-thread nested acquisition would
trivially succeed and prove nothing) attempting a separate, non-blocking
`_RecordLock` on the same path from inside the fake
`stop_fsmonitor_daemon` -- it correctly fails to acquire.

Full `plugins/agent-worktrees` suite throughout: 5728 passed, 26 skipped,
1 failed, 1 error -- the same 2 pre-existing/environment-dependent
failures this effort's Journal has noted every round.

### 2026-09-27 — PR #4159: Phase 3's seventh migrated call-site cluster, register_session itself (the first sessionStart-hook-critical verb)
After PR #4064 landed and its own design survey found no further
standalone Phase 3 candidates, reported the finding back to the operator
as a genuine crossroads: tackle the harder EMBEDDED call sites (needing
real redesign), or call Phase 3 done at 6 clusters and move to Phase 4.
The operator's actual direction reframed the problem instead of choosing
between those two options: rather than extracting only `register_session`'s
internal `link_handoff` call (the embedded fragment this effort's own
guidance had been avoiding), verb-ify the WHOLE `register_session`
transaction as one atomic unit -- since agent-worktrees is meant to fully
own this transaction anyway, and splitting one atomic write into two
(the narrower extraction) doesn't reduce risk, it *adds* a real one.

New `tracking_session_registration_write.py`: `apply_session_register`
mirrors the former `register_session` transaction exactly -- every
branch, in the same order, under the same single `_RecordLock` -- so
behavior is unchanged; only the wire shape differs. The extensive
existing `register_session` test suites (743 tests across 10 files) pass
unchanged through the new dispatch path, proving branch-for-branch parity.

The operator also flagged, unprompted, the exact risk this cluster
uniquely carries: "we have to watch out for end-to-end time which
includes venv boot and daemon connection" -- a sessionStart hook runs
synchronously in the path of every session launch, unlike every prior
cluster's explicit CLI command. `register_session`'s own dispatch call
passes `boot_wait_s=0` (never spin-wait for a cold daemon boot) from the
first commit, anticipating exactly the review round that followed.

GitHub's Copilot code review then found two further gaps this cluster's
stakes make unignorable, both of which had lower priority as long as
verb dispatch was reserved for explicit CLI commands:

1. **Reachable-but-stalled latency**: `boot_wait_s=0` only bounds the
   cold-boot case; a reachable-but-stalled daemon would still block for
   the module's default 8s `REQUEST_DEADLINE_S` (~9s including the
   socket timeout). Fixed with `_SESSION_REGISTER_REQUEST_DEADLINE_S`
   (1.5s) -- this verb's own work is simple file I/O expected to complete
   in milliseconds on a healthy daemon.
2. **Rolling-upgrade version skew** (copilot-extensions#3812, filed and
   *deliberately deferred* back in PR #3807 for `status_disposition_write`
   as "narrow, real-but-rare... not fixed inline... deserves its own
   focused design"): fixed for real this time, since the stakes on this
   hot path make the same deferral irresponsible. First attempt
   (`compute()` returns `{"unsupported_verb": True}` instead of raising)
   was correctly found incomplete by a second review round: it only
   protects once BOTH sides already run the fix, never a daemon process
   that predates it entirely. The real fix: `rendezvous_fields()` now
   publishes this process's own registered verb names
   (`tracking_write_verbs`), and `endpoint_from_rendezvous()`/
   `write_with_boot()` refuse to dial an endpoint whose own published
   capability list doesn't cover the requested verb -- including data
   with no such field at all -- treating it identically to "no endpoint
   found," which safely triggers the boot-wait/fallback path (and prompts
   a fresh daemon spawn via `ensure_monitor()`) instead of a doomed
   connection. This closes #3812 for every future verb, not just this
   one, going forward from whenever a daemon process first runs this fix.
   The `unsupported_verb` marker stays as defense-in-depth for the
   residual read-then-connect race (the daemon could restart in between).

Third review round found nothing new -- confirmed via `git show` against
each finding's own line before resolving. Full `plugins/agent-worktrees`
suite throughout: 5697 passed, 26 skipped, 1 failed, 1 error -- the same
2 pre-existing/environment-dependent failures this effort's Journal has
noted every round.

**Lesson for `deregister_session`** (the symmetric session-end hook, the
next natural candidate now that `register_session` has a proven
template): expect the identical review focus -- latency bounds AND
rolling-upgrade safety are now a checklist for ANY sessionStart/
sessionEnd-hook verb, not something to rediscover per cluster. The
capability-aware endpoint selection landed here protects it automatically
(it's in `tracking_write.py` itself), but the `boot_wait_s=0` +
short-deadline pattern is call-site-specific and must be repeated
deliberately.

### 2026-09-27 — PR #4064: Phase 3's sixth migrated call-site cluster, finalize.py's own-session claim settlement; design survey found no further standalone candidates
Migrated `finalize.py`'s `_settle_current_session_claim` onto the shared
`claim_settle` verb's `skip_if_released` guard, instead of a bespoke
locked transaction duplicating the exact same release guard PR #3911
already implemented. This function was a good candidate precisely
because -- unlike `terminal_conclusion.py`'s `_save_session_conclusion`,
still deferred -- it already opened its own SELF-CONTAINED `_RecordLock`,
never sharing a lock scope with `finalize()`'s surrounding flow: a
genuinely standalone transaction, just not itself a top-level CLI
command. Confirmed via inspection of its only call site before
migrating, per this effort's own design-survey-first guidance.

GitHub's Copilot code review caught one real gap: the existing
`test_finalize_gate.py` tests all run under an autouse fixture that
disables the resident status monitor, so they only ever exercised the
direct in-process fallback -- never the actual daemon dispatch path
(including `AmbiguousWriteOutcome` handling) this migration introduces.
Added two tests: one proving the function reaches the verb via an
actual `CoalescingServer` (mirroring the same live-daemon pattern already
used in `test_tracking_claim_write.py`), and one proving that when the
dispatch itself raises, the caller's `record` reference comes back
exactly as passed in -- never a partially-applied or reloaded snapshot --
matching the pre-migration transaction's own `except Exception: pass`
contract precisely.

Before landing this cluster, did a design survey (per the operator's
"continue Phase 3" direction) across every remaining write function in
`tracking_session_registry.py` and `tracking_claims.py`, looking for the
next standalone candidate. Found none:
- `tracking_session_registry.py`: `register_session`/`deregister_session`
  remain hot-path-excluded (sessionStart/sessionEnd hooks);
  `seal_worktree_identity` is embedded in `finalize.py`'s own cascade
  (called right before `_settle_current_session_claim`, but itself saved
  by a *later* step in that same cascade, not a standalone transaction);
  `record_repo_fetch_confirmed` writes an entirely different file (a
  shared repo-freshness registry cache, not a per-worktree
  `WorktreeRecord` YAML) -- out of this effort's scope, which is
  specifically about the daemon-mediated `WorktreeRecord` write-through
  path.
- `tracking_claims.py`: `load_or_create_anchor_record` is a helper inside
  a bigger claim-journaling flow (`worktree_ops_cli.py`'s
  `_ensure_anchor_ledger`/`_journal_run_claim`), not a standalone
  transaction of its own; every other exported write function
  (`sweep_abandoned_obligations`/`release_all_resources`/
  `release_at_rest_resources`/`rehome_abandoned_obligations`/
  `remove_orphaned_obligations`) is a multi-worktree batch operation,
  already explicitly excluded by `tracking_claim_write.py`'s own module
  docstring.

**Phase 3's remaining work is now genuinely the three still-deferred
EMBEDDED call sites** (`terminal_conclusion.py`'s
`_save_session_conclusion`, `register_session`'s own internal
`link_handoff` call) -- each needs fresh design thinking on how to safely
peel a verb out of a bigger orchestrated flow without duplicating its
surrounding choreography. This is harder, riskier work than the six
clusters landed so far, all of which were standalone-shaped transactions
merely waiting to be found. Any future session picking this up should
expect to spend real design time on the choreography question itself,
not another quick survey for an easy remaining leaf.

Full `plugins/agent-worktrees` suite throughout: 5688 passed, 26 skipped,
1 failed, 1 error -- the same 2 pre-existing/environment-dependent
failures this effort's Journal has noted every round
(`test_doctor.py::test_no_drift_when_consistent`,
`test_registration_home.py`), unrelated to this change.

### 2026-09-27 — PR #3911: Phase 3's fifth migrated call-site cluster, handoff-cutover repairs (a lock-policy detour and its revert)
Migrated `handoff_cutover.py`'s two confirmed-retire best-effort repairs
(`_settle_predecessor_session_claim`/`_conclude_retired_predecessor`) onto
the daemon write path, reusing the already-landed `claim_settle`/
`session_conclude` verbs (per the prior session's own naming of this as
the next slice) rather than registering new ones — each repair passes a
new opt-in no-op guard arg (`skip_if_released`/`only_if_active`, checked
inside the verb's own locked transaction, never at the caller) that
reproduces the exact guard the old inline transaction had.

GitHub's Copilot code review ran this PR through 4 rounds, all genuinely
useful:
1. A stale docstring wording ("a later PR" when this PR itself lands the
   reuse).
2. A real ordering bug: `skip_if_released`'s no-op check was placed AFTER
   the reservation check, so a released claim with a stale reservation
   would surface `{"error": "reserved"}` instead of silently no-op'ing —
   the old repair never even reached a reservation check once a claim was
   released. Fixed by moving the guard first.
3-4. The deepest finding, across two rounds: preserving the repair's
   old best-effort *lock* semantics under contention. The old code's OUTER
   `_RecordLock(yaml_path)` degraded (proceeded on the in-process lock
   alone) if the cross-process sidecar was contended — so an initial fix
   added a `require_sidecar=False` arg threaded into `apply_claim_settle`
   to preserve that degrade. Round 4 correctly found this incomplete:
   `tracking.save_record()`'s own NESTED lock call still hardcoded
   `require_sidecar=True` internally, and the reentrancy fast path in
   `_RecordLock.__enter__` only treats a nested acquisition as free when
   the OUTER lock's own attempt actually succeeded -- a genuinely degraded
   outer lock left the nested `save_record` to hard-require the sidecar
   anyway, unaffected by the new arg. Threading the flag through
   `save_record` too (an added `require_sidecar` parameter, default
   `True`, every other caller unchanged) closed that gap syntactically —
   but a closer trace of what the OLD code's OUTER-degrade +
   NESTED-hard-require combination actually did on contention revealed the
   real answer: the net effect was ALWAYS to raise inside the nested lock
   and let the repair's own `contextlib.suppress(Exception)` swallow it,
   NEVER to write a stale snapshot without cross-process exclusion. Fully
   threading `require_sidecar=False` through, as the fix attempted, would
   have let the write proceed *without* exclusion on contention — risking
   exactly the "resurrect an already-released claim" bug the guard exists
   to prevent, if a concurrent `deregister_session` released the claim
   mid-transaction. Reverted to `apply_claim_settle`'s original
   `require_sidecar=True` throughout (no new parameter on `save_record`
   either), which matches the old code's real behavior on contention
   exactly: fail this one settlement attempt, safe for a later
   handoff-cutover cycle or sweep to retry.

**Lesson for the next verb migration touching a lock-policy question:**
trace a pre-migration transaction's *net* behavior across every nested
lock acquisition it makes, not just its outermost lock's stated
`blocking`/`require_sidecar` policy — an inner call can silently override
what the outer call's parameters suggest.

Full `plugins/agent-worktrees` suite: 5666 passed, 26 skipped, 1 failed, 1
error throughout every round — the 2 failures are the same
pre-existing/environment-dependent ones this effort's Journal has noted
before (`test_doctor.py::test_no_drift_when_consistent`,
`test_registration_home.py`), unrelated to this change.

### 2026-09-27 — PR #3886: Phase 3's fourth migrated call-site cluster, session-lifecycle writes (design-survey-first)
Per operator direction, did a design survey of `tracking_lifecycle.py`'s
remaining write functions BEFORE writing any verb code this round, rather
than picking a candidate and discovering complexity mid-implementation.

**Survey findings** (why most of `tracking_lifecycle.py` stays deferred):
- `open_handoff`/`link_handoff` are called from inside
  `register_session`'s own body (`tracking_session_registry.py:186`) --
  the sessionStart-hook-critical path this effort's Phase 3 planning
  already named as too risky for an early verb. Not migrated.
- `handoff_cutover.py`'s `_conclude_retired_predecessor`/
  `_settle_predecessor_session_claim` are standalone single-lock
  transactions in SHAPE, but both are best-effort repairs
  (`contextlib.suppress(Exception)`-wrapped) reachable from the live
  handoff-cutover choreography -- migrating them needs to decide how
  `AmbiguousWriteOutcome` interacts with an existing "silently swallow
  and no-op" contract, which deserves its own focused look rather than
  folding into this cluster. Interesting followup idea: these could
  plausibly just REUSE the now-landed `session_conclude`/`claim_settle`
  verbs (they wrap the identical `conclude_session`/`settle_resource_claim`
  calls) instead of inventing new ones -- worth trying next.
- `terminal_conclusion.py`'s `_save_session_conclusion` calls
  `tracking.save_record` directly with no `_RecordLock` of its own -- it's
  one step inside a bigger caller's already-open lock (the disposable-
  worktree conclusion cascade), not a standalone transaction. Not a verb
  candidate on its own; the whole cascade would need to become one verb,
  a materially bigger and riskier slice.

**What was actually clean:** `session_tracking_cli.cmd_conclude_session`
and `cmd_link_succession` -- both dedicated ground-layer write CLI commands
("the ground-layer WRITE that context-handoff's live cutover shells to",
per their own docstrings), each a single-worktree, single-lock,
project-agnostic transaction (`_find_tracking_file` already resolves the
correct path regardless of ambient project -- confirmed no cross-project
scoping concern, unlike PR #3807's disposition-history fix) with no
`activity.log_event` call at all. New `tracking_session_lifecycle_write.py`
registers `session_conclude`/`session_link_succession` verbs wrapping each
transaction exactly (including the original post-save reload, kept for
behavior parity). Landed in PR #3886 with **zero review findings** -- the
first Phase 3 PR to get a clean first-round review (Copilot's own
automated verdict is recorded as `COMMENTED`, with an approval
recommendation, never a formal `APPROVED` state), likely because the
design survey caught the risk upfront instead of a reviewer catching it
after the fact.

Tests: new `test_tracking_session_lifecycle_write.py` (verb registration,
both lifecycle-error rejections, the head-clearing/moving behavior, one
live-daemon end-to-end proof), plus a `test_session_lifecycle.py` case
proving `AmbiguousWriteOutcome` is reported not swallowed. All 58 existing
`test_session_lifecycle.py` tests continue passing unchanged. Full suite:
5658 passed, same pre-existing failures. All gates clean.

**Process lesson worth keeping:** design-survey-before-implementation paid
off concretely this round (zero findings vs. 1-8 findings on every prior
Phase 3 PR) -- for any future call site touching `tracking_lifecycle.py`/
`tracking_session_registry.py` specifically (both have write functions
embedded in bigger flows or hot paths, unlike `tracking_claims.py`'s
mostly-standalone shape), survey every real call site FIRST and confirm
genuine single-lock/no-choreography self-containment before writing verb
code, rather than assuming a function's OWN shape (`save=False` + an
external caller lock) guarantees the call site is simple.

**Next Phase 3 slice:** try reusing `session_conclude`/`claim_settle` from
`handoff_cutover.py`'s confirmed-retire repairs (a design question: how do
their existing best-effort/silent-no-op semantics compose with
`AmbiguousWriteOutcome`?), or continue surveying
`tracking_session_registry.py` for a genuinely standalone remaining
candidate (still avoiding `register_session`/`deregister_session`, both
hot per-session hook paths of the same risk class as `register_session`).

### 2026-09-26 — PR #3876: Phase 3's third migrated call-site cluster, the resource-claim ledger
Migrated the outbound resource-claim ledger's three single-worktree write
transactions (`claims add`/`release`/`settle`) as a `tracking_claim_write.py`
module, mirroring `tracking_followup_write.py`'s own shape exactly. Also
deliberately scoped OUT two things that stay call-site-level: `claims
sweep`/`claims reconcile-at-rest` (multi-worktree batch operations
iterating every local ledger, not a single-worktree transaction the verb
shape fits) and the cross-machine `owner_ref` resolution/deferral path (a
CLI-level concern resolved BEFORE a local `yaml_path` is even known — there
is nothing to migrate when the answer is "deferred to the lease mirror, no
local write").

One real review finding: `apply_claim_add`'s own frozen-state pre-check
only covered `finalizing`/`orphaned` (mirroring what the CLI transaction
checked before migration), but `tracking.add_resource_claim` itself ALSO
rejects a completed `system`/`bridge` record, and separately a ref
collision with a reservation-held claim, both via `ValueError`. Uncaught,
either would have been silently swallowed by `CoalescingServer` when
dispatched via the daemon and surfaced to the CLI as `AmbiguousWriteOutcome`
— turning a deterministic, non-mutating validation failure into an
apparently unknown write outcome. Fixed by catching `ValueError` generically
around the `add_resource_claim` call rather than duplicating its internal
guard predicate (the reviewer's own suggestion was a narrower "mirror the
guard" fix; catching generically is strictly more robust and can never drift
from the one place the invariant lives). **General lesson for every future
verb migration:** when a verb wraps an existing `tracking_claims.py`/
`tracking_lifecycle.py`/`tracking_session_registry.py` function that raises
`ValueError` for more cases than the call site's own pre-checks already
cover, catch it generically at the verb boundary rather than assuming the
call site's historical pre-checks were exhaustive.

Tests: new `test_tracking_claim_write.py` (verb registration, both the
finalizing/orphaned AND the completed-managed-record rejections, the
reopen-on-add path, both not-found rejections, one live-daemon end-to-end
proof), plus a `test_claims_cmd.py` case proving `AmbiguousWriteOutcome` is
reported not swallowed. All 65 existing `test_claims_cmd.py` tests plus 156
tests across 8 related claim test files continue passing unchanged. Full
suite: 5651 passed, same pre-existing failures. All gates clean.

**Next Phase 3 slice:** the remaining single-worktree candidates in
`tracking_lifecycle.py` (`open_handoff`/`link_handoff`/`conclude_session`/
`link_succession`) and `tracking_session_registry.py`, still avoiding
`register_session` (sessionStart-hook-critical) and `mark_resumed`
(embedded in a bigger resume flow) per the effort's own original guidance.
Three call-site clusters now migrated (disposition, follow-ups, claims) —
the pattern is well-proven; each subsequent PR should mainly need to watch
for (a) cross-project ambient-context reads becoming explicit args and (b)
any wrapped function's OWN validation-exception surface being fully caught,
not just the call site's historical pre-checks.

### 2026-09-26 — PR #3868: Phase 3's second migrated call-site cluster, the follow-up ledger
Picked the itemized follow-up ledger's three write transactions
(`follow-ups add`/`resolve`/`dismiss`) as the next cluster, following
`status_disposition_write`'s (#3807) own template exactly: a new
`tracking_followup_write.py` registers `follow_up_add`/
`follow_up_resolve`/`follow_up_dismiss` verbs, each wrapping the whole
guarded transaction (load -> mutate -> `save_record` ->
`activity.log_event`) previously inline in `follow_ups_cli.py`; the CLI's
three functions now dispatch through a shared `_dispatch_follow_up` helper
instead.

Notably simpler than #3807: none of `follow_up_added`/`follow_up_resolved`/
`follow_up_dismissed` are stage-mapped in `activity.HANDOFF_STAGE_MAP`, so
`activity.log_event` never reaches `handoff_trace.append_event` (the
ambient-project sink #3807 had to explicitly re-scope) for any of them —
confirming that lesson generalizes (only STAGE-MAPPED events carry the
cross-project durable-trace hazard) rather than needing to be re-derived
per call site. Only one low-severity review finding this round (a missing
`Documentation impact:` PR-description statement, same as #3823's own
finding) — a good sign the migration pattern itself is now well-understood
and mechanically repeatable.

Tests: new `test_tracking_followup_write.py` (verb registration, the
frozen-owner rejection, the reopen-on-add path, both not-found rejections,
one live-daemon end-to-end proof), plus a `test_follow_ups_cmd.py` case
proving `AmbiguousWriteOutcome` is reported not swallowed. All 12 existing
`test_follow_ups_cmd.py` tests continue passing unchanged. Full suite: 5639
passed, same pre-existing failures (a subset varied this run — environment-
dependent, per this Journal's own prior entries). All gates clean.

**Next Phase 3 slice:** survey `tracking_claims.py` (resource-claim
settle/release), `tracking_lifecycle.py` (`open_handoff`/`link_handoff`/
`conclude_session`/`link_succession`), or `tracking_session_registry.py`
for the next candidate, still avoiding `register_session` (sessionStart-
hook-critical) and `mark_resumed` (embedded in a bigger resume flow) per
the effort's own original guidance.

### 2026-09-26 — PR #3807: Phase 3's first migrated call site, `status_disposition_write`
Picked the "narrower, lower-traffic disposition-assertion path" this Plan
named as a safer first pick than `register_session`/`mark_resumed` — the
`status` CLI command's write mode (`_cmd_status_write`). New
`tracking_disposition_write.py` registers the whole guarded transaction
(load -> terminal/effort-bound guards -> conditional reactivation ->
`set_disposition` -> `save_record` -> once-per-session `status_reported`
event) as a single `status_disposition_write` verb; `_cmd_status_write`
now calls `tracking_write.dispatch(...)` instead of running the
transaction directly.

8 review rounds, all fixing real bugs, none inline-fixable without real
design decisions:
- **Cross-project scoping (2 findings, both real):** the disposition-
  history sidecar (`disposition_history.append`) and the `status_reported`
  durable trace (`activity.log_event` -> `handoff_trace.append_event`)
  both defaulted to the *executing process's own ambient* `cfg.
  tracking_dir()`/`cfg.active_project()` -- correct for every prior
  caller (always the CLI process itself), but wrong the instant a
  transaction can run inside the resident daemon, whose own ambient
  project need not match the project a dispatched request actually
  targets. Fixed by threading both explicitly through the verb's own
  `args` (a `tracking_path`/`project` the CALLER resolves and passes in,
  mirroring how `session_id` was already handled) rather than trusting
  ambient context inside the verb. This is the general lesson for every
  future call site this effort migrates: any ambient read inside a
  transaction that becomes daemon-executable must become an explicit,
  caller-resolved argument.
- **Resolved #3798 for real** (previously just a tracked follow-up): the
  accept-to-handler-dispatch drain race the reviewer correctly pointed out
  is no longer purely theoretical once a real verb exists.
  `CoalescingServer.active_handler_count()` now counts a connection from
  `accept()` (before its handler thread is even spawned) through the
  handler's full return -- a strict superset of the previous subscriber-
  count-based busy window -- and `status_monitor_cli._tracking_write_busy()`
  ORs it in. A follow-up round caught (and fixed) a leak: if delegating to
  the handler thread itself fails (e.g. `Thread.start()` under resource
  exhaustion), the counter must be decremented immediately rather than
  relying on a handler-thread `finally` that will never run.
- **Vendored-lib version discipline:** `work_coalescing_singleton` is
  vendored in two places (agent-worktrees, worktree-manager) --
  `check-vendored-libs-sync.py` requires byte-identical `src/` + matching
  versions across both. Missed on the first pass (only the agent-worktrees
  copy was edited); synced + bumped both to `0.1.0-dev3`. Separately,
  worktree-manager has its OWN payload version
  (`worktree_manager/__init__.py` + its `pyproject.toml`) gating
  `self_install.py`'s upgrade check -- an existing installation would never
  pick up the changed vendored code without also bumping that payload
  version (`0.1.0-dev78` -> `0.1.0-dev79`). Two genuinely different version
  surfaces, easy to conflate.
- **One residual finding deliberately deferred** (matching #3798's own
  original precedent): an unsupported-verb response during a daemon
  rolling upgrade is misclassified as `AmbiguousWriteOutcome` (the daemon's
  `compute()` raises for an unregistered verb, the shared library's
  `_Handler.handle()` swallows it with no response, and the client reads
  that as "request sent, outcome unknown" rather than "nothing could have
  run"). Filed as [#3812](https://github.com/ThomasMichon/copilot-extensions/issues/3812)
  -- narrow, real-but-rare (only during a rolling daemon restart), never a
  data-corruption risk, and the right fix touches shared wire-protocol
  plumbing beyond this first-verb migration's scope.

`tracking.py` is at its exact 4009-line grandfathered ceiling; the small
`set_disposition`/`disposition_history.append` parameter additions were
paid for by rewrapping two unrelated pre-existing multi-line comments
elsewhere in the same file (content unchanged, only relineated), the same
"import-merge trick" pattern as PR #3755. `__main__.py` hit its own 7840-
line ceiling from the call-site migration itself; paid for the same way
(one pre-existing extra-blank-line lint nit fixed, two unrelated comment
blocks rewrapped) plus reverting an ill-advised import-merge ruff/isort
rejected.

New tests: `test_tracking_disposition_write.py` (verb registration, both
guards, the reactivation path, the once-per-session event, the cross-
project scoping fix, one live-daemon end-to-end proof), a
`test_status_write.py` case proving `AmbiguousWriteOutcome` is reported not
swallowed, a `test_activity.py` case for the explicit-project override, and
`work-coalescing-singleton`'s own `test_wire.py`/`test_server.py` gained
real-TCP and simulated-failure coverage for `active_handler_count()`. Full
suite: 5614 passed, same 5 pre-existing failures. All gates
(`check-module-size.py`, `check-vendored-libs-sync.py`,
`check-version-consistency.py`, ruff) clean.

**Next Phase 3 slice:** pick the next `tracking.py`-family write call site
using `status_disposition_write` as the template (whole-transaction verb,
caller-resolved ambient context, its own reviewable PR).

### 2026-09-26 — PR #3779 review round 8: missing wire-kind validation
`compute` dispatched a verb without checking the request's own `kind`
field, so a request mislabeled with a sibling daemon's kind (e.g.
`"classify"`, sent to this daemon's own port) would still be dispatched as
a mutation. Fixed by rejecting any request whose `kind != KIND`, mirroring
`mux_link.py`'s own existing `_compute` guard exactly. One new test
(`test_compute_rejects_a_request_labeled_with_a_different_kind`) — 132
tests total across both files. Full suite: 5577 passed, same 5
pre-existing failures. All gates clean.

### 2026-09-26 — PR #3779 review round 7: a residual microsecond race, tracked as a follow-up rather than fixed inline
Round 7 caught one further, genuinely real but much narrower race than
round 6's: a connection can be `accept()`ed by the server's socket layer
and queued to its handler thread *before* that thread's first line runs
`owner.touch()` -- the call that increments the subscriber count
`_tracking_write_busy()` reads. In that microsecond window, the busy
predicate reads `False` even though a handler is about to execute a write.

**Decision: track as a follow-up issue
([#3798](https://github.com/ThomasMichon/copilot-extensions/issues/3798)),
not fixed inline in this PR.** Reasoning:
- Closing it properly requires the vendored `work_coalescing_singleton`
  library itself (shared by `classify_daemon`/`worktree_status_daemon`/
  `mux_link`, not just `tracking_write`) to track "accepted but not yet
  dispatched to a handler" as its own state -- a shared-library change
  serving four daemon kinds, warranting its own scoped design/review
  rather than folding into an already-large Phase 2 PR seven review
  rounds deep.
- Phase 2 (this PR) ships **zero production write verbs** -- there is no
  live write traffic today this microsecond race could actually affect.
  Phase 3 (not yet started) registers the first real one.
- The much larger, already-real gaps in this same shutdown path (draining
  work accepted well before `close()`, stopping new work before draining,
  the ambiguous/safe fallback boundary at the connect/send-receive split)
  are already fixed by rounds 1-6.
- This is "track it," not "ignore it," per the facility's own
  pre-existing-issue discipline -- the follow-up issue records the
  suggested direction (an explicit accept-time counter, independent of
  subscribe/touch/release) for whoever picks it up, most naturally
  alongside Phase 3's first real verb migration when write traffic
  actually starts to exist.

No code change this round; validation unchanged from round 6 (130 tests,
all gates clean).

### 2026-09-26 — PR #3779 review round 6: shutdown must stop accepting work before draining
- **A new write could still race the shutdown wait.** Rounds 4/5 waited for
  `_tracking_write_busy()` to clear *before* calling
  `tracking_write_server.close()` -- but the server keeps accepting new
  connections the entire time it isn't yet closed, so a fresh write could
  arrive right after the final busy check passed and start executing just
  as `close()` ran, which -- since `close()` never drains an already-
  dispatched handler thread -- could still terminate the process
  mid-transaction. Fixed by reordering: `close()` first (stops accepting
  any *new* request immediately), *then* wait for `_tracking_write_busy()`
  to clear. Every request the predicate can still observe from that point
  on was necessarily accepted before `close()`, so the drain now genuinely
  only waits on already-accepted work -- no request can arrive during the
  wait itself.
- Fixed the Plan's own stale validation count (still said "5573 passed"
  after round 4 had already added 3 tests reaching 5576) -- the historical
  journal entries correctly kept their own point-in-time counts; only the
  live Plan checklist line needed the update.
- No new tests needed: the existing `TestWaitForTrackingWriteIdle` unit
  tests exercise the wait/deadline logic itself, which is unchanged; the
  fix is purely a call-order swap in `cmd_status_monitor`'s `finally`
  block, verified by rerunning the full daemon-lifecycle suite. Full
  suite: 130 tests across `test_tracking_write.py`/`test_status_monitor.py`
  passed; `check-module-size`/`check-install-contract`/
  `check-changefile-presence`/`check-effort-vision-structure`: all clean.

### 2026-09-26 — PR #3779 review round 5: shutdown wait used the wrong predicate; a documentation-accuracy fix
- **The shutdown wait checked the wrong signal.** Round 4's
  `_wait_for_tracking_write_idle` call passed `tracking_write.
  has_inflight_write` directly -- but a request already *accepted* (its
  `CoalescingServer` subscriber registered) has not yet entered `compute()`
  (where the in-flight counter increments), so a shutdown could still slip
  through that narrow window and close the server while a request was about
  to execute. Fixed: pass this module's own combined `_tracking_write_busy`
  predicate (subscriber count OR in-flight counter) instead -- the same one
  the empty-strike branch already used. Renamed the function's parameter
  from `has_inflight_write` to the more accurate `is_busy` and clarified its
  docstring.
- **Documentation didn't match the daemon it describes.** `tracking_write.py`'s
  own docstring said it adds the *third* wire `KIND`, but `mux_link.py`
  already publishes its own `CoalescingServer` kind from the same resident
  monitor -- `tracking_write` is the *fourth*. Fixed the module docstring
  and three other stale `classify`/`worktree_status`-only mentions in this
  effort's own Context/Plan sections to include `mux_link` throughout.
- No new tests needed (the fix is a one-line predicate swap covered by the
  existing `TestWaitForTrackingWriteIdle` tests, which pass an arbitrary
  `is_busy` callable already -- they never assumed
  `has_inflight_write` specifically). Full suite: 5576 passed, same 5
  pre-existing failures. `ruff check`, `check-module-size`,
  `check-install-contract`, `check-changefile-presence`, `check-effort-
  vision-structure`: all clean.

### 2026-09-26 — PR #3779 review round 4: shutdown-safety gap, docstring overclaim, and a recurring stale count
- **The busy check only protected one shutdown path.** Round 2's fix
  guarded the empty-strike idle-exit branch, but `runtime_superseded()` /
  `_other_current_monitor()` can reach the same `finally` block by a
  different route (a runtime handoff, another monitor taking ownership) and
  close `tracking_write_server` unconditionally, regardless of
  `has_inflight_write()`. Fixed: extracted the wait/deadline logic into its
  own `_wait_for_tracking_write_idle()` function (directly unit-testable
  without driving the full monitor lifecycle) and call it, unconditionally,
  right before `tracking_write_server.close()` in the `finally` block --
  covering every shutdown path, not just one. Bounded at 10s
  (`_TRACKING_WRITE_SHUTDOWN_GRACE_S`) so a genuinely wedged compute can
  never block a shutdown/handoff forever.
- **Docstring overclaimed what gets logged.** The module docstring promised
  every direct-fallback log line carries "worktree/verb/reason," but
  `run_direct`'s generic `(verb, args, reason)` API has no dedicated
  worktree parameter -- verbs are deliberately shape-agnostic (see the
  granularity note), so this was never actually enforceable. Fixed two
  ways: revised the docstring to describe what's actually guaranteed
  (verb + reason, always), and made `run_direct` opportunistically include
  `args["worktree_id"]` in the log line when a verb's own args happen to
  carry one under that key -- a best-effort inclusion, explicitly not a
  contract every future verb must satisfy.
- **The stale "14 tests" count recurred a third time** -- round 2 fixed the
  Journal narrative, round 3 fixed one live checklist line but missed a
  second one the reviewer found in the very next round. Fixed for real this
  time (checked every remaining literal count in the Plan/Context sections,
  not just the one flagged).
- 3 new tests (`_wait_for_tracking_write_idle`, isolated from the full
  monitor lifecycle: returns immediately when never busy, polls until busy
  clears, gives up at the grace deadline). 30 tests total across
  `test_tracking_write.py` (27) + the new `TestWaitForTrackingWriteIdle`
  class in `test_status_monitor.py` (3). Full suite: 5576 passed, same 5
  pre-existing failures. `ruff check`, `check-module-size`,
  `check-install-contract`, `check-changefile-presence`: all clean.

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
