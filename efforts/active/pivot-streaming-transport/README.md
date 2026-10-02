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
aware**: prefer the fastest channel a pivot's own CLI can actually offer,
behind the CLI-owned client boundary the Picker already talks to — a CLI
whose own `--stream`/`--subscribe` implementation internally relays its
already-running daemon's live feed (direct HTTP/SSE to that daemon, chosen
and owned entirely by the CLI, never by the Picker) when one is discoverable
and reachable, degrading to the CLI's own poll-and-diff streaming mode when
no daemon exists or isn't reachable, degrading further to the original
one-shot JSON call only as the safety-net floor — and to make the Picker's
own render path genuinely incremental (diff incoming state, write only the
layout regions that actually changed) rather than redrawing more than it
needs to on every update. The Picker itself never grows a new transport or a
direct daemon connection of its own (per Copilot review on this effort's own
plan PR #4764, confirmed and detailed further by Phase 3's own design
review — see `phase-3-design.md` — this keeps the Picker inside the
vision's stated engine boundary, "reaches each engine only by invoking its
machine-readable CLI verbs," `visions/picker/README.md:332-336`). This
closes the loop opened by the render-perf investigations landed in the
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
_(Revised per Copilot review on #4764: the fast path stays behind the
CLI-owned client boundary — the Picker still only ever invokes
`list --stream`/`subscribe`; the speedup comes from each CLI's own
`--stream` implementation relaying its already-running daemon's live feed
internally, invisible to the Picker. Design reviewed 2026-10-02 per this
phase's own Validation Plan gate — see the Journal for the full review
history. **Full detail moved to a sibling document**
(`efforts/README.md`'s "extract substantial phase designs" convention):
[`phase-3-design.md`](phase-3-design.md). Summary: agent-dispatch and
agent-bridge are not symmetric — agent-dispatch's coordinator already
publishes a genuine roster-relevant event feed (`GET /events`) a CLI-side
relay can consume directly; agent-bridge's daemon has no existing
roster-change event stream, so its own fast path is a daemon-side cache
(3b) first, with a roster-push SSE route (3c) deferred until 3b proves
insufficient.)_

- [ ] **3a — agent-dispatch relay** (full detail:
      [phase-3-design.md](phase-3-design.md)): treat any `stream_events()`
      event as a wake trigger for an immediate full re-fetch-and-diff, not
      a per-event row transform. Covers: scope to the direct (non-delegated)
      path only; an unbounded read timeout for the idle SSE connection; a
      version-skew-gated ready-frame handshake closing the startup/reconnect
      subscription gap (with a no-observable-barrier fallback to plain
      polling for an old daemon); debounced, rate-limited event-woken
      re-fetches with a trailing-dirty flag; a local no-network tick for
      every purely clock-driven field (activity TTL, stalled-text,
      recent-mins cutoff, relay-staleness); a single snapshot-owner lock
      serializing every writer; and bounded-backoff reconnection with
      fresh-client endpoint re-resolution. Every board-visible mutation
      across `coordinator_tasks.py`, `mcp_http.py`, and
      `coordinator_verification.py` needs at least one bus event (audited
      as a standing, provisional requirement, not a closed list).
- [ ] **3b — agent-bridge daemon-side cache** (land first; full detail:
      [phase-3-design.md](phase-3-design.md)): move the per-call resolver
      scan into the daemon as a background-refreshed, supervised cache;
      `GET /api/v1/agents` becomes a cheap read for a healthy cache hit
      only — forced/incomplete/uninitialized reads intentionally still
      block on a real scan. Covers: last-known-good-per-namespace retention
      with opportunistic single-flight refresh on any incomplete read (not
      just an explicit `force_refresh`, which is protocol-gated); a
      server-side, version-independent `503` contract (not a new response
      field) for "nothing authoritative to serve" — covering both
      pre-first-discovery startup and exhausted retries with no
      last-known-good; a dynamic namespace set tracking
      `refresh_provider_resolvers()`'s own add/remove/replace; and
      single-flight-plus-generation-guarded concurrent refreshes.
- [ ] **3c — agent-bridge roster-change SSE** (deferred; do not start
      until 3b is shipped and measured insufficient): add a genuine
      `GET /api/v1/agents/stream` daemon route pushing `delta`/`removed`
      when the 3b cache detects a roster change, so the CLI can relay it
      the same way 3a does for agent-dispatch — deferred because it
      duplicates Phase 2's client-side diffing logic on the daemon side and
      introduces the daemon-connection failure mode this phase's Validation
      Plan gate is actually about.

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
        on that reconnect; a test proving the ready frame (and any
        other control frame `stream_events()` may gain) never reaches an
        unrelated existing consumer of that same method, e.g.
        `agent-dispatch watch`/`task_query_cli._cmd_watch()`, not just the
        relay's own consumer; **deterministic concurrency regression
        tests** for each race this design introduces — the ready-frame
        handshake actually closing the startup subscription gap (not just
        narrowing it), an event arriving during an in-flight fetch actually
        producing exactly one trailing fetch (not zero, not a pile-up), and
        the event-woken fetch / long reconcile / local recompute tick
        writers actually serializing against each other without a
        stale-overwrite — none of which a coordinator-kill test alone
        exercises; **and the fallback-poller/reconnect handoff
        specifically**: a deterministic test proving the current poll tick
        finishes (with no next tick scheduled) and cannot publish after the
        reconnect's promotion reconcile has already run — sharing the
        snapshot-owner lock alone doesn't prove the quiescence ordering is
        actually enforced.
  - **3b:** a regression test per new failure mode the cache introduces —
        recovery from an uninitialized namespace (never silently published
        as complete), recovery from a hung/crashed background refresh task
        (the freshness deadline actually marks it incomplete and an
        opportunistic `GET` actually triggers a real rescan), a provider
        added/removed **or replaced** at runtime (the cache's namespace set
        actually tracks `refresh_provider_resolvers()`'s own membership
        changes, and a same-namespace replacement actually invalidates the
        prior generation's cache entry rather than serving the old
        provider's stale agents as authoritative), competing concurrent
        refreshes of the same namespace (single-flight plus
        generation-guarded publication actually prevents a stale-overwrite
        and doesn't duplicate the scan), **daemon startup before any
        namespace has ever been discovered** (the route responds `503`,
        never a `200` with an empty-but-apparently-complete body),
        **initial-scan retry exhaustion with nothing authoritative to
        serve** (the same `503` contract, verified against both an old and
        a new client — the fix is server-side and version-independent, not
        a client-side blocking change only a new client would observe), and
        **`refresh_provider_resolvers()`'s own discovery-generation
        expiring** (the `503` fires even while every existing namespace
        still has last-known-good data — never conditioned on namespace-
        level staleness also being absent — and a discovery result that
        reports a per-manifest construction failure, not only a raised
        scan exception, must count as a failed attempt that does not
        advance the generation), **and a zero-downtime daemon-generation
        cutover** (a new generation is never promoted — or traffic routed
        to it — before its own cache has completed a first authoritative
        scan, or the retiring generation's cache state is transferred,
        whichever this design implements; prove the cutover itself never
        introduces a new `503` blip a pre-3b cutover wouldn't have had).
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

### 2026-10-02 — Phase 3 design PR (#4928) review round 8: the recompute tick missed a fourth clock-only transition, and the buffer fix contradicted the wake-only model
Two findings:
- **The local recompute tick's "three clock-only transitions" framing was
  incomplete.** `board_fields_for_task()` (`board_cli.py:470-480`) carries
  its own relay-derived freshness/age fields that clear
  `artifacts_summary`/`length_display` once the relay entry itself goes
  stale (`worktree_status_relay.py`) — a fourth clock-only transition,
  distinct from the task-timestamp-derived ones. Fixed by requiring the
  cache to retain the **raw relay entry** alongside each cached row (the
  rendered row alone doesn't carry enough to recompute this), and widening
  the recompute tick to cover all four transitions, not three.
- **Round 7's own fix ("apply the buffered events on top") quietly
  contradicted the wake-only model it was supposed to fit into** — a
  buffered event has no row-level state to "apply," since round 4 already
  established every event is a content-free wake signal, not a transform
  input. Fixed by making the correct behavior explicit: buffered events are
  counted only, and if any landed during the reconcile window, schedule one
  coalesced full re-fetch afterward through the same debounced wake path
  ordinary events already use — not a second, inconsistent mechanism.

Both replied-to inline.

### 2026-10-02 — Phase 3 design PR (#4928) review round 9: ready-frame version skew, trailing-fetch gap, provider-replacement cache staleness, a contradictory claim
One new finding plus three previously-missed ones surfaced together:

- **New: the ready-frame handshake itself needed version-skew handling.**
  Waiting unboundedly for a frame an older coordinator will never send would
  hang the relay forever (this design also removes the read timeout for
  exactly this stream). Fixed by having the coordinator advertise ready-frame
  support on `/health` (agent-dispatch has no existing protocol-version
  module like agent-bridge's `protocol.py` — a minimal, purpose-built
  capability signal was the right scope here, not importing that machinery);
  the client only waits for the frame when the daemon advertises it, and
  even then with a bounded timeout, never an indefinite one. The frame
  itself is explicitly a control frame the relay filters out before it
  reaches row/diff logic, never exposed as a task event to an unrelated
  `watch` consumer.
- **Previously missed: the debounce no-op was wrong for an event arriving
  mid-fetch.** Coalescing a not-yet-started pending fetch is correct, but
  silently no-op'ing an event that arrives *while* a fetch is already in
  flight can lose it — that fetch may have already read the pre-mutation
  snapshot. Fixed with a trailing-dirty flag: an event during an in-flight
  fetch triggers exactly one more re-fetch immediately after, rather than
  being coalesced away.
- **Previously missed: the dynamic-namespace fix covered add/remove but not
  same-namespace replacement.** `refresh_provider_resolvers()` can
  unregister and re-register a provider under the *same* namespace; keeping
  that namespace's last-known-good cache entry across the swap would let
  the old provider's agents keep serving as authoritative if the new
  resolver's first scan fails. Fixed by requiring a provider-generation
  change (not just a namespace add/remove) to invalidate that namespace's
  cache entry and reset it to uninitialized before the new resolver's first
  scan.
- **Previously missed: 3b's own intro claimed "no new failure mode," which
  the design's own later bullets (and its own Validation Plan) contradict.**
  Fixed by correcting the claim in both the README and the PR description to
  describe what's actually true — no new HTTP-call-shape change for existing
  callers, but real new failure modes in the background refresh itself,
  addressed by the bullets that already follow it rather than claimed away.

All four replied-to inline (one new thread; three on previously-missed
findings in code this design had already touched by this round).

### 2026-10-02 — Phase 3 design PR (#4928) review round 10: the ready-frame filter was scoped to the wrong layer, and a validation gap it itself created
Two findings:
- **Filtering the ready frame only in the relay's own consumer doesn't
  protect every other caller of the same client method.**
  `task_query_cli._cmd_watch()` already iterates `DispatchClient.
  stream_events()` directly and would print the control frame as a task
  event. Fixed by moving the filter into `stream_events()` itself — it
  never yields a control frame to any caller by default — and exposing
  readiness to the relay through a distinct, explicit signal rather than
  relying on each caller to filter independently.
- **The Validation Plan's 3b criterion didn't name the replacement case
  round 9 just added to the Plan.** The design now explicitly treats
  same-namespace provider replacement as distinct from add/remove (stale
  serving risk), but the acceptance test list still only said
  "added/removed." Fixed by naming replacement and its cache-invalidation
  requirement explicitly in that same bullet, alongside a new 3a criterion
  proving the ready frame never leaks to an unrelated existing consumer
  like `agent-dispatch watch`.

Both replied-to inline.

### 2026-10-02 — Phase 3 design PR (#4928) review round 11: a third eventless mutation, retry exhaustion as silent success, and no gate before any namespace exists at all
One new high-severity finding plus two previously-missed:

- **New, high: `GET /api/v1/agents` has no gate at all before provider
  discovery has ever completed once.** The per-namespace uninitialized
  state only protects a namespace the cache already knows about; production
  startup serves a placeholder resolver with an **empty** namespace set
  while `topology_ready` is false, and the route reads it with no readiness
  check — a subscriber in that window gets a clean, authoritative-looking
  empty roster, since there's nothing to mark incomplete when there are no
  namespaces yet. Fixed by a **global** discovery-readiness flag, checked
  before any per-namespace state, so the whole response reports incomplete
  during that window rather than an empty one reporting complete.
- **Previously missed: `POST /tasks/{id}/steer/take` is a third silent
  mutation**, alongside activity/heartbeat/recover — it updates
  `lease_expires_at`/`last_seen_at`/`updated_at` (board-sort-relevant) with
  no event. Folded into the same requirement, and — since this is now the
  *third* round an enumerated "complete" list of eventless mutations turned
  out not to be — reframed the whole requirement as explicitly provisional:
  require an audit of every `queue`-mutating route in both touched files,
  not trust in the specific names listed.
- **Previously missed: forced-rescan exhaustion can still silently become a
  successful publish.** `_fetch_complete_initial_rows()` returns whatever
  rows it has after its bounded retries regardless of outcome, with no
  incomplete marker on the envelope at all — if every forced rescan
  genuinely fails and there's no last-known-good, this design would still
  publish that partial roster as the initial snapshot, exactly what the
  uninitialized-state bullet exists to prevent. Required the exhausted-retry
  path to either keep retrying under a different contract or carry an
  explicit partial-snapshot marker, rather than defaulting to silent
  success.

All three replied-to inline (one new thread; two on previously-missed
findings).

### 2026-10-02 — Phase 3 design PR (#4928) review round 12: discovery failure itself needs the same treatment as never-discovered, retry exhaustion gets a committed contract, and two carried-over gaps
One new high finding, a resolved-then-reopened one, a medium, a low, plus two previously-missed:

- **New, high: `refresh_provider_resolvers()` swallowing its own failures
  means ongoing discovery failure looks identical to success.** It already
  swallows registry-scan and resolver-construction failures and returns
  `None` on failure. After one successful discovery, a *provider-discovery*
  failure (not an individual namespace's agent-scan failure) could leave
  the cache refreshing the same old resolver set forever, reporting
  complete even while removal/replacement discovery keeps failing. Fixed
  by tracking discovery itself as its own generation/freshness state: a
  failed discovery attempt never advances it, and that generation going
  stale marks the *whole* cache incomplete — the same global treatment the
  round-11 pre-first-discovery fix already uses, since ongoing failure
  isn't meaningfully different from never having discovered at all.
- **Resurfaced: the global discovery-readiness gate from round 11, now
  folded into the discovery-generation fix above** rather than treated as
  two separate mechanisms — a single generation/freshness concept covers
  both "never discovered yet" and "discovery is failing now."
- **Medium: the retry-exhaustion contract was left as an open either/or.**
  Committed to one: **blocking, not a new partial-snapshot envelope** — a
  Picker-visible partial marker would need Picker-side changes, which
  contradicts this effort's own CLI-only vision boundary. The initial
  fetch now withholds `begin` until discovery succeeds or a bounded,
  generous ceiling elapses, then falls back to the existing topology-error
  `error` frame convention — never silent success, never an unbounded
  hang.
- **Low: the 3b Validation Plan gate didn't name the newest failure modes.**
  Added explicit acceptance criteria for pre-discovery startup, retry
  exhaustion, and discovery-failure-after-success, alongside a parallel
  addition of missing 3a concurrency regression tests (the ready-frame
  handshake actually closing the gap, an in-flight-fetch event actually
  producing one trailing fetch, and writer serialization actually
  preventing a stale overwrite) that a coordinator-kill test alone never
  exercised.
- **Previously missed: the trailing-dirty fetch had no rate limit of its
  own.** Sustained per-task event traffic could keep the dirty flag
  continuously set, turning the relay into back-to-back full fetches worse
  than the 2s poll this phase replaces. Fixed by subjecting the trailing
  fetch to the same minimum-interval floor as the debounce window.

All four discussion threads replied-to inline; the two carried-over
findings addressed via the same content fixes described above.

### 2026-10-02 — Phase 3 design PR (#4928) review round 13: reverse-skew needed a server-side fix, the design moved to a sibling doc, and two more carried-over gaps
Three new findings plus three previously-missed:

- **Medium, new: the retry-exhaustion fix from round 12 only protected a
  new client.** A new client's longer blocking ceiling lives in its own
  updated code; an already-shipped old CLI has its own fixed three-retry
  loop baked in and will never see that ceiling. A client-side fix cannot
  retroactively protect an already-deployed old client. Fixed by making the
  contract **server-side and version-independent**: when nothing
  authoritative exists to serve, `GET /api/v1/agents` responds `503`
  instead of a normal `200` — every caller's existing non-2xx error
  handling already has to react to this, old or new, since it's not a new
  failure shape to learn, just an existing one applied to a case that
  previously returned a false-positive `200`.
- **Low, new: "the whole response reports incomplete" wasn't a defined wire
  contract.** The `503` fix above resolves this directly: "globally
  incomplete" is now carried at the **HTTP status level**, not as a new
  field or sentinel inside `incomplete_namespaces` — that field's existing
  shape stays exactly what it already is, so this needs no protocol-version
  bump and no new response schema.
- **Low, new: this change had grown a substantial phase design directly
  into the shared effort README**, against `efforts/README.md`'s own
  "extract substantial phase designs into sibling documents" convention.
  Fixed by moving the full 3a/3b/3c detail into a new sibling document,
  [`phase-3-design.md`](phase-3-design.md), keeping only a concise
  checklist and link here. The Journal (this section) stays in the shared
  README, since it's chronological review history, not phase-design detail.
- **Previously missed: the old-daemon fallback for the ready-frame
  handshake didn't actually narrow anything.** `stream_events()` is a lazy
  generator — calling it doesn't open the HTTP request until iteration
  begins, so reconciling right after it "returns" happens before any
  subscription attempt exists at all. Fixed by falling back to the
  **unmodified Phase 1 poll-and-diff loop in its entirety** for an old
  daemon, rather than claiming a narrowed (but actually unnarrowed) race.
- **Previously missed: a third pair of eventless mutation paths** —
  `POST /tasks/{id}/run-waiter/register` and the `verify-submitted` opt-in
  in `coordinator_verification.py`, neither publishing despite updating
  `updated_at`. Folded into the same provisional audit requirement,
  expanded to name all three touched files explicitly.
- **Previously missed: the O(1) cache-read claim was stated as a blanket
  property**, contradicting the design's own later contract that forced/
  incomplete/uninitialized reads intentionally block on a real scan. Fixed
  by scoping the claim explicitly to a healthy, fresh cache hit.

All findings addressed via the content fixes above (now partly in
`phase-3-design.md`); the three new discussion threads replied-to inline.

### 2026-10-02 — Phase 3 design PR (#4928) review round 14: the 503 fix needed widening in two directions, ready-frame skew cut both ways, and the sibling doc inherited the timeless-prose problem it was meant to avoid
Five findings, all in `phase-3-design.md` now that the design lives there:

- **High: the fallback poller is a fourth snapshot writer that wasn't in
  the serialization lock, and the reconnect handoff could race it.** It can
  read stale state and publish it *after* a reconnect's reconcile already
  published newer state unless it shares the same lock and is explicitly
  quiesced before reconnect promotion runs, not just left running alongside
  it. Fixed by adding the fallback poller to the shared snapshot-owner lock
  and requiring its current tick to finish (with no next tick scheduled)
  before the reconnect's promotion reconcile runs.
- **Medium: the ready-frame fix only handled new-client-vs-old-daemon, not
  the reverse.** A new daemon would still emit the ready frame to an
  already-installed **old** `DispatchClient.stream_events()`, which parses
  purely by `data:` prefix regardless of `event:` field — any SSE framing
  trick an old parser might "structurally ignore" doesn't actually apply
  here, so the control frame would leak straight into `agent-dispatch
  watch` output. Fixed by making the frame **opt-in via the request itself**
  (e.g. a query parameter only a new relay sends): an old client never asks
  for it and therefore never receives it from any daemon, regardless of
  daemon version — version skew resolved by what's requested, not by what
  the daemon happens to be running.
- **Medium: the 503 condition was narrower than the mixed-cache case.** A
  namespace with last-known-good data alongside a *different*,
  newly-added-or-replaced namespace still uninitialized after its own
  joined scan fails would still return `200` under the prior wording.
  Fixed by triggering `503` whenever **any** known namespace lacks an
  authoritative value after a refresh attempt, not only the all-or-nothing
  case.
- **Medium: discovery-generation expiry needs to force `503`
  independently**, not only as a fallback when namespace-level
  last-known-good is also absent — a stale discovery generation means
  additions/removals/replacements are unknown, and `incomplete_namespaces`
  can't name a namespace that was never discovered, so even "existing
  namespaces still look fine" isn't enough to call the roster complete.
  Fixed by making discovery-generation expiry its own independent `503`
  trigger.
- **Low: the newly-extracted sibling doc immediately re-accumulated the
  same PR/review-round chronology problem the extraction was meant to
  solve.** Per `CONTRIBUTING.md`'s timeless-prose rule, that history
  belongs only in this dated Journal. Fixed by rewriting
  `phase-3-design.md`'s introduction to describe the design's current
  state without "revised per review on #N" framing, at every location the
  finding named.

All five replied-to inline.

### 2026-10-02 — Phase 3 design PR (#4928) review round 15: validation-plan drift from the design doc, and a discovery-success signal that doesn't exist yet
Three low-severity findings, all about keeping the Validation Plan and the
design doc's own internals honest with each other:

- **The Validation Plan didn't name the fallback-poller/reconnect handoff
  test** round 14's fix actually requires — sharing a lock doesn't prove a
  quiescence *ordering* is enforced without a dedicated deterministic test.
  Added explicitly.
- **The Validation Plan's 3b wording still conditioned the
  discovery-generation `503` on "no last-known-good survives,"
  contradicting round 14's own fix** in `phase-3-design.md`, which made
  discovery-generation expiry an *independent* trigger. Reworded to match.
- **`refresh_provider_resolvers()` has no observable success/failure signal
  for "failed discovery must not advance the generation" to hook into at
  all** — it returns `-> None` unconditionally on both its success path and
  its `except Exception: return` failure path, and a per-manifest
  resolver-construction exception inside the reconciliation loop is caught
  and continued past silently, so even a "successful" scan can have quietly
  dropped a namespace. Fixed by requiring the method itself to return an
  explicit discovery-result distinguishing a raised scan, a completed scan
  with one or more per-manifest construction failures, and a genuinely
  clean pass — only the clean-pass case advances the generation.

All three replied-to inline.

### 2026-10-02 — Phase 3 design PR (#4928) review round 16: a cutover gap 3b itself would introduce, a doc-generation duplication bug, and a stale Guiding Intent
Three findings — one genuine new design gap, two documentation-quality bugs
in this session's own output:

- **Medium: 3b's background cache has no defined readiness/cutover seam.**
  Today's daemon-generation promotion gate marks a new generation ready once
  its resolvers are *constructed*, not once its cache has completed a first
  authoritative scan — those stop being the same moment once the scan moves
  to a background task. A zero-downtime cutover could therefore promote a
  generation whose cache is still fully uninitialized right as the old,
  warm-cache generation retires — a brand-new `503` blip this design itself
  would introduce, not a pre-existing one. Fixed by requiring promotion
  readiness to gate on cache warm-up (or an equivalent cache-state transfer
  from the retiring generation), plus a dedicated Validation Plan cutover
  test.
- **Low: the Validation Plan's Phase 4 entry had been accidentally
  duplicated with a truncated first copy** (an artifact of this session's
  own earlier programmatic edit extracting the Phase 3 detail into the
  sibling doc). Collapsed into the single complete item.
- **Low: the Guiding Intent section still described the superseded
  "direct HTTP/SSE/WebSocket to the daemon" hierarchy**, left over from
  before Phase 3's own design review settled on the CLI-owned relay shape —
  two incompatible guiding intents in the same effort. Updated to describe
  the CLI-owned daemon relay this design actually implements, with a
  pointer to `phase-3-design.md` for the full rationale.

All three replied-to inline.
