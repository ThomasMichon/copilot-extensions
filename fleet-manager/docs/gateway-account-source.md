# Account sources on the existing service channel

`fleet_manager.gateway_account_source.AccountSourceHandler` adapts an explicitly
selected account custodian to the existing outbound `ServiceConnector`. It adds
no listener, broker, token cache, issuer, subscription store or controller
supervisor. The deployment owner hosts the connector in its existing process
and owns its admission, lifetime and reconnect policy.

This closes part of the [machine-fleet credential-relay
intent](../../visions/machine-fleet/README.md#scoped-credential-relay) by reusing
the credential owner and authenticated service channel. It does **not** make a
broad account token scoped. The separate `GatewayClient`/`Controller` sealed,
scoped-credential protocol is unchanged, and no existing Gateway is replaced or
implicitly migrated.

## Explicit composition

The controller embedding supplies an existing custodian with the
`credential_relay.account_relay.AccountRelay.resolve(fields, capability,
source_lease=False)` API. Fleet does not import, install, discover or start that
custodian. Its policy file, source capabilities and signed-in provider remain
with their existing owner.

```python
from fleet_manager.gateway_account_source import AccountSourceGrant, AccountSourceHandler
from fleet_manager.gateway_service_connector import ServiceConnector

source_handler = AccountSourceHandler(
    custodian,
    fleet="fleet-a", gateway="gateway-a", slot="account-source",
    installation="source-installation",
    grants=(AccountSourceGrant(
        principal="caller-a", source="repo-a", account="account-a",
        recipient="worker-a", host="github.com", action="get-github-token",
    ),),
)
connector = ServiceConnector(
    slot="account-source", gateway_origin=gateway_origin,
    handler=source_handler.binding(),
    access_token_callback=current_gateway_access, proof_key=controller_proof_key,
    gateway_ca=selected_ca,
)
await connector.run(owner_stop_event)
```

The names and objects above are trusted deployment inputs, not request-selected
references. The existing Gateway service policy must independently admit the
connector and caller, including the selected slot and routes.
`caller-context-v1` is required; an older Gateway refusing that profile is not
retried as an identity-less HTTP backend. See the [service
profile](gateway-service-profile.md#opt-in-caller-aware-handler).

The handler requires the channel-derived fleet/Gateway/slot/principal to match
its explicit assignment. Each grant also fixes source, account, recipient, host
and action. Only `get-github-token` is implemented. An admitted caller must
still supply its **own** exact source capability. The custodian rereads its
current policy and capability and validates its provider result on each request.
No stored controller capability is substituted for a missing or denied one.

## HTTP compatibility and failure

Paths below are after the service Gateway strips the selected route prefix:

| Request | Response |
|---|---|
| `GET /health`, empty body | `installationId` and `source_credentials_contract: {lease_contract: "credential-relay.account-source-lease/v1", exchange: "http/v1"}`; no provider acquisition |
| `POST /source-credentials` | Exact JSON fields `source`, `account`, `recipient`, `host`, `action`, `capability`, plus optional `lease_contract` |

A successful baseline response contains `account`, `host`, `token` and
`installationId`. Selecting the exact lease contract additionally returns
`lease_contract`, `lease_id`, `lease_issued_at`, `lease_expires_at` and
`lease_remaining_seconds`. Acquisition consumes the remaining source budget;
invalid, expired or regressed-clock leases are refused. None of these fields
attests token expiry, downscoping, native execution authority or permission to
resume an interrupted action. Pin `installationId` to the selected serving
handler; a package version or reachable channel does not establish support.
Every POST must carry exactly one matching `X-Agent-Index-Installation-Id`.
This caller precondition crosses the channel as an allowlisted header, not
identity or authorization. Missing, mismatched and duplicate pins refuse before
acquisition. Discovery may omit the pin; a supplied pin must match. A previous
channel implementation that strips the header causes pre-acquisition refusal,
never a pin-less downgrade.

Responses are `no-store`. Request bodies are limited to 16 KiB, use strict
duplicate/nonfinite/unknown-field rejection and are never logged by the adapter.
The source result must exactly match the requested account and host and the
implemented account wire format. Provider output and exceptions are replaced
by fixed error codes, never relayed as diagnostics. The async acquisition
deadline is 20 seconds or the caller's remaining admission, whichever is
smaller. Cancellation propagates. As with other service handlers, the custodian
must cooperate: this deadline cannot preempt synchronous filesystem I/O or
noncooperative cleanup. The existing account custodian reads bounded local
policy/capability files and invokes its existing headless token command;
deployment must qualify those blocking stages on the selected source host.

Unavailable or withdrawn sources return `503`, without ambient-account fallback
or cache reuse. Channel loss or replacement retains the existing transport's
`outcome-unknown` behavior for a transmitted POST; it is **not** automatically
replayed on another controller. Reconnect admits a new channel for new requests,
not old demand. Multi-source selection must happen under the deployment owner's
explicit route/policy before dispatch, not by retrying an uncertain acquisition.

This is provider-first source support. Existing consumer endpoints and Gateway
authentication remain unchanged until their owner explicitly composes a route.
The tests cover exact grants, source-policy withdrawal, renewal and real
TLS/PoP/WebSocket delivery with synthetic credentials, including loss and
reconnect. They do not qualify production enrollment, deployment, a real signed-in
provider or end-to-end consumer access.
