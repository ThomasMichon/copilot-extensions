# agent-worktrees — External-Status-Consumer Accelerator

- **Slug:** `agent-worktrees-external-status-accelerator`
- **Repo:** copilot-extensions (plugin home; PR-gated `main`)
- **Branch(es):** independent per-phase worktrees (land each phase's PR before
  starting the next)
- **Created:** 2026-09-20
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** extends
  [`visions/plugins/agent-worktrees`](../../../visions/plugins/agent-worktrees/README.md)
  (§`external-status-consumer-contract`, §`force-refresh-is-opt-in-not-implicit`,
  §`a-full-health-check-leaves-nothing-stale`, added 2026-09-20 during the
  `agent-dispatch-tasks-pane-ux-overhaul` effort's Phase 8 design pass) —
  **vision-realizing**: the contract is already written; this effort builds
  the thing it describes.
- **Related:** `agent-dispatch-tasks-pane-ux-overhaul` (the consumer whose
  Phase 8/Phase 5 are blocked on this work landing) —
  `efforts/active/agent-dispatch-tasks-pane-ux-overhaul/README.md`.
  `docs/patterns/work-coalescing-singleton.md` (the shared pattern this
  effort's daemon is the **second** concrete implementation of, after
  `#2323`'s classify/list accelerator).

## Guiding Intent

Give any external capability — starting with agent-dispatch's Tasks-board
Worktree Status card, but written so it is not the only possible consumer —
a **fast, coalesced, cross-venv-reachable read path** to a worktree's current
status: session/worktree mapping, session lineage + lifecycle event history,
last-known liveness, last-known git state, and the claims graph. Per the
vision's `external-status-consumer-contract`, this is **warmth, not truth**:
the accelerator computes nothing a direct caller couldn't already compute
itself from agent-worktrees' existing primitives — it exists purely so a
render loop or click handler that is itself forbidden from spawning a
subprocess can still get a fast, current answer instead of either blocking or
rendering nothing.

This is **not** new data. Every fact this effort exposes already has an
owning module in agent-worktrees today (`git_ops.classify_worktree`,
`lineage_surfaces.worktree_lineage`, `sessions.scan_sessions_fast` /
`mux_status_many`, `tracking.WorktreeRecord.claims`). This effort's job is
purely the **accelerator plumbing**: bundle those existing computations
behind one coalesced, resident-daemon-backed request per worktree (mirroring
`classify_daemon.py`'s already-landed, already-validated pattern exactly),
and document the wire contract precisely enough that a separate-plugin
consumer can reach it without importing agent-worktrees' Python at all.

## Context

`#2323` (the `plugin-process-hygiene` effort) already built and landed the
**first** concrete instance of the `work-coalescing-singleton` pattern:
`classify_daemon.py` + `work_coalescing_singleton` (vendored, pure-stdlib,
JSON-over-socket) + wiring into `cmd_status_monitor`/`_classify_records`.
That daemon coalesces concurrent `list --json --classify` batch-classification
passes for the *whole fleet* of one project onto one resident execution.

This effort is the **same pattern, a different fact set, a different key
shape**: not a per-project batch classification, but a **per-worktree status
bundle** — because the Tasks-board card renders one worktree's status at a
time, keyed by worktree id, and because a render loop's per-row cost must
stay small even at fleet scale (a per-project batch pass is too coarse-
grained for "the operator only opened the card for worktree X").

Reference precedent read in full during design (see `Concrete building
blocks` below): `classify_daemon.py`, its wiring into `cmd_status_monitor`
(`__main__.py`, `_classify_records`/`_classify_daemon_compute`), and the
vendored `work_coalescing_singleton` library (`client.py`, `server.py`) —
all in `plugins/agent-worktrees/`.

**Two things this effort does NOT build**, deliberately left to the
consumer's own effort:

1. **agent-dispatch's own client.** `agent-dispatch-tasks-pane-ux-overhaul`'s
   Phase 8 (and the now-blocked Phase 5) own building the actual cross-venv
   client and wiring it into `board_cli.py`/the Worktree Status card. This
   effort's job ends at: a running daemon, a documented wire contract, and a
   working in-process reference consumer inside agent-worktrees itself that
   proves the design end-to-end.
2. **A new claims-computation model.** The claims graph this effort exposes
   is `tracking.WorktreeRecord.claims`, already durable and already the
   authority per the vision's `Claims, leases, and obligations` concept —
   this effort surfaces it faster, it does not redesign it.

## Concrete building blocks (already exist — this effort assembles, does not reinvent)

| Fact | Existing source | Notes |
|------|------------------|-------|
| Git state (branch, ahead/behind, dirty, disposition) | `git_ops.classify_worktree()` | ~5 git calls per worktree; the same call `cmd_status`'s fleet loop and the classify daemon already make. |
| Session lineage + lifecycle history | `lineage_surfaces.worktree_lineage()` | Already a **bounded** JSON surface (session nodes, head transitions, handoffs, controller findings, reciprocal relation, graph) — exposed today via the `worktree-lineage` CLI command. Reused as-is. |
| Liveness (mux/Copilot lock) | `sessions.scan_sessions_fast()` / `sessions.mux_status_many()` | Same calls `cmd_status`'s fleet loop already makes per worktree. |
| Claims graph | `tracking.WorktreeRecord.claims` (+ `cmd_claims`'s existing read path) | Durable ledger; already the authority per the vision. |
| Asserted disposition (summary/title/follow-up) | `tracking.WorktreeRecord` (`title`, `resume_count`, disposition fields) + `disposition_history.py` | Already durable; `disposition_history` gives the "arc," not just the latest value. |

None of these need new computation logic. What's missing is: (a) one
coalesced entry point that assembles all five per worktree id, (b) a resident
daemon serving it (mirroring `classify_daemon.py`), and (c) a documented,
cross-venv-reachable wire contract.

## Request

Operator directive (recorded in the `agent-worktrees` vision's Provenance,
2026-09-20): build the accelerator the vision's
`external-status-consumer-contract` describes. Explicit requirements named at
that time:

- Treat the resident daemon as the live "database" for this state: every
  status read funnels through it; its own in-memory state is the current
  authority, best-effort persisted to disk, and should be "complete."
- State to track: worktree/session mapping per repo, session lineage + core
  lifecycle event history, last-known liveness (mux, Copilot lock), last-known
  git state, and the claims graph.
- Fast-cache access for all of the above; force-refresh available but
  **only** at explicit user/agent discretion — never triggered by a normal
  read just to function.
- `doctor` (or an equivalent full health/consistency pass) should leave the
  cache "fully up to date" for every fact it can actually confirm.
- Message/conversation history is explicitly **not** part of this cache — an
  on-demand pull from the owning session host instead (agent-bridge or
  whichever host owns that session).
- "Write the vision for agent-worktrees to reflect this requirement" — done
  in the prior session (see the vision's 2026-09-20 Provenance entries); this
  effort is the follow-through.

## Plan

### Phase 1 — Design (this document)
- [x] Confirm the fact bundle, key shape, wire `kind` name, freshness-marker
      shape, and force-refresh mechanism below with the operator before any
      code lands. **Approved 2026-09-20** (see the Journal entry below).

#### Proposed design

**Wire `kind`:** `"worktree_status"` (new, alongside `classify_daemon.py`'s
existing `"classify"` kind, same `CoalescingServer`/`work_coalescing_singleton`
transport — a **second daemon instance** on its own rendezvous fields, not a
shared one with `classify`, since the two have unrelated request shapes and
independent linger/TTL tuning needs).

**Key:** `worktree_status_daemon.coalescing_key(project, worktree_id)` —
length-prefixes `project` (`"<len(project)>:<project>:<worktree_id>"`) so the
split point is unambiguous regardless of either component's own content.
Coalesces concurrent requests for the *same* worktree, but never batches
unrelated worktrees into one compute call (unlike classify's per-project
batch key) — a render loop asking about worktree A must never wait on, or
receive, worktree B's compute. (A plain `"<project>|<worktree_id>"` was the
original design and an early implementation, but round 9's review caught
that it is not injective — neither component is restricted to reject `|`,
so two different `(project, worktree_id)` pairs could collide onto the
identical key. A future cross-venv consumer building its own client against
this wire contract must reproduce the same length-prefixed encoding, not
the pipe-delimited shape.)

**Payload:** `{"project": "<name>", "worktree_id": "<id>"}` — the daemon-side
`compute` callback resolves the record itself from `payload` (never trusts a
caller-serialized record), exactly mirroring `_classify_daemon_compute`'s own
contract.

**Response shape** (draft — refined during Phase 2 implementation):

```jsonc
{
  "worktree_id": "...",
  "project": "...",
  "machine": "...",
  "started_at": 1234567889.0,      // server wall-clock when assembly began
  "as_of": 1234567890.0,           // server wall-clock when this bundle completed
  "facts": {
    "git_state":  {"value": {...}, "confirmed": true,  "observed_at": 1234567890.0},
    "lineage":    {"value": {...}, "confirmed": true,  "observed_at": 1234567889.0},
    "liveness":   {"value": {...}, "confirmed": false, "observed_at": 1234567800.0},
    "claims":     {"value": {"resources": [...], "owner_ref": "..."}, "confirmed": true, "observed_at": 1234567890.0},
    "disposition":{"value": {...}, "confirmed": true,  "observed_at": 1234567890.0}
  }
}
```

Every fact is independently timestamped at its own observation (not the
bundle's start) and wrapped with `confirmed` + `observed_at` — never a single
all-or-nothing "fresh" flag for the whole bundle — per the vision's
`external-status-consumer-contract` (which now explicitly requires preserving
`Derived status`'s per-fact freshness/unconfirmed markers) and
`marked-not-multiplied-uncertainty`. `confirmed: false` means the compute
callback could not confirm that one fact this pass (e.g. a `git_ops` call
failed, or `mux_status_many` timed out for that worktree) — `value` in that
case is the **last durably-known value** where one exists (e.g. from the
tracking record itself), never fabricated, and never simply omitted.

**Force-refresh & the cache layer (revised 2026-09-20 — see Journal):**
`work_coalescing_singleton.CoalescingServer` (the classify_daemon precedent)
has **no persistent cache** — it only deduplicates truly concurrent requests
for the same key; the next call after one completes always recomputes from
scratch. That is sufficient for classify_daemon (subprocess-spawn avoidance
is its whole win) but does not honor the operator's explicit ask: "its own
in-memory state should be the true, current authority... fast-cache
access... force-refresh only at explicit discretion" — which requires a
**genuine cache with real staleness**, not just concurrency-deduplication.

So this daemon adds a layer `classify_daemon` doesn't have: a **durable,
SQLite-backed status cache** (`worktree_status_cache.py`), modeled after the
already-durable pattern this codebase family uses for exactly this shape
(agent-dispatch's own "single-writer, WAL-mode SQLite" store) rather than
`list_cache.py`'s file-sidecar-per-args-shape approach (a good precedent for
TTL/demand-registration *behavior*, but this effort follows the operator's
explicit "lightweight DB tech" steer over file-sidecar for the actual
storage engine):

- **One SQLite file** (WAL mode), one row per `(project, worktree_id)`.
  **As shipped** (Phase 2 implementation, revised from this original design
  sketch to match): a single `bundle_json` column holding the *entire*
  serialized bundle (all five facts, each already wrapped with its own
  `value`/`confirmed`/`observed_at` inside that JSON — see
  `_worktree_status_fact` in `__main__.py`) rather than per-fact SQLite
  columns, plus `computed_at` (when the bundle was last recomputed) and
  `demanded_at` (when a caller last asked about this worktree — the
  demand-registration signal, mirroring `list_cache.py`'s own concept, that
  tells the sweep which worktrees are worth refreshing). One row read/write
  is simpler and sufficient: the per-fact freshness structure already lives
  *inside* the bundle, so a second, column-level split added nothing a
  reader couldn't already get from the JSON itself.
- **In-memory dict is the hot-path read authority** — a request is answered
  from memory, never a live DB read on the request path; SQLite exists
  purely for **durability across a daemon restart** (warm-restore on boot)
  and as the vision's own "best-effort persisted to disk" requirement,
  never as the source a live request blocks on.
- **A background periodic sweep** (its own thread, mirroring the resident
  status-monitor's own sweep-interval convention) refreshes any demanded
  worktree whose cached entry has aged past a TTL — this is what makes an
  ordinary read fast (already-warm) *and* eventually-current without any
  caller ever forcing it, per `continuously-revalidated-freshness`/
  `freshness-is-pursued-not-assumed`.
- **Force-refresh** (`payload["force"]: true`) bypasses the cache's TTL
  check and recomputes immediately — still funneled through
  `CoalescingServer`'s own per-`(kind, key)` coalescing, so concurrent
  force-refresh requests for one worktree still join a single recompute,
  per `force-refresh-is-opt-in-not-implicit`. The in-process reference
  consumer (Phase 4) is the first caller to actually set this flag, from an
  explicit CLI flag — never set implicitly by an ordinary status read.
- A read for a worktree **never before demanded** (cold) computes inline on
  that first request (no sweep has run yet), then registers demand so
  subsequent sweeps keep it warm.

**Rendezvous:** namespaced `worktree_status_*` fields in the same monitor
lock file `classify_daemon.py` already writes into (`worktree_status_transport`,
`worktree_status_endpoint`, `worktree_status_token`,
`worktree_status_generation`) — same pattern as `classify_daemon
.rendezvous_fields`, never colliding with `classify_*`/`hook_*` fields already
there.

**Cross-venv contract (what a separate plugin needs to build a client) —
RESOLVED 2026-09-20, using the standard already-established discovery flow:**
1. Locate the monitor's lock file via the **exact same "standard discover
   flow" precedent that already exists** for a non-`agent_worktrees`-
   importing consumer: `scripts/hook_client.py` (the resident monitor's own
   hook client) resolves the runtime root as `~/.agent-worktrees`
   (`%USERPROFILE%\.agent-worktrees` on Windows) by default, or an
   installation-cell-scoped root when `COPILOT_EXTENSIONS_CONTEXT` is set,
   via the reusable `scripts/registry_root.py` helper
   (`resolve_registry_root`/`resolve_registry_context`, parameterized by
   `_PLUGIN_ID`). This is a **user-global, well-known location** — not a
   dedicated new pointer file — and it is already the exact mechanism a
   script outside `agent_worktrees`' own package uses today. A cross-venv
   consumer either vendors `registry_root.py` verbatim (it already carries
   no dependency on the rest of agent-worktrees' Python) or implements the
   simpler legacy-only fallback (`home/.agent-worktrees`) if it doesn't need
   installation-cell awareness — mirroring `board_cli.py`'s own existing
   stdlib-only precedent for reaching agent-dispatch's coordinator.
2. Read `status-monitor.lock` at that root, and pull the `worktree_status_*`
   fields (transport/endpoint/token/generation) out of it.
3. Speak the `work_coalescing_singleton` wire protocol directly (a documented,
   versioned, pure-JSON-over-socket protocol — no agent-worktrees Python
   import required; `client.py`'s `request()`/`subscribe()`/`release()` are
   ~80 lines of pure stdlib a consumer could vendor or reimplement against
   the documented `PROTOCOL_VERSION`).
4. On any miss (no daemon, unreachable, malformed response), report the
   requested facts as stale/unknown — never block the render/click path,
   per the vision's cross-venv fallback exception.

### Phase 2 — Cache layer + daemon compute callback ✅ DONE 2026-09-20
- [x] Add `worktree_status_cache.py`: SQLite (WAL) schema, an in-memory
      hot-path dict loaded from it on daemon start (warm-restore), a
      demand-registration table/method, and a TTL-aware
      `get_or_refresh(project, worktree_id, *, force, compute)` entry point
      that reads from memory, refreshes via `compute()` when stale/forced/
      cold, and write-through persists to SQLite on every refresh.
- [x] Add `worktree_status_daemon.py` (mirroring `classify_daemon.py`'s
      structure: `start_server`, `rendezvous_fields`,
      `endpoint_from_rendezvous`, a `_with_boot` client helper) — its
      `compute` callback delegates to `worktree_status_cache.get_or_refresh`
      rather than recomputing unconditionally, so `CoalescingServer`'s own
      concurrency-coalescing and this effort's TTL/demand cache compose
      cleanly (coalescing protects one recompute from a concurrency
      stampede; the cache decides *whether* a recompute is needed at all).
      Implemented as `build_cached_compute(cache, assemble)`, wrapping any
      `assemble(project, worktree_id) -> dict` into a `CoalescingServer`-
      shaped callback.
- [x] Add the actual fact-assembly function (`compute` in
      `worktree_status_compute.py`, re-exported as `_worktree_status_compute`
      via `__main__.py`, mirroring `_classify_daemon_compute`'s resolve-from-
      payload contract): resolve the record from `payload`, assemble the
      five facts from the existing building blocks table above, wrap each
      in `{"value", "confirmed", "observed_at"}`. This is the function the
      cache layer's `compute` parameter calls on a miss/TTL-expiry/force.
- [x] A background sweep thread (`worktree_status_daemon.start_sweep_thread`)
      walks the demand-registration table and calls `get_or_refresh` for any
      entry past its TTL, keeping demanded worktrees warm without any caller
      ever waiting on that work.
- [x] Unit tests: coalescing behavior (concurrent requests for the same
      worktree join one compute), per-worktree key isolation (worktree A's
      request never blocks on or returns worktree B's data), a fact that
      fails to compute renders `confirmed: false` with its last-known value
      (never an exception surfacing to the caller, never a fabricated
      value), force-refresh bypassing a fresh cache entry, a stale entry
      triggering an automatic refresh, and warm-restore from the SQLite
      file after a simulated daemon restart. 23 new tests total
      (`test_worktree_status_cache.py`, `test_worktree_status_daemon.py`,
      `test_worktree_status_compute.py`), all passing.

### Phase 3 — Wire into `cmd_status_monitor` ✅ DONE 2026-09-20
- [x] Start `worktree_status_daemon`'s server alongside the existing
      `hook_server`/`classify_server`, publish its rendezvous fields in
      `_lock_extra()`. Starting it must never be fatal to the monitor (same
      `try/except -> None` degrade `classify_server` already uses). Also
      starts the background sweep thread and constructs the
      `WorktreeStatusCache` at `_aw_runtime_home() /
      "worktree-status-cache.sqlite3"` (one host-level file, since the
      monitor serves every project on the host, not just one). Cleanup
      (`finally` block) stops the sweep, closes the server, closes the
      cache's SQLite connection.
- [ ] Confirm `cmd_status_monitor_restart`/the ZDD cutover path carries this
      daemon's generation token the same way it already does for
      `classify_server`/`hook_server` (no orphaned daemon after a monitor
      restart) — **not yet verified**, deferred to this effort's Validation
      Plan pass rather than blocking Phase 4.

### Phase 4 — In-process reference consumer ✅ DONE 2026-09-20
- [x] Wired a **new** `worktree-status-bundle --worktree <worktree-id>`
      command through
      the daemon first, uncoalesced direct-compute as fallback (mirroring
      `_classify_records`'s own `classify_with_boot` → fallback structure
      exactly, via `worktree_status_daemon.status_with_boot`). Added to
      `session_tracking_cli.py` (next to `worktree-lineage`, same
      cross-project `_find_tracking_file`/`_project_for_tracking_file`
      resolution pattern), re-exported and registered in `__main__.py`'s
      `COMMANDS` dict and `_NO_PROJECT_COMMANDS` set (it resolves the
      worktree's project itself, like `worktree-lineage`, so it never
      requires an ambient active project). Ships `--force-refresh`,
      completing Phase 5 in the same change (see below).
- [x] Unit tests (`test_worktree_status_bundle_cli.py`): worktree/project
      resolution failures report a JSON error; the daemon path is used and
      receives the correct payload when reachable; `--force-refresh`
      propagates into the payload's `force` field; a disabled/unreachable
      monitor degrades to the direct-compute fallback (`ensure_monitor is
      None`, confirming the monitor opt-out is honored, not just an
      unreachable-daemon path).

### Phase 5 — Force-refresh CLI surface ✅ DONE 2026-09-20 (landed together with Phase 4)
- [x] `--force-refresh` on `worktree-status-bundle` is the explicit,
      user/agent-discretion-only entry point — confirmed no other code path
      in `cmd_worktree_status_bundle` sets `force` implicitly (it defaults
      `False` and only flips via the explicit CLI flag).

### Phase 6 — Cross-venv wire-contract documentation ✅ DONE 2026-09-20
- [x] Rendezvous-discovery question **resolved 2026-09-20** (operator
      direction: use the standard discover flow, already-established, in a
      user-global location) — see Phase 1's design above:
      `scripts/hook_client.py` + `scripts/registry_root.py`'s existing
      `~/.agent-worktrees` (or installation-cell-resolved) root, no new
      pointer file.
- [x] Documented as a new "Cross-venv consumers" section in
      `docs/patterns/work-coalescing-singleton.md`, plus a new named
      consumer entry and a "Landed" Sequencing entry for this effort's own
      daemon.
- [x] Handed off explicitly to `agent-dispatch-tasks-pane-ux-overhaul`'s
      Phase 8/Phase 5: updated that effort's Runbook to point at this
      effort's landed daemon + documented contract.

### Phase 7 — Ground-truth audit + telemetry ✅ DONE 2026-09-21
- [x] `worktree-status-audit` CLI command (`worktree_status_audit.py`):
      reads the durable cache's own SQLite snapshot read-only, samples a
      random rotating subset (cached entries preferred -- real demand --
      falling back to every actively-tracked worktree across every
      registered project when the cache is cold/empty), and diffs each
      sampled entry against a fresh, cache-bypassing recompute via
      `worktree_status_compute.compute` -- the same ground truth a cache
      miss itself would produce.
- [x] Checks: git-state accuracy, liveness accuracy, cache-freshness
      bounds (TTL + sweep interval + slack), identity consistency (a
      cached or freshly-computed bundle's own `project`/`worktree_id`
      fields must agree with the key it's served under), and daemon
      liveness (lock file parses, its owner pid is actually alive, its
      rendezvous fields resolve, and -- given a real worktree to probe
      with -- it actually answers a real request through the daemon, not
      just that stale rendezvous fields are still on disk).
- [x] Appends one JSON telemetry line per run to a local log (default
      `<runtime home>/worktree-status-audit.jsonl`, `--log-path`
      override, `--no-log` to skip) -- a durable trail of the
      accelerator's refresh cadence (`cache_age_seconds`/
      `demand_age_seconds` per entry) and liveness over time, not just a
      single point-in-time read.
- [x] Exit code: nonzero when any mismatch, per-worktree error, or a
      failed daemon probe was found -- scriptable by a scheduled task
      (the operator's own hourly cadence, run via this session's
      `manage_schedule` for now).
- [x] Deployed and validated live 2026-09-21: `agent-worktrees update`
      picked up **dev215** (PR #3147's merge, the version deployed at the
      time -- not this PR's own, which bumps further as Phase 7 itself
      lands) cleanly; `worktree-status-bundle` confirmed working
      end-to-end against a real tracked worktree; `worktree-status-audit`
      confirmed correctly reporting `responsive: false` while the
      resident monitor was cold, then `responsive: true` and zero
      mismatches once it came up -- closing this effort's own "a live
      end-to-end check" Validation Plan item below.

## Validation Plan

- [x] Every phase's own unit tests pass (`plugins/agent-worktrees` suite).
- [x] A live end-to-end check: start `agent-worktrees status-monitor`,
      confirm the new rendezvous fields appear in the lock file, hit the
      daemon with a real worktree id via Phase 4's consumer, confirm a
      coalesced/cached response and a correct fallback when the monitor is
      killed mid-session. (Performed 2026-09-21 via `agent-worktrees
      update` + `worktree-status-bundle` + Phase 7's own audit tool --
      see Phase 7's journal entry.)
- [ ] Concurrent-request coalescing: N simultaneous callers for the same
      worktree id receive one compute's answer (mirroring `#2323`'s own
      `concurrent-callers-during-cold-boot` validation scenario), and a
      different worktree id's concurrent request is never blocked by it.

## Proposal

_Pending operator review of Phase 1's design above._

## Journal

### 2026-09-20 — Kickoff + Phase 1 design drafted
- Effort created following the operator's explicit request ("make a proper
  plan, document it in the effort, get its architecture reviewed, and then
  start building"), per the directive already recorded in the
  `agent-worktrees` vision's 2026-09-20 Provenance entries.
- Read `classify_daemon.py`, its `cmd_status_monitor`/`_classify_records`
  wiring, and `work_coalescing_singleton`'s wire protocol (`client.py`) in
  full as the reference precedent.
- Surveyed existing agent-worktrees modules for each required fact and found
  **all five already have an owning module** (`git_ops`, `lineage_surfaces`,
  `sessions`, `tracking.WorktreeRecord.claims`, `disposition_history`) — this
  effort's scope is accelerator plumbing only, not new computation, sharply
  narrowing what Phase 2 actually has to build.
- Drafted the wire `kind`/key/payload/response shape and force-refresh
  mechanism above (Phase 1), deliberately per-worktree-keyed (not a
  per-project batch like classify) since the card's own consumption pattern
  is "one worktree at a time."
- Flagged one genuinely open question for Phase 6 rather than assuming an
  answer: how a cross-venv consumer discovers the rendezvous lock file in
  the first place (reads agent-worktrees' existing lock path directly, vs.
  a dedicated smaller pointer file agent-worktrees publishes for exactly
  this purpose).
- **Not yet implemented** — this is the design-review checkpoint the
  operator asked for; Phase 2 starts once the design above is confirmed.

### 2026-09-20 — Design approved; rendezvous-discovery question resolved
- Operator approved Phase 1's design as drafted, with one directive: use the
  standard discover flow already established, in a user-global location —
  not a new mechanism.
- Found the exact existing precedent while resolving this:
  `scripts/hook_client.py` (the resident monitor's own hook client, which
  itself does not import the `agent_worktrees` package) resolves the
  runtime root via `scripts/registry_root.py`'s `resolve_registry_root` —
  `~/.agent-worktrees` (or `%USERPROFILE%\.agent-worktrees`) by default, an
  installation-cell-scoped root when `COPILOT_EXTENSIONS_CONTEXT` is set.
  This closes Phase 6's open question immediately: a cross-venv consumer
  either vendors `registry_root.py` verbatim or implements the simpler
  legacy-only fallback, then reads `status-monitor.lock` at that root — no
  new pointer file needed. Recorded in Phase 1's design and marked Phase 6's
  first item done.
- Phase 2 starts next.

### 2026-09-20 — Design refined: a genuine durable cache, not just coalescing
Started Phase 2 by reading `work_coalescing_singleton/server.py`'s actual
`CoalescingServer.handle_request` in full (not just its docstrings) and found
a real gap: it **only deduplicates concurrent requests for the same key** —
once one compute finishes, the very next call (even microseconds later)
always recomputes from scratch. There is no persistent cache at all in that
component, so "force-refresh" as I'd drafted it in Phase 1 had nothing
meaningful to bypass — every read was already maximally fresh, which doesn't
match the operator's explicit ask ("its own in-memory state should be the
true, current authority... fast-cache access... force-refresh only at
explicit discretion" implies genuine staleness a normal read tolerates and a
forced one doesn't).

Surfaced this gap to the operator rather than silently building something
that wouldn't actually satisfy their stated requirement. Resolved direction:
build a genuine in-daemon cache (not `list_cache.py`'s file-sidecar-per-args
pattern, which is a good behavioral precedent but not the storage engine
wanted here) using SQLite (WAL mode) — the operator's own steer toward
"actual lightweight DB tech that supports durable sidecar-file sync,"
matching agent-dispatch's own already-established "single-writer, WAL-mode
SQLite" convention in this same codebase family. Revised Phase 1's design
(see above) to add `worktree_status_cache.py`: an in-memory hot-path dict
(the true read authority, never blocked on disk I/O) backed by a SQLite file
for durability/warm-restore, a demand-registration table so a background
sweep knows which worktrees to keep warm, and a TTL check the `force`
payload flag explicitly bypasses (still coalesced via `CoalescingServer` so
concurrent force-refreshes for one worktree still join a single recompute).
Phase 2's own Plan items rewritten to build this cache layer alongside the
daemon wrapper and the fact-assembly function.

### 2026-09-20 — Phases 2-3 implemented and landed
- Built `worktree_status_cache.py` (SQLite/WAL-backed, in-memory hot-path
  dict, demand registration, `get_or_refresh`/`sweep_due`) and
  `worktree_status_daemon.py` (`start_server`, `rendezvous_fields`,
  `endpoint_from_rendezvous`, `status_via_daemon`/`status_with_boot` mirroring
  `classify_daemon`'s own client helpers, `build_cached_compute` wiring the
  cache into a `CoalescingServer`-shaped callback, `start_sweep_thread`).
- Added `_worktree_status_compute(project, worktree_id)` in `__main__.py`,
  assembling all five facts from the building-blocks table
  (`git_ops.classify_worktree`, `lineage_surfaces.worktree_lineage`,
  `sessions.verify_worktree_active`, `record.resources`,
  `disposition_history.read` + the record's own disposition fields) — every
  fact independently wrapped via `_worktree_status_fact`, a single fact's
  exception degrading to `confirmed: false` rather than aborting the whole
  bundle or raising past the daemon to every joined caller.
- Wired both into `cmd_status_monitor`: the server starts alongside
  `hook_server`/`classify_server` (same never-fatal `try/except` degrade),
  publishes its own namespaced rendezvous fields, starts the sweep thread,
  and all three are torn down in the existing `finally` cleanup block.
- **Found and fixed a real regression while validating**:
  `test_classify_daemon_started_published_in_lock_and_closed_on_exit` spied
  on `CoalescingServer.close` (the class both `classify_daemon` and this
  effort's daemon import from the same vendored library) expecting exactly
  one close — now there are two `CoalescingServer` instances, so the count
  needed updating to 2, plus new assertions for the `worktree_status_*`
  rendezvous fields. Fixed directly rather than loosening the test.
  Confirmed via `git stash` that a separate failing test
  (`test_monitor_retire_handoff_predecessor_preserves_identity_guard`) is
  pre-existing and unrelated to this change (fails identically with this
  effort's `__main__.py` wiring reverted).
- 23 new tests total across `test_worktree_status_cache.py`/
  `test_worktree_status_daemon.py`/`test_worktree_status_compute.py`, all
  passing; full `test_status_monitor.py` + `test_classify_daemon*.py`
  suites: 93 passed + the one pre-existing unrelated failure noted above.
- `cmd_status_monitor_restart`/ZDD-cutover generation-token carrying for
  this new daemon is **not yet verified** — deferred to the Validation Plan
  pass, not blocking Phase 4.

### 2026-09-20 — Phases 4-5 implemented and landed
- Added `agent-worktrees worktree-status-bundle --worktree <worktree-id>
  [--force-refresh]`: the in-process reference consumer proving the
  accelerator design end-to-end, mirroring `worktree-lineage`'s own
  cross-project
  resolution (`_find_tracking_file` + a new `_project_for_tracking_file`
  reuse) and `_classify_records`'s own daemon-first/direct-compute-fallback
  structure (`worktree_status_daemon.status_with_boot`, honoring the
  resident-monitor opt-out the same way classify's fast path does).
- `--force-refresh` (Phase 5) landed in the same change — the only path
  that sets the daemon payload's `force` flag; confirmed no other code path
  does.
- 5 new tests (`test_worktree_status_bundle_cli.py`): resolution failures,
  daemon-reachable payload shape, force-refresh propagation, and the
  monitor-opted-out fallback path (asserting `ensure_monitor is None`, not
  just "daemon unreachable").
- **Caught and fixed my own editing mistake mid-change**: adding
  `"worktree-status-bundle"` to `_NO_PROJECT_COMMANDS` via a search-replace
  accidentally dropped the adjacent `"conclude-session"` entry from that
  set — caught by re-reading the diff before committing, not by a test
  (no existing test asserts every entry of that set). Fixed before landing.
- Both phases' plan items marked done above.

### 2026-09-20 — Full-suite validation, issue filed, Phase 6 landed
- Ran the full ~4925-test agent-worktrees suite for a final regression
  check. Hit scattered failures starting ~19% in, in files unrelated to
  this effort (`test_execution_leg_cli.py`, `test_ext_reload_warning_
  retirement.py`, others). Confirmed these are **pre-existing full-suite
  ordering fragility, not a regression from this work**: the affected files
  pass cleanly standalone, and the failures reproduce well before this
  effort's own alphabetically-late `test_worktree_status_*.py` files would
  even run in collection order. Filed
  [ThomasMichon/copilot-extensions#3093](https://github.com/ThomasMichon/copilot-extensions/issues/3093)
  per the operator's explicit direction ("something will need to tackle it
  holistically") rather than attempting a narrow fix inside this effort.
- Landed Phase 6: added a "Cross-venv consumers" section to
  `docs/patterns/work-coalescing-singleton.md` documenting the
  `hook_client.py`/`registry_root.py` discovery precedent generically (not
  just for this effort's own daemon), a new named-consumer entry for this
  effort, and a "Landed" Sequencing entry.
- Updated `agent-dispatch-tasks-pane-ux-overhaul`'s Runbook to point at
  this now-landed daemon and documented contract (see that effort's own
  Journal for the corresponding entry).
- All six planned phases now done. Remaining before calling this effort
  Done: the Validation Plan's live end-to-end check (start a real
  `status-monitor`, hit the daemon with a real worktree id) and confirming
  `cmd_status_monitor_restart`'s ZDD-cutover carries this daemon's
  generation token — both still open, tracked in the Validation Plan below.

### 2026-09-20 — PR opened, rebased over concurrent version bumps, review fixes landed
- Opened PR #3102. `create-pr` hit genuine rebase conflicts against
  `origin/main` (three concurrent version bumps to `1.5.5-dev18x` from
  unrelated PRs merged while this effort was in progress) — resolved
  manually (bump past the highest conflicting version each time, `dev196`
  then `dev197`), no content conflicts in `__main__.py` itself.
- **Copilot review caught four real, fixable issues** — all addressed
  directly rather than dismissed:
  1. **Git-state fallback on failure.** A transient `classify_worktree`
     exception rendered `git_state` as `{"value": None, ...}` even when the
     durable record already carried a last-known state
     (`WorktreeRecord.git_state`). Fixed to fall back to
     `{"state": record.git_state}` when set — the "confirmed: false ...
     best available last-known value" contract wasn't actually being
     honored for this specific failure path. New regression test.
  2. **Sweep-thread shutdown ordering.** `start_sweep_thread`'s return value
     was discarded, so shutdown never waited for it — a sweep mid-`_persist`
     could still write after `cache.close()`, racing a successor monitor's
     own SQLite writer. Fixed: retain the thread handle, `join(timeout=5)`
     before closing the server/cache. New unit test proving the thread
     actually stops promptly after its stop event is set.
  3. **SQLite cross-thread access.** `WorktreeStatusCache` is constructed on
     the monitor's startup thread but read/written from `CoalescingServer`
     request-handler threads and the sweep thread; `sqlite3.connect`'s
     default `check_same_thread=True` would raise on every post-boot write
     — silently swallowed by the existing `except sqlite3.Error: pass` in
     `_persist`, so the daemon *appeared* to work while nothing after boot
     was ever actually durable. Fixed with `check_same_thread=False` (safe:
     every access already serializes through the cache's own `self._lock`).
     New test proves a write from a different thread than construction
     actually reaches disk (verified via a fresh `WorktreeStatusCache`
     instance reading it back).
  4. **CLI syntax mismatch in documentation.** The PR/effort text advertised
     a positional `worktree-status-bundle <id>` invocation, but the actual
     parser (mirroring `worktree-lineage`'s own option-only convention)
     only accepts `--worktree`/`--worktree-id`. Rather than add a positional
     form that would make this command inconsistent with its sibling,
     fixed every occurrence of the incorrect syntax in this README, the
     `agent-dispatch-tasks-pane-ux-overhaul` handoff note, and
     `docs/patterns/work-coalescing-singleton.md`.
- 3 new regression tests total for items 1-3 (34 tests now across the four
  `test_worktree_status_*.py` files). Full targeted re-run (cache, daemon,
  compute, CLI, classify_daemon, status_monitor) clean except the same
  confirmed-pre-existing, unrelated failure noted earlier.

### 2026-09-20 — Second review round: 8 more findings, all fixed
Pushed the round-1 fixes; the rebase (needed again, another concurrent
version bump landed on `main` in the interim — resolved the same way,
`dev197` → `dev198`) triggered a fresh Copilot review that caught 8 further
issues, 2 of them HIGH severity. All fixed directly:

1. **HIGH — drain race on monitor shutdown.** `CoalescingServer.close()`
   stops its own accept/reaper threads but does not wait for an
   already-dispatched request-handler thread to finish (no drain primitive
   exists in the vendored library). A handler still mid-compute when
   shutdown reached `cache.close()` could therefore still call into the
   cache afterward. Fixed with an explicit `_closed` flag inside
   `WorktreeStatusCache`, checked by `_open()`: a late call finds the cache
   closed and skips SQLite entirely (still answers in-memory-only) instead
   of reopening/writing through a torn-down connection. New test proves a
   post-`close()` call never reaches disk.
2. **HIGH — path traversal.** `worktree_status_daemon.build_cached_compute`
   only checked `project`/`worktree_id` for non-emptiness, but
   `_worktree_status_compute` uses them to build filesystem paths. A client
   able to read the daemon's rendezvous token could submit a crafted id
   (`../`) to load a YAML outside the selected project's tracking dir.
   Fixed with the same path-safety check `_find_tracking_file` already
   uses (`_is_safe_identity_token`, no `/`/`\`/`..`) before ever dispatching
   the request. New test covering both fields and multiple traversal
   shapes.
3. **Liveness fallback on failure** — the same "confirmed:false ... retain
   the last-known value" contract wasn't honored for the liveness fact
   either (only fixed for `git_state` in round 1): a transient
   `verify_worktree_active` failure now falls back to the record's own
   cached `mux_live`/`bound_live` hints instead of `None`. New test.
4. **`mkdir` `OSError` not caught.** `_connect()`'s `Path.mkdir` call can
   raise `OSError` before ever reaching sqlite3; the original `_open` only
   caught `sqlite3.Error`, so a read-only/unavailable runtime home
   propagated out of the constructor instead of degrading like every other
   durability failure. Fixed; new test.
5. **Non-dict warm-restore rows.** A corrupt/older SQLite row could parse
   to a non-dict JSON value (a list, a scalar); treating it as a real
   bundle would silently and permanently suppress recomputation (the
   fresh-looking entry blocks a refresh forever). Fixed: validate
   `isinstance(bundle, dict)` before inserting into `_entries`. New test.
6. **Synchronous SQLite writes on every fresh-cache read.** The original
   `get_or_refresh` called `_persist` on every read (even a fresh-cache
   hit) just to update the demand timestamp, defeating the "memory-only
   fast read" design goal — SQLite's busy-timeout (up to 2s) could
   serialize otherwise-independent worktrees' requests behind one slow
   disk write. Fixed: a fresh hit updates the in-memory `demanded_at` only;
   only an actual recompute persists (which is what durability exists for
   in the first place).
7. **Sweep race with a concurrent request-driven refresh.** `sweep_due`
   samples then recomputes outside the lock (deliberately, for the same
   reason `get_or_refresh` does); it was previously publishing its result
   unconditionally, so a concurrent request-driven refresh (a miss or an
   explicit force) for the same key finishing first could be silently
   overwritten by the sweep's now-stale answer for a full TTL window.
   Fixed: re-check the entry's `computed_at` hasn't advanced past what was
   sampled before publishing; discard the sweep's result if it has. New
   test simulates the exact race (a refresh callback that itself triggers
   a concurrent force-refresh mid-sweep).
8. **Stale CLI syntax in one more doc.** `agent-dispatch-tasks-pane-ux-
   overhaul/README.md` still had one un-fixed `worktree-status-bundle <id>`
   instance (line 125) missed in round 1's sweep. Fixed.
- 6 new regression tests this round (43 total across the four
  `test_worktree_status_*.py` files). Full targeted re-run clean except the
  same confirmed-pre-existing, unrelated failure.

### 2026-09-20 — Third review round: 6 more findings (2 real bugs, 4 stale carryovers)
Pushed round 2's fixes; the next review caught 2 genuinely new issues plus
re-flagged 4 already-addressed-but-stale-context findings:

1. **HIGH — sweep bypassed request-path identity validation.** The
   background sweep calls `_worktree_status_compute` directly (not through
   `build_cached_compute`'s own validation), and warm-restored SQLite rows
   are never re-validated on load — a corrupt/tampered row could carry a
   `../` id and have the sweep read outside the selected project's tracking
   dir. Fixed by factoring the validation into a shared
   `worktree_status_daemon.validated_refresh()` wrapper, used by both
   `build_cached_compute` (the request path) and the sweep's own refresh
   callback in `cmd_status_monitor`'s wiring. New test.
2. **MEDIUM — disposition history read the ambient project, not the
   explicit one.** `disposition_history.read()`/`history_path()` resolved
   the sidecar through `cfg.tracking_dir()` (the ambient active project) —
   but neither the daemon nor the new CLI command ever sets one; a fresh
   monitor process would raise (caught, degrading the whole disposition
   fact to unconfirmed/`None`) or, worse, read another project's history if
   one happened to be ambiently active. Added an explicit `tracking_path`
   parameter to both functions (default preserves the old ambient
   behavior for every other existing caller) and passed it from
   `_worktree_status_compute`. New tests at both the `disposition_history`
   unit level and the `_worktree_status_compute` integration level.
- Also fixed two smaller, genuinely still-present bugs found while
  addressing the above: `get_or_refresh`'s recompute path was stamping
  `computed_at`/`demanded_at` from *before* `compute()` ran rather than
  after completion — a slow git refresh exceeding the TTL could store an
  already-expired result, immediately triggering another refresh on the
  very next read; and `sweep_due`'s expired-entry pop sampled `demanded_at`
  once and then popped unconditionally, so a concurrent refresh extending
  an entry's life between sampling and the pop could have its work deleted
  — both now re-check at the point of the actual mutation, not just at
  sampling time.
- The remaining 4 re-flagged findings (documented CLI syntax, drain
  request handlers, and two already-fixed items appearing as duplicates)
  were stale review context against files that hadn't changed since round
  2's fix — traced the actual source: the **PR description itself** still
  had the old `worktree-status-bundle <id>` syntax (never updated when the
  effort README/docs were fixed), which the review was diffing against.
  Fixed the PR description and added the required Documentation-impact
  statement.
- 4 new regression tests this round (47 total across the four
  `test_worktree_status_*.py` files, plus 1 new `test_disposition_history.py`
  test for the `tracking_path` parameter itself). Fixed two test-authoring
  mistakes caught by re-running immediately: a stubbed `disposition_history
  .read` in `test_worktree_status_compute.py` needed its signature updated
  for the new `tracking_path` kwarg, and a stray orphaned line survived an
  edit in `test_disposition_history.py`. Full targeted re-run clean except
  the same confirmed-pre-existing, unrelated failure.

### 2026-09-20 — Fourth review round: 4 more real findings, plus stale-carryover triage
Pushed round 3's fixes; the next review caught 4 further genuine issues (1
HIGH) and re-flagged 3 already-fixed items as stale carryovers:

1. **HIGH — cross-process cutover overwrite race.** The round-2 `_closed`
   guard only protects one process' own connection; during a ZDD cutover, a
   still-in-flight handler on the *outgoing* monitor could persist a stale
   write to the *same durable SQLite file* after the *successor* monitor
   already wrote something newer. Fixed by making the upsert itself
   monotonic regardless of which process/generation wrote it: `_persist`'s
   `ON CONFLICT ... DO UPDATE` now carries a `WHERE excluded.computed_at >
   worktree_status_cache.computed_at` guard, so an older write can never
   overwrite a newer row already on disk. New test writes a "newer" row
   directly, then attempts an "older" write, and asserts the newer one
   survives.
2. **HIGH — `"."` accepted as a safe project identity token.**
   `cfg.project_dir(".")` builds `f".{project}"` = `".."`, so a bare `"."`
   alone (no separators, no literal `".."`) already escapes the intended
   directory — the traversal regex from round 2 didn't catch this exact-
   match case. Fixed by mirroring `lineage_surfaces._safe_identity_token`
   exactly (its own reference implementation already excludes `{".",
   ".."}` alongside separators). New test.
3. **MEDIUM — the monitor's idle-exit never counted status-cache demand.**
   A direct worktree-status consumer (agent-dispatch's own future card, or
   the `worktree-status-bundle` CLI) could keep asking this cache without
   ever touching a live mux session, a Picker project, or `list_cache`
   demand — the three signals the monitor's empty-strikes decision already
   checks. After three empty sweeps the monitor would tear itself (and this
   daemon's own background refresh) down while a real consumer was still
   active. Added `WorktreeStatusCache.has_active_demand()` and wired it
   into both the empty-strikes increment and the retry-before-break check
   in `cmd_status_monitor`, alongside the existing mux/Picker/list_cache
   signals. New test for the method itself (the monitor-loop wiring is
   integration-level, covered by inspection + the existing status-monitor
   suite still passing).
4. **MEDIUM — demand-expired entries were never pruned from durable
   storage.** `sweep_due` (and originally `_warm_restore`) only dropped an
   expired entry from the in-memory dict, never deleted its SQLite row —
   so the demand TTL bounded nothing on disk: every worktree ever viewed
   kept a row forever, and every restart's warm-restore paid to reload
   (then immediately re-drop, after round 3's fixes) all of them. Added
   `_delete_row` and wired it into both `sweep_due`'s expiry path and
   `_warm_restore` (a row already past its demand TTL by restart time is
   pruned rather than rehydrated at all). Two new tests.
5. **Design-doc drift.** The effort's own Phase 1 design sketch still
   described per-fact SQLite columns (`value_json`/`confirmed`/
   `observed_at`); the shipped Phase 2 schema instead stores one
   `bundle_json` blob (the per-fact structure already lives inside that
   JSON) plus `computed_at`/`demanded_at`. Corrected the design section
   above to describe what was actually built, with a note on why the
   simpler shape was sufficient.
6. **Stale carryovers, verified and left alone.** Three findings
   (`worktree-status-bundle <id>` positional syntax in two files, the
   Documentation-impact statement) were re-flagged against content already
   fixed in earlier rounds — verified via direct file inspection that the
   current on-disk content is correct in every case; these are the review
   tool re-surfacing prior-round threads, not new problems.
- 6 new regression tests this round (53 total across the four
  `test_worktree_status_*.py` files). Full targeted re-run (cache, daemon,
  compute, CLI, classify_daemon, status_monitor) clean except the same
  confirmed-pre-existing, unrelated failure.

### 2026-09-20 — Fifth review round: 3 more findings, all fixed
Pushed round 4's fixes; the next review caught 3 further genuine issues:

1. **Missing coordinated `metadata.version` bump.** `AGENTS.md` requires
   bumping `plugin.json`, `pyproject.toml`, and the plugin's
   `marketplace.json` entry for any plugin change — but agent-worktrees
   specifically also requires bumping the marketplace catalog's own
   top-level `metadata.version` field, a separate field from any
   per-plugin version. This was missed across every prior round's version
   bump. Fixed by bumping `metadata.version` to `1.7.7-dev172` alongside
   the usual per-plugin bump.
2. **CLI direct-compute fallback bypassed identity validation.**
   `session_tracking_cli.py`'s `cmd_worktree_status_bundle` falls back to
   calling `_worktree_status_compute` directly when no daemon is
   reachable. Unlike the request path (`build_cached_compute`) and the
   sweep path, this fallback wasn't wrapped in `validated_refresh` — and
   `record.worktree_id` here comes from on-disk YAML content, not
   necessarily the already-validated `args.worktree_id` the CLI was
   invoked with. Fixed by wrapping the fallback with the same
   `validated_refresh` guard used by the other two call sites.
3. **Sweep's durable-row deletion had a race with a concurrent re-persist.**
   `sweep_due`'s expiry-prune sampled a demand-expired entry, then issued
   a plain `DELETE` outside the lock — a concurrent refresh could persist
   a fresh row for the same key in between, and the plain delete would
   wipe it out. Fixed by moving the deletion inside the lock and
   conditioning it on `if_demanded_at` (the sampled `demanded_at` value):
   the delete is now a no-op unless the row's `demanded_at` still matches
   what was sampled. Applied the same guard to `_warm_restore`'s
   startup prune.

Added a regression test per fix: a CLI test that a tampered
`record.worktree_id` is rejected by the fallback path
(`test_direct_fallback_rejects_a_tampered_record_worktree_id`), and a
cache test proving `_delete_row(..., if_demanded_at=...)` is a no-op
against a row a concurrent write has already moved past
(`test_delete_row_never_removes_a_row_re_persisted_after_the_sampled_demanded_at`).
65 total tests across the four `test_worktree_status_*.py` files plus
`test_disposition_history.py`; full targeted re-run (cache, daemon,
compute, CLI, disposition_history, classify_daemon, classify_daemon_wiring,
status_monitor, lineage_surfaces) clean except the same
confirmed-pre-existing, unrelated `test_status_monitor.py` failure.
`check-version-consistency.py` and a fresh `__main__` import both clean;
`check-module-size.py` shows the same pre-existing, non-blocking overage.

### 2026-09-20 — Sixth review round: 4 more real findings, 3 stale carryovers
Pushed round 5's fixes via `push-changes`; the rebase brought in an
unrelated upstream commit that had already split `_worktree_status_compute`
out of `__main__.py` into its own `worktree_status_compute.py` module
(shrinking the module-size overage from ~29,436 to ~29,273 lines -- still
over the grandfathered ceiling, still non-blocking, no action needed) and
had already bumped the per-plugin version surfaces (`plugin.json`,
`pyproject.toml`, the marketplace plugin entry) to `1.5.5-dev200`, ahead of
where this effort's own commits had left them. The next review caught 3
further genuine issues plus 3 stale re-flags:

1. **Per-fact timestamps described the start of the bundle, not each
   fact's own observation.** `now = time.time()` was captured once before
   any probe and reused for every fact's `observed_at` and the bundle's
   `as_of`. Since `git_state`'s probe runs `classify_worktree(fetch=True)`
   (can take seconds), every other fact's `observed_at` -- and the
   returned `as_of` itself -- described when assembly *began*, not when
   each value was actually observed; `as_of` could already be stale by the
   time a caller received it. Fixed by calling `time.time()` fresh at each
   fact's own completion and computing `as_of` after every probe finishes;
   added a new `started_at` field (bundle-assembly start) alongside it so
   a caller can see both endpoints.
2. **The claims fact only exposed the outward ledger, not the inward
   link.** `record.resources` (what a worktree claims) was serialized, but
   `record.owner_ref` (whose claim this worktree itself answers to -- the
   durable *backward* link, e.g. a knowledge worktree paired to a harness
   one) was dropped. The vision's claims contract requires answering
   ownership in both directions, and the existing status surfaces expose
   both. Changed the `claims` fact's `value` from a bare list to
   `{"resources": [...], "owner_ref": ...}`; updated all in-repo
   references (the effort README's own Phase 1 design example) to match.
3. **`_is_safe_identity_token` didn't actually mirror
   `lineage_surfaces._safe_identity_token`.** It rejected `/`, `\`, `..`,
   and a lone `.`, but not an embedded NUL byte -- `lineage_surfaces`'s own
   predicate does. A crafted id like `wt\x00x` passed this guard, then
   `Path`/`tracking.load_record_by_id` raised a `ValueError` past the
   documented fallback -- the daemon request path would drop the socket
   instead of returning its documented error, and the CLI's direct
   fallback would propagate a raw exception instead of degrading. Fixed
   the regex to reject `\x00` too.
4. Three findings ("Add required coordinated version bumps", "Support the
   documented positional command syntax", "Add required Documentation-impact
   statement") were re-flagged against content already correct on disk and
   in the current PR description -- verified directly (`plugin.json`,
   `pyproject.toml`, and the marketplace entry all already at the same
   version the rebase brought in; the PR description already uses
   `--worktree <id>` throughout, never a bare positional form; the PR
   description already carries a "Documentation impact:" statement
   matching the final diff). Same stale-carryover pattern observed in
   round 3 -- the review tool re-surfacing prior threads against text it
   had already flagged once, not new problems.

Added 2 new regression tests
(`test_claims_fact_includes_the_owner_ref_backward_link`,
`test_each_fact_is_timestamped_at_its_own_observation_not_bundle_start`)
plus a NUL-byte case added to the existing path-traversal parametrization
in `test_worktree_status_daemon.py`. 67 total tests across the four
`test_worktree_status_*.py` files plus `test_disposition_history.py`; full
targeted re-run (cache, daemon, compute, CLI, disposition_history,
classify_daemon, classify_daemon_wiring, status_monitor) clean except the
same confirmed-pre-existing, unrelated `test_status_monitor.py` failure.
A fresh `__main__` import is clean.

### 2026-09-21 — Seventh review round: 1 real bug, 3 stale docs, PR metadata drift
Pushed round 6's fixes; the next review caught one genuine bug plus three
doc/metadata staleness issues (no new logic bugs):

1. **`InProcessRuntime.start()` could leak a partial startup on failure.**
   If the cache or server came up but a later step (server start, sweep
   thread start) raised, the handler only cleared `self.server`, leaking an
   open SQLite connection and/or a still-running server/threads -- and a
   still-non-``None`` `self.cache` could keep `has_active_demand()`
   reporting activity, holding the monitor alive on a runtime that no
   longer actually serves. Fixed by calling `self.shutdown()` itself from
   the failure handler (safe against any partial state, since every step
   already null-checks) before clearing all fields to a fresh, fully
   torn-down state.
2. This effort README's header claimed `copilot-extensions` uses
   "direct-push `main`", contradicting the repo's actual PR-gated workflow
   -- fixed to say "PR-gated `main`".
3. The Phase 1 design checkbox was still unchecked with a stale "In
   review" note despite being approved the same day (see the Journal
   entry above) -- checked it off and pointed at the approval.
4. The Phase 2 checklist still described the fact-assembly function as
   living in `__main__.py`; corrected to point at
   `worktree_status_compute.py` (the actual owning module after round 5/6's
   split), noting `__main__.py` only re-exports it.
5. The PR description cited a stale `1.5.5-dev198` version; the actual
   manifests (`plugin.json`, `pyproject.toml`, marketplace entry) publish
   `1.5.5-dev201` -- updated the PR description to match.

### 2026-09-21 — Eighth review round: 2 more real bugs, PR metadata drift again
Pushed round 7's fixes (a rebase over two more merged upstream PRs --
`agent-worktrees: extract namespace CLI from __main__ (#3131)` shrank
`__main__.py`'s actual size further, so the module-size baseline was
re-tightened to the file's new true line count rather than staying at the
prior, now-stale widened value). The next review caught 2 more genuine
issues plus the same PR-metadata staleness pattern:

1. **`has_active_demand()` missed an in-flight cold request.** It only
   consulted `WorktreeStatusCache.has_active_demand()`, which gets an entry
   only once a request *completes* and publishes a result. A fresh cold
   request has a live `CoalescingServer` subscriber the entire time its
   compute is running, with nothing in the cache yet -- so with zero other
   mux/Picker/list activity, the monitor's own empty-strikes idle-shutdown
   could close this runtime (and its cache) while that first compute was
   still in flight, dropping its result. Fixed by also checking
   `CoalescingServer.subscriber_count() > 0` (already tracked by the
   vendored library for its own ref-counted idle-exit) alongside the cache
   check.
2. **The liveness fact was unconditionally `confirmed=True`.**
   `sessions.verify_worktree_active` is itself fail-open (it swallows a mux
   or reclaim probe failure internally and returns a default/partial
   `LiveVerdict` rather than raising), so this fact's own `try/except`
   around the call never actually fired on a degraded probe -- every
   verdict looked fully confirmed regardless. Added a `probes_ok: bool`
   field to `LiveVerdict` (set `False` when either probe swallows an
   exception) and gated the liveness fact's `confirmed` marker on it
   instead of a hardcoded `True`.
3. Same PR-metadata staleness pattern as round 7: the PR description's
   version reference had drifted again (this round's rebase bumped
   `1.5.5-dev201` -> `dev202`) -- updated to match.

Added 2 new regression tests
(`test_liveness_fact_is_unconfirmed_when_verify_worktree_active_degrades`,
`test_in_process_runtime_has_active_demand_counts_a_live_subscriber`).

### 2026-09-21 — Ninth review round: 3 more real bugs, 1 test-coverage gap
Pushed round 8's fixes (guards+lint transiently failed on an unrelated
`agent-index`/payload-invocation test race, confirmed flaky by a clean
re-run of the same commit -- no code change needed). The next review
caught 3 further genuine issues plus one test-coverage gap in round 8's
own fix:

1. **The coalescing key was not injective.** `cmd_worktree_status_bundle`
   built its `CoalescingServer` key as a plain `f"{project}|{worktree_id}"`
   -- but `_is_safe_identity_token` never restricted `|` (only `/`, `\`,
   `..`, NUL matter for the *filesystem* path each component later builds),
   so `("a", "b|c")` and `("a|b", "c")` collided onto the identical key,
   letting two different worktrees' concurrent requests join the same
   in-flight computation and each receive the *other's* bundle. Fixed by
   adding `worktree_status_daemon.coalescing_key(project, worktree_id)`,
   which length-prefixes `project` so the split point is unambiguous
   regardless of either component's content, with no new character
   restriction needed.
2. **`status_via_daemon` (the no-boot helper) sent no `client_id`.** Unlike
   `status_with_boot`, it never registered a subscriber, so round 8's own
   `has_active_demand()` fix couldn't see a request in flight through this
   path either -- the same drop-in-flight-result race round 8 fixed for
   the booting path. Fixed by carrying a per-call `client_id` and releasing
   it in a `finally`, matching `status_with_boot` exactly.
3. **The liveness fact still discarded last-known hints on a degraded
   (not raised) probe.** Round 8 added `LiveVerdict.probes_ok` and gated
   `confirmed` on it, but still serialized the degraded/partial verdict
   itself as the fact's `value` -- since `verify_worktree_active` is
   fail-open, that branch (not the `except`) is what actually reaches the
   transient-failure case, so the record's own last-known `mux_live`/
   `bound_live` hints were never used even though the bundle contract
   requires retaining them here. Fixed by branching on `probes_ok`: only a
   fully-probed verdict is serialized as-is, a degraded one falls back to
   the same last-known-hints helper the `except` branch already used.
4. Added the missing `probes_ok` assertions round 8's own new field
   should have carried into every existing `test_verify_worktree_active.py`
   case (healthy paths assert `True`; the two probe-exception cases assert
   `False`) -- closing the gap where a regression could silently report
   `probes_ok=True` on a degraded probe without any test catching it.

Added 3 new regression tests
(`test_coalescing_key_is_injective_despite_a_pipe_in_either_component`,
`test_status_via_daemon_registers_and_releases_a_client_id`) plus updated
`test_liveness_fact_is_unconfirmed_when_verify_worktree_active_degrades` to
assert the last-known-hints fallback (not the degraded verdict) is what's
actually serialized.

### 2026-09-21 — Tenth review round: 2 more real issues, 1 stale carryover, PR metadata drift
Pushed round 9's fixes. The next review caught 2 genuine issues (1 real
bug, 1 stale doc), 1 confirmed stale-carryover re-flag, and the recurring
PR-metadata drift:

1. **A loaded record's own identity was never checked against the
   requested id.** `_worktree_status_compute` calls
   `tracking.load_record_by_id(worktree_id, ...)`, which resolves purely by
   *filename* (`{worktree_id}.yaml`, already path-traversal-validated) --
   it never checks that the loaded YAML's own `worktree_id` field actually
   matches. A tampered or concurrently-replaced `wt1.yaml` declaring `wt2`
   would produce a bundle labeled `wt1` but assembled from `wt2`'s facts,
   and cache that mixed result under `wt1`'s key. Fixed by rejecting an
   identity mismatch (raising `ValueError`, same as a missing record)
   before assembling any facts.
2. **The effort README's own design section still documented the
   pipe-delimited key round 9 replaced.** Left as-is, a future cross-venv
   consumer building its own client against this documented wire contract
   could recreate the exact collision round 9 fixed. Updated the design
   section to describe `coalescing_key()`'s length-prefixed encoding
   instead.
3. A re-flag that the `test_verify_worktree_active.py` cases don't assert
   `probes_ok` was stale -- round 9 already added that assertion to every
   case (5 healthy-path `True`s, 2 degraded-probe `False`s); verified
   directly against the current file. Same stale-carryover pattern
   observed in rounds 3 and 7.
4. Updated the PR description's version reference again
   (`1.5.5-dev202` -> `dev204`).

Added 1 new regression test
(`test_rejects_a_record_whose_stored_identity_does_not_match_the_filename`).

### 2026-09-21 — Merge race, PR follow-up split, and eleventh review round
PR #3102 was merged at round 8's state while rounds 9-10's fixes were still
in flight (a genuine race between the operator's merge and this session's
next push) -- rounds 9-10's real fixes were recovered from the pre-merge
branch tip and re-derived cleanly onto the merged `main` as a follow-up,
**PR #3147**. That PR's own first review round caught 3 further issues,
2 of them genuine (the third a mechanical artifact of the recovery
process, not a design defect):

1. **The recovery process regressed unrelated marketplace/baseline
   entries.** Reconstructing the rounds-9/10 diff against an older
   snapshot of `main` (taken before the merge) carried forward stale
   version numbers and module-size ceilings for entirely unrelated plugins
   (agent-bridge, agent-codespaces, agent-containers, agent-dispatch,
   agent-index, ai-attribution, context-handoff, customizing-copilot, and
   `agent-worktrees/__main__.py`'s own since-refactored ceiling) that had
   moved forward on `main` in the meantime. Fixed by re-deriving those two
   files fresh from the current `origin/main` and only reapplying
   `agent-worktrees`'s own required version bump on top -- not by hand-
   editing the stale values.
2. **The identity-mismatch guard added in round 10 was bypassed by its own
   caller.** `_worktree_status_compute` rejects a loaded record whose
   `worktree_id` doesn't match the requested id -- but
   `cmd_worktree_status_bundle` substitutes `worktree_id =
   record.worktree_id` *before* calling it, so the check becomes a
   tautology (both sides are the same substituted value). A `wt1.yaml`
   declaring the real, existing identity `wt2` would silently serve `wt2`'s
   facts for a `wt1` request. Fixed by validating the loaded record's
   identity against the actual requested filename (`yaml_path.stem`) in
   the CLI command itself, before any substitution, returning a JSON error
   (consistent with this function's other early-exit checks) rather than
   relying on a guard several calls downstream that this call site itself
   defeats.

Updated the existing tampered-record-id regression test (it previously
asserted a raised `ValueError`, which was really testing the old,
bypassable path -- now asserts the earlier JSON-error rejection) and added
a new regression test proving the *safe, real* mismatched-identity bypass
(`wt1.yaml` declaring `wt2`) is caught too, not just an unsafe/traversal
id the old path-validation guard already rejected.

### 2026-09-21 — Twelfth review round: 1 more real bug, 2 stale carryovers
Pushed round 11's fixes. The next review re-flagged the marketplace/
baseline regression and the CLI-substitution bypass -- both confirmed
already fixed by round 11 (verified directly against the pushed diff: both
files are clean vs `origin/main` except the intended version bump; the
`yaml_path.stem` check is present in `session_tracking_cli.py`) -- stale
carryovers, same pattern as rounds 3, 7, and 10. The review's fourth
finding was genuine:

1. **A fresh-cache hit never re-validated the bundle's own identity
   against its key.** Even with the CLI-level and `compute()`-level
   identity guards in place, `WorktreeStatusCache.get_or_refresh`'s
   fresh-cache-hit path returns whatever is stored under the requested
   `(project, worktree_id)` key unconditionally -- it never checks that
   the bundle's own `project`/`worktree_id` fields actually agree with it.
   A warm-restored SQLite row is never re-validated on load (`_warm_restore`
   only checks it parses as a JSON object), and any bundle persisted by an
   older `compute()` build (before its own identity guard existed) would
   keep being served under the wrong key indefinitely -- never
   recomputing until the next TTL expiry or force-refresh happened to
   coincide with the corruption being noticed. Fixed by treating a cached
   bundle that actively disagrees with its own key (when it carries
   `project`/`worktree_id` fields at all -- this cache is otherwise
   generic, per its own tests) as a miss: evict it (both in-memory and the
   durable row) and fall through to a real recompute.

Added 1 new regression test
(`test_a_cached_bundle_disagreeing_with_its_own_key_forces_a_recompute`).

### 2026-09-21 — Thirteenth review round: 1 more real bug, 3 stale carryovers
Pushed round 12's fix. The review re-flagged the marketplace/baseline
regression, the CLI-substitution bypass, and the cache-identity gap --
all three confirmed already fixed (rounds 11-12; verified directly against
the current diff/files each time) -- the same stale-carryover pattern as
rounds 3, 7, 10, and 12. One genuine issue in round 12's own fix:

1. **The mismatch-eviction delete was unconditional, racing a concurrent
   recompute.** `get_or_refresh`'s new mismatch-eviction path called
   `self._delete_row(project, worktree_id)` with no condition -- if a
   concurrent recompute (the sweep, or a successor monitor mid-cutover)
   published a fresh, correct row for the same key between the in-memory
   eviction and this delete actually running, the delete would remove
   that newer row instead of the stale one it meant to prune. This cache
   already has exactly this race-safety mechanism (`_delete_row`'s
   `if_demanded_at` parameter, added for `sweep_due`'s own identical race
   in an earlier round) -- round 12 just didn't reuse it. Fixed by
   capturing the evicted entry's own sampled `demanded_at` and passing it
   through.

Added 1 new regression test
(`test_mismatch_eviction_deletes_conditioned_on_the_evicted_demanded_at`),
verifying `get_or_refresh` actually passes the conditional guard rather
than an unconditional delete.

### 2026-09-21 — Fourteenth review round: 2 more real issues, 4 stale carryovers
Pushed round 13's fix. The review re-flagged the marketplace/baseline
regression, the CLI-substitution bypass, the cache-identity gap, and the
delete's own unconditional-vs-conditional framing -- all four confirmed
already fixed (rounds 11-13; verified directly). Two genuine issues:

1. **Round 13's conditional delete ran outside the cache lock, itself
   introducing a different race.** `_persist`/`_delete_row` are only
   safe to call while holding `self._lock` -- that's what serializes them
   against `close()`'s own teardown of `self._conn`/`self._closed` (see
   `_persist`'s established call site, always inside the lock). Round 13
   moved the eviction's delete to run *after* releasing the lock,
   reasoning it was I/O like `compute()` -- but `compute()` is the slow,
   expensive git/session work this cache exists to keep off the lock;
   `_delete_row` is the same cheap, already-lock-synchronized operation
   `sweep_due` already runs *inside* the lock for the identical reason.
   Fixed by moving the delete back inside the lock, matching `sweep_due`'s
   own pattern exactly.
2. **PR title/description scope had drifted from the actual diff.** The
   title/description still said "rounds 9-10" after four more rounds (11-
   14) of real fixes landed on top -- reconciled both to describe the
   PR's actual rounds 9-13 scope (this round's own fixes are documented
   here, in the journal, rather than retroactively renumbering the title
   again for round 14 itself).
3. Also caught: the marketplace catalog's `metadata.version` field had
   drifted behind agent-worktrees' own plugin-entry version across rounds
   12-13 (bumped the plugin entry each time but not the catalog-level
   field) -- bumped it to match.

### 2026-09-21 — Deployment confirmed, Phase 7 (audit + telemetry) landed
PR #3147 (rounds 9-14) merged. Ran `agent-worktrees update`: picked up
dev215 cleanly (agent-bridge zero-downtime cutover, all other plugins
updated alongside). `worktree-status-bundle` confirmed working
end-to-end against a real tracked worktree (an unrelated harness project's
own worktree) -- full bundle returned, all facts populated.

Per the operator's request ("ensure it's deployed, audit its
functionality... since everything should be relying on it, it needs to be
accurate"), built Phase 7: `worktree-status-audit`, a new CLI command
that periodically reads the durable cache's own snapshot and diffs a
sample against a fresh, cache-bypassing ground-truth recompute --
git-state accuracy, liveness accuracy, cache-freshness bounds, identity
consistency, and daemon liveness (lock parses, owner pid alive,
rendezvous resolves, and a real probe request actually answers). Appends
one JSON line per run to a local telemetry log (refresh cadence +
liveness trail over time, not just a point-in-time read); nonzero exit on
any finding, meant to be scriptable by a scheduled task.

Validated live: ran the new command against the real deployed runtime
before the daemon had come back up post-update -- correctly reported
`responsive: false` (a true finding, not a bug) -- then re-ran after
`worktree-status-bundle`'s own `ensure_monitor` had booted it, correctly
reporting `responsive: true` and zero mismatches against the one real
cached entry. 34 new unit tests (cache-snapshot reading incl. a corrupt-
row case, sampling incl. cache-vs-fallback preference and sample-size
bounding, each diff/check function, the full `audit_one`/`run_audit`
orchestration incl. a log-write-failure-never-raises case, daemon-
liveness incl. a stale-lock and a real-probe-against-a-real-server case,
and the CLI command's JSON output + exit-code contract).

Scheduling: the operator's own `manage_schedule` runs this hourly for
now (session-scoped -- stops when this session ends); the command itself
is durable and independent of any particular scheduler, so a future OS-
level task or `agent-dispatch schedule` job can drive it identically
without any further code change.

### 2026-09-21 — PR #3186 review round 1: a real cross-environment import bug
CI's `worktree-manager (out-of-plugin)` job failed: `worktree_status_audit`
imported `worktree_status_daemon` at module scope, and `__main__.py`
imports `worktree_status_audit` eagerly for its CLI alias -- so any
environment importing `agent_worktrees.__main__` now transitively required
the vendored `work_coalescing_singleton` package at import time, including
the standalone `worktree-manager` package's own test suite, which doesn't
install it. `__main__.py` itself and `session_tracking_cli
.cmd_worktree_status_bundle` both already import `worktree_status_daemon`
lazily (inside the function, not at module scope) for exactly this reason
-- this module just didn't follow that convention. Fixed by moving both
the `worktree_status_daemon` import and its `SWEEP_INTERVAL_SECONDS`
re-export into the two functions that actually need them
(`check_daemon_liveness`, `_check_freshness`). Verified directly: a
subprocess that blocks `work_coalescing_singleton` at the `builtins
.__import__` level now imports both `worktree_status_audit` and
`__main__` cleanly; added that exact scenario as a regression test
(`test_module_imports_without_work_coalescing_singleton_installed`).

### 2026-09-21 — PR #3186 review round 2: 4 more real issues
Pushed round 1's fix. The next review caught 4 further genuine issues:

1. **The read-only SQLite URI was not Windows-safe.** A plain
   ``f"file:{db_path}?mode=ro"`` is not a valid file URI on Windows (a
   bare drive letter/backslash path) and can also misparse a path with
   spaces -- either could make the audit silently treat a genuinely
   readable cache DB as unreadable, degrading to an empty snapshot.
   Fixed by building the URI via `Path.as_uri()` (percent-encodes,
   normalizes the drive letter/separators correctly on every platform).
2. **`cache_age`/`demand_age` used direct dict indexing.** A malformed or
   older-schema cache row missing `computed_at`/`demanded_at` would raise
   `KeyError` here, violating the module's own "never raises past
   run_audit" contract. Added a `_safe_age` helper that tolerates a
   missing/non-numeric field.
3. **The Phase 7 Validation-Plan checklist bullet's version reference was
   ambiguous.** "Deployed and validated live... picked up dev215" could
   read as describing *this PR's own* deployment (which bumps further as
   Phase 7 lands), when it actually describes the already-merged PR
   #3147's deployment, immediately before Phase 7's own work began.
   Clarified in place.
4. **`--json` was a dead argparse flag.** `action="store_true",
   default=True` can never be `False` -- removed entirely; the command is
   unconditionally JSON-out already, so the flag added nothing.
5. Added the `# noqa: E402` marker `__main__.py`'s own convention already
   uses for its other mid-file re-export imports (see
   `worktree_identity`'s import), for consistency.

### 2026-09-21 — PR #3186 review round 3: 2 more real issues, 4 stale carryovers
Pushed round 2's fixes. The review re-flagged the SQLite-URI, unsafe-
indexing, ambiguous-version, and dead-`--json`-flag findings from round
2 -- all four confirmed already fixed (verified directly against the
current file each time), same stale-carryover pattern seen throughout
this effort. Two genuine issues:

1. **`select_sample` could raise on a non-positive `--sample`.**
   `random.sample` raises `ValueError` for a negative count -- a
   misconfigured `--sample -1` would crash the CLI instead of degrading
   to an empty sample, violating the module's own "never raises"
   contract (which this round's finding correctly points out extends to
   a caller-supplied argument, not just internal data shapes). Fixed by
   clamping `sample_size` to `max(0, sample_size)`.
2. **A specific downstream project name in the journal.** The prior
   entry named a real internal project when describing which worktree
   the live validation used -- replaced with a neutral placeholder, since
   this repository is public.

Added 1 new regression test
(`test_select_sample_never_raises_on_a_non_positive_sample_size`).

### 2026-09-21 — post-merge: daemon-liveness redesigned to boot-and-wait like a real caller
After PR #3186 merged and deployed (dev219), an hourly scheduled run of
`worktree-status-audit --sample 15` fired twice and both times reported
`daemon.responsive: false` / `cache_row_count: 0`, with a manual retry
immediately after each firing showing `responsive: true`. Investigated
rather than dismissing the second occurrence as a fluke: the resident
status-monitor's PID and lock `created_at` differed on each check --
confirming the monitor idle-exits between infrequent callers (its own
empty-strike logic, see `cmd_status_monitor`) and only comes back up when
something actually demands it via `ensure_monitor`. The audit's original
`check_daemon_liveness` only ever did a bare, non-booting snapshot read
(parse the lock, check the owner PID, resolve the rendezvous fields) --
exactly the probe that will see this idle-exited resting state and
misreport it as an outage, even though every real caller
(`worktree-status-bundle`'s own fallback path) already tolerates it by
booting the monitor and waiting.

Redesigned `check_daemon_liveness` to route its probe through
`worktree_status_daemon.status_with_boot` -- the same dial/boot/wait/
fallback path every real production caller uses -- instead of the old
bare `status_via_daemon` snapshot. Threaded an `ensure_monitor` callable
through `run_audit` and `cmd_worktree_status_audit`, resolved from
`core._ensure_status_monitor` (only when `core._status_monitor_enabled()`
is true, mirroring `session_tracking_cli.cmd_worktree_status_bundle`'s
own opt-out check exactly -- an operator who's disabled the resident
monitor via `AGENT_WORKTREES_STATUS_MONITOR=0` should never have the
audit spawn one anyway). When no real `(project, worktree_id)` probe
pair exists (empty sample), the check still degrades to the old static
read rather than guessing. Added 2 new regression tests covering the
boot-wait path: one confirming `ensure_monitor` is invoked when nothing
is currently reachable, and one confirming a lock write that lands
*during* the wait window is picked up before the boot-wait limit expires
(not just a before/after snapshot). Updated the 4 existing CLI-level
tests' fake `core` stand-ins to supply `_status_monitor_enabled`/
`_ensure_status_monitor` now that `cmd_worktree_status_audit` always
resolves them.

This is expected to eliminate the two false-positive firings above once
deployed: after this change, `responsive` should reliably read `true`
even when the monitor was resting between callers, because the audit's
own probe now boots and waits for it exactly as a real caller would.

### 2026-09-21 — PR #3206 review round 1: 1 real bug, 1 weak test tightened
1. **A stale (dead-owner) lock would never trigger `ensure_monitor`.**
   `status_with_boot`'s own dial step treats any syntactically parseable
   rendezvous as reachable -- it doesn't check whether the lock's owner
   PID is still alive. Passing `locks.read_lock` straight through as
   `read_lock_data` meant a monitor that crashed without cleaning up its
   own lock file would look "dialable" forever: the probe would keep
   trying (and failing) to reach the dead process and report
   `responsive: false` instead of ever booting a live replacement --
   regressing the `lock_is_live` guard the audit's own non-probe static
   read branch already applies. Fixed by wrapping the reader
   (`_read_live_lock`) to reject a non-live lock owner before it ever
   reaches `status_with_boot`'s dial step, treating a stale lock exactly
   like no lock at all.
2. **The delayed-readiness boot-wait test wrote its lock synchronously.**
   `test_daemon_liveness_ensure_monitor_boot_is_picked_up_within_the_wait`
   had `ensure_monitor` write the lock immediately, before
   `status_with_boot`'s poll loop even ran once -- it would have passed
   even if the poll loop didn't exist at all. Tightened to publish the
   lock from a background thread after a real ~0.3s delay, so the test
   actually exercises the poll loop picking up a lock that lands *during*
   the wait window, not merely before it starts.
3. This PR body's own doc-impact note: no user-facing documentation
   changes are needed -- the only behavior visible to a human is the
   audit's exit code/telemetry now reliably reflecting a real caller's
   own daemon-boot experience instead of a bare snapshot; the effort
   README's journal (this file) is the durable record of that change.

### 2026-09-21 — PR #3206 review round 2: 1 more real bug
The stale-lock dial fix from round 1 was confirmed resolved. One
further genuine issue, in code the round-1 fix hadn't touched:

1. **The post-probe rendezvous snapshot didn't apply the same liveness
   check as the dial step.** After `status_with_boot` returns (having
   correctly rejected a stale lock at dial time and fallen back),
   `check_daemon_liveness` re-read the lock to report
   `lock_present`/`rendezvous_present` -- but that re-read only parsed
   the rendezvous fields, without checking `lock_is_live`. A dead
   monitor's leftover lock (still carrying old, well-formed endpoint
   data) would therefore report `rendezvous_present=True` alongside
   `responsive=False`, contradicting the no-probe static-read branch a
   few lines above, which already gates `rendezvous_present` on
   liveness. Fixed by adding the identical `locks.lock_is_live(data_after)`
   check to the post-probe read.

Added 1 new regression test
(`test_daemon_liveness_stale_lock_with_probe_reports_unresponsive_not_rendezvous`)
covering a stale lock with valid-looking rendezvous fields under a real
probe: confirms `ensure_monitor` is still invoked, and that
`responsive`/`rendezvous_present` land consistently (both false), not
the previous `rendezvous_present=True`/`responsive=False` contradiction.

### 2026-09-21 — PR #3206 review round 3: 1 real gap (version bump), 1 stale carryover
1. **Round 1/2's runtime-payload changes hadn't bumped the version.**
   Each of the two prior commits changed `worktree_status_audit.py`'s
   actual behavior but left `plugin.json`/`pyproject.toml`/
   `marketplace.json` at the version bumped for the original redesign
   commit -- an installed client would treat those fixes as already
   applied and skip the update. Bumped to `1.5.5-dev224` /
   `1.7.7-dev192`.
2. **No CLI-level regression test for the `ensure_monitor` opt-out
   wiring.** `cmd_worktree_status_audit` resolves `ensure_monitor` from
   `core._ensure_status_monitor` only when `core._status_monitor_enabled()`
   is true, but nothing asserted what `run_audit` actually received --
   a regression that always booted (or never did, regardless of the
   opt-out) would still have passed every existing CLI test. Added two
   tests spying on `run_audit`'s `ensure_monitor` kwarg: one confirming
   the real callable is passed through when enabled, one confirming
   `None` when disabled.
3. The "Add regression test for stale lock with live probe" finding was
   re-flagged again this round -- confirmed still a stale carryover (the
   test from round 2 was already present and passing).

### 2026-09-21 — PR #3206 review round 4: 1 real bug (the original motivating scenario itself), rest stale carryovers
The three "Open" findings this round (version bump, CLI opt-out test,
stale-lock-with-probe test) were all re-flagged stale carryovers,
confirmed already fixed. One genuine "previously missed" finding, and
an important one -- it's the *exact* scenario that started this whole
redesign:

1. **The no-probe path could still fail an audit for an idle-exited
   monitor.** `run_audit` passes `probe=None` whenever its sample is
   empty (an empty cache -- `cache_row_count: 0`, precisely what the two
   original false-positive hourly firings reported). The no-probe
   branch's *success* path already reported the conservative
   `responsive=None` ("nothing to probe with, don't guess"), but every
   one of its *failure* sub-paths (no lock, stale lock, no endpoint)
   still reported `responsive=False` -- and `cmd_worktree_status_audit`
   turns any `responsive=False` into a failing exit code. So the
   original bug this whole PR set out to fix was still fully reachable
   through the empty-sample path, even after the probed path was fixed.
   Fixed by making the no-probe branch always report `responsive=None`
   (never `False`) regardless of outcome -- since there is no real
   request in flight, there is nothing that actually *failed*, only "no
   one to ask" -- while still giving an idle-exited monitor the same
   boot-and-wait chance a real probe would get (via `ensure_monitor`)
   before taking its final snapshot, so `lock_present`/
   `rendezvous_present` still reflect a freshly-booted monitor when one
   is available.

Added 2 new regression tests: one confirming a bare no-lock/no-probe
read now reports `responsive=None` (not `False`), one confirming the
no-probe path also boots-and-waits via `ensure_monitor` when given one.
Updated the two existing no-probe-failure-path tests
(`test_daemon_liveness_no_lock_file`,
`test_daemon_liveness_stale_lock_reports_not_live`) to assert
`responsive is None` instead of the previous (incorrect)
`responsive is False`.

