# agent-remote-driver

> A user-global Copilot CLI SDK extension that gives any `copilot` session
> baseline drivability -- attach to its live event stream, send/steer it, and
> abort its current turn -- from the moment it launches, with no dependency on
> agent-bridge, agent-worktrees, or any other coordination plugin being
> installed in that venue.

This plugin realizes the
[`cli-default-bridging`](https://github.com/ThomasMichon/copilot-extensions/blob/dev/visions/cli-default-bridging/README.md)
vision's **remote-driver extension** concept: agent-bridge's own
`extensions/agent-bridge/` is narrower by design -- it was built for
*reporting on and lightly steering sessions a human already launched*, not
for driving one end-to-end. This plugin is the floor capability underneath
that: "can this session be driven at all," decoupled from any one
coordination layer's daemon or protocol.

## What it does (and how to use it)

Installing and enabling `agent-remote-driver` (see *Install* below) makes
every subsequent `copilot` session on that machine/image write a small
**discovery descriptor** once its session id is known:

```
~/.copilot/remote-driver/sessions/<session-id>.json
```

```json
{
  "version": 1,
  "driverVersion": "0.1.0-dev1",
  "sessionId": "<session-id>",
  "pid": 12345,
  "host": "127.0.0.1",
  "port": 54321,
  "token": "<bearer token>",
  "cwd": "/path/to/checkout",
  "startedAt": "2026-10-03T04:00:00.000Z",
  "updatedAt": "2026-10-03T04:00:30.000Z"
}
```

Any local process (a hand-rolled script, a curl one-liner, or a future
`agent-bridge` consumer) that can read that file can then drive the session
over plain HTTP, loopback-only, bearer-authenticated:

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | `{ok, sessionId, pid}` -- liveness/identity check |
| `/events` | GET | Server-Sent Events stream of every SDK event, from the moment of attach forward |
| `/send` | POST | `{content, mode?}` -- send a message into the session |
| `/steer` | POST | `{content}` -- abort the current turn, then send immediately |
| `/abort` | POST | Abort the current turn |

```bash
descriptor=~/.copilot/remote-driver/sessions/<session-id>.json
base="http://$(jq -r .host $descriptor):$(jq -r .port $descriptor)"
token=$(jq -r .token $descriptor)

curl -s -H "Authorization: Bearer $token" "$base/health"
curl -s -H "Authorization: Bearer $token" -N "$base/events"
curl -s -H "Authorization: Bearer $token" -H 'content-type: application/json' \
  -d '{"content":"what is the current plan?"}' "$base/send"
```

### Install

Enable the plugin wherever it needs to be present at launch:

- **A local machine or Dev Box image:** add it to the **user-global**
  `~/.copilot/settings.json`'s `enabledPlugins` (not a per-repo
  `.github/copilot/settings.json`) -- this is what makes it present
  regardless of which repo/cwd a given `copilot` invocation starts in,
  matching the vision's launch-time-presence requirement.
- **A GitHub CodeSpace:** list it in a `<repo>-harness`-style plugin's
  `codespacePlugins` manifest entry (see
  [`docs/patterns/codespace-repo-provenance.md`](../../docs/patterns/codespace-repo-provenance.md))
  so `agent-codespaces` injects it into the CodeSpace's own user
  `~/.copilot/settings.json` on connect -- no agent-worktrees or agent-bridge
  install needed in the CodeSpace itself.
- **A container:** the equivalent `agent-containers` venue-injection seam
  (same shape as the CodeSpace case).

```json
{
  "extraKnownMarketplaces": {
    "copilot-extensions": { "source": { "source": "github", "repo": "ThomasMichon/copilot-extensions" } }
  },
  "enabledPlugins": { "agent-remote-driver@copilot-extensions": true }
}
```

## Fleet hygiene (many parallel sessions on one machine)

A machine running a **fleet** of parallel agent sessions -- agent-dispatch
workers, several worktrees, several CodeSpaces/containers, each with its own
`copilot` process -- writes many descriptors into the same
`~/.copilot/remote-driver/sessions/` directory. Two structural facts mean
there is nothing for two sessions to **collide** over: each descriptor is
named by the globally unique session id, and each server binds an
OS-assigned ephemeral port (never fixed/shared/guessed). The real risk at
fleet scale is **accumulation and runaway resource use**, not collision, and
this plugin addresses both directly:

