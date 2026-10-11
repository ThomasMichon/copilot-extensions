# agent-fleet-vault-adapter

The **credential vault service adapter** for the
[machine-fleet routing foundation](../../efforts/active/machine-fleet-routing-foundation/README.md)
campaign realizing the [machine-fleet vision](../../visions/machine-fleet/README.md).
Sibling to [`agent-fleet-index-adapter`](../fleet-index-adapter/README.md) --
same `vault-v1` pattern as that package's `index-v1`.

This is a **prototype/spike**: it proves out the `vault-v1` typed-adapter
contract ahead of Phase 2's standalone controller/connector runtime
existing. It is not yet wired into any shipped plugin or daemon, and it does
not implement, replace, or claim compatibility with any existing private
Gateway deployment -- see "Relationship to an existing Gateway" below.

```python
from fleet_vault_adapter import route

response = route(request, offer, base_url="http://127.0.0.1:52731")
if response.credential.available:
    token = dict(response.credential.fields)["token"]
```

## What this adapter is for

A registered Windows-side **controller/manager machine** (already
interactively authenticated -- `az login`, `gh auth login`, etc.) holds or
can mint credentials a consumer elsewhere in the fleet needs but cannot
itself obtain (a headless Linux container has no interactive sign-in path).
This adapter is the **pull side** of shuttling one such credential, scoped
and on demand, through the Gateway:

```text
Windows controller --(push, out of band)--> vault backend <--(pull, this adapter)-- consumer
```

- **Push** (how a credential gets into the vault backend's custody) is
  explicitly **out of scope for this adapter** -- it is a separate,
  producer-side concern (see "Relationship to an existing Gateway").
- **Pull** is everything `route()` does: given an already-authorized
  `credential.get` request, call the fixed vault backend and return a typed,
  bounded `fleet_contracts.RouteResponse` carrying a `CredentialResult`.

## The wire contract this adapter speaks

`GET {base_url}/health` -> `{"status": "ok" | <anything else>}`, mapped to
`HealthResult` the same way `agent-fleet-index-adapter` does.

`POST {base_url}/relay` with `{"action": <action>, "fields": {...}}` (the
non-null fields from `CredentialParameters`: `host`, `resource`, `tenant`,
`scope`) -> one of:

- `{"available": true, "fields": {"token": "...", "expires_on": "..."}}`
- `{"available": false, "reason": "no_cached_token" | "expired" | "denied" | ...}`
  (any bounded reason string; the HTTP status for this case may be a non-2xx
  code -- the adapter reads and parses the body either way, the same bounded
  way, rather than treating every non-2xx response as a transport failure)

`action` is restricted to `fleet_contracts`'s closed `_CREDENTIAL_ACTIONS` set
(`get-azure-token`, `get-github-token`, `get-access-token`) -- the same
dedicated, hostless-or-host-scoped token actions `libs/credential-relay`
already defines, deliberately excluding its generic git-credential
`get`/`store`/`erase`/`fill`/`approve`/`reject` verbs (see
`fleet_contracts.records._CREDENTIAL_ACTIONS`'s own docstring for why: those
accept an open-ended field set shaped for git's own credential-helper
protocol, a much larger surface than a typed, bounded fleet route should
expose).

An **unavailable credential is not an error.** A cache miss, an expired
entry, or a scope denial are the vault backend's ordinary, honest answers --
`response.credential.available` is `False` with a bounded `reason`, never a
raised exception. `BackendUnavailable` is reserved for an actual transport
failure or a malformed/out-of-bounds backend response (see
`fleet_vault_adapter.adapter`'s module docstring).

## What this adapter does not do

Per the architecture proposal's authority table (the same one
`agent-fleet-index-adapter` follows), this module owns exactly one thing: **a
typed route to a fixed trusted backend and its API contract**. It does not:

- mint, cache, or custody any credential itself;
- resolve `base_url` (the eventual connector does, from its own locally
  approved configuration -- `base_url` is never client-supplied, so there is
  no SSRF surface despite the generic-looking `urlopen`/`Request` calls);
- authenticate or authorize the request (the eventual controller/connector
  does, before this module is ever reached, by binding
  `request.check_offer(registration, now=...)` to a live, scoped
  registration);
- decide *which* credentials a consumer may request (that is the
  registration/offer's own scope, enforced upstream of this module).

## Deployment model: only the Gateway is addressable

A containerized consumer of this adapter **never reaches a vault backend
directly on its own published port.** `base_url` always resolves to an
internal, Gateway-fronted address -- a Caddy- or Chisel-published route, a
container-network-internal hostname, or a loopback reverse-proxy -- and the
vault backend itself publishes no other port anywhere. This is the same
deployment assumption `agent-fleet-index-adapter` makes for the index
backend it routes to, and the same "only the Gateway exposes non-management
ports" rule the containerized-service-fleet vision states generally.

## Relationship to an existing Gateway

A real, private Gateway-HTTP credential relay already exists and runs in
production (not in this repository): a bearer-authenticated HTTP service
with `POST /credentials/push` (a Windows producer lands a freshly-minted
token) and `POST /relay` (serves a previously-pushed token back out),
backed by a persistent credential cache, and already aligned with
`libs/credential-relay`'s own action vocabulary
(`get-azure-token`/`get-github-token`/`get-access-token`/`capabilities`).

This adapter is the **portable, generic realization** of that relay's
*pull* side, proven here against a stand-in backend rather than that real
deployment -- the same relationship `agent-fleet-index-adapter` has to a
private, hand-rolled bounded index proxy that already exists and runs in
production elsewhere. Aligning that real deployment to this portable
contract (or vice versa) is explicitly **not** done by this prototype; it
is a private-repo integration decision for whoever operates that Gateway.

See `tests/test_adapter.py` for a minimal stand-in HTTP backend proving
every bound and error path, including the available/unavailable credential
distinction above.
