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

For a protected Gateway exposed by an existing local conduit, a caller-aware
handler may explicitly pass `gateway_route=LoopbackRoute("127.0.0.1", local_port)`
from `fleet_manager.gateway_connect_route`. This changes only DNS/TCP routing,
not the canonical `gateway_origin`, HTTP Host, TLS name or proof target.
The route accepts only literal loopback addresses and bounded ports. It cannot
be selected for an identity-less HTTP backend, redirects or other target hosts,
and does not fall back to direct networking. Discovery, observation expiry and
generation replacement remain the embedding owner's responsibility.
The existing [Vault service embedding](../../plugins/agent-vault/docs/federated-relay.md)
provides one explicit opt-in owner; it does not make Fleet own the custodian.

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
| `GET /health`, empty body | `installationId`, `source_credentials_contract: {lease_contract: "credential-relay.account-source-lease/v1", exchange: "http/v1"}` and additive `normal_fetch_contract`; no provider acquisition |
| `POST /source-credentials` | Exact JSON fields `source`, `account`, `recipient`, `host`, `action`, `capability`, plus optional `lease_contract` |

A successful baseline response contains `account`, `host`, `token` and
`installationId`. Selecting the exact lease contract additionally returns
`lease_contract`, `lease_id`, `lease_issued_at`, `lease_expires_at` and
`lease_remaining_seconds`. Acquisition consumes the remaining source budget;
invalid, expired or regressed-clock leases are refused. None of these fields
attests token expiry, downscoping, native execution authority or permission to
resume an interrupted action. Pin `installationId` to the selected serving
handler; a package version or reachable channel does not establish support.
Every legacy `/source-credentials` POST must carry exactly one matching `X-Agent-Index-Installation-Id`.
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

## Explicit normal-fetch selection

The separate `fleet-manager.vault-normal-fetch/v1` contract permits bounded
**ordinary provider retrieval**, including its normal refresh behavior. It is
not the [available-only contract](gateway-vault-available.md), a cache-only read
or a guarantee that a token is unexpired or usable by a consumer. It never
runs a new login command or starts a browser/native authentication ceremony.
The existing signed-in provider retains refresh and physical-custody policy.

`AccountSourceHandler` advertises this contract as `normal_fetch_contract`
through `/health`. `POST /token-fetch` requires the exact source fields and
capability above plus `fetch_contract` and `installationId`, with no lease
fields. The selected installation pin is mandatory in the body, allowing
Gateway forwarding to preserve the original filtered headers. A supplied
installation header must still match; a contradictory pin never gets replaced.
The handler checks contract, installation and caller/source grants before
acquisition. Successful replies contain the same five source fields,
`fetch_contract`, `installationId`, `acquisition: "normal-fetch"`, `token` and
`token_expires_at`. The current `gh-auth` custodian reports the last field as
JSON `null`: its existing getter does not supply actual credential expiry.
Source leases and cache age are never substituted. The typed selector also
supports finite provider-reported credential expiry and rejects known expiry.

An SDK deployment can explicitly register
`fleet_manager.gateway_vault_fetch.GatewayAccountFetch` as a local Gateway owner.
It supplies a complete desired `ServiceSlot`, selected Controller service name
and `AccountFetchGrant` tuple. Each grant fixes original principal, logical
binding name, account, recipient, host and `AccountSourcePin` selections
(Controller slot, source name, installation). `selection.binding()` supplies
the normal `LocalHandlerBinding`, including those independent source-slot
selections. Controllers explicitly advertise the same chosen service, for
example `vault-normal-fetch-v1`, through the existing Controller profile.
No policy-selected Python module, new listener or automatic CLI activation is
introduced.

The consumer POSTs to the local slot's `/token-fetch` with exactly:

```json
{
  "fetch_contract": "fleet-manager.vault-normal-fetch/v1",
  "binding": "account-access",
  "sources": {"account-source": "<consumer-owned-source-capability>"}
}
```

`sources` maps only explicitly granted Controller slots to the consumer's own
capabilities. Gateway never substitutes a capability from its filesystem.
Current authorized offers, original caller policy, exact connection/offer
generations and source installation pins are checked through the existing
channel; the custodian independently rereads current policy and capabilities
before and after provider retrieval. The internal request retains the original
caller and admitted origin, not a Gateway account identity.

The shared selector applies one deadline of at most 30 seconds, starts at most
32 sources, prefers the last successful source for the same binding, and
accepts the first currently authorized result. Its bounded affinity stores
only source preference, never tokens or capabilities. Selection dimensions
identify the existing account-token action; they do not assert OAuth scopes or
downscope a broad account token. Successful consumer responses are `no-store`
and explicitly say `state: "fetched"`, `acquisition: "normal-fetch"` and actual
expiry or `null`. Retrieval is not consumer-readiness evidence.

Loss, timeout and replacement never replay a dispatched request. Nonwinning
work is cancelled and stays counted until it settles. `pending_count`,
`stop()` and bounded `drain(timeout=...)` expose that responsibility to the
embedding's existing lifecycle owner; a false drain result is unresolved work,
not permission to forget it or kill another process. The embedding owns these
shutdown calls as well as Gateway shutdown. Unavailable, no-source and uncertain
results are non-success outcomes; none opens a login UI.

This is opt-in provider expansion plus an SDK consumer. Existing callers,
legacy lease semantics and the strict available-only profile are unchanged.
An old/rolled-back source has no matching `/token-fetch` operation; new sources
refuse a missing/foreign contract or pin before acquisition. There is no
effectful fallback to `/source-credentials`, and no legacy retirement is
scheduled. Deploy capable source receivers before opting in the local Gateway
owner. Real TLS/channel tests cover this composition, affinity, source-policy
withdrawal, unknown expiry, lost/replaced sources, deadline/caller denial and
old/misbound replies. Installed profiles, live signed-in credentials, pod
renewal and attended-auth transactions remain separately qualified.
