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
  "startedAt": "2026-10-03T04:00:00.000Z"
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

## What this plugin provides -- and what it doesn't

**Provides:**
- Launch-time baseline drivability (attach/send/steer/abort) for any
  `copilot` session this extension loads into.
- A discovery descriptor any external process can read with no shared
  daemon, registry, or protocol dependency.

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
- **Any coordination, registration, or heartbeat logic.** That is
  agent-bridge's own job if agent-bridge is present; this extension has no
  opinion about who, if anyone, reads its discovery descriptor.

**Assumes:** a loopback-capable environment (the server binds
`127.0.0.1` only) and a filesystem `~/.copilot/remote-driver/sessions/`
directory this process can create and write to.

## What's in this plugin

- [`extensions/agent-remote-driver/extension.mjs`](extensions/agent-remote-driver/extension.mjs) --
  the SDK-coupled glue: joins the session, writes/removes the discovery
  descriptor, and forwards events to the driver server under the hot-potato
  discipline (no blocking work on the CLI's own event loop).
- [`extensions/agent-remote-driver/driver-server.mjs`](extensions/agent-remote-driver/driver-server.mjs) --
  the dependency-free HTTP server (health/events/send/steer/abort), decoupled
  from the SDK so it is directly unit-testable with a fake driver.
- [`extensions/agent-remote-driver/discovery.mjs`](extensions/agent-remote-driver/discovery.mjs) --
  pure helpers: descriptor path/shape, token generation, bearer-token
  verification.
- [`tests/`](tests/) -- `node --test` coverage for both of the above.

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

Contribute through this repo's normal worktree/PR flow (see
[`CONTRIBUTING.md`](../../CONTRIBUTING.md)); file issues against
[`ThomasMichon/copilot-extensions`](https://github.com/ThomasMichon/copilot-extensions/issues).