- **Stale-descriptor reaping, no separate daemon required.** Every new
  session sweeps the shared discovery directory at its own startup
  (`extensions/agent-remote-driver/registry.mjs`'s `sweepStale`), removing
  any descriptor whose process is no longer alive. A crashed session (OOM,
  `SIGKILL`, host reboot) never gets to run its own cleanup -- the *next*
  session to start reaps it instead, so the directory stays bounded across a
  long-running fleet with zero dedicated reaper process.
- **Heartbeat-qualified liveness, not bare pid-liveness.** A dead process's
  pid can be recycled by an unrelated later process, so a bare
  `process.kill(pid, 0)` check alone is not trustworthy at fleet scale.
  Every live session refreshes its own descriptor's `updatedAt` every 30s;
  a descriptor only counts as live when its pid exists **and** its heartbeat
  is recent (default: 90s, 3x the refresh interval). This is the same class
  of defense as `agent-codespaces`' Connection Owner process-birth-identity
  beacon -- a genuine cross-platform birth-time check is a named follow-up,
  not yet built (heartbeat freshness already closes the common case: a
  crashed pid being reused within one heartbeat interval is exceedingly
  unlikely).
- **One aggregated view, not N consumer-side scans.** `bin/list-sessions.mjs`
  sweeps + lists every live session in a single pass, with an optional,
  bounded-concurrency `GET /health` confirmation per survivor (max 8
  in-flight probes regardless of fleet size). A fleet controller should call
  this instead of re-implementing its own directory walk + per-session
  liveness guessing -- exactly the kind of duplicated-probe traffic that
  turns into a connection storm against a large fleet.

  ```bash
  node plugins/agent-remote-driver/bin/list-sessions.mjs            # human-readable
  node plugins/agent-remote-driver/bin/list-sessions.mjs --json      # machine-readable (tokens redacted)
  node plugins/agent-remote-driver/bin/list-sessions.mjs --no-probe  # skip the /health confirmation
  ```
- **Bounded, non-looping failure modes.** `MAX_SSE_CLIENTS` (16) caps
  concurrent `/events` subscribers per session -- a reconnect-storming or
  misbehaving caller gets a `503`, never unbounded resource growth. The
  driver server's own `listen()` is retried a bounded 3 times on a bind
  race, then gives up and degrades silently (the session still runs exactly
  as it would without the extension) -- it never retry-loops indefinitely.
  A crash this process catches (`uncaughtException`/`unhandledRejection`)
  still runs descriptor cleanup before the process exits -- its entry is
  gone immediately, not merely stale. A crash that bypasses all JS handlers
  entirely (`SIGKILL`, OOM-killer, host reboot) gets no such cleanup: its
  descriptor physically persists on disk until something actively sweeps
  the directory (the next session to start, or `bin/list-sessions.mjs`) --
  there is no background timer that deletes it on its own. What the
  heartbeat timeout actually guarantees is *correctness for a reader*: any
  sweep that runs, whenever it runs, will correctly classify that entry as
  stale once its last heartbeat is more than ~90s old, never "unbounded
  staleness before anyone notices" -- but the file's physical removal still
  depends on a sweep actually happening.
  `SIGINT`/`SIGTERM` handlers remove themselves and re-raise the signal
  after cleanup, so Node's default termination still actually happens
  (a listener alone would otherwise suppress it and leave the heartbeat
  timer re-creating the descriptor it just removed).
- **No TOCTOU between a sweep's staleness snapshot and its delete.** A sweep
  lists descriptors as a snapshot, but the owning session can legitimately
  refresh its heartbeat before the sweep acts on it. A plain re-read before
  deleting still leaves a narrower race (the owner's fresh rename could land
  between that re-read and the unlink), so `reapIfStillStale` instead CLAIMS
  the entry first -- an atomic `renameSync(path, claimPath)`, which cannot
  be interleaved with the owner's own atomic write -- then revalidates
  staleness against the claimed copy and only deletes (or restores it,
  losing the claim race gracefully) based on that. The heartbeat rewrite
  itself is also atomic (temp file + rename, `writeDescriptorAtomic`), so a
  concurrent reader never observes a half-written descriptor mid-refresh in
  the first place.
- **Forward-compatible with a future heartbeat-protocol change.** A
  descriptor with NO `updatedAt` key at all (as opposed to one present but
  unparseable) is treated as pid-liveness-only, never reaped on heartbeat
  grounds -- so an already-running session launched by an OLDER version of
  this plugin during a rolling update is never deleted just for predating a
  protocol change it was never taught to satisfy. Every descriptor this
  version of the plugin writes already carries `updatedAt`, so this case
  cannot occur today; it is pure forward-compatibility.



**Provides:**
- Launch-time baseline drivability (attach/send/steer/abort) for any
  `copilot` session this extension loads into.
- A discovery descriptor any external process can read with no shared
  daemon, registry, or protocol dependency.
- Fleet-scale hygiene: self-healing stale-descriptor reaping, heartbeat-
  qualified liveness, bounded per-session resource limits, and a single
  aggregation entry point (`bin/list-sessions.mjs`) -- see *Fleet hygiene*
  above.

**Does NOT provide (delegated elsewhere, or future phases of the same
effort):**
- **Driver-exclusivity arbitration.** Any holder of a session's bearer token
  can call `/send`/`/steer`/`/abort` with no claim/generation primitive
  preventing two concurrent drivers from racing the same session. This is
  `cli-default-bridging`'s Phase 2 scope, not yet built.
- **The blocked-interaction escalation ladder** (pre-decide hooks, best-guess
  `ask_user` answers, elicitation decline, the `/clear`/`/exit` TTY command
  bridge) -- Phase 3 of the same effort.
- **Durable event replay across a reconnect.** `/events` streams forward from
  the moment of attach; it does not replay history. A real mux
  (`tmux`/`psmux`) session's own scrollback already gives reattach/replay at
  the terminal layer -- this extension does not reinvent that.
- **Cross-platform process-identity verification.** Staleness uses pid
  liveness + heartbeat freshness (see *Fleet hygiene*), not a true
  birth-time check -- a pid reused by an unrelated process within one
  heartbeat window (90s default) could theoretically produce a false
  "still alive" read. Named, not silently assumed solved.
- **Any coordination, registration, or heartbeat-to-a-third-party logic.**
  That is agent-bridge's own job if agent-bridge is present; this extension
  has no opinion about who, if anyone, reads its discovery descriptor.

**Assumes:** a loopback-capable environment (the server binds
`127.0.0.1` only) and a filesystem `~/.copilot/remote-driver/sessions/`
directory this process can create and write to.

## What's in this plugin

- [`extensions/agent-remote-driver/extension.mjs`](extensions/agent-remote-driver/extension.mjs) --
  the SDK-coupled glue: joins the session, writes/removes/refreshes the
  discovery descriptor, sweeps fleet-wide stale descriptors at startup, and
  forwards events to the driver server under the hot-potato discipline (no
  blocking work on the CLI's own event loop).
- [`extensions/agent-remote-driver/driver-server.mjs`](extensions/agent-remote-driver/driver-server.mjs) --
  the dependency-free HTTP server (health/events/send/steer/abort, with a
  bounded `MAX_SSE_CLIENTS`), decoupled from the SDK so it is directly
  unit-testable with a fake driver.
- [`extensions/agent-remote-driver/discovery.mjs`](extensions/agent-remote-driver/discovery.mjs) --
  pure helpers: descriptor path/shape, token generation, bearer-token
  verification, and the atomic (temp file + rename) descriptor writer.
- [`extensions/agent-remote-driver/registry.mjs`](extensions/agent-remote-driver/registry.mjs) --
  fleet hygiene: pid-liveness + heartbeat-qualified staleness, atomic-claim
  stale-descriptor reaping (no TOCTOU), and the aggregated live-session list.
- [`extensions/agent-remote-driver/lifecycle.mjs`](extensions/agent-remote-driver/lifecycle.mjs) --
  SDK-free process-lifecycle helpers (bounded retry, remove-then-reraise
  signal cleanup, a start/stop-able interval timer), every dependency
  injected so each is independently unit-testable without a real SDK
  session, real OS signals, or real timers.
- [`bin/list-sessions.mjs`](bin/list-sessions.mjs) -- the one aggregation CLI
  a fleet controller should use instead of its own directory scan + probes.
- [`tests/`](tests/) -- `node --test` coverage for all of the above.

## Troubleshooting, contributing & issues

- **No discovery descriptor appears for a session.** Confirm the plugin is
  actually enabled at the scope that session loaded from (`~/.copilot/settings.json`
  for a plain local launch; the venue's own injected user settings for a
  CodeSpace/container) -- extensions do not load retroactively into an
  already-running session, and this one does not auto-load in ACP mode
  (extensions never do, regardless of this plugin).
- **A request gets `401`.** The bearer token in the descriptor file must
  match exactly; re-read the descriptor rather than caching an old token
  (sessions do not reuse a token across restarts).
- **Two drivers stepped on each other's `/send`.** Expected today -- see
  *driver-exclusivity arbitration* above. Track
  `efforts/active/cli-default-bridging/README.md` Phase 2 for the fix.
- **The discovery directory keeps growing / an entry outlives its session.**
  Every new session self-heals this at its own startup (see *Fleet
  hygiene*); it is not a sign of a leak by itself. If a specific entry
  persists well past its heartbeat-timeout window (default 90s) with a
  genuinely dead process, that is a real `registry.mjs` bug -- file it,
  don't hand-delete the file as a workaround (another session's sweep would
  have removed it on its own next startup).

Contribute through this repo's normal worktree/PR flow (see
[`CONTRIBUTING.md`](../../CONTRIBUTING.md)); file issues against
[`ThomasMichon/copilot-extensions`](https://github.com/ThomasMichon/copilot-extensions/issues).
