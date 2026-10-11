# agent-fleet-index-adapter

The **index service adapter** for the
[machine-fleet routing foundation](../../efforts/active/machine-fleet-routing-foundation/README.md)
campaign realizing the [machine-fleet vision](../../visions/machine-fleet/README.md).

This is a Phase 3 **prototype/spike**: it proves out the "typed service
adapter" seam the architecture proposal calls for, ahead of Phase 2's
standalone controller/connector runtime existing. It is not yet wired into
any shipped plugin or daemon.

```python
from fleet_index_adapter import route

response = route(request, offer, base_url="http://127.0.0.1:52731")
```

`route()` takes an already-validated `fleet_contracts.RouteRequest` and the
`ServiceOffer` it was matched against (via `RouteRequest.check_offer(...)` --
this module never authenticates or authorizes anything itself), issues the
corresponding **unmodified** HTTP call against agent-index's existing,
documented query API (`GET /health`, `GET /search`), and returns a bounded,
typed `fleet_contracts.RouteResponse`.

Per the architecture proposal's authority table, this module owns exactly
one thing: **a typed route to a fixed trusted backend and its API contract**.
It does not:

- resolve `base_url` itself (the eventual connector does, from its own
  locally approved configuration -- `base_url` is never client-supplied, so
  there is no SSRF surface despite the generic-looking `urlopen` call);
- authenticate or authorize the request (the eventual controller/connector
  does, before this module is ever reached);
- retry, pool connections, or own any lifecycle/reconnect concern (the
  eventual connector does).

A malformed or oversized backend response raises `BackendUnavailable` rather
than being relayed as-is -- the point of a typed adapter is that a backend
defect cannot become an unbounded or malformed wire response merely by being
forwarded.

See `tests/test_adapter.py` for both a minimal stand-in HTTP backend (fast,
deterministic unit coverage of every bound and error path) and a genuine
integration test against the real `agent_index.server.build_app()` FastAPI
app, running on a real OS-assigned TCP port -- not a mocked transport --
proving the full request/response contract end-to-end.
