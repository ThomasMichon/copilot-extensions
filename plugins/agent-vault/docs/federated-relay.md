# Federated account-source relay

The existing Vault service can optionally host an authenticated outbound
Controller-service channel. It consumes a Controller-owned local Gateway
conduit; it does not create a tunnel, second daemon or account-relay listener.
Local-only Vault defaults and KeePassXC activation remain unchanged.

This is the first functional source slice of the
[federated-relay plan](../../../efforts/active/vault-service/federated-relay.md).
The `federated-relay` Python extra supplies the SDK and credential custodian.
Native installer profile selection and installed-runtime qualification remain
separate plan gates; the existing native `base` and `ssh` profiles do not yet
select this extra. Do not copy source into a deployed runtime to enable it.

## Installation-local selection

The service reads `federated-relay.json` in its existing installation home
(`AGENT_VAULT_HOME`, otherwise `~/.agent-vault`) once at startup. This file is
not portable repository configuration and cannot be selected by a remote
request. Its parent and referenced policy/key files must remain under the
deployment owner's native access controls. Missing means disabled. To explicitly
disable, use only `schema`, `version` and `"mode": "disabled"`. Invalid selected
configuration or missing optional dependencies fails visibly before listener
publication, not as a silent local-only fallback.

Example shape (replace every reference with an absolute native path):

```json
{
  "schema": "agent-vault.federated-relay",
  "version": 1,
  "mode": "federated-relay",
  "fleet": "fleet-a",
  "gateway": "gateway-a",
  "slot": "account-source",
  "installation": "source-installation",
  "service": "vault-available-v1",
  "prefix": "/api",
  "gateway_origin": "https://gateway.example",
  "conduit_file": "/private/controller/state/gateway-conduit.json",
  "gateway_ca": "/private/trust/gateway-ca.pem",
  "proof_key_file": "/private/keys/controller-proof.pem",
  "account_policy_file": "/private/auth/account-policy.json",
  "auth": {
    "kind": "az-login",
    "resource": "gateway-admission-resource",
    "tenant": "selected-tenant"
  },
  "grants": [{
    "principal": "caller-a",
    "source": "repo-a",
    "account": "account-a",
    "recipient": "worker-a",
    "host": "github.com",
    "action": "get-github-token"
  }]
}
```

`gateway_ca: null` uses platform trust. The proof reference is an existing
unencrypted P-256 PEM key; no key generation, enrollment or role grant occurs.
`auth` selects either the existing `AzLoginSource` with one exact resource and
tenant, or `{"kind":"machine","principal":"service-a","audience":"gateway-a"}` for an already
admitted machine proof identity. No arbitrary provider import, command, issuer
or token value is accepted. Bootstrap admission is independent of the
not-yet-established relay. The Azure provider uses its existing native signed-in
session/cache; it never runs interactive `az login`.

The Gateway separately admits the slot, route, proof identity and consumer.
The source handler grants above bind the channel-derived caller to exact
source/account/recipient/host/action fields. The existing account custodian
rereads its own policy and the caller's capability for every request. Channel
admission is not a source capability or downscoped token. This slice implements
only the existing `gh-auth`/`get-github-token` account-source operation; source
lease expiry does not attest token expiry. See the
[source exchange](../../../fleet-manager/docs/gateway-account-source.md).

## Conduit and lifetime

The descriptor has schema `fleet-manager.gateway-conduit`, version `2`, and
exact fields `gateway_origin`, `connect_host`, `connect_port`, `generation`,
`state`, `expires_at`. Ready observations must select the configured canonical
origin, a literal loopback address, a valid port, a nonempty generation and a
finite expiry no more than ten seconds ahead. Each observation also receives a
monotonic cap of at most nine seconds. Missing, stale, unavailable, foreign or
unsupported observations disconnect the channel without direct-network fallback.
The Controller refreshes discovery; Vault checks it in the existing service
loop, not in CLI/ping/render callbacks.

Only the TCP route changes. HTTPS origin, HTTP Host, TLS server name and proof
target stay canonical; CA and hostname verification remain enabled.
Environment proxies, redirects and transparent request replay remain disabled.
An active generation advertises one explicitly selected service, meaning
readiness to receive requests, not proof a credential is cached or usable.

The current Vault generation owns channel start, bounded reconnect and
withdrawal before writer retirement/shutdown. Five consecutive channel failures
exhaust a generation's retry budget with 0.5-4 second backoff; an attachment of
at least sixty seconds resets that budget. A changed conduit generation or
explicit service restart permits a new budget. Existing daemon inactivity and
password-lifetime settings are unchanged. Configure the normal persistent
service mode when continuous relay availability is intended.

`ping` adds only `federated_relay.state` and `channel_attached`; it performs no
discovery or credential acquisition. States distinguish disabled, waiting for
the current Vault generation, waiting for conduit, connecting, attached,
reconnect exhausted and stopped. Provider/consumer readiness is not inferred.
Lost accepted requests retain the channel's unknown-outcome behavior; reconnect
does not resend them. Durable attended-auth transactions and multi-source
cache-first selection remain distinct plan gates, not automatic login popups.
