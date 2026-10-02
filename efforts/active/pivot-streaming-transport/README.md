# Pivot Streaming Transport & Render Performance

- **Slug:** `pivot-streaming-transport`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `dev`
- **Created:** 2026-09-30
- **Status:** Active <!-- Draft | Active | Blocked | Done -->
- **Vision:** [`visions/picker`](../../../visions/picker/README.md) —
  §Behaviors/`live-not-snapshot`, `graceful-capability-scaling`;
  §Non-Goals/*Not in-process with the engine — it sits on top of the CLI*:
  the Picker reaches each engine **only by invoking its machine-readable CLI
  verbs** (`visions/picker/README.md:332-336`). Per Copilot review on this
  effort's own plan PR (#4764), Phase 3 below is revised to stay inside this
  boundary rather than propose a vision change — see Phase 3's note.
- **Umbrella issue:** [#4762](https://github.com/ThomasMichon/copilot-extensions/issues/4762)
- **Sub-issues:** _filed per-phase as each is scoped for execution_
- **Related, not absorbed:**
  [`ThomasMichon/copilot-extensions#918`](https://github.com/ThomasMichon/copilot-extensions/issues/918)
  (the agent-worktrees-owned resident status-updater daemon — prior art and a
  direct dependency for Phase 5 below, but a separately-owned effort with its
  own "comment before picking up a phase" convention); the (now-archived)
  `efforts/2026/09/26 picker-venue-pivots` effort (contributed-pivot rendering,
  not transport); `efforts/active/worktree-manager-control-plane` (the Picker's
  broader engine-boundary retirement — this effort's Phase 4 builds directly on
  that effort's completed Phase 3c non-blocking-I/O work, but is scoped
  narrowly to transport/diffing, not a restatement of that larger campaign).

## Guiding Intent

Every pivot the Picker renders (the built-in Worktrees pivot, and every
plugin-contributed pivot — Bridges, Tasks/agent-dispatch, CodeSpaces,
Containers) currently gets its live data the same way: the Picker shells out
to that plugin's CLI, most of the time as a **one-shot** call re-run on every
poll tick. That is the slow path. Several of those same plugins **already run
a persistent, addressable daemon** for their own purposes (agent-bridge's HTTP
daemon with SSE event streaming; agent-dispatch's per-host coordinator daemon,
also HTTP with an SSE `/events` stream) — the Picker already pays for that
daemon's existence through the plugin's own operation, but never talks to it
directly, and still re-spawns a cold CLI process on every refresh instead.

This effort's intent is to make the Picker's data-fetch path **capability-
aware**: prefer the fastest channel a pivot can actually offer — a direct
HTTP/SSE/WebSocket connection to a pivot's own live daemon when one is
discoverable and reachable, degrading to the CLI's own streaming/NDJSON mode
when no daemon exists, degrading further to the original one-shot JSON call
only as the safety-net floor — and to make the Picker's own render path
genuinely incremental (diff incoming state, write only the layout regions that
actually changed) rather than redrawing more than it needs to on every update.
This closes the loop opened by the render-perf investigations landed in the
`worktrees-pivot-ux-overhaul` effort (2026-09-30): those fixed two *concrete*
staleness/latency bugs, but both investigations surfaced the same underlying
shape — a slow, synchronous, no-caching round trip standing in for a channel
that, for several pivots, doesn't need to exist in CLI-cold-start form at all.

## Participants

Single-session effort for now; no cross-machine/CodeSpace/container
dispatch is anticipated. Revisit if a phase needs parallel execution.

## Context

### What already exists (don't rebuild it — extend it)

- **The streaming/subscribe contract already exists and is unused by the two
  pivots that would benefit most.** `RegisteredPivot` (`worktree-manager/src/
  worktree_manager/production_picker/picker_tui/pivot_manifest.py:219-230`)
  already declares two opt-in manifest flags:
  - `stream: bool` — the runtime re-invokes the pivot's `list` command with a
    trailing `--stream` and consumes line-delimited NDJSON envelopes
    (`begin`/`row`/`entry`/`delta`/`removed`/`summary`/`done`/`error` —
    documented in `tasks.py`'s `RegisteredPivotRuntime` docstring around
    line 370-390), falling back to the original one-shot JSON call when the
    CLI doesn't understand `--stream` — "always safe to declare."
  - `subscribe: bool` (only meaningful alongside `stream`) — **holds the
    channel open for live updates**: no overall deadline (`tasks.py:416-422`,
    `STREAM_TIMEOUT` is only applied when `not self.pivot.subscribe`), so
    `delta`/`removed` envelope lines repaint the Picker live without a
    re-fetch.
  - **Adoption today:** only `agent-codespaces`' manifest
    (`plugins/agent-codespaces/pivots/agent-codespaces.json`) sets
    `"stream": true` — and even that is one-shot streaming, not held/live.
    **`agent-bridge`'s and `agent-dispatch`'s own pivot manifests
    (`plugins/agent-bridge/pivots/agent-bridge.json`,
    `plugins/agent-dispatch/pivots/agent-dispatch.json`) declare neither flag
    at all** — despite both plugins already running a persistent daemon with
    its own SSE stream for unrelated purposes, their Picker pivot still runs
    the slowest available path (a cold one-shot JSON CLI call) on every
    refresh. `subscribe: true` has **zero adopters** anywhere in the tree.
  - This means Phases 1-2 below are **wiring, not new mechanism** — the
    consumption side is built, tested, and already safe-by-design
    (`stream: true` degrades automatically if the CLI doesn't support it).
- **agent-bridge is already a standalone persistent daemon** with an HTTP/SSE
  surface, advertising its live endpoint at `~/.agent-bridge/active.json`
  (`plugins/agent-bridge/README.md:4,25,44,50`). "Human mode retains one SSE
  delivery loop" (`README.md:191`).
- **agent-dispatch already runs a per-host coordinator daemon** reachable over
  HTTP, with a **genuine SSE event stream** (`GET /events`, also exposed as
  `agent-dispatch watch`, `README.md:17-24,1260`).
- **D4 progress actions already stream NDJSON off the render thread**
  (`PivotAction.progress`, `pivot_manifest.py` — "the action's stdout is the
  NDJSON progress envelope... the picker renders it live in the modal
  `ProgressScreen`") — the same line-delimited-JSON convention `stream`/
  `subscribe` use for pivot `list`, just for a one-off action instead of the
  roster. Reuse this convention; don't invent a second envelope shape.
- **Issue #918** ("Consolidated worktree status-updater daemon," agent-worktrees)
  is the **built-in Worktrees pivot's own** version of this problem, much
  further along: a resident daemon already caches `list --json` (TTL +
  proactive-warm, Phases 4/4-slice-2), tracks liveness roots (Phase 2), and
  continuously reconciles mux/session state **onto the tracking records
  themselves** (Phase 3 — `tracking.stamp_bound_live`/`stamp_mux_live`), with a
  push-wake primitive so a mutation wakes the sweep immediately instead of
  waiting out a poll interval (#4354). **The gap this effort's Phase 5 closes:**
  `picker-reconcile-local` (`plugins/agent-worktrees/src/agent_worktrees/
  picker_reconcile_cli.py`) — the Group C endpoint behind the Picker's
  Actions-dialog refine and claims data — does **not** use any of this. It
  calls `reclaim.resolve_bound_copilots()` fresh, unfiltered, every single
  call, even though `_fresh_bound_live_hint()` already exists in the same file
  to read the daemon-maintained hint off the record — wired **backwards**: the
  live rescan runs first, the cheap fresh hint is only a fallback when the
  scan fails. (Today's `picker-reconcile-local` fix already removed one other
  cost from this path — an unconditional `cfg.load_config()` — see below.)

### What this session already found and fixed (prerequisite context, not open work)

From the `worktrees-pivot-ux-overhaul` effort's 2026-09-30 journal entries —
summarized here because they motivated this effort, not restated as open
items:

1. **Fixed, merged (`#4719`):** the ~10fps idle render tick's cosmetic `pulse`
   flip forced a full clear+rebuild of the entire worktree `OptionList` even
   with zero live rows, because the data signature included `pulse` but the
   fast-repaint path didn't scope to just the rows that actually pulse. Added
   `row_sess_pulses()` + `_try_pulse_repaint()` (`engine_regions.py`).
2. **Fixed, merged (`#4738`):** two further bugs —
   - `_PickerNativeData._signature()`'s per-row fingerprint tracked
     `(id, title, state, age_secs)` but **not** `sess`, so an async mux-status
     correction (`PROC` → `MUX(1)`) landing after first paint never triggered a
     rebuild (mux attachment doesn't change `state`, both read ACTIVE) — the
     row stayed stuck on its stale glyph indefinitely. Fixed by adding `sess`
     to the fingerprint.
   - `picker-reconcile-local`'s single-worktree call (what the Actions dialog
     waits on) took 7-11s **every** time, not just the first: `cfg.
     load_config()` alone cost ~6.7s (a 1733-record tracking re-scan plus
     plugin-activation resolution) for a result used only to reconcile a PR —
     a no-op when nothing in scope has one. Fixed by skipping it when no
     record in scope has a reconcilable PR: ~8-11s → ~4.5-5.8s.
3. **Found, deliberately deferred as follow-ups (this effort continues them):**
   - `resolve_bound_copilots()`'s own remaining ~4.8s cost (an unfiltered,
     system-wide session-state-dir walk) — **this effort's Phase 5**, now
     additionally informed by the `#918`/`_fresh_bound_live_hint` precedence
     finding above (trust the fresh hint; scan live only as a fallback).
   - `_refresh_nf_segments()` (`engine_rendering.py`) unconditionally refreshes
     all 7 screen segments (title/pivots/chrome/machine/buttons/footer/
     body-data) on every screen refresh, correlating with Textual's compositor
     choosing a full (non-incremental) repaint over its own diff-based one —
     **this effort's Phase 4**.

## Request

> Render performance hasn't just been bad in the picker rows, opening and
> interacting with dialogs is also ver[y] unresponsive. We need to be mindful
> of what runs on the r[e]nder thread, as well as how much we are actually
> doing to process updates. I think we still have an outstanding tracking item
> in copilot-extensions or an associated effort to let the pivot sources
> continuously stream live updates to the Manager incrementally, or let the
> connection upgrade to an HTTP port using SSE or WebSockets. Switching to a
> server-to-server model would be a ton more efficient. We also want to be a
> bit more React-esque, diffing incoming state updates to ensure we target our
> writes to only the parts of the layout which need it.
>
> Let's gather up everything performance related, build out an effort, and get
> cracking on it. For Pivots (which are provided via plugins), we want a way
> for the CLI commands registered to support a streaming mode (JSONL), which
> can be escalated into an HTTP/WS persistent connection while the given Pivot
> is being rendered. Different pivots already have backing, live daemons (like
> agent-bridge and agent-dispatch), and so we'd want the slower CLI flow to
> "get out of the way" when a faster path exists.

(Light typo correction only — "ver" → "very", "rnder" → "render" — content
otherwise verbatim across both messages.)

## Plan

### Phase 0 — Fix the `subscribe` EOF/reconnect gap (prerequisite, blocks Phase 1)
_(Added per Copilot review on #4764: "the current contract cannot provide this
fallback... if that command emits an envelope and then exits, it is accepted
as ready and a subscribe pivot is never repolled, so there is no automatic
degradation." This is a correctness gap in the EXISTING `subscribe`
consumption code, not just this plan's assumption about it — confirmed
against `tasks.py:395` and the `subscribe` timeout-skip logic: today, nothing
distinguishes "the channel is genuinely still open" from "the producer exited
and we're silently frozen on its last snapshot.")_
- [x] Define an explicit contract: a `subscribe` pivot's stream process exiting
      (EOF on stdout) must be treated as the channel dropping, not as
      "finished successfully." On EOF, either (a) reconnect by re-invoking the
      `list --stream` command after a short backoff, keeping the last-known
      rows visible in the interim (never blank the pivot on a transient drop),
      or (b) fall back to repolling via the plain one-shot path if reconnect
      attempts are exhausted. **Done 2026-10-01**: added `_subscribe_live`/
      `_subscribe_retries` tracking + `_handle_subscribe_drop()`
      (`tasks.py`) — bounded reconnect with backoff
      (`SUBSCRIBE_MAX_RECONNECT_ATTEMPTS`/`SUBSCRIBE_RECONNECT_BACKOFF_SECS`),
      resetting the retry budget on any successful row delivery so a
      genuinely-flaky-but-working channel reconnects indefinitely, while a
      channel that never delivers anything exhausts and hands control back to
      `repoll()`.
- [x] Add regression coverage: a `subscribe` pivot whose process exits
      mid-session must resume updating (via reconnect or fallback), never
      silently freeze on stale rows forever. **Done**:
      `test_subscribe_reconnects_after_unexpected_eof` (recovering case) and
      `test_subscribe_exhausts_and_falls_back_to_repoll` (exhaustion case),
      `test_pivot_streaming.py`.
- [x] This is a `worktree-manager`-owned fix (the consuming runtime), landed
      and verified **before** Phase 1 flips any real plugin's manifest to
      `subscribe: true` — flipping the flag today would durably freeze that
      pivot the first time its CLI process exits for any reason.

### Phase 1 — Adopt `stream`/`subscribe` for agent-dispatch's pivot
_(agent-recommended ordering: lowest-risk, highest-signal first adopter —
agent-dispatch's CLI already emits SSE-sourced JSON lines for `watch`, so this
is closest to a manifest-only change. Depends on Phase 0 — now unblocked.)_
- [x] Confirm `agent-dispatch-board --machine {machine}` (the pivot's current
      `list` command) can emit the `stream`/`subscribe` NDJSON envelope shape
      `tasks.py` expects (`begin`/`row`/`delta`/`removed`/`summary`/`done`), or
      scope the CLI-side change needed to produce it from the daemon's
      existing `/events` SSE stream. **Done 2026-10-01**: added `--stream`/
      `--subscribe`/`--interval` to `agent-dispatch-board`
      (`board_cli.py`) — `_run_stream()` emits `begin`/`row`/`done`, then with
      `--subscribe` re-fetches on a timer (default 2s) and diffs via
      `_diff_rows()` into `delta`/`removed` frames. Periodic in-process
      re-scan (same shape as agent-codespaces' `pool --stream --subscribe`),
      not a raw pass-through of the daemon's `/events` SSE stream — lower risk
      for the first adopter, and still removes the per-refresh CLI re-exec
      cost since the channel stays open across re-scans.
- [x] Flip `plugins/agent-dispatch/pivots/agent-dispatch.json` to
      `"stream": true, "subscribe": true` once the CLI side is ready.
      **Done.**
- [x] Verify live: Picker's Tasks pivot reflects a task-state change without a
      poll-interval delay, and degrades cleanly when `agent-dispatch` is
      absent/stale (the existing one-shot fallback), and recovers per Phase 0's
      contract if the stream process exits mid-session. **Done**: ran the
      patched `agent-dispatch-board --stream` and `--stream --subscribe`
      directly against this machine's live coordinator (57 real tasks) —
      `begin`/57×`row`/`done` on the initial fetch, then further frames
      emitted on the held channel during a live `--subscribe` session with no
      process re-exec. Mid-session process-exit recovery is Phase 0's own
      regression-tested contract (`test_subscribe_reconnects_after_unexpected_eof`),
      unchanged by this phase; degrade-when-absent is the pre-existing
      `stream: true` auto-fallback (`tasks.py`), also unchanged.

### Phase 2 — Same for agent-bridge's pivot
- [x] Same shape as Phase 1 against `agent-bridge --json agents`. **Done**:
      added `--stream`/`--subscribe`/`--interval` to the `agents` subcommand
      (`inventory_cli.py`) — same periodic in-process re-scan shape as
      Phase 1 (re-fetches `/api/v1/agents`; does **not** consume
      agent-bridge's own SSE delivery loop — that remains Phase 3's job, the
      daemon-relay fast path), keyed by agent `name` (the manifest's
      `entry.id`). A topology-profile error on the initial fetch surfaces as
      a stream `error` frame rather than being silently dropped (the plain
      path's `_report_topology_errors` exit-2 doesn't fit the NDJSON
      envelope contract).
- [x] Flip `plugins/agent-bridge/pivots/agent-bridge.json`. **Done**:
      `"stream": true, "subscribe": true`.
- [x] Verify live. **Done**: ran the patched `agent-bridge --json agents
      --stream` directly against this machine's live bridge daemon (21 real
      agents) — full `begin`/21×`row`/`done` envelope.

### Phase 3 — CLI-relayed daemon fast path (the new capability, revised)
_(Revised per Copilot review on #4764: the original shape — the Picker
connecting directly to a pivot's daemon over HTTP, bypassing the CLI — directly
contradicts the Picker vision's stated boundary, "reaches each engine **only by
invoking its machine-readable CLI verbs**" (`visions/picker/README.md:332-336`).
Rather than propose a vision change for this, the fast path stays **behind the
CLI-owned client boundary** the reviewer suggested: the Picker still only ever
invokes `list --stream`/`subscribe`; the SPEEDUP comes from that CLI's own
`--stream` implementation choosing, internally, to relay its already-running
daemon's live feed (e.g. `agent-dispatch`'s own `/events` SSE stream) through
its stdout NDJSON instead of re-deriving the same data from scratch on each
invocation — invisible to the Picker, which is unaffected by where the CLI's
own implementation gets its data. This also resolves Phase 0's EOF/reconnect
concern for the daemon-backed case specifically: the CLI process, not the
Picker, owns reconnecting to its own daemon.)_
_(Design reviewed 2026-10-02, per this phase's own Validation Plan gate — see
the 2026-10-02 journal entry for the full finding. Summary: agent-dispatch and
agent-bridge are **not symmetric** here. agent-dispatch's coordinator already
publishes a genuine roster-relevant event feed (`EventBus`/`GET /events`,
lifecycle events like `task.submitted`/`task.completed` carrying the full task
dict) that a CLI-side relay can consume directly. agent-bridge's daemon has
**no existing roster-change event stream** — its SSE routes
(`routes/live_sessions.py`, `routes/remote.py`, `routes/sessions.py`) are all
per-session event logs, not an aggregate "the agent roster changed" feed. The
plan below splits accordingly; agent-bridge's own daemon-side cache (3b) must
land and be validated before any agent-bridge SSE relay (deferred, not part of
this phase) is considered.)_
- [ ] **3a — agent-dispatch relay:** in `board_cli.py`'s `_run_stream()`,
      replace the `--subscribe` branch's `time.sleep(interval)` poll-and-diff
      loop with: keep the existing initial `begin`/`row`/`done` fetch
      unchanged, then open `DispatchClient.stream_events()` (`GET /events`)
      and use **any** event received as a wake trigger for an immediate full
      `_fetch_rows_direct()` + `_diff_rows()` pass — the exact same
      full-board re-fetch-and-diff the poll loop already does, just woken by
      a real event instead of a timer tick, **not** a per-event row
      transform. (Revised from an earlier per-event-transform draft: a
      partial, event-sourced row can't correctly maintain a `--limit`-capped,
      newest-first set — see the dedicated bullet below — and a full re-fetch
      sidesteps that class of bug entirely by construction, at the cost of
      one full fetch per wake instead of a cheaper partial update.)
  - [ ] **Debounce the wake:** a burst of events (several task mutations in
        quick succession) must coalesce into a single pending re-fetch, not
        one re-fetch per event — if a fetch is already in flight or already
        scheduled, a newly arriving event extends/no-ops rather than queuing
        a second one.
  - [ ] **Close the gap between the initial snapshot and the subscription
        actually being live — and prove it's actually live, not just that
        the HTTP response started:** a mutation that lands after the
        initial `begin`/`row`/`done` fetch but before `stream_events()`'s
        subscription is actually established has no event to consume — the
        same kind of non-replay gap the reconnect path already has to
        solve. Opening the connection is not sufficient proof of this: the
        `/events` route sends no initial frame today, and
        `EventBus.subscribe()` only registers its queue once the route's
        generator begins iterating — a client can receive response headers
        before that registration actually happens, so starting the
        reconcile right after `stream_events()` returns can still race the
        exact gap this bullet means to close. Fixed by having the route
        emit an explicit **ready frame** immediately after queue
        registration (before yielding any real event), and having the
        client: (1) wait for that ready frame, (2) buffer any events
        received from that point on rather than applying them immediately,
        (3) run the immediate reconcile pass, then (4) apply the buffered
        events on top of the reconciled snapshot — so nothing in the
        handoff window is lost to either side.
  - [ ] **Scope to the direct (local) path only:**
        `_fetch_rows()` already branches to `_fetch_rows_delegated()` for a
        cross-machine `--machine`. The local coordinator's `/events` stream
        only describes *this* machine's tasks, so relaying it for a
        delegated board would silently mix in the wrong machine's events (or
        none at all for the real target). The relay path applies **only**
        when `_fetch_rows` resolves to `_fetch_rows_direct()`; a delegated
        board keeps today's poll-and-diff loop unmodified, full stop — not a
        gap to close later, a hard scope boundary for this phase. (Relaying
        a delegated board would require the *remote* host's own coordinator
        to run the `--subscribe` relay and forward its NDJSON through the
        inbox — a materially different mechanism, not an extension of this
        one — and is explicitly out of scope here.)
  - [ ] **Keep the stream alive through idle quiet periods:**
        `DispatchClient` configures a single 10s timeout across
        connect/read/write/pool (`client.py:54`), and `/events` emits no
        periodic keepalive (`coordinator_status.py:83-89`) — a coordinator
        with no task activity for >10s would make `stream_events()`'s
        `iter_lines()` raise on read-timeout well before any real event
        occurs, permanently tripping the relay into its poll fallback on
        every quiet board. The relay's own stream call must use a read
        timeout of `None` (unbounded) for this one long-lived GET — httpx
        supports a per-call timeout override
        (`client.stream("GET", "/events", timeout=httpx.Timeout(10.0,
        read=None))`) without changing the 10s default for every other
        short request this client makes. (A server-side SSE heartbeat is a
        reasonable complementary hardening but is not required to make this
        design correct — an unbounded client-side read timeout is sufficient
        on its own and needs no coordinator change.)
  - [ ] **A `--limit`-capped, newest-first board can't be maintained from a
        single event's own row in isolation:** `/tasks` applies `--limit` to
        the whole result set before `_build()` — it is not a per-task filter.
        When a mutation would add a task to an already-full capped set,
        another row must be displaced; when a task leaves the visible set,
        a previously-untracked row may need to be backfilled in. Neither
        direction is discoverable from one event's own payload. This is
        exactly why the wake-trigger model above always re-fetches the whole
        board rather than attempting to patch one row: the full fetch
        recomputes the true capped newest-N set every time, so membership
        changes (additions, displacements, backfills) are handled by
        construction, the same as the poll loop already handles them today
        — there is no separate membership-maintenance logic to get right.
  - [ ] **Every board-visible mutation must publish *some* bus event, even
        content-free, since this design only needs a wake signal, not row
        identity:** because any event now only triggers a full re-fetch (not
        a per-event transform), the event-coverage bar is much lower than a
        payload-carrying requirement — existence is sufficient. Most
        mutation paths already publish *something* (including the
        aggregate, no-`task`-payload `task.reconciled`/`task.reaped`/etc.
        events the background reconcilers already emit — those work fine as
        pure wake signals even without task identity). The genuine gaps are
        the mutation endpoints that publish **nothing at all** today:
        `POST /tasks/{id}/activity` and `POST /tasks/{id}/heartbeat`
        (`coordinator_tasks.py`, `_guard()` called with no `event_type`), the
        MCP heartbeat path's own `_mutate(..., None)` call (`mcp_http.py`),
        **and the manual recovery entry points** — `POST /recover`
        (`coordinator_tasks.py:888-891`) and MCP `dispatch_recover`
        (`mcp_http.py:801-805`) both call `queue.reconcile_liveness()`
        directly with no event published at all, even though that call can
        requeue, suspend, or dead-letter rows. Add an event — any
        `event_type`, e.g. `task.activity_updated`/`task.heartbeat`/
        `task.recovered` — to each of these specific call sites; no other
        audit is needed once every mutation path publishes at least one
        event, since the relay no longer depends on that event's contents.
  - [ ] **Time-derived fields change with no mutation and no event at all,
        and need their own refresh path, not a daemon round-trip:**
        `_build()` expires `activity` after `ACTIVITY_TTL_SECONDS`, advances
        the "stalled Nm" text purely from `time.time()`, **and** removes a
        terminal row entirely once `completed_at`/`updated_at` crosses the
        `--recent-mins` cutoff (`board_cli.py:388-407`) — none of these three
        transitions is triggered by any task mutation, so neither the event
        relay above nor a 30-60s reconcile is a sufficient refresh cadence
        for any of them (the current 2s poll happens to mask this today
        only because it's frequent enough to feel live). Fixed by a
        **separate, local, no-network recompute tick** on a short cadence
        (comparable to today's 2s, e.g. 1-2s) that recalculates all three
        clock-only transitions — not just the two display-text fields —
        from the already-cached rows' own stored timestamps: emitting a
        `delta` for a row whose displayed text changed purely from clock
        advancement, and a `removed` for a row that has now aged out of
        `--recent-mins`, purely locally, zero daemon load either way.
  - [ ] **Reconnect-gap safety net (non-negotiable, not an optimization):**
        `EventBus.subscribe()` (`events.py`) is a live, in-memory, non-replay
        broadcast — an event published during a dropped/reconnecting SSE
        connection is gone forever, which would silently desync the board
        (a completed task never marked `removed`, or a new one never
        appearing) until the next full resync. Run a **background full
        reconcile re-fetch** on a long interval (default on the order of the
        existing poll cadence's upper end, e.g. 30-60s — "trust but verify,"
        not a return to 2s polling) that re-diffs the complete board against
        the tracked snapshot the same way `--subscribe` already does today,
        catching anything the event stream missed.
  - [ ] **Serialize every writer of the tracked snapshot against the
        others, including the time-derived recompute tick above:** more
        than one of these can run close together (an event wakes a fetch
        just as the long reconcile's own timer also fires, or the fast
        local recompute tick fires mid-fetch) — running any two
        concurrently risks the same stale-overwrite race: a slower writer
        that started earlier finishing *after* a faster, later one would
        re-emit older state, visibly reverting a row (including non-time
        fields the recompute tick never touched, if it reads a stale cached
        row while a full fetch is publishing newer task state). Fixed by a
        single snapshot-owner lock shared by **every** writer — the
        event-woken full re-fetch, the long reconcile, and the local
        recompute tick's own read-recompute-diff-emit sequence all take the
        same lock; a trigger arriving while another writer holds it queues
        (coalescing with any already-pending debounced wake) rather than
        running concurrently.
  - [ ] **A transient SSE failure degrades to polling temporarily, not
        permanently, and reconnecting means a genuinely fresh client, not a
        retried stale one:** falling back to poll-and-diff forever on the
        very first `stream_events()` failure contradicts this design's own
        stated goal (the CLI, not the Picker, owns reconnecting to its
        daemon) and means one transient blip or a routine daemon-generation
        cutover disables the Phase 3 speedup for the rest of a long-lived
        Picker channel. Instead: on failure, fall back to poll-and-diff
        immediately (correctness first), but keep retrying the SSE
        connection with bounded backoff in the background; on a successful
        reconnect, run one full reconcile pass (catching anything missed
        while on the poll fallback) and resume the event-woken relay. Only
        settle into *permanent* polling once reconnect retries are
        genuinely exhausted (a bounded cap, not indefinite retry either) —
        not on the first failure. Critically, each reconnect attempt must
        **re-resolve the endpoint and construct a fresh `DispatchClient`**
        (re-reading `active.json` via the same `_endpoint()` logic the
        initial client already uses) rather than retrying the same client
        instance — a routine zero-downtime coordinator-generation cutover
        flips `active.json` to a new bind/port, and a client built against
        the old endpoint can never recover by retrying itself, the same
        problem `ResolvingDispatchClient` (`client.py:1112`) already exists
        to solve for long-running supervisors; this reconnect reuses that
        exact pattern rather than inventing a second one.
- [ ] **3b — agent-bridge daemon-side cache (land first; smaller, no new
      failure mode):** `AgentResolver`'s per-call resolver scan is the actual
      cost (Phase 2's `incomplete_namespaces` work was about tolerating its
      partial-failure shape, not removing the cost). Move that scan **into
      the daemon**, on its own background refresh timer, maintaining an
      in-memory roster cache; `GET /api/v1/agents` becomes an O(1) cache read
      for every caller (CLI `--subscribe` tick included) instead of a fresh
      multi-resolver scan per poll, with N concurrent Pickers now sharing one
      scan instead of paying for N. The CLI's `--subscribe` loop keeps its
      current shape (poll on `--interval`, diff, emit) — only what each tick
      costs changes.
  - [ ] **Preserve the initial-scan recovery contract:**
        `_fetch_complete_initial_rows()` relies on each retried
        `GET /api/v1/agents` call actually re-scanning so an incomplete
        namespace has a real chance to resolve on a later attempt — a pure
        O(1) cache read would instead return the *same* stale partial
        snapshot on every retry until the background timer happens to fire,
        letting a new subscriber publish an incomplete roster as
        authoritative long before a real rescan ever occurs. The cache
        therefore retains **last-known-good per namespace** (an entry whose
        most recent resolve failed keeps serving its last successful
        result, still correctly flagged in that tick's
        `incomplete_namespaces`), and the endpoint accepts an explicit
        force-refresh signal (e.g. a `force_refresh=true` query param) that
        `_fetch_complete_initial_rows()`'s retry loop sets, triggering an
        immediate out-of-band re-scan of just the still-incomplete
        namespace(s) before responding — not a cache read. Every other
        caller (the CLI's ordinary `--subscribe` poll tick) keeps the cheap
        unconditional cache read; only the bounded initial-scan retry path
        pays for a forced rescan, exactly the callers that need one.
  - [ ] **A stalled or crashed refresh task must not serve a stale roster as
        complete forever, and the recovery can't be merely passive:**
        moving the scan to a
        background task adds a new failure mode the current per-call scan
        doesn't have — if that task exits or hangs after one successful
        scan, every subsequent O(1) read keeps returning an
        apparently-complete old snapshot, and neither `incomplete_namespaces`
        nor the CLI's retry logic has any way to detect it (nothing failed;
        nothing is marked incomplete). Fixed by three requirements together:
        (a) the background task is **supervised** — a process-level
        watchdog restarts it if it exits or stops making progress, the same
        standard this codebase already holds every other long-lived loop
        to; (b) every cache entry carries a **freshness deadline** (its own
        namespace's `last_refreshed_at` plus a bound meaningfully larger
        than the normal refresh period, e.g. 3× the refresh interval) — a
        namespace whose deadline has passed is reported as incomplete in
        that response's `incomplete_namespaces`; and (c) **marking incomplete
        is not enough on its own — any `GET` that observes a namespace as
        incomplete, uninitialized, or past its freshness deadline must
        itself opportunistically join that namespace's single-flight refresh
        (the same mechanism `force_refresh` triggers explicitly), not merely
        report the state and wait for the background timer.** Without (c),
        an **old CLI** that never sends `force_refresh` and only performs a
        few plain `GET`s 0.5s apart (`_fetch_complete_initial_rows()`'s
        existing retry shape) would keep reading the same stale snapshot
        across all of them and publish it before the background timer ever
        fires — the deadline alone only changes what the response *reports*,
        not whether a real rescan is actually in flight. (c) makes
        `force_refresh` a pure optimization (skip straight to refreshing
        instead of waiting to notice staleness) rather than the only path
        that can ever trigger a rescan, which is what actually preserves
        reverse skew.
  - [ ] **A namespace that has never completed its first scan is not the
        same as one with a last-known-good value:**
        the last-known-good design above only covers a namespace that has
        *previously* succeeded at least once. While the daemon is still
        warming up, `GET /api/v1/agents` must not answer with an
        empty/unpopulated cache for a namespace with no prior successful
        scan and no incomplete marker — `_fetch_complete_initial_rows()`
        would have nothing to detect and would publish that gap as
        authoritative immediately. The cache therefore has an explicit
        **uninitialized** state per namespace (distinct from
        last-known-good-but-currently-failing), and any namespace still in
        that state is *always* reported in `incomplete_namespaces` until its
        first successful scan completes — never silently omitted as if it
        had simply resolved to zero agents.
  - [ ] **The namespace set itself is dynamic, not fixed at startup:**
        `refresh_provider_resolvers()` (`agent_registry_resolver.py:159`)
        already adds, replaces, and unregisters `providers.d`-declared
        resolvers at runtime on its own TTL — the cache's namespace set must
        track this, not assume the set discovered at daemon startup is
        permanent. The background refresh cycle re-runs this same provider
        scan on its own cadence and reconciles cache membership against it:
        a newly-registered namespace enters the cache in the
        **uninitialized** state above (not silently absent until some
        unrelated trigger populates it), and a namespace whose provider was
        unregistered is retired from the cache outright — never left
        serving its last-known-good agents indefinitely as if that provider
        still existed.
  - [ ] **`force_refresh` is a protocol-gated capability, not an additive
        response field:** unlike Phase 2's
        `incomplete_namespaces` (an additive, tolerant-reader *response*
        field correctly exempted from a version bump per `protocol.py`'s
        own documented rule), `force_refresh` is a new **request** parameter
        with real server behavior — exactly the case `protocol.py:13-19`
        says must bump `HTTP_PROTOCOL_VERSION`. Bump it, add a
        `FORCE_REFRESH_PROTOCOL_VERSION` constant (following the existing
        `RELAY_INTERRUPT_PROTOCOL_VERSION`/`FAILED_ACP_HANDSHAKE_PROTOCOL_
        VERSION` precedent in the same file), and gate the CLI's use of the
        query param through `BridgeClient.daemon_supports()` so an old
        daemon that doesn't understand the param is never sent it.
        **Reverse skew** (an old CLI, with no knowledge of `force_refresh`
        at all, talking to a new cached daemon) is covered by the
        "incomplete GETs opportunistically join the refresh" requirement
        above, not by the protocol gate: an old CLI's retry loop just
        repeats plain `GET`s, but any one of those `GET`s against an
        incomplete/uninitialized/stale namespace is now itself what
        triggers the real rescan — the daemon doesn't need the caller to
        know about `force_refresh` at all for the rescan to actually happen,
        only to request it eagerly instead of opportunistically.
  - [ ] **Concurrent refreshes of the same namespace must not race each
        other, and must not recreate the N-scan cost this cache exists to
        remove:** the periodic timer, a forced refresh, and concurrent
        initial-scan subscribers can all end up triggering a scan of the
        same namespace at once. Without coordination, two real risks follow:
        a slower scan that started earlier can finish *after* a faster,
        later one and overwrite its newer result and freshness timestamp
        with older data; and each concurrent caller naively triggering its
        own scan reintroduces the exact N-callers-pay-for-N-scans cost this
        cache is meant to eliminate. Fixed by **per-namespace single-flight
        refresh** (a namespace already being scanned has at most one
        in-flight scan; any other caller/trigger that arrives meanwhile
        awaits that same in-flight result rather than starting a second
        one) plus **generation-guarded publication** (each namespace tracks
        a monotonic generation counter; a scan only publishes its result if
        its starting generation is still current when it completes,
        discarding — not overwriting with — a result that lost the race to
        a newer one).
- [ ] **3c — agent-bridge roster-change SSE (deferred; do not start until 3b
      is shipped and measured insufficient):** add a genuine
      `GET /api/v1/agents/stream` daemon route that pushes `delta`/`removed`
      when the background cache refresh (3b) detects a roster change, so the
      CLI can relay it the same way 3a does for agent-dispatch. This
      duplicates Phase 2's client-side roster-diffing logic on the daemon
      side and introduces the daemon-connection failure mode this phase's
      Validation Plan gate is about — scope it as its own reviewed increment
      if 3b's win doesn't suffice, not folded into this pass.
- [ ] Preserve graceful degradation **inside the CLI**: daemon unreachable →
      the CLI's own existing poll-and-diff `--stream` implementation; CLI
      doesn't support `--stream` at all → the Picker's own existing one-shot
      JSON fallback (already built, see Context). The Picker-side fallback
      chain does not grow a new rung; only the CLI's internal implementation
      gains a faster data source.
- [ ] Evaluate extending this pattern to other daemon-backed plugins only
      after it lands for these two.

### Phase 4 — Segment-level React-esque diffing in the Picker's own render path
- [ ] Profile `_refresh_nf_segments()` (`engine_rendering.py`) against a
      representative session to confirm (per the 2026-09-30 investigation)
      that it correlates with Textual's compositor choosing a full
      (non-incremental) repaint.
- [ ] Narrow `_refresh_nf_segments()` to refresh only the segment(s) whose
      backing state actually changed for a given refresh cause (a cosmetic
      pulse tick only needs chrome; a nav-only change only needs the sticky
      header + body-data; etc.) — verified against the existing "any state
      change that refreshes the screen must re-render the child segments too"
      invariant and its current test coverage so this narrowing can't silently
      desync a segment from the screen state it reads.

### Phase 5 — Group C: trust the resident monitor's fresh hint before rescanning
### Phase 5 — Group C: trust an affirmative fresh hint, never a negative one
_(extends `#918`'s Phase 3 catalog-reconciliation work into
`picker-reconcile-local`; comment on `#918` claiming this phase before
starting, per that issue's own convention. **Revised per Copilot review on
#4764**: the original framing — skip the live rescan whenever the hint is
"fresh," in either direction — would make a non-authoritative cache
authoritative. The already-reviewed, already-shipped precedent for this exact
hint (`agent_worktrees/__main__.py:478-486`, `scan_sessions_fast`'s own mux
check) is **asymmetric**: a fresh `True` short-circuits the check (a false
positive here is harmless — the session really is live), but a fresh `False`
or missing hint **always** falls through to the authoritative probe, because
"a cached negative is not proof a session hasn't attached since the stamp."
This phase adopts that exact asymmetry, not a new, weaker rule.)_
- [ ] In `picker_reconcile_cli.py`, when `_fresh_bound_live_hint(rec)` (and an
      analogous fresh-hint read for `mux_live`/`mux_live_at`, mirroring the
      same fields `tracking.stamp_mux_live` already stamps) is **affirmatively
      `True` and fresh**, trust it and skip `reclaim.resolve_bound_copilots()`
      for that record. A `False`, stale, or absent hint **always** falls
      through to the live rescan — never trusted to skip it.
- [ ] Keep the mux **client-count** scan (`sessions.mux_status_many()`)
      unconditional regardless of the bound-live hint: the boolean hint has no
      `mux_clients`/`mux_attached` granularity, and Group C's row payload needs
      those fields. The hint only ever short-circuits
      `resolve_bound_copilots()` (the ~4.8s unfiltered scan) for the
      already-known-live case — it does not replace the (already cheap, ~tens
      of ms) mux session-count probe.
- [ ] Preserve every existing safety note from Phase 3's own history (never
      infer a conclusion from liveness alone; this hint-trust is a latency
      optimization for a confirmed-positive case only, never a new source of
      truth for a negative or unknown one).


## Validation Plan

- [ ] **Phase 0 (blocks Phase 1):** a regression test proving a `subscribe`
      pivot whose stream process exits mid-session recovers (reconnects or
      falls back to repolling) rather than freezing on stale rows forever.
- [ ] **Phases 1-2:** a live-timed before/after of the Tasks/Bridges pivot's
      refresh latency (mirroring the `picker-reconcile-local` before/after
      methodology from 2026-09-30), plus a headless test proving the Picker
      repaints on a `delta`/`removed` envelope line without a poll tick.
- [ ] **Phase 3 (design review gate):** 3a's agent-dispatch relay and 3b's
      agent-bridge daemon-side cache each introduce their own new internal
      failure modes beyond what the existing poll-and-diff/scan-per-call
      paths have, and each needs its own acceptance test, not one shared
      generic check:
  - **3a:** kill the coordinator mid-render and confirm the relay degrades
        to its existing poll-and-diff path (never a frozen or crashed
        pivot), reconnects with bounded backoff once the coordinator comes
        back (not permanently stuck on polling), and runs a full reconcile
        on that reconnect.
  - **3b:** a regression test per new failure mode the cache introduces —
        recovery from an uninitialized namespace (never silently published
        as complete), recovery from a hung/crashed background refresh task
        (the freshness deadline actually marks it incomplete and an
        opportunistic `GET` actually triggers a real rescan), a provider
        added/removed at runtime (the cache's namespace set actually tracks
        `refresh_provider_resolvers()`'s own membership changes), and
        competing concurrent refreshes of the same namespace (single-flight
        plus generation-guarded publication actually prevents a
        stale-overwrite and doesn't duplicate the scan).
- [ ] **Phase 4:** a regression test asserting only the expected segment(s)
      refresh for a given cause (cosmetic pulse vs. nav vs. reload vs. pivot
      switch), plus confirmation (via the same real-timer profiling method
      used 2026-09-30) that Textual's compositor now chooses incremental
      updates for the common cosmetic-tick case.
- [ ] **Phase 5:** the same live-timed before/after methodology as the
      `cfg.load_config()` fix, run against a worktree with a genuinely fresh
      affirmative hint; a regression test proving (a) the scan is skipped only
      when the hint is fresh AND `True`, and (b) a stale/absent/`False` hint
      — including a simulated "session attached after the stamp" case — always
      still falls through to the live rescan and is never missed.
- [ ] Full relevant test files green per phase (`test_picker_tui.py` for
      Picker-side phases; the owning plugin's test suite for CLI-side
      manifest/transport changes); a full-suite run is impractically slow on
      this machine (see the 2026-09-30 journal entry) — rely on CI's
      dedicated per-plugin jobs as the authoritative gate, per that same
      entry's precedent.

## Proposal

_Resolved during the plan's own review gate (#4764) — see the 2026-09-30
journal entry below for what changed and why. Phase 3's daemon-relay shape
(CLI-internal, not Picker-direct) and Phase 5's asymmetric hint-trust rule are
now settled design, not open questions._

## Journal

### 2026-09-30 — Kickoff: consolidated from two render-perf investigations + a new transport ask
- Effort created after two investigation rounds in `worktrees-pivot-ux-overhaul`
  (stale MUX/PROC glyph + slow Actions dialog, both fixed and merged — `#4719`,
  `#4738`) surfaced a common shape: slow, synchronous, no-caching round trips.
- Operator asked directly whether an existing tracking item covers "pivot
  sources streaming to the Manager" / SSE-WS escalation. Found `#918`
  (agent-worktrees' own resident-daemon effort for the **built-in** Worktrees
  pivot) — far more complete than expected (Phases 2-5 all landed, plus a
  push-wake primitive, #4354) — but confirmed it does **not** cover
  `picker-reconcile-local`, which still does a live, unfiltered rescan instead
  of trusting the daemon-maintained hint already sitting in the same file.
- Operator then asked to consolidate everything and build a dedicated effort,
  specifically naming: a `stream`-mode (JSONL) contract for registered-pivot
  CLI commands, escalating to a direct HTTP/WS connection to a pivot's own
  live daemon while rendered, with the daemon-backed path taking precedence
  over the CLI path when available.
- Investigated the existing Picker pivot-manifest contract
  (`pivot_manifest.py`/`tasks.py`) before planning anything: found the
  `stream`/`subscribe` NDJSON mechanism **already fully built** and safe to
  adopt, with exactly one partial adopter (`agent-codespaces`, `stream` only)
  and zero adopters of `subscribe` (the held/live mode) anywhere — including
  agent-bridge and agent-dispatch, both of which already run a persistent
  HTTP/SSE daemon for unrelated purposes. This reframed Phases 1-2 from "build
  a mechanism" to "wire two already-built, already-safe manifest flags" —
  confirmed via direct inspection of both plugins' `pivots/*.json` manifests
  and their own READMEs' daemon/SSE descriptions.
- Phase 3 (the literal HTTP/WS escalation past the CLI entirely) is the one
  genuinely new mechanism this effort adds; flagged for a design review gate
  before implementation given the new trust-boundary/failure-mode surface it
  introduces.

### 2026-09-30 — Plan PR (#4764) review: three findings, plan revised before merge
Per this effort's own review gate (`planning-efforts` skill), submitted the
plan as PR #4764 before any implementation. Copilot's review came back
`COMMENTED` (non-blocking per this repo's zero-required-reviews ruleset) but
with three genuinely substantive, code-grounded findings — addressed in the
plan itself rather than dismissed:

1. **Phase 1/3's assumed `subscribe → one-shot` fallback doesn't exist.**
   Verified against `tasks.py:395`: `subscribe` only removes the timeout and
   disables repolling — it does not distinguish a genuinely-held-open channel
   from a producer that emitted once and exited. Added **Phase 0** (a
   prerequisite correctness fix to the existing, currently-unused `subscribe`
   consumption code) requiring an explicit EOF/reconnect contract before any
   real pivot adopts `subscribe: true`.
2. **Phase 5's original "trust any fresh hint" framing would make a
   non-authoritative cache authoritative.** Verified against
   `agent_worktrees/__main__.py:478-486`: the existing, already-reviewed
   precedent for this exact hint is asymmetric — trust a fresh `True` (a false
   positive is harmless), never trust a fresh `False`/absent hint to skip the
   check ("not proof a session hasn't attached since the stamp"). Revised
   Phase 5 to adopt that exact asymmetry rather than a weaker new rule, and
   clarified the mux **client-count** scan stays unconditional (the boolean
   hint lacks `mux_clients`/`mux_attached` granularity Group C's payload
   needs) — the hint only short-circuits `resolve_bound_copilots()`'s
   expensive scan for the already-confirmed-live case.
3. **Phase 3's original shape (Picker connects directly to a pivot's daemon
   over HTTP) contradicts the Picker vision's stated boundary** —
   `visions/picker/README.md:332-336`: the Picker reaches each engine **only
   by invoking its machine-readable CLI verbs**, never in-process or via a
   side-channel to another plugin's runtime. Rather than propose a vision
   change, adopted the reviewer's suggested alternative: keep the fast path
   **behind the CLI-owned client boundary** — each plugin's own `--stream`
   implementation may internally relay its own already-running daemon's feed,
   but the Picker still only ever invokes that CLI verb and never
   distinguishes where the data actually came from. This also resolves most of
   Phase 0's reconnect concern for the daemon-backed case specifically: the
   CLI process, which already owns its own daemon's lifecycle, owns
   reconnecting to it too.

### 2026-10-01 — Phase 0 landed: the subscribe EOF/reconnect contract
Implemented in `worktree-manager/src/worktree_manager/production_picker/
picker_tui/tasks.py`'s `RegisteredPivotRuntime`:
- Added `_subscribe_live`/`_subscribe_retries` (per-machine) tracking and a new
  `_handle_subscribe_drop()` method, the common tail every `_run_list_stream`
  termination path now routes through (an `error` frame, a plain EOF with no
  `done`/`error`, or an unexpected bare `done`).
- A `subscribe` pivot's channel dropping schedules a reconnect
  (`SUBSCRIBE_RECONNECT_BACKOFF_SECS` backoff, re-invoking `_run_list_stream`)
  up to `SUBSCRIBE_MAX_RECONNECT_ATTEMPTS` times; the budget resets to 0 on any
  termination that delivered at least one real row, so a flaky-but-working
  channel reconnects indefinitely while one that never produces anything
  genuinely exhausts.
- `repoll()`'s gate changed from trusting the **static** `pivot.subscribe`
  manifest flag forever to reading the **dynamic** `_subscribe_live` state,
  defaulting to "treat as live" (no-op, matching prior behavior) until a
  channel has actually been observed and explicitly exhausted — so an
  exhausted `subscribe` pivot falls back to ordinary one-shot repolling
  instead of freezing on stale rows for the rest of the session.
- Regression tests (`test_pivot_streaming.py`): a recovering-channel case
  (`subscribe_row_then_drop` — delivers a row, drops, reconnects repeatedly,
  never exhausts) and an exhausting case (`subscribe_empty_done` — never
  delivers a row, exhausts after the configured attempts, hands control back
  to `repoll()`). Both needed care around timing races in test assertions
  (`_subscribe_live` reads `False` transiently during every backoff window,
  not only the terminal one) — settled on waiting for a *stable* reading
  rather than the first sighting.
- Full `test_picker_tui.py` + `test_pivot_streaming.py`: 289 passed.

### 2026-10-01 — Phase 1 landed: agent-dispatch's pivot adopts stream/subscribe
Implemented in `plugins/agent-dispatch/src/agent_dispatch/board_cli.py`:
- Added `--stream`/`--subscribe`/`--interval` to `agent-dispatch-board`. The
  existing one-shot direct-fetch and cross-machine-delegate code paths are
  untouched (deliberately kept byte-identical — their fetch logic is
  duplicated, not refactored in place, into new `_fetch_rows_direct()`/
  `_fetch_rows_delegated()` functions used only by the new stream path) so
  this is a pure addition with zero risk to the plain-JSON path every other
  consumer (`inbox --board`, the installed binstub) still uses.
- `_run_stream()` emits the registered-pivot NDJSON envelope — `begin` -> a
  `row` per task -> `done` — then, with `--subscribe`, holds the channel open:
  every `--interval` seconds (default 2s) it re-fetches and diffs
  (`_diff_rows()`, whole-row-by-`id`) against the last snapshot, emitting
  `delta`/`removed` frames. Same shape as agent-codespaces' `pool --stream
  --subscribe` (periodic in-process re-scan, not a raw relay of
  agent-dispatch's own `/events` SSE stream) — chosen as the lower-risk first
  adopter per the Plan's own ordering rationale; a true SSE-sourced push path
  is explicitly Phase 3's job (CLI-relayed daemon fast path), not this one. A
  transient re-fetch failure during `--subscribe` skips that tick rather than
  killing the channel; only the initial fetch failing is fatal (`error`
  frame + exit 1, matching the one-shot path's stderr+exit-1 contract).
- Flipped `plugins/agent-dispatch/pivots/agent-dispatch.json` to
  `"stream": true, "subscribe": true` — validated against
  `worktree-manager`'s own `test_real_checkout_manifests_match_contract`
  (parses every real `plugins/*/pivots/*.json` through the manifest
  contract).
- Added 11 new tests to `tests/test_board_cli.py` (stream envelope framing,
  one-shot vs. held-open behavior, the initial-fetch-failure `error` frame,
  delta/removed diffing, transient re-scan-failure resilience, `_diff_rows`
  itself, and both `_fetch_rows` paths) — `test_cli.py` (181 passed) +
  `test_board_cli.py` (29 passed) both green.
- Live-verified against this machine's own running coordinator (57 real
  tasks): `agent-dispatch-board --stream` produced the full
  `begin`/57×`row`/`done` envelope; `--stream --subscribe --interval 2` held
  the channel open across multiple re-scans with no process re-exec.
- `worktree-manager`'s full picker suite (pre-review-fix baseline, before the
  two consumer-side bugs below were found): ran `test_plugin_contracts.py`
  (19 passed), `test_pivot_streaming.py` + `test_picker_tui.py` (291 total,
  1 failure on first pass -- `test_streaming_delta_and_removed_update_in_place`
  and, on a separate run, `test_steering_card_and_form_actions_gate_and_drive`
  -- both pre-existing Textual/asyncio timing flakes unrelated to this
  phase's changes, confirmed by passing cleanly in isolation.

**Copilot review on PR [#4840](https://github.com/ThomasMichon/copilot-extensions/pull/4840)
found two real bugs in the existing consumer contract**, surfaced only now
because agent-dispatch is the *first* real `subscribe: true` adopter:
1. **The manifest's `subscribe` flag alone never held the channel open.**
   `RegisteredPivotRuntime._run_list_stream()` always appended only
   `--stream` to argv, never `--subscribe` — so a "subscribe" pivot's
   provider ran its one-shot envelope and exited immediately, and every
   normal exit fell through to Phase 0's reconnect path instead of ever
   holding one process open and receiving live deltas. **Fixed**: argv now
   appends `--subscribe` too when `pivot.subscribe` is set
   (`tasks.py:_run_list_stream`).
2. **A held `subscribe` channel whose initial board was empty would stay
   stuck `loading` forever.** The loop only called `publish()` on a
   `row`/`delta`/`removed` frame; a `done` frame with zero rows (the channel
   stays open afterward, it's not EOF) never triggered a publish, so the
   pivot never left its initial state waiting for a row that might never
   come. **Fixed**: `done` now also publishes the empty snapshot
   immediately when nothing has been delivered yet — without touching the
   `ready`/`had_rows` flag the subscribe-reconnect retry budget depends on,
   so an always-empty channel still exhausts its reconnect budget normally
   (doesn't mask a genuinely dead channel as healthy).
   Added 4 regression tests (`test_subscribe_pivot_passes_subscribe_flag_to_
   provider`, `test_stream_only_pivot_does_not_pass_subscribe_flag`,
   `test_subscribe_empty_board_still_becomes_ready`, plus the existing
   exhaustion test re-verified unaffected) — full
   `test_pivot_streaming.py`: 21 passed.
3. Also reverted three hand-edited version fields (`pyproject.toml`,
   `plugin.json`, `.github/plugin/marketplace.json`) — this repo's version
   lifecycle is changefile-driven, not contributor-edited
   (`CONTRIBUTING.md`'s "Contributing a change: add a changefile"); replaced
   the single `agent-dispatch`-only `dev` changefile with one covering both
   touched plugins (`agent-dispatch`, `worktree-manager`) at `patch`.

### 2026-10-01 — Phase 2 landed: agent-bridge's pivot adopts stream/subscribe
Implemented in `plugins/agent-bridge/src/agent_bridge/inventory_cli.py`, same
shape as Phase 1, now additionally guarding against the two consumer-contract
invariants Phase 1's review corrected in `worktree-manager`'s `tasks.py`
(an explicit `--subscribe` argv flag, and publishing an empty board on
`done`) — both already baked into this phase's design from the start:
- Added `--stream`/`--subscribe`/`--interval` to the `agents` subcommand. The
  existing plain `_cmd_agents` print path is untouched (the new stream path
  is a dispatch at the top of `_cmd_agents`, calling a standalone
  `_fetch_agent_rows()` that duplicates only the fetch+project-filter
  selection logic, not the printing).
- `_run_agents_stream()` emits `begin`/`row`/`done`, then with `--subscribe`
  holds the channel open on a periodic re-scan (default 45s — see the
  corrected cadence below), diffing
  (`_diff_agent_rows()`, keyed by agent `name` — the manifest's `entry.id`)
  into `delta`/`removed` frames. A topology-profile error on the initial
  fetch raises `RuntimeError` from `_fetch_agent_rows()` and is framed as a
  stream `error` (exit 1) rather than the plain path's
  `_report_topology_errors` exit-2 convention, which doesn't fit the NDJSON
  envelope contract.
- Flipped `plugins/agent-bridge/pivots/agent-bridge.json` to
  `"stream": true, "subscribe": true` — validated against
  `worktree-manager`'s `test_real_checkout_manifests_match_contract`.
- Added tests (`tests/test_inventory_streaming.py`): envelope framing,
  one-shot behavior, the initial-fetch-failure `error` frame (including the
  topology-error case specifically), delta/removed diffing, transient
  re-scan-failure resilience, `_diff_agent_rows` itself, and the
  `_cmd_agents` stream/non-stream dispatch — `test_project_override.py`
  (22 passed, unaffected) + `test_client_routing.py` (unaffected) +
  `test_inventory_streaming.py` all green (final count below, after the
  round-2/round-3 review fixes grew the file further).
- Live-verified against this machine's own running bridge daemon (21 real
  registered agents): `agent-bridge --json agents --stream` produced the
  full `begin`/21×`row`/`done` envelope.
- README documented (new "Worktree-picker 'Bridges' pivot" section, no prior
  pivot-doc section existed for this plugin).

`AgentResolver.list_agents_async()` (`agent_registry_resolver.py`)
deliberately drops any namespace resolver (e.g. `codespace:`, `container:`)
that times out or raises, logging a warning but returning a roster that's
silently partial -- an architectural property of that method, not something
this phase introduced. The plain `agents` JSON path never diffs against a
prior snapshot, so a transient partial roster there is low-impact and
self-heals on the next poll; but `--subscribe`'s diffing is exactly the kind
of consumer that CAN'T tolerate it: comparing an incomplete roster against a
complete prior one reads every agent in the transiently-unavailable
namespace as `removed`, making the open pivot flicker valid entries in and
out on every resolver hiccup. Closing that gap requires the daemon to tell a
diffing client which namespace(s) (if any) were incomplete on a given call,
and for a daemon too old to say so at all to never be trusted as "complete"
by omission -- an old daemon silently dropping agents is indistinguishable
from a well-behaved empty response unless its own response can be told
apart from one that genuinely declares nothing incomplete.
**Fixed** in three layers:
1. `AgentResolver` tracks which prefixes were incomplete on the *most
   recent* `list_agents_async()` call (`incomplete_namespaces` property,
   reset every call so it never latches a stale failure), surfaced through
   `GET /api/v1/agents`'s `incomplete_namespaces` response field.
2. `BridgeClient.list_agents_with_incomplete()` (additive --
   `list_agents_with_diagnostics()`'s 2-tuple contract is untouched for
   every other caller) detects whether the DAEMON IT JUST TALKED TO
   advertises this capability at all by checking the response dict for
   *key presence*, not merely reading the field with a default: an older
   daemon's route handler omits `incomplete_namespaces` from the JSON body
   entirely (it's a tolerant-reader dict response, not a schema-enforced
   one), so `"incomplete_namespaces" in resp` is itself the exact, reliable
   capability signal for THIS specific response -- no protocol-version
   negotiation infrastructure needed at all. `capability_known=False`
   degrades the caller to "cannot confirm any namespaced removal", never
   trusting an absent field as proof the scan was complete.
3. `_run_agents_stream()`'s subscribe loop suppresses `removed` for any id
   whose namespace prefix is named in that tick's `incomplete_namespaces`
   (capability-aware daemon) -- or, against a daemon that doesn't advertise
   the capability at all, suppresses every namespaced (`prefix:name`)
   removal outright, since none can be confirmed. A suppressed id's
   last-known row is carried forward in the tracking snapshot, so neither
   this tick nor a later comparison treats a transient gap as removal; only
   an actually-complete scan reports an agent gone. The INITIAL scan (first
   launch, or any Phase 0 reconnect after a dropped channel) has no prior
   snapshot for that suppression logic to fall back on -- publishing an
   incomplete initial roster as authoritative would make the Picker replace
   its whole cache with the smaller set, silently dropping the missing
   namespaced agents with no `removed` frame at all. `_fetch_complete_
   initial_rows()` retries the initial fetch (bounded,
   `INITIAL_SCAN_MAX_RETRIES` attempts) past a detected incomplete
   namespace before that first publish; a capability-unknown daemon isn't
   retried (there is no signal retrying could ever resolve), and an
   initial scan that stays incomplete across every retry still publishes
   eventually -- bounded means bounded, not blocked forever.

Regression tests at all four touched layers: `test_agent_registry.py`
(`incomplete_namespaces` reported then reset on a subsequent clean scan,
157 passed), `test_client_routing.py` (key-presence detection for both the
reporting and capability-unknown cases, plus the "key present but empty"
case and defaulting safely against an older daemon's response, 15 passed),
`test_routes.py::TestAgentRoutes` (the field actually serializes through
the live route, not just the resolver unit, 6 passed), and
`test_inventory_streaming.py` (subscribe-loop removal suppression for both
the capability-aware and capability-unknown cases, plus the initial-scan
bounded-retry behavior across all three shapes -- resolves, exhausts, and
never-retries-when-capability-unknown -- 15 passed).

First attempt at the capability-detection mechanism bumped
`HTTP_PROTOCOL_VERSION` to a new generation and re-attested agent-bridge's
contract-registry fixtures to match -- caught on review as fabricating
false provenance: the referenced historical commit's actual `protocol.py`
still declared the OLD generation, so claiming it as the source of NEW
generation constants made the fixture's own `captured_from` block
internally false regardless of which generation number sat next to it.
Replaced with the simpler key-presence check above, which needs no
protocol-version machinery and no contract-registry fixture work at all.

**`client.py` module-size baseline widening, explicitly reconciled** (caught
on review as contradicting this entry's own prior "reverted" claim, which
was wrong -- only the *protocol-version-specific* widening reverted; the
two new `BridgeClient` methods themselves are a real, if smaller, net
addition that still needs one). The new capability genuinely needs
`list_agents_with_incomplete()` (the real GET + key-presence check) plus a
one-line `list_agents_with_diagnostics()` delegation to it -- net +14 lines
over `origin/dev` after trimming both to single-line signatures and
one-line docstrings; there's no further meaningful extraction (it's already
the smallest honest expression of "fetch once, derive the 2-tuple view
from the 4-tuple one"). Widened `tools/module-size-baseline.json`'s entry
from 1686 to 1700 (the file's exact resulting line count) -- a deliberate,
reviewed exception per `CONTRIBUTING.md`'s "manual, reviewed edit" escape
hatch for the shrink-only baseline, not a silent/automated ratchet.

**`--subscribe`'s default interval, corrected** (a real perf finding):
copied agent-dispatch's 2s default verbatim without accounting for the
fact that `GET /api/v1/agents` is not a cheap single coordinator call --
`AgentResolver.list_agents_async()` invokes every registered namespace
resolver concurrently, and CodeSpaces enumeration alone is documented at
4-10s (`docs/architecture.md`), backed by only a 12s per-resolver cache
(`AGENT_BRIDGE_NAMESPACE_LIST_TTL`). A 2s poll would trigger that scan far
more often than the Picker's prior one-shot repoll cadence (45s,
`engine_runtime.py`'s `POLL_SECS`) ever did. Changed `DEFAULT_SUBSCRIBE_
INTERVAL` to 45.0 -- matching the existing cadence it replaces, rather than
copying a sibling plugin's cadence for a cheaper call shape.

**Review round 5 -- revisited (and declined) the protocol-version bump,
fixed stale "2 seconds" docs.** Most of round 5's 14 findings were stale
re-listings of round-2/3 findings already resolved in the diff (e.g. the
fixture-provenance concerns no longer apply once the whole protocol-bump
attempt was reverted). Two genuinely new items:

- `routes/agents.py:25` asked to bump `HTTP_PROTOCOL_VERSION` for the new
  `incomplete_namespaces` response field, per `protocol.py`'s own
  documented convention. Declined, with a reply comment on the review
  thread giving the concrete justification: `protocol.py`'s own rule
  explicitly carves out "additive, tolerant-reader changes (a new optional
  field an old client simply ignores)" as **not** requiring a bump --
  `BridgeClient.list_agents_with_incomplete()` already detects support via
  response dict **key presence**, not protocol negotiation, which is
  exactly that carved-out case and needs no version number to be correct
  against an old daemon. Separately (and this would block the bump even if
  it were wanted): re-verified empirically that `tools/check-agent-bridge-
  contracts.py` requires every fixture's `captured_from.commit` to resolve
  to a real, already-existing git commit whose historical source blob
  matches the claimed generation -- no such commit can exist for a
  generation this PR itself introduces, since its own commits get
  squash-merged into an unpredictable hash. Confirmed locally that bumping
  the constant alone (fixtures left at generation 19) fails 4
  `test_contract_registry.py` tests, and that the `agent-bridge` CI job
  (full `pytest -q`, not `-m guard`-filtered) is a real, currently-passing
  required check on this PR -- this is not merely a local pre-push nicety.
  This reconfirms round 3's own "keep semantic, attest in follow-up"
  finding rather than contradicting it: no protocol.py change at all is
  needed in this PR, so there is nothing left to attest in a follow-up.
- Fixed the two remaining stale "2 seconds" references round 5 flagged:
  `plugins/agent-bridge/README.md:50` ("every two seconds" -> "every 45
  seconds") and this file's own streaming-architecture note above (now
  describes the 45s default instead of the superseded 2s one).

**Review round 6 -- genuine bug: real CLI-backed namespace providers
swallow failures that `incomplete_namespaces` was supposed to catch.**
`AgentResolver.list_agents_async()`'s `incomplete_namespaces` tracking
(round 2's fix) only records an exception that escapes a namespace
resolver's `list()` call. The production manifest-backed providers are
`CliNamespaceResolver`s (`agent_registry_namespace.py`) with no in-process
fallback, and its `list()` converted a missing binstub, a non-zero exit,
an execution error/timeout, or malformed JSON output into the *same*
successful empty list -- so a genuine provider failure never escaped as
an exception at all, leaving `incomplete_namespaces` empty and letting
`--subscribe` emit a false `removed` frame for every agent in that
namespace: exactly the bug the field exists to prevent.

Fixed by splitting the two cases `CliNamespaceResolver.list()` was
conflating: a missing binstub (the provider genuinely isn't installed on
this machine -- a legitimate "contributes nothing" absence, checked via
`shutil.which` on the resolved executable, the explicit `command` vector
included) still degrades to `[]` with no fallback; anything after that --
non-zero exit, an execution failure/timeout (`_run` returning `None`
despite a found executable), or unparseable output -- now raises a new
`NamespaceListIncomplete` (defined alongside the resolver) when there's no
fallback to cover it, so it reaches `list_agents_async()`'s existing
generic exception handling exactly like the test-double failure round 2
already covered. A resolver *with* a fallback is unaffected -- the
fallback still absorbs the failure as before. Deliberately left `_run()`
itself untouched: it's shared by `resolve()`/`ensure_ready()`/
`target_repo()`, whose own fallback-or-raise handling already treats a
`None` return correctly, and widening its contract would have changed
behavior for those unrelated call sites for no benefit here. Added 4 new
`CliNamespaceResolver`-level tests (timeout, non-zero exit, unparseable
output each raising with no fallback; the same non-zero-exit case still
degrading gracefully through a fallback) plus one `AgentResolver`-level
integration test using a real `CliNamespaceResolver` (not a test double)
to close the loop the reviewer specifically asked for.

### 2026-10-02 — Phase 3 design review: agent-dispatch and agent-bridge are not symmetric
Read both daemons' actual SSE/event surfaces before writing any Phase 3 code,
per this phase's own Validation Plan gate ("each plugin's own daemon-relay
change must be reviewed before landing"). Finding: the Plan's framing (both
plugins "already running a persistent daemon with its own SSE stream") is only
half true for the specific feed a roster relay needs.

- **agent-dispatch**: `DispatchClient.stream_events()` (`client.py:1103`,
  `GET /events`) already yields the coordinator's real lifecycle event bus
  (`EventBus.publish`/`subscribe`, `events.py`) -- `task.submitted`,
  `task.completed`, `task.abandoned`, etc., each carrying the full task dict
  under `"task"`. This is directly relayable: a CLI-side `--subscribe` loop
  can consume it instead of polling.
- **agent-bridge**: grepped every SSE route in the plugin
  (`routes/live_sessions.py`, `routes/remote.py`, `routes/sessions.py`) --
  all of them are **per-session** event logs (a represented live session's
  own translated SDK events, or a remote-forwarded session's stream). There
  is **no existing aggregate "the agent roster changed" feed** to relay.
  Phase 2's `AgentResolver`/`CliNamespaceResolver` scan is a pure poll; the
  daemon does not push roster deltas to anyone today.

Revised the Phase 3 plan (above) into three parts instead of one undifferentiated
checklist:
- **3a (agent-dispatch):** relay `stream_events()` directly, filtered to
  task-bearing events, re-using the *existing* row transform
  (`_fetch_rows`/`_fetch_rows_direct`) so an event-sourced row can't drift
  from a polled one in shape or filter semantics. Because `EventBus.subscribe()`
  is a live, non-replay broadcast (nothing buffers an event published during a
  dropped connection), this is paired with a **mandatory background full
  reconcile** on a long interval (trust-but-verify, not a return to tight
  polling) -- without it, a reconnect gap would silently and permanently desync
  the board rather than self-heal on the next tick. Degradation on any stream
  failure is literally Phase 1's existing poll-and-diff code, unmodified, not
  a new third path.
- **3b (agent-bridge, land first):** move the resolver scan into the daemon
  as a background-refreshed in-memory cache; `GET /api/v1/agents` becomes an
  O(1) cache read. No new failure mode (the HTTP call shape is unchanged),
  and it is the one change both the real cost (N Pickers each separately
  paying for a 4-10s CodeSpaces enumeration) and this effort's "CLI relays a
  live feed" intent reduce to "CLI polls a now-cheap endpoint" -- a smaller,
  safer slice than inventing a new SSE route first.
- **3c (agent-bridge roster SSE, deferred):** only pursue a genuine
  `GET /api/v1/agents/stream` push route -- which *would* introduce the new
  daemon-connection failure mode this phase's Validation Plan gate is
  actually about -- if 3b's measured win doesn't suffice. Scoping it out now
  keeps this design review's surface area matched to what's actually being
  built next, rather than pre-approving a daemon-push architecture that may
  never be needed.

No code changed in this session leg -- this is the design-review artifact
itself (effort README revision), landed as its own reviewed PR per the gate's
own wording ("must be reviewed before landing"), before any Phase 3
implementation PR opens.

### 2026-10-02 — Phase 3 design PR (#4928) review round 1: four real gaps, all fixed
Copilot's review on the design PR itself (the mechanism this phase's own gate
calls for) found four genuine gaps the first draft missed -- all now folded
into the 3a/3b plan above:

- **High: delegated-machine boards would relay the wrong machine's events.**
  `_run_stream()` also serves `--machine <peer>` boards via
  `_fetch_rows_delegated()`; the local coordinator's `/events` only describes
  *this* machine. Fixed by scoping the relay strictly to the
  `_fetch_rows_direct()` case -- a delegated board keeps the unmodified
  poll-and-diff loop, a hard boundary for this phase, not a follow-up gap.
- **Medium: the relay would trip into its own fallback on every quiet
  board.** `DispatchClient`'s single 10s timeout covers connect/read/write/
  pool, and `/events` emits no heartbeat -- past 10s of coordinator quiet,
  `stream_events()` would read-timeout and permanently downgrade to polling.
  Fixed by requiring an unbounded read timeout specifically for this one
  long-lived GET (an httpx per-call override), leaving the 10s default
  untouched for every other short request the same client makes.
- **Medium: activity-only updates would regress from a 2s to a 30-60s
  cadence.** `POST /tasks/{id}/activity` publishes no bus event today
  (`_guard()` called with no `event_type`), yet drives the board's `wt_live`
  liveness flag and subtitle. Fixed by requiring a new `task.activity_updated`
  event on that endpoint, the same `_guard(..., event_type)` pattern every
  other mutating endpoint already uses -- keeps activity on the fast path
  instead of carving out a slower-cadence exception for one field.
- **Medium: an O(1) cache read would break the existing incomplete-roster
  recovery contract.** `_fetch_complete_initial_rows()`'s bounded retry only
  works today because each retried `GET /api/v1/agents` call is a real
  re-scan; a pure cache read would return the same stale partial snapshot on
  every retry until the background timer happened to fire, letting a new
  subscriber publish an incomplete roster as authoritative. Fixed by
  requiring the cache to retain last-known-good *per namespace* plus an
  explicit `force_refresh` signal the retry loop sets to trigger an
  immediate out-of-band re-scan of just the still-incomplete namespace(s) --
  every other caller keeps the cheap unconditional cache read.

All four replied-to inline with the concrete fix and the exact README lines
revised, per this effort's established review-response convention (state the
fix, don't just acknowledge).

### 2026-10-02 — Phase 3 design PR (#4928) review round 2: five more gaps in the 3a/3b detail, all fixed
Round 2 reviewed the round-1 fixes themselves and found deeper detail gaps in
exactly the two areas round 1 touched -- a pattern worth naming: a fix that
names the right mechanism (an event, a cache) still needs its *own* failure
modes worked through before it's actually complete.

- **3a -- every eventless mutation, not just the one round 1 found.** Audited
  `coordinator_tasks.py`'s other `_guard()` call sites past `activity`:
  `POST /tasks/{id}/heartbeat` also publishes nothing, yet
  `lease_expires_at`/`updated_at` (which `_build()` uses for row sort order)
  update on every heartbeat -- the same 2s-poll-observes-it,
  reconcile-only-sees-it gap. Fixed by adding `task.heartbeat` alongside
  `task.activity_updated`, and generalized the requirement: audit *every*
  `_guard()` call in that file for a missing `event_type` before considering
  3a's event coverage complete, rather than patching one field at a time as
  review happens to surface each.
- **3a -- the reconcile and the live stream can race.** If the background
  reconcile fetches its snapshot just before a mutation, but the matching
  event is consumed and applied just after that fetch and before the
  reconcile's own diff/emit runs, the reconcile's stale view re-emits the
  row's *old* state, visibly reverting a just-applied update until the next
  reconcile pass. Fixed by a single snapshot-owner lock: the reconcile holds
  one serialization guard across its whole fetch-diff-emit sequence, and any
  event arriving while it's held is queued, never interleaved mid-reconcile.
- **3b -- a stalled background scan is a new, silent-forever failure mode.**
  Moving the resolver scan off the request path means a dead or hung
  refresh task would leave every read serving an apparently-complete old
  snapshot with no signal anything is wrong -- the current per-request scan
  has no equivalent failure shape. Fixed by requiring the background task be
  supervised (restarted on exit/hang, same standard every other long-lived
  loop in this codebase is already held to) plus a per-namespace freshness
  deadline: a namespace whose last successful scan is older than the
  deadline reports as incomplete, the same signal a genuinely-failing
  resolver already produces.
- **3b -- "last-known-good" doesn't cover "never scanned yet."** The
  previous design's last-known-good retention only handles a namespace that
  has *previously* succeeded; during daemon warm-up a namespace with no
  prior scan at all would read as an empty-but-apparently-complete cache,
  defeating `_fetch_complete_initial_rows()`'s detection entirely. Fixed by
  an explicit uninitialized-per-namespace state, always reported incomplete
  until that namespace's first successful scan.
- **3b -- `force_refresh` needs the repo's own protocol-version gate, and an
  explicit reverse-skew story.** `force_refresh` is a new request parameter
  with real server behavior, not an additive response field -- exactly the
  case `protocol.py`'s own documented rule requires a `HTTP_PROTOCOL_VERSION`
  bump for (unlike Phase 2's key-presence approach, which was correctly
  exempt as tolerant-reader-only). Fixed by requiring the bump plus a
  `BridgeClient.daemon_supports()` gate, following the file's own
  `RELAY_INTERRUPT_PROTOCOL_VERSION` precedent -- and, separately, specified
  that an **old CLI talking to a new daemon** (one that never sends
  `force_refresh` at all) is covered by the freshness-deadline mechanism
  above, not by the protocol gate: the deadline expiring makes the daemon
  self-report a stuck namespace as incomplete regardless of what the caller
  requested, so the old CLI's existing retry-on-incomplete logic still has
  something real to react to.

All five replied-to inline with the concrete fix and the exact README
section revised, continuing the same review-response convention.

### 2026-10-02 — Phase 3 design PR (#4928) review round 3: timeless-prose violation + incomplete event-coverage scope
Two findings:
- **Timeless-prose violation in the Plan.** The Plan section had accumulated
  "(round-1 review finding)"/"(round-2 review finding)" annotations directly
  in its bullet text — `CONTRIBUTING.md`'s timeless-prose rule reserves
  review-round history for the dated Journal, which already records all of
  it. Fixed by stripping every such annotation from the Plan prose (8
  occurrences across the Phase 3 section); the Journal entries above are
  the sole place this review's history is recorded.
- **Event-coverage audit was scoped to one file.** Round 2's "audit every
  `_guard()` call" requirement only named `coordinator_tasks.py`. Two more
  entry points mutate task state with no event at all: the MCP heartbeat
  path (`mcp_http.py`'s own `_mutate(..., None)`) and the background
  reconcilers (`coordinator_loops.py`'s liveness/cooldown/orphan/run-waiter
  sweeps, `coordinator.py`'s run-waiter recovery) — the latter publish only
  aggregate, no-`task`-payload count events, which the then-current design's
  task-payload filter would have silently dropped as pure noise. Folded into
  the broader requirement below (see round 4) once the relay's own event
  model changed to no longer need task-identity-carrying events at all.

### 2026-10-02 — Phase 3 design PR (#4928) review round 4: the per-event-transform model itself had a gap no patch could close — redesigned 3a around a simpler model
Round 4 found something more fundamental than a missing detail: the
per-event row-transform model from rounds 1-3 **cannot correctly maintain a
`--limit`-capped board** — `/tasks` applies `--limit` to the whole result
set, not per task, so a single event's own payload can never tell the relay
whether adding/removing that one task should displace or backfill another
row. Patching this within the per-event-transform model would mean teaching
the relay to reconstruct whole-set membership logic the daemon itself
already owns — solving the same problem twice, in two places, with two
chances to disagree.

Redesigned 3a around a simpler model instead of patching this one bullet:
**every event is now treated as a pure wake signal that triggers an
immediate full `_fetch_rows_direct()` + `_diff_rows()` pass** — the exact
same full-board re-fetch-and-diff the existing poll loop already performs
correctly today, just woken by a real event instead of a timer. This single
change collapses several findings at once rather than requiring four more
patches:
- **The `--limit`/membership problem disappears by construction** — a full
  re-fetch always recomputes the true capped, newest-first set, the same
  way the poll loop already handles additions, displacements, and backfills
  today. No separate membership-maintenance logic to write or get wrong.
- **The event-coverage bar drops from "needs task identity" to "needs to
  exist at all"** — since the relay no longer parses event payloads into
  rows, an aggregate count event (no `task` field) is just as good a wake
  signal as a full lifecycle event. This resolves round 3's reconciler
  concern directly: `coordinator_loops.py`'s/`coordinator.py`'s aggregate
  events work fine as-is. Only the handful of mutation endpoints that
  publish **nothing at all** today (`activity`, `heartbeat`,
  `mcp_http.py`'s `_mutate(..., None)`) still need an event added — a much
  smaller, purely mechanical fix than the earlier "carry correct task
  identity" requirement.
- **Debouncing becomes necessary** (new requirement): a burst of events must
  coalesce into one pending re-fetch, not one re-fetch per event, to avoid
  hammering the coordinator on a busy board.

Two further, independent findings from the same round:
- **Time-derived fields (the `activity` TTL expiry, the "stalled Nm" text)
  change with no mutation and no event at all** — `_build()` derives them
  from `time.time()` directly. Neither the event relay nor the 30-60s
  reconcile would refresh them at a cadence close to today's. Fixed by a
  separate, local, no-network recompute tick (comparable to today's 2s)
  that only recalculates these display fields from already-cached
  timestamps — never a daemon round-trip.
- **3b's background refresh has its own concurrency race**: a periodic
  scan, a forced scan, and concurrent initial-scan subscribers can all
  target the same namespace at once; an earlier-started-but-slower scan
  finishing after a later one would overwrite the newer result, and naive
  concurrent triggering reintroduces the N-scan cost the cache exists to
  remove. Fixed by per-namespace single-flight refresh (concurrent
  callers await the one in-flight scan) plus generation-guarded publication
  (a scan only publishes if its starting generation is still current).

All three replied-to inline (one reply per thread; the `--limit` finding and
the time-derived-fields finding and the concurrency finding each had a
duplicate noted on a second line in the same file, addressed by the same
single design change rather than two separate patches).

### 2026-10-02 — Phase 3 design PR (#4928) review round 5: the new mechanisms from round 4 had their own two gaps
Round 5 reviewed round 4's redesign itself and found two gaps in the
mechanisms it introduced:

- **The new time-derived recompute tick wasn't covered by the snapshot lock
  round 4 added for the other writers.** It reads cached rows and emits
  deltas just like the event-woken fetch and the reconcile do, so without
  the same lock it can read a stale cached row while a full fetch is
  concurrently publishing newer task state, then emit that stale row
  afterward and revert non-time fields the tick never meant to touch.
  Fixed by widening the snapshot-owner lock to cover all three writers —
  the event-woken fetch, the long reconcile, and the recompute tick's own
  read-recompute-diff-emit sequence all take the same lock now.
- **Falling back to polling permanently on the very first SSE failure
  contradicts this phase's own premise** (the CLI owns reconnecting to its
  daemon) and means one transient blip disables the whole speedup for a
  long-lived channel's remaining lifetime. Fixed by keeping poll-and-diff
  as the *immediate* correctness fallback but adding bounded-backoff SSE
  reconnect attempts in the background; a successful reconnect runs one
  full reconcile (covering anything missed while on the fallback) before
  resuming the event-woken relay, and only a genuinely exhausted retry
  budget settles into permanent polling.

Both replied-to inline with the concrete fix.

### 2026-10-02 — Phase 3 design PR (#4928) review round 6: reconnect identity, reverse-skew enforcement, and three carried-over gaps in unchanged code
Round 6 found one more new gap in the round-5 reconnect fix, confirmed two
findings from round 5 were still genuinely open (not yet fixed despite being
replied-to — caught here because they'd stopped showing as newly-surfaced),
and surfaced three further gaps in code the design hadn't touched yet:

- **New: reconnecting must build a fresh client, not retry the stale one.**
  A routine zero-downtime coordinator-generation cutover flips `active.json`
  to a new bind/port; retrying the *same* `DispatchClient` instance can
  never recover from that, since its base URL is fixed at construction.
  Fixed by requiring each reconnect attempt to re-resolve the endpoint
  (`_endpoint()`, the same logic the initial client already uses) and
  construct a fresh client — reusing the exact pattern
  `ResolvingDispatchClient` already exists for supervisors, rather than
  inventing a second one.
- **Confirmed still open from round 5: reverse skew needs an active
  trigger, not just a passive report.** The freshness-deadline mechanism
  only changes what a `GET` *reports*; an old CLI that never sends
  `force_refresh` and only retries a few plain `GET`s 0.5s apart would keep
  reading the same stale snapshot across all of them and publish it before
  the background timer ever fires. Fixed by making any `GET` against an
  incomplete/uninitialized/deadline-expired namespace opportunistically
  join that namespace's single-flight refresh itself — `force_refresh`
  becomes a pure "skip straight to it" optimization, not the only path that
  can trigger a real rescan, which is what actually preserves reverse skew.
- **Confirmed still open from round 5: the PR description's own 3a summary
  was stale.** It still described the per-event-transform model round 4
  replaced. Updated to describe the final wake-only-trigger design, per the
  required Documentation-impact-matches-the-diff convention.
- **Previously missed, in code untouched by this design so far — the
  initial-subscription gap:** a mutation landing after the initial fetch
  but before `stream_events()`'s subscription is actually live has no event
  to consume — the same non-replay gap the reconnect path already handles.
  Fixed by treating startup the same way: establish the subscription, then
  immediately run one full reconcile before processing any of its events.
- **Previously missed: the recent-mins cutoff is also a clock-only
  transition.** `_build()` removes a terminal row once it ages past
  `--recent-mins`, with no mutation or event involved — exactly the same
  class of gap the activity-TTL/stalled-text fix already identified, just
  a third instance of it. Folded into the same local recompute tick rather
  than a separate mechanism.
- **Previously missed: the agent-bridge namespace set is dynamic, not fixed
  at daemon startup.** `refresh_provider_resolvers()` already adds,
  replaces, and unregisters providers at runtime; the cache's namespace set
  must track this on its own refresh cycle — a newly-registered namespace
  enters as uninitialized, and an unregistered one is retired from the
  cache outright, never left serving stale agents for a provider that no
  longer exists.

All six replied-to inline (three new threads; three confirmations/fixes on
carried-over and previously-missed findings).

### 2026-10-02 — Phase 3 design PR (#4928) review round 7: subscription-ack race, two more silent-mutation paths, stale Validation Plan
Three more findings:
- **Opening the SSE connection isn't proof the subscription is live.**
  `/events` sends no initial frame, and `EventBus.subscribe()` only
  registers its queue once the route's generator starts iterating — a
  client can observe response headers before that registration happens, so
  running the startup reconcile right after `stream_events()` returns can
  still race the exact gap it's meant to close. Fixed by requiring the
  route to emit an explicit ready frame right after queue registration,
  and the client to wait for it, buffer events arriving from that point,
  run the reconcile, then apply the buffered events on top — closing the
  gap on both sides instead of just moving where it could happen.
- **Two more mutation paths publish nothing:** `POST /recover` and MCP
  `dispatch_recover` both call `queue.reconcile_liveness()` directly with
  no event at all, despite being able to requeue/suspend/dead-letter rows.
  Folded into the same "every mutation path needs at least one event"
  requirement as the activity/heartbeat fix.
- **The Phase 3 Validation Plan still only described the two-daemon-relay
  shape this design abandoned for agent-bridge.** Killing a daemon mid-render
  doesn't exercise 3b's actual new failure modes (an uninitialized
  namespace, a hung refresh task, runtime provider add/remove, competing
  concurrent refreshes) at all. Rewrote the Phase 3 Validation Plan entry
  into 3a-specific and 3b-specific acceptance criteria matching what each
  part of the redesigned plan actually introduces.

All three replied-to inline.
