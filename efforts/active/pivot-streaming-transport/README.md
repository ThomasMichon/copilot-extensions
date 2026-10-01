# Pivot Streaming Transport & Render Performance

- **Slug:** `pivot-streaming-transport`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `dev`
- **Created:** 2026-09-30
- **Status:** Draft <!-- Draft | Active | Blocked | Done -->
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
- [ ] Define an explicit contract: a `subscribe` pivot's stream process exiting
      (EOF on stdout) must be treated as the channel dropping, not as
      "finished successfully." On EOF, either (a) reconnect by re-invoking the
      `list --stream` command after a short backoff, keeping the last-known
      rows visible in the interim (never blank the pivot on a transient drop),
      or (b) fall back to repolling via the plain one-shot path if reconnect
      attempts are exhausted.
- [ ] Add regression coverage: a `subscribe` pivot whose process exits
      mid-session must resume updating (via reconnect or fallback), never
      silently freeze on stale rows forever.
- [ ] This is a `worktree-manager`-owned fix (the consuming runtime), landed
      and verified **before** Phase 1 flips any real plugin's manifest to
      `subscribe: true` — flipping the flag today would durably freeze that
      pivot the first time its CLI process exits for any reason.

### Phase 1 — Adopt `stream`/`subscribe` for agent-dispatch's pivot
_(agent-recommended ordering: lowest-risk, highest-signal first adopter —
agent-dispatch's CLI already emits SSE-sourced JSON lines for `watch`, so this
is closest to a manifest-only change. Depends on Phase 0.)_
- [ ] Confirm `agent-dispatch-board --machine {machine}` (the pivot's current
      `list` command) can emit the `stream`/`subscribe` NDJSON envelope shape
      `tasks.py` expects (`begin`/`row`/`delta`/`removed`/`summary`/`done`), or
      scope the CLI-side change needed to produce it from the daemon's
      existing `/events` SSE stream.
- [ ] Flip `plugins/agent-dispatch/pivots/agent-dispatch.json` to
      `"stream": true, "subscribe": true` once the CLI side is ready.
- [ ] Verify live: Picker's Tasks pivot reflects a task-state change without a
      poll-interval delay, and degrades cleanly when `agent-dispatch` is
      absent/stale (the existing one-shot fallback), and recovers per Phase 0's
      contract if the stream process exits mid-session.

### Phase 2 — Same for agent-bridge's pivot
- [ ] Same shape as Phase 1 against `agent-bridge --json agents` / agent-bridge's
      own SSE delivery loop.
- [ ] Flip `plugins/agent-bridge/pivots/agent-bridge.json`.
- [ ] Verify live.

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
- [ ] For agent-dispatch and agent-bridge (Phases 1-2 already proved the NDJSON
      shape against their CLIs): change each plugin's own `--stream`
      implementation to detect its daemon is live (the same discovery each
      plugin's CLI already uses for its own non-Picker commands — e.g.
      `~/.agent-bridge/active.json`) and relay the daemon's live feed through
      stdout instead of a cold poll-and-diff loop, while keeping the exact
      same NDJSON envelope shape the Picker already consumes.
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
- [ ] **Phase 3 (design review gate):** each plugin's own daemon-relay change
      must be reviewed before landing — it introduces a new internal failure
      mode (the CLI's own daemon connection drops or misbehaves) that its
      existing poll-and-diff path doesn't have. Validate that CLI-internal
      degradation (daemon unreachable → existing poll-and-diff `--stream`
      behavior) actually happens — kill the daemon mid-render and confirm the
      Picker keeps getting data, just via the slower internal path, never a
      frozen or crashed pivot.
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
