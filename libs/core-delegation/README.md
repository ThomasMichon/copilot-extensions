# core-delegation

A tiny, dependency-free library that lets a service-bearing Copilot CLI plugin
reach its heavy **core** — an in-process default, a remote host engine, or a
container — as *just another transport target*, over a **tokened
loopback IPC seam**, with a **user-mode fall-through** when no core is wired.

It composes two existing primitives — `endpoint-rendezvous` for discovery and the
newline-framed JSON wire the service daemons already speak — into a single
read-side client transport. It is the client half of the adapter↔core seam; the
packaging half (a core built inside a container from a read-only mounted payload)
is a separate concern.

## Why

A portable plugin should be a thin, container-agnostic **host adapter** that can
delegate its heavy lifting to a core running elsewhere, **without** build-time
coupling to that core and **without** ever *requiring* one. The seam makes the
core reachable as a transport: discover where it is, ship the request, get the
response — or, if no core is wired, return `None` so the plugin's built-in
transports and self-contained user-mode path handle the request unchanged.

This preserves `visions/plugin-services` §`standalone-reachability` (a service is
reachable using only what its own installer put on the machine) and
§`degrade-gracefully` (absent an optional coordinator, local function still
works). It is an **opt-in** boundary crossing layered on
`docs/patterns/service-transport.md` rung 4 — no plugin ever *requires* a tunnel,
broker, or core.

## What it does

`delegate(app, request, ...)`:

1. **Discovers** the core's endpoint with the `endpoint-rendezvous` ladder rooted
   at `runtime_dir` (default `~/.<app>/run`): `override` → the core's rendezvous
   file (if present and not stale) → `legacy`. Nothing resolves → **returns
   `None`** (no core wired).
2. **Narrows** the resolved endpoint to a transport dialable in *this* process
   (`unix` socket off Windows / named `pipe` on Windows / loopback `tcp`
   anywhere), honoring the rendezvous `alt` list. None dialable → `None`.
3. **Attaches** a bearer `token` (when supplied) under the `_token` key
   (token-bind) — a core that enforces it validates it; one that doesn't ignores
   the extra key, so it is always wire-safe.
4. **Ships** the request as **newline-framed JSON** — wire-identical to the
   framing the service daemons already speak (`json.dumps(request) + "\n"`, read
   to newline) — and returns the decoded response dict, or `None` on any
   send/parse failure.

The seam is **protocol-agnostic**: it frames and ships whatever `request` dict
the caller hands it and returns whatever dict comes back, so multiple plugins
reuse the same primitive.

## Usage

```python
from core_delegation import delegate

# In a plugin's client-transport hook: reach a wired core, else fall through.
def core_transport(request, timeout, ctx):
    return delegate(
        "agent-x-core",
        request,
        timeout=timeout,
        token=os.environ.get("AGENT_X_CORE_TOKEN"),
        override=os.environ.get("AGENT_X_CORE_ENDPOINT"),  # "tcp:127.0.0.1:52731"
        runtime_dir=Path.home() / ".agent-x" / "core",     # distinct from the
                                                            # local daemon's run dir
    )
# `core_transport` returns None when no core is wired -> the built-in transports
# and user-mode path still handle the request.
```

Register it through the plugin's existing client-transport seam (e.g.
agent-vault's `register_transport`) rather than forking the client — typically as
a **fallback** (consulted after the built-in local transports), so a wired core
is used only when the local daemon isn't handling the request.

## Consumed by

Vendored into the venvs of the plugins that delegate to a core (agent-vault
first, agent-index next), the same way `endpoint-rendezvous` is vendored — a
single in-package module copy, no distribution dependency, so a
marketplace-installed plugin stays self-contained. Pure standard library.

## Test

```
pytest        # from this directory (endpoint-rendezvous src is put on the path
              # by tests/conftest.py)
```
