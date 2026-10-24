# Opt-in account credential relay

This is distinct from the scoped JWT/PoP Gateway broker. An account token is
**not** a downscoped repository credential, and a capability lifetime does not
attest that token's expiry. No token expiry is invented. The Bridge remains
the credential source owner; an embedding's existing lifecycle owns transport.

## Source policy

Explicitly install `account-relay.json` in the selected Bridge configuration
directory, then reconcile that Bridge through its normal lifecycle. Missing
configuration leaves the existing relay unchanged. Never forward the legacy
general-purpose listener to an account-only worker.

```json
{
  "schema": "credential-relay.account-policy",
  "version": 1,
  "providers": {
    "selected-account": {
      "kind": "gh-auth",
      "account": "example-user",
      "host": "github.com"
    }
  },
  "grants": [{
    "provider": "selected-account",
    "source": "github:example/project",
    "account": "example-user",
    "recipient": "index-worker",
    "host": "github.com",
    "action": "get-github-token",
    "capability_file": "/owned/runtime/secrets/index-worker",
    "expires_at": 1900000000
  }]
}
```

Paths are absolute references, not inline credentials. The source/lifecycle
owner approves and securely provisions a random capability of at least 32
characters, with private directory/file permissions, to that exact recipient.
This library neither mints nor distributes it. Capability rotation and policy
withdrawal are reread before each operation and rechecked before delivery.
Use different capabilities for different recipient/source/account bindings.
Grant expiry is a capability limit, **not account-token validity**.

The Bridge publishes `account-relay-port.json` beside the policy, containing
only the actual loopback port, process ID and generation. This dedicated listener accepts
only `get-account-token`; legacy get/fill/token actions cannot bypass it. The
embedding must authenticate the selected local process and remote SSH peer,
and keep any forwarded listener private. Discovery is not identity by itself.
Bridge shutdown withdraws its discovery and cancels in-flight operations.
Generation cleanup never removes another process's published endpoint.

Each provider is explicitly pinned to its configured GitHub CLI account and
host. Account-bound acquisition ignores ambient token/host environment
overrides, never switches the global active account, and acquires anew for
every operation. No interactive login, account-token cache or persistent token
store is introduced. Use independent providers/grants for different accounts.

## Worker configuration

```json
{
  "schema": "credential-relay.account-worker",
  "version": 1,
  "transport": {"host": "127.0.0.1", "port": 19999},
  "bindings": [{
    "source": "github:example/project",
    "account": "example-user",
    "recipient": "index-worker",
    "host": "github.com",
    "action": "get-github-token",
    "capability_file": "/owned/runtime/secrets/index-worker"
  }]
}
```

`fleet_manager.account_relay.token_provider(path, source=..., account=...)`
returns a renewable callback. It reads policy/capability anew and performs one
finite request. Local loopback or explicit RFC1918 IPv4 endpoints and absolute
Unix sockets are supported; URLs, public addresses, redirects, arbitrary
actions and ambient-auth fallback are not. Response account and host must match.
Failures are value-free; disconnect, missing source, denial and expiry never
make an anonymous request or replay automatically.

For agent-index, opt in with `auth: {account: example-user, relay:
/owned/runtime/worker.json}`. `auth.gateway` remains the separate scoped
broker, and configuring both modes is rejected. `git` sources still require
an explicit repository path and HTTPS remote materialization policy.
Install the explicit `agent-index[relay]` extra for this standard-library SDK,
or compose `agent-index-service[native,relay]` for a standalone native host.
Selecting relay does not select the scoped broker's authentication extras.
The standalone HostConfig accepts the same relay reference and preserves its
existing loopback-only API/engine listener contract.

`exchange(transport, fields, capability)` is the same fixed operation for a
trusted embedding. It does **not** authenticate the embedding's HTTP caller;
that caller must present its exact source/account/recipient capability.
An application role or ordinary search permission alone is insufficient.

Transport enrollment, durable Windows connection availability, deployment
roles and orchestrator configuration belong to their existing owners. This
SDK does not install a tunnel, launch a feeder, enroll an identity or claim a
desired declaration is a live service.
