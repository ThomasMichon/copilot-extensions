# Gateway desired manifests and publication

`gateway_manifest` and `gateway_reconcile` are optional, standard-library-only
desired-state primitives. They do not start a Gateway, authenticate a connector,
resolve a key reference, replace `GatewayConfig` or alter existing index and
credential routes. Live serving integration remains an explicit later gate.

## Manifest version 1

The strict `fleet-manager.gateway-manifest` JSON document declares `version: 1`,
stable `fleet` and `gateway` identifiers, a positive integer `revision`, declared
`roles`, `key_sources` and `slots`.

```json
{
  "schema": "fleet-manager.gateway-manifest",
  "version": 1,
  "fleet": "example",
  "gateway": "gateway",
  "revision": 1,
  "roles": ["search"],
  "key_sources": [
    {"name": "search-key", "provider": "owner", "reference": "keys/search@v1"}
  ],
  "slots": [
    {
      "name": "search",
      "role": "search",
      "principal": "search-instance",
      "key_source": "search-key",
      "configuration_ref": "config/search",
      "routes": [
        {"prefix": "/_api/search", "methods": ["GET"], "capabilities": ["http"]}
      ]
    }
  ]
}
```

Unknown fields, duplicate JSON keys, nonfinite numbers, booleans used as numeric
versions/revisions and oversized/deep input are rejected. Existing bounded
planning JSON primitives enforce the 1 MiB, depth 12 and collection bounds.
There are at most 64 declared roles, 128 key references, 64 slots, 16 routes per
slot and 256 routes overall. An empty slots list explicitly withdraws all roles;
it does not mean a failed or unavailable configuration source.

Slot and key-source names are unique. Roles and key sources must be declared
before a slot uses them. Multiple slots may explicitly name the same principal;
that declaration does not authenticate it or infer permission.
References are bounded opaque ASCII names, with alphanumeric-led segments using
`_.-`, separated by `/`, and an optional final `@version`. They are not URLs,
executable instructions or inline reusable credentials. Lexical validation is
not a general secret detector; the source owner must supply non-secret
references. No reference is resolved by this library.

Routes use canonical absolute ASCII prefixes, excluding the root, trailing or
repeated separators, dot segments, percent encoding, backslashes, query and
fragment forms. Namespace ownership may not overlap, even within one slot.
Control prefixes `/v1`, `/_fleet`, `/health`, `/ready`, `/metrics` and `/status`
are reserved at segment boundaries. `/v10` is not `/v1`.
Methods are a unique selection of GET, HEAD, POST, PUT, PATCH, DELETE and OPTIONS.
Declared capabilities are `http` and optional `response-stream`; declaration is
not proof that a transport implements them.

`GatewayManifest.load(value)` validates an in-memory JSON tree; `parse(bytes)`
uses the strict decoder. Frozen domain records contain tuples, not mutable
input aliases. `to_dict()` returns a fresh tree. Semantically unordered
collections normalize deterministically, so `canonical_bytes()` and the
SHA-256 `digest` property identify equivalent content consistently. Revision
participates in that identity. Direct dataclass construction is not admission;
untrusted input must use the validating loader.

## Explicit source and process-local publication

An embedding owner constructs:

```python
store = ManifestStore(
    fleet="example",
    gateway="gateway",
    source=read_explicit_owner_manifest,
)
publication = store.reload()
snapshot = store.snapshot()
```

The selected callback returns complete bytes. It runs outside the publication
lock; constructor, status and snapshot reads do not read or refresh the source.
The deployment owner is responsible for authenticating and constraining that
source, including path/repository access and complete-file publication. A
callback, parsed document, digest or Git commit is not independent authority.
There is no ambient source discovery or network/key lookup.

`reload()` validates the entire candidate and pinned fleet/Gateway identity
before one locked pointer swap. A lower revision is refused; changed content
at the same revision conflicts. An identical revision and digest returns the
same snapshot with `changed=False` and unchanged generation. A higher revision
advances the process-local generation. Concurrent callers re-check against the
current published revision after source reads, so a late older result cannot
roll back a newer publication.

Invalid input or an `OSError` from the source leaves the prior snapshot intact
and raises a value-free `ReconcileError`. Source I/O failures have code
`source-unavailable`. Unexpected callback exceptions propagate instead of being
converted into an empty or successful configuration. `status()` reports desired
revision, digest, generation, slot count and the last classified error; it does
not contain key/configuration references or raw source exceptions.

Snapshots are desired state only. Publication does **not** atomically replace
identity verifiers, admission permits or service connections. Readers must
revalidate current authority at their actual admission/effect boundary.
The generation/revision fence lasts only for the store's process lifetime:
durable owner revision custody and crash recovery are not implemented here.
These APIs alone therefore do not establish restart-safe publication,
live trust rotation, connector revocation or a running service tunnel.

## Related

- [Existing Gateway runtime](gateway-runtime.md)
- [Gateway Role Tunnels plan](../../efforts/active/gateway-role-tunnels/README.md)
- [Initial contract](../../efforts/active/gateway-role-tunnels/initial-contract.md)
