# agent-fleet-contracts

Portable, standard-library-only schema-v1 records for driver snapshots,
connector registrations, locally approved service offers, and
health/index-search request **and response** envelopes. No Copilot,
marketplace, server, model, provider or process dependency is required.
Controller and connector distributions can consume this package without
importing each other.

The canonical tests live in this library and are also collected by the
`agent-ssh` consumer's thin test projection, so its required test lane exercises
both the wire contract and the real static-driver CLI.

`from_dict` rejects unknown fields, unsupported versions/operations, duplicate
identities and invalid types; JSON arrays become immutable tuples. Direct
constructors reject mutable collections. `decode_json` additionally
rejects duplicate JSON keys, nonfinite numbers, excessive nesting and messages
over 64 KiB. Limits: 256 selected targets, 32 offered services, 4 KiB UTF-8 query,
1-100 search results, 4 KiB per hit's body content and 1 KiB per file path.
Records are frozen and use immutable collections. `encode_json` enforces the
same message bound, and `RouteResponse` additionally validates at
**construction** time that its own encoded wire form fits that bound -- a
response with many large, individually-valid hits can still exceed the
aggregate 64 KiB limit even though every per-field bound passed, so this is a
distinct check from the field-level ones above.

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

`health-v1` permits only `health`; `index-v1` additionally permits `index.search`.
Search parameters match the existing index query API (`q`, `limit`, `source`,
`language`, `repo`); filters do not grant source access. There is no method, URL,
path, shell, environment, credentials or generic execute field.

`RouteResponse.for_request(request, health=..., hits=...)` builds a response
correlated to its originating request's own validated identity fields
(`request_id`, `service_id`, `installation_id`, `operation`) -- never a value an
adapter would otherwise have to re-assert by hand. `HealthResult` and
`SearchHit` are its operation-typed payloads, mirroring `RouteRequest`'s own
`parameters` field. This package defines the **shape** of a response only: an
adapter still owns converting one specific backend's actual data into these
records (see `agent-fleet-index-adapter` for the `index-v1` adapter), and
authentication, service/backend identity checks, state persistence, the
controller/connector runtime and standalone lifecycle remain subsequent effort
slices, not features implemented by this data-contract package.

See `efforts/active/machine-fleet-routing-foundation/` and
`visions/machine-fleet/` in the repository.
