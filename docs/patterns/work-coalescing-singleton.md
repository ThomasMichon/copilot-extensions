# Pattern: work-coalescing singleton (fold many callers onto one warm daemon)

> **Serves the vision:** `visions/plugin-services` §`work-coalescing-singleton`,
> §`graceful-composition`, §`process-count-scales-with-services-not-sessions`,
> §`hooks-and-callbacks-are-transient`.
> **Embodied by:** `agent-worktrees`' resident status-monitor + `hook_ipc.py` +
> `list_cache.py` (the reference implementation this doc generalizes).
> **Building on:** the `single-instance-lease` guard (one live daemon per
> installation cell), the `zdd` zero-downtime cutover (updating the daemon
> without dropping subscribers), rendezvous discovery (finding the live
> endpoint without spawning to check).
> **Origin:** `plugin-process-hygiene` effort (#736), issue #744 — the
> cross-cutting design named there before any further plugin code change.

## The problem

Several plugins independently face the identical shape: **many short-lived
callers each recompute or re-request the same cheap, idempotent, shareable
work**, one full-price execution per caller, with no way for one caller's
answer to help the next:

- `agent-worktrees` — every `list --json --classify` invocation (Picker
  polling, a hook, a plain CLI call) independently re-runs `scan_sessions_fast`,
  git classification, mux queries, and the bare-orphan scan.
- `agent-mcp` — today's optional serve tier spawns **one heavy stdio bridge
  process per session per server**; a host with several sessions sharing one
  MCP server config pays that cost once per session instead of once per
  server (dozens of processes observed for a couple of shared servers — the
  dominant process-count/memory multiplier named in #744).

Both are the same caller shape from `hooks-and-callbacks-are-transient`: a
repeated, on-demand caller of a cheap, idempotent answer should reach a
resident singleton as a thin, ref-counted subscriber — booting it on demand
if none is reachable — rather than compute (or run a heavyweight process)
independently every time. This pattern names that reusable shape once, so it
is built as a shared helper instead of re-invented per plugin.

## The shape (non-negotiable invariants)

1. **Coalesce, don't fan out.** Identical requests arriving close together
   collapse onto one in-flight execution (or one shared warm process); the
   daemon never spawns a second worker for the same key while one is live.
2. **Distinct work is queued, never unbounded-forked.** Side-effecting or
   per-identity work (a distinct MCP server session, a distinct classify key)
   gets bounded concurrency and backpressure, not one thread/process per
   caller.
3. **Refcounted, idle-exit.** The daemon exists only while at least one
   subscriber needs it. Active subscriber count is an **explicit** signal
   (register on connect, release on disconnect/exit), not inferred from a
   fixed timer alone; the daemon lingers a bounded grace period after the
   last release, then exits. It is **warmth, not truth** — losing it costs
   only recomputation, never data.
4. **Always optional, with a correct inline fallback.** A lone caller, or any
   caller that cannot reach the daemon within its own bounded wait, computes
   the answer itself (or runs the direct-bridge path) — the exact behavior
   this pattern must never regress under any timeout or crash.
5. **Only across callers that share identity and credentials.** The daemon
   consolidates the **warm runtime and shared upstream connection**, never a
   caller's private state; agent-mcp must never pool a server instance
   across two callers with different credentials for that server.
6. **Guarded and discovered by the existing primitives**, not new ones: the
   `single-instance-lease` bounds the daemon to one live instance per
   installation cell; `zdd` (or the daemon's own generation token) makes an
   update to the daemon itself a graceful cutover, not a stop-the-world
   restart; rendezvous (a published endpoint file, liveness-checked) is how a
   client finds it — never by spawning one "to make sure."

## The wire protocol (generalizing `hook_ipc.py`)

`agent-worktrees`' `HookIpcServer`/`_Handler` already implements the core
shape for one call kind (`session-lifecycle-v1`): a dynamic-port, loopback-only,
token-authed `ThreadingTCPServer`, one JSON request-per-line in, one JSON
response-per-line out, a server-side deadline the client sets per request. The
reusable helper generalizes it to any request `kind`, not just hook decisions,
and adds the ref-counted subscriber lifecycle `hook_ipc.py` does not yet need:

```
Client                                   Daemon
  │  resolve rendezvous file             │
  │  (installation-cell scoped)          │
  ├── found + live? ──────────────────►  │  (skip boot)
  │                                       │
  ├── not found / stale ──► boot on demand, with a
  │                          single-instance-lease guarding against a
  │                          concurrent second boot; wait (bounded) for the
  │                          daemon to publish its rendezvous endpoint
  │                                       │
  ├── SUBSCRIBE {version, token, client_id} ──────►
  │                                       │  refcount += 1; start/refresh
  │                                       │  this client's linger timer
  │◄───────── {version, ok:true} ─────────┤
  │                                       │
  ├── REQUEST {kind, key, payload,        │
  │            deadline} ─────────────►  │  coalesce on (kind, key): join an
  │                                       │  in-flight execution for the same
  │                                       │  key, or start one and let joiners
  │                                       │  share its result
  │◄── {result} | {fallback:true} ────────┤  (deadline exceeded ⇒ fallback,
  │                                       │   never block past it)
  │                                       │
  ├── (socket close / process exit) ─────►│  refcount -= 1 (also reaped by a
  │                                       │  liveness check, not only an
  │                                       │  explicit RELEASE, so a crashed
  │                                       │  client can't pin the daemon up
  │                                       │  forever)
  │                                       │
  │                                       │  refcount == 0 ⇒ start bounded
  │                                       │  linger; still 0 at expiry ⇒
  │                                       │  idle-exit, clear rendezvous
```

Request/response envelopes stay the existing `hook_ipc.py` shape
(`{"version": 1, "token": ..., "kind": ..., "payload": ..., "deadline": ...}`
in; `{"version": 1, "result": ...}` or `{"version": 1, "fallback": true}` out)
so the daemon can serve both the existing hook-decision kind and new
coalescing kinds over one listener. `SUBSCRIBE`/implicit-release is additive:
a caller that never subscribes (today's one-shot hook decision) behaves
exactly as it does today — refcounting is opt-in per request kind, not a
breaking change to the existing protocol.

## Timeout budgets (two very different cost profiles, one shape)

`hook_ipc._READ_TIMEOUT_S = 1.0` is calibrated for a hot-path hook decision.
The reusable helper must **not** hard-code one budget — a cold classify pass
or a cold MCP server boot is seconds, not ~1s, and forcing a single fixed
timeout would either make the hot path sluggish or make a cold caller give up
too early. Each consumer's client declares its own two-phase budget:

| Phase | What it bounds | Reference value |
|---|---|---|
| **Boot-wait** | Time a first caller (no daemon reachable) waits for a freshly-spawned daemon to publish its rendezvous endpoint and accept a subscribe. | Resident classify daemon: several seconds (cold git classification). MCP multiplexer: bounded by the slowest server's own cold-start (server-declared, or a conservative shared default) — never open-ended. |
| **Request-deadline** | Time a single in-flight request may take before the client gives up on the daemon and takes the fallback path. Carried per-request (existing `deadline` field), so a slow individual request degrades that one caller, not every subscriber. | Hot hook decision: ~1s (unchanged). Classify/list: a few seconds. MCP request: the server's own call semantics — the multiplexer forwards, it does not impose a new ceiling beyond what a direct (unpooled) call would already have. |

A caller whose boot-wait **or** request-deadline expires falls back to its
existing direct/inline path (live computation, or the existing one-bridge-
per-session behavior) — full correctness, not degraded correctness, per
invariant 4. This must hold as a property, not merely a start-of-life
edge case: a first caller after an idle-exit is never worse off than before
this tier existed.

## Ref-count / linger algorithm

- **Register** on `SUBSCRIBE` (or first successful `REQUEST` for a
  fire-and-forget caller that skips explicit subscribe) — one entry per
  `client_id`, storing last-seen time.
- **Release** two ways, both required (neither alone is reliable):
  - **Explicit**: the client's clean shutdown (socket close after a
    `RELEASE`, or plain disconnect) drops its entry immediately.
  - **Liveness-reaped**: a subscriber whose connection silently dies (crash,
    kill -9) is dropped once its entry exceeds a bounded staleness window,
    the same liveness-reconciliation discipline the `single-instance-lease`
    already applies to daemon ownership — never a subscriber pinning the
    daemon alive forever because it vanished uncleanly.
- **Linger**: refcount reaching zero starts a bounded grace timer (not an
  immediate exit) so a rapid unsubscribe/resubscribe churn (e.g. consecutive
  hook invocations a few hundred ms apart) does not thrash the daemon up and
  down. Any new subscribe before the timer fires cancels it.
- **Idle-exit**: the daemon exits only when the linger timer fires with
  refcount still zero; it clears its own rendezvous file first so no client
  can discover a stale endpoint mid-shutdown (same discipline `zdd`'s
  cutover already uses for its routing table).

## Applying the shape: the two named consumers

- **#2323 — agent-worktrees resident accelerator (classify/list).** Extends
  `hook_ipc.py`'s existing listener with a new coalescing request kind, and
  makes `list_cache.py`'s demand-inferred idle-exit explicit per this
  document's ref-count/linger algorithm instead of the current fixed-TTL
  heuristic. The Phase 4c single-instance-lease around `_classify_records`
  remains the fallback path for "no daemon reachable," not superseded.
- **#744 (this issue) — agent-mcp multiplexer.** A per-`(host, server)`
  daemon holding one warm runtime + one shared upstream connection; each
  session's stdio process becomes a thin shim that subscribes and forwards.
  **Strictly gated by identity/credential equivalence** — the multiplexer key
  is `(server identity, resolved credentials)`, never just `(host, server
  name)`; two sessions with different credentials for the same named server
  never share a runtime. A session that cannot reach the multiplexer (or
  whose credentials don't match any pooled instance) falls back to today's
  direct, per-session bridge — unconditionally correct, per invariant 4.

## Validation

Extend the adversarial mock harness already used for Phase 4b(ii)/#2323
(`tools/clean-room/scenarios/plugin-process-hygiene-convergence/`) with
scenarios that hold regardless of which consumer sits on top of the shared
helper:

- **cold-boot-and-wait-for-port** — first caller boots the daemon and
  receives a correct answer within its boot-wait budget.
- **concurrent-callers-during-cold-boot** — N simultaneous first callers:
  exactly one boots, the rest wait, all receive the correct answer (no
  duplicate boot, no caller silently dropped).
- **ref-counted-exit-after-linger** — the daemon lingers until the last
  subscriber's grace period elapses, then exits and clears rendezvous.
- **fallback-to-direct-computation-on-boot-timeout** — a boot-wait or
  request-deadline expiry produces the correct inline/direct-bridge answer,
  not an error or a hang.
- **credential-mismatch-never-pools** (agent-mcp-specific) — two callers for
  the same named server with different resolved credentials each get their
  own runtime; a coalescing bug that would pool them is a hard test failure.

Each scenario asserts **zero leftover processes** (daemon or transient
client) after the run, the same census discipline the existing mock harness
already applies.

## Sequencing

This document was the design-first step the effort's own convention
requires before further plugin code change. **Landed:** the reusable helper
— [`libs/work-coalescing-singleton/`](../../libs/work-coalescing-singleton/README.md),
a pure-stdlib library implementing this wire protocol, the coalescing map,
and the ref-count/linger/liveness-reap algorithm described above (unit +
wire tests covering coalescing, distinct-key independence, error
propagation, linger cancel-on-resubscribe, and liveness reaping). It is not
yet vendored into any consuming plugin. **Still open:** `#2323` (agent-
worktrees resident classify/list accelerator) and the agent-mcp multiplexer
each adopt it — vendoring their own copy per
`tools/check-vendored-libs-sync.py` convention — as independent, separately
reviewed follow-up PRs.
