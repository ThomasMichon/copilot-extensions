# Opt-in Controller services on the existing Gateway channel

The SDK's `controller-services-v1` profile lets one authenticated outbound
Controller channel advertise up to 16 named services, each at an exact route
prefix already assigned to that Controller's desired slot. Several Controller
slots can offer the same logical service. Existing one-slot/one-current-channel
replacement, TLS, identity, caller policy and namespace ownership remain.
An offer is an availability observation, not a credential, service grant,
readiness proof or permission to borrow an account.

This closes part of the [Gateway outbound-channel intent](../../visions/machine-fleet/gateway/README.md#authenticated-outbound-role-channels)
and [machine connector intent](../../visions/machine-fleet/README.md#gateway-controller-and-machine-connectors).
It adds no listener, daemon, provider login, token store, transport owner or
default activation. Deployment still supplies the existing protected route.

## Explicit SDK selection

Supply a normal `HandlerBinding` and a tuple of
`ControllerService(service, prefix)` values to `ServiceConnector(services=...)`.
An empty tuple selects a connected Controller with no currently offered
services. Omission selects the unchanged legacy backend/caller-context profile.
The handler receives the normal verified `ServiceRequest`, with `service`
identifying the admitted logical service and its normal prefix-relative target.
The embedding handler maps that service to its existing custodian.
No arbitrary backend URL or incoming module name is accepted.

`await connector.advertise(tuple_of_services)` replaces observations on the
current channel and waits for that exact revision's acknowledgment. Names and
prefixes are unique within a Controller. Unknown fields, duplicate names/routes,
oversized lists and prefixes outside that slot's desired routes refuse.
The selected desired observations are used on a later explicitly owned
reconnection; requests lost with the old channel are never replayed.
A failed/uncertain update closes that channel rather than reusing its revision.

Withdrawal immediately removes the offer at the Gateway and cancels its pending
requests. It does not withdraw another service on the same connection.
Disconnect, replacement, expiry or policy revocation makes observations
unavailable. Re-advertising a withdrawn service has a new offer generation,
even on the same connection, so a retained old observation cannot become
current again. Unchanged offers retain their generation across unrelated
advertisement changes.

An embedding can inspect `runtime.service_offers(service)` and call
`runtime.check_service_offer(observation)` to check an exact current connection
and offer generation. These process-local observations are not portable handles
or caller grants. There is no unauthenticated remote inventory endpoint.

## In-process local-owner composition

A Gateway `LocalHandlerBinding` may register explicit
`ControllerSourceBinding(service, controllers)` selections in its `sources`
tuple. The values name desired Controller slots, not discovered endpoints.
The local handler uses:

```python
offers = local.service_offers("example-available-v1")
response = await local.call_service(
    offers[0], target="/available", body=b"{}", timeout=5,
)
```

The handler must handle an empty authorized inventory; this example is not a
source-selection or credential policy. Each source call requires:

- The original local request's retained authenticated grant, actual method,
  exact current local binding, route, target and connected lifetime.
- An explicit code-registered service/source-slot binding and current caller
  policy independently granting that destination slot and the same method.
- The exact current source connection, offer generation, desired source route
  and independently authenticated service grant.

The original grant is neither replaced nor retargeted. A GET cannot become a
POST through this API. No new caller proof/token, worker identity or backend
capability is manufactured. The Controller receives Gateway-asserted caller
context for its destination slot plus `request.origin` containing the original
admitted slot, method and authenticated target URI. That origin is provenance,
**not** a newly signed request proof, operation permit or source-account grant.
The Controller's handler must independently authorize the actual source,
account, recipient, operation and any credential custody.
An ordinary directly routed Controller request has `origin=None`.

Dispatch stays in the existing Gateway process and uses the existing
`Channel`/`Pending` correlation, filtered headers, request limits, response
ACK/backpressure and cancellation. It does not call the Gateway over loopback
HTTP or create another service connection. Responses are buffered to 32 KiB.
Each internal call is separately counted against existing channel/global
admission and has a positive deadline of at most 30 seconds; it does not extend
the original caller's lifetime. Authorization, connection and offer checks run
before dispatch, on response acceptance and after cleanup before returning.

A timeout/disconnect cancels transport work but is not proof that a remote
provider or native effect settled. Original source custody and unknown-outcome
handling remain with that provider. No internal call is automatically retried.

## Contract and rollout

The separately versioned wire identity is `controller-services-v1`; package
versions do not select support. The running Gateway advertises it through the
safe read-only `GET /_fleet/controller-challenge`, returning exactly `profile`
and the existing authority's `nonce`. An explicitly selected Controller checks
this response **before obtaining credentials or posting admission**. Missing,
invalid or unknown support refuses; it never probes an effectful new form and
replays a legacy form. Redirects and ordinary TLS/Host checks remain effective.

Only the selected new profile adds `services` to service admission, preserves
that selection in the single-use ticket/attachment, and adds service/provenance
coordinates to handler requests. Its channel accepts ordered full-state
`services` frames and matching `services-accepted` replies. No manifest or policy
schema changes are required: offers cannot add or move declared routes.
Other profiles retain their strict original frames and defaults.

This is the provider expansion plus an **explicit opt-in** SDK consumer, not
automatic fleet migration. Roll out capable Gateway receivers first, then select
the profile on Controllers. Old callers continue unchanged against new
receivers. A new Controller facing an old/rolled-back Gateway refuses before
credentials/admission; its ordinary legacy connector mode remains available
when the deployment does not require Controller offers. No legacy retirement
is scheduled, and this feature never silently downgrades itself.

## Validation and limits

`tests/test_gateway_controller_services.py` exercises synthetic real TLS,
JWT/PoP, actual outbound WebSockets, two Controllers, multiple services per
connection, changed/withdrawn/re-advertised offers, replacement/disconnect,
caller/source-binding denials, original-admission provenance, deadline and
policy loss. Legacy transport/handler/policy/enrollment tests cover unchanged
profiles; real old/invalid discovery responses prove pre-admission refusal.
Run through `tools/run-standalone-tests.py fleet-manager` using those focused
test selectors and `--prepare` when rebuilding changed package code.

The inventory is bounded by the existing 64 desired slots and 16 offers per
Controller. A local binding selects at most 32 source slots. Inventory scans
perform no filesystem, history, process or network work. Exact-offer response
checkpoints reuse the channel and generation in memory rather than rescanning
the full roster. Actual cold/warm deployed latency and provider settlement
need their separately authorized integration lane; contained synthetic-channel
tests are not production performance qualification.

This API does not compose the [available-token coordinator](gateway-vault-available.md)
with a real source custodian, expose a Gateway Vault token route, implement
auth-needed fan-out/local UX, activate subscriptions or establish deployed
Controller readiness. Those remaining integrations are tracked by
[the Gateway effort](../../efforts/active/linux-role-fleet/README.md) and
[the credential coordination issue](https://github.com/ThomasMichon/copilot-extensions/issues/8729).
