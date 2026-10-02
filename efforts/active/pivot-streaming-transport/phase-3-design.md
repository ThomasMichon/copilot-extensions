# Phase 3 — CLI-relayed daemon fast path (detailed design)

Extracted from [`README.md`](README.md)'s Plan per `efforts/README.md`'s
"extract substantial phase designs into sibling documents" convention — the
Phase 3 design review (PR #4928) accumulated enough detail across its review
rounds that it no longer belonged inline in the shared coordination document.
The README keeps a concise checklist and links here; this file is the
authoritative detail for 3a/3b/3c. Review-round history for this design
remains in the README's own dated Journal, not duplicated here.

## Phase 3 — CLI-relayed daemon fast path (the new capability, revised)
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
  - [ ] **Debounce the wake, but never drop an event that arrives mid-fetch
        as a no-op — and keep the trailing fetch itself rate-limited:**
        a burst of events (several task mutations in quick succession) must
        coalesce into a single pending re-fetch, not one re-fetch per event
        — if a fetch hasn't started yet and is merely scheduled, a newly
        arriving event coalesces into that same pending fetch. But an event
        arriving **while a fetch is already in flight** cannot be a plain
        no-op: that fetch may already have read the pre-mutation snapshot,
        so silently dropping the new event would leave the mutation
        invisible until the 30-60s safety reconcile. Fixed by a
        trailing-dirty flag: any event arriving during an in-flight fetch
        sets a flag that triggers one more re-fetch after the current one
        completes. **That trailing fetch is itself subject to the same
        minimum-interval floor as the debounce window** (not "immediately,
        unconditionally") — sustained per-task traffic (e.g. activity or
        heartbeat events firing for every live task in quick succession)
        would otherwise keep the dirty flag continuously set and turn the
        relay into back-to-back full-board fetches, a worse load than the
        fixed 2s poll this phase exists to reduce. The dirty bit persists
        across a throttled wait; it never causes an update to be dropped,
        only delayed to the next allowed tick.
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
        registration (before yielding any real event), with its own
        version-skew handling (agent-dispatch has no existing
        protocol-version module like agent-bridge's `protocol.py` — this
        introduces a minimal, purpose-built capability signal rather than
        importing that machinery): the coordinator advertises support for
        the ready frame on `/health` (the same place it already advertises
        other capabilities); the client checks that advertisement before
        relying on the frame at all. Against a daemon that advertises
        support, wait for the ready frame (bounded — if it doesn't arrive
        within a short timeout despite being advertised, treat that as a
        stream failure and fall back to polling, never wait forever).
        Against a daemon that doesn't advertise it, there is **no observable
        subscription barrier at all to reconcile against** — `stream_events()`
        is a lazy generator (`client.py:1103`): calling it does not open the
        HTTP request until iteration begins, so "the call returns" proves
        nothing was even attempted yet, and reconciling at that point would
        not narrow the startup gap, it would just run before any
        subscription attempt exists. Don't try to narrow the gap at all in
        this case: fall back to the **unmodified Phase 1 poll-and-diff
        loop, in its entirety**, for that connection's whole lifetime — the
        same degraded path a genuine stream failure uses elsewhere in this
        design, not a half-optimized relay with an unclosed race. **The filtering must live in `stream_events()`
        itself, not only in the relay's own consumer:** `task_query_cli.
        _cmd_watch()` already iterates the same `DispatchClient.
        stream_events()` API, which today yields every SSE `data:` frame —
        a relay-side-only filter would still leak the ready frame to
        `agent-dispatch watch` and any other existing consumer of that
        method. `stream_events()` therefore filters the ready (and any
        other future control) frame out by default for every caller;
        readiness is exposed to the relay through a distinct, explicit
        signal (e.g. a dedicated parameter/mode on `stream_events()`, or a
        thin wrapper around it) rather than every caller independently
        having to know to filter it. Once past that gate, the
        client: (1) waits for the ready frame (when the daemon supports
        it), (2) buffers any events
        received from that point on (count only — these are wake signals,
        not row payloads, consistent with the wake-only model below; there
        is no per-event state to "apply"), (3) run the immediate reconcile
        pass, then (4) if any event was buffered during that window,
        immediately schedule one coalesced full re-fetch afterward (the
        same debounced wake path ordinary events already use) rather than
        discarding them or trying to apply them as row-level deltas — so a
        mutation landing in the handoff window still surfaces promptly
        instead of waiting for the next long reconcile.
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
        **the manual recovery entry points** — `POST /recover`
        (`coordinator_tasks.py:888-891`) and MCP `dispatch_recover`
        (`mcp_http.py:801-805`) both call `queue.reconcile_liveness()`
        directly with no event published at all, even though that call can
        requeue, suspend, or dead-letter rows — **and `POST /tasks/{id}/
        steer/take`** (`coordinator_tasks.py:870-881`), which calls
        `queue.take_steer()` (`queue_steering.py:358-373`) updating
        `lease_expires_at`/`last_seen_at`/`updated_at` — all board-sort- and
        liveness-relevant (`_build()` sorts by `updated_at`,
        `board_cli.py:329-332`) — with no event either, **and the
        `coordinator_verification.py` routes** — `POST /tasks/{id}/
        run-waiter/register` (`prepare_run_waiter()`, which can move a task
        from `started` to `suspended` and updates `updated_at`,
        `queue_run_waiters.py:63-70`) and the `verify-submitted` opt-in
        (also updates `updated_at`) — neither publishes either. Add an
        event — any `event_type`, e.g. `task.activity_updated`/
        `task.heartbeat`/`task.recovered`/`task.steer_taken`/
        `task.run_waiter_registered` — to each of these specific call
        sites; **treat this list itself as provisional, not closed** — it
        has already grown across three separate review rounds — require the
        implementer to audit every `queue`-mutating route across
        `coordinator_tasks.py`, `mcp_http.py`, **and
        `coordinator_verification.py`** for a missing `event_type` before
        considering 3a's event coverage complete, rather than trusting any
        enumerated list, including this one, to be exhaustive.
  - [ ] **Time-derived fields change with no mutation and no event at all,
        and need their own refresh path, not a daemon round-trip — and this
        covers more than the task's own timestamps:** `_build()` expires
        `activity` after `ACTIVITY_TTL_SECONDS`, advances the "stalled Nm"
        text purely from `time.time()`, removes a terminal row entirely once
        `completed_at`/`updated_at` crosses the `--recent-mins` cutoff
        (`board_cli.py:388-407`), **and** calls `board_fields_for_task()`
        (`board_cli.py:470-480`), whose own relay-derived freshness/age
        fields clear `artifacts_summary`/`length_display` once the relay
        entry itself goes stale (`worktree_status_relay.py:190-202,
        307-345`) — a fourth clock-only transition, not merely a
        consequence of the first three. None of these is triggered by any
        task mutation, so neither the event relay above nor a 30-60s
        reconcile is a sufficient refresh cadence for any of them (the
        current 2s poll happens to mask this today only because it's
        frequent enough to feel live). Fixed by a **separate, local,
        no-network recompute tick** on a short cadence (comparable to
        today's 2s, e.g. 1-2s) that recalculates all four clock-only
        transitions — not just the task-timestamp-derived ones — from
        already-cached state: this requires retaining the **raw relay
        entry** alongside each cached row internally (the rendered row
        alone doesn't carry enough to recompute the relay-staleness
        transition), emitting a `delta` for a row whose displayed text
        changed purely from clock advancement, and a `removed` for a row
        that has now aged out of `--recent-mins`, purely locally, zero
        daemon load either way.
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
- [ ] **3b — agent-bridge daemon-side cache (land first; smaller than the
      agent-dispatch relay, no new HTTP-call-shape change, though it does
      introduce its own new failure modes around the background refresh
      itself — see the bullets below, not "no new failure mode" at all):**
      `AgentResolver`'s per-call resolver scan is the actual
      cost (Phase 2's `incomplete_namespaces` work was about tolerating its
      partial-failure shape, not removing the cost). Move that scan **into
      the daemon**, on its own background refresh timer, maintaining an
      in-memory roster cache; `GET /api/v1/agents` becomes a cheap O(1)
      cache read **for a healthy, fresh cache hit** (this claim does not
      extend to the recovery paths below — a forced initial retry, or any
      `GET` observing incomplete/uninitialized/stale state, intentionally
      blocks on a real resolver scan by design, not a regression to treat
      as a missed optimization) for every caller (CLI `--subscribe` tick
      included) instead of a fresh multi-resolver scan per poll, with N
      concurrent Pickers now sharing one scan instead of paying for N. The
      CLI's `--subscribe` loop keeps its current shape (poll on
      `--interval`, diff, emit) — only what each tick costs changes.
  - [ ] **Preserve the initial-scan recovery contract — including what
        happens if every forced rescan still fails:**
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
        **Retry exhaustion resolves to one explicit, server-side contract —
        and it cannot depend on the calling CLI's own version:**
        `_fetch_complete_initial_rows()` today returns whatever `rows` it
        has after its bounded retries regardless of outcome, and the
        `begin`/`row`/`done` envelope it emits carries no incomplete
        marker at all — if every forced rescan still fails and no
        last-known-good exists for a namespace, this design would still
        publish that partial roster as the initial snapshot, exactly what
        the uninitialized-state bullet below exists to prevent. A
        client-side fix alone cannot close this for every caller: an
        **already-shipped old CLI** has its own fixed retry loop (three
        plain `GET`s, 0.5s apart, `inventory_cli.py:250-260`) baked into
        code that will never see this design's longer blocking ceiling,
        since that ceiling lives in the *new* client's own
        `_fetch_complete_initial_rows()` — a new client's longer wait
        cannot retroactively protect an old one. The fix therefore has to
        be **server-side and version-independent**: when the daemon has
        genuinely nothing authoritative to serve (no namespace has ever
        completed a scan, or every forced rescan has failed with no
        last-known-good anywhere), `GET /api/v1/agents` responds with a
        **non-2xx status** (e.g. `503`) instead of a normal `200` body —
        every caller's existing HTTP-error handling already has to react to
        a non-2xx response somehow (old or new, since this is not a new
        failure shape a client has to learn about, just an existing one
        applied to a case that previously returned a false-positive `200`).
        This also **directly defines the wire contract the global
        discovery-readiness gate needs** (see the uninitialized-state
        bullet below): "globally incomplete" is carried at the **HTTP
        status level** (`503`), not as a new field or sentinel inside
        `incomplete_namespaces` — that field's existing shape and meaning
        (a list of specific, currently-known incomplete namespace keys)
        stays exactly what it already is, unchanged and untouched, so this
        needs no protocol-version bump and no new response schema either;
        a `200` response's `incomplete_namespaces` only ever names
        namespaces that genuinely exist and are currently incomplete, and
        a `503` is what covers "there is nothing to name yet, or nothing
        authoritative survived the retries."
  - [ ] **A stalled or crashed refresh task must not serve a stale roster as
        complete forever, and the recovery can't be merely passive — and
        this applies to the provider-discovery scan itself, not only to an
        individual namespace's agent scan:**
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
        reverse skew. **This same supervised-plus-freshness-deadline
        treatment applies to `refresh_provider_resolvers()` itself, not
        just to a namespace's agent scan:** that call already swallows
        registry-scan and resolver-construction failures internally,
        returning `None` on failure (`agent_registry_resolver.py:159-170,
        245-252`) — after one successful discovery, a *provider-discovery*
        failure (as opposed to an individual namespace's agent-scan
        failure) could silently leave the cache refreshing the same old
        resolver set forever, reporting it complete even while
        provider-registry removal/replacement discovery is failing. Fixed
        by tracking discovery itself as its own generation/freshness state,
        separate from any individual namespace's: a failed discovery
        attempt must not advance that generation, and that generation
        going stale past its own deadline — with no previously-discovered
        namespace retaining a usable last-known-good either — means the
        route responds `503` under the same server-side, version-independent
        contract defined above, rather than continuing to report a stale
        roster as complete.
  - [ ] **The "nothing authoritative to serve" state must exist before
        daemon startup even discovers which namespaces exist at all, not
        only per already-known namespace:** production startup serves a
        placeholder `AgentResolver` with an **empty** namespace set while
        `topology_ready` is still false (`app.py:388-393,471-484`;
        `service_start_cli.py:167-170`), and the `agents` route reads it
        with no readiness gate at all (`routes/agents.py:10-25`). The
        per-namespace uninitialized state below only protects a namespace
        the cache already *knows about* — with zero namespaces registered
        yet, there is nothing to mark incomplete, so a subscriber during
        this window would receive a clean, authoritative-looking **empty**
        roster. Fixed by the same `503` contract defined above: while
        provider discovery itself hasn't completed at least once, the
        route responds `503`, not a `200` with an empty body — the same
        global, version-independent signal as the discovery-generation
        staleness case just above, not a second, separate mechanism.
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
  - [ ] **The namespace set itself is dynamic, not fixed at startup — and a
        same-namespace provider *replacement* is its own case, not covered
        by add/remove alone:** `refresh_provider_resolvers()`
        (`agent_registry_resolver.py:159`) already adds, replaces, and
        unregisters `providers.d`-declared resolvers at runtime on its own
        TTL — the cache's namespace set must track this, not assume the set
        discovered at daemon startup is permanent. The background refresh
        cycle re-runs this same provider scan on its own cadence and
        reconciles cache membership against it: a newly-registered
        namespace enters the cache in the **uninitialized** state above
        (not silently absent until some unrelated trigger populates it),
        and a namespace whose provider was unregistered is retired from
        the cache outright — never left serving its last-known-good agents
        indefinitely as if that provider still existed. **Replacement**
        (the same namespace unregistered and re-registered to a *different*
        resolver, not simply removed) is distinct from either case: keeping
        that namespace's existing last-known-good cache entry across the
        swap would let the *old* provider's agents keep serving as
        authoritative if the *new* resolver's first scan fails. Any
        namespace whose provider generation changes — not just whose
        namespace is added or removed — must have its cache entry
        invalidated (discarding any in-flight scan against the old resolver
        and resetting to **uninitialized**) before the new resolver's first
        scan runs, exactly the same treatment as a brand-new namespace gets.
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

