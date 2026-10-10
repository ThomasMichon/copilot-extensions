# agent-fleet-contracts

Portable, standard-library-only schema-v1 records for driver snapshots,
connector registrations, locally approved service offers and health/index-search
request envelopes. No Copilot, marketplace, server, model, provider or process
dependency is required. Controller and connector distributions can consume this
package without importing each other.

The canonical tests live in this library and are also collected by the
`agent-ssh` consumer's thin test projection, so its required test lane exercises
both the wire contract and the real static-driver CLI.

`from_dict` rejects unknown fields, unsupported versions/operations, duplicate
identities and invalid types; JSON arrays become immutable tuples. Direct
constructors reject mutable collections. `decode_json` additionally
rejects duplicate JSON keys, nonfinite numbers, excessive nesting and messages
over 64 KiB. Limits: 256 selected targets, 32 offered services, 4 KiB UTF-8 query
and 1-100 search results. Records are frozen and use immutable collections.
`encode_json` enforces the same message bound.

```python
from fleet_contracts import ConnectorRegistration, RouteRequest, decode_json

registration = ConnectorRegistration.from_dict(decode_json(registration_bytes))
request = RouteRequest.from_dict(decode_json(request_bytes))
offer = request.check_offer(registration, now=epoch_seconds)
```

Parsing and `check_offer` **do not authenticate or authorize**. The caller must
bind the authenticated principal to fleet, provider instance, target, connector,
service installation and permitted operation before accepting either record.
Connector identity is not taken from a client-supplied principal field.
Generation comparison requires an authoritative current registration; this
library does not persist a fence or prevent an older registration replacing it.
Registration TTL defaults to 600 seconds and cannot be accepted at expiry;
renewal and revocation remain the controller's responsibility. When explicitly
configuring another permitted TTL (up to 3600 seconds), pass that same `max_ttl`
to registration acceptance and request/offer checks.

Driver `configured` means declared transport only, never verified reachability,
readiness, admission or enrollment. Missing capabilities must not be inferred.
A provider instance and canonical target ID qualify identity across drivers;
the source revision identifies the exact input snapshot, not permission to
execute it.

Service installation identity is an opaque, bounded UTF-8 value (256 bytes),
not a logical name, path or endpoint. Native index cells use values such as
`<marketplace-id>/agent-index`; preserve and compare them exactly. Empty values
and control characters are rejected. Accepting this identity still grants no
authority and never causes a filesystem or network lookup.

`health-v1` permits only `health`; `index-v1` additionally permits `index.search`.
Search parameters match the existing index query API (`q`, `limit`, `source`,
`language`, `repo`); filters do not grant source access. There is no method, URL,
path, shell, environment, credentials or generic execute field. The response
transport adapter, authentication, live service identity checks, state persistence,
controller/connector runtime and standalone lifecycle are subsequent effort
slices, not features implemented by this data-contract package.

`IndexHealth` and `IndexSearchResult` validate native index response bodies with
an explicit 1 MiB maximum, distinct from the default 64 KiB message budget.
Health checks the expected opaque installation identity and process promotion/
drain state, then strips backend process tokens and PIDs. `can_accept_reads`
does not assert model/search readiness. Search preserves the original hit fields
and full content within bounds; `available: false` is an explicit error, never
a successful empty result. Invalid query identity, oversized bodies, excess
hits and malformed/nonfinite scores are rejected without clipping or coercion.
Parsing a body is not a live backend check or an access grant.

See `efforts/active/machine-fleet-routing-foundation/` and
`visions/machine-fleet/` in the repository.
