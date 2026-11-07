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

### Renewable source-authorization leases

A grant may explicitly add `source_lease_seconds`, an integer from 1 through
3600. Absence preserves the existing token operation and refuses lease issuance.
This is authorization to borrow that exact signed-in source for a bounded
interval, **not an account-token expiry claim**. An older source policy reader
rejects this added field; install capable source support before adopting it and
restore the previous policy before rolling that source back.

The existing account-only listener accepts `describe-account-relay` with no
fields and replies with `contract=credential-relay.account-source-lease/v1`
and `operation=get-account-source-lease`. This read acquires no credential or
authority. Discover and authenticate the actual serving endpoint through its
existing lifecycle owner; a newer installed package is not live support.
Older listeners close without this declaration. A consumer requiring a lease
must report unsupported support, never fall back to the unrestricted listener
or an ordinary token response.

For a connection-bound lease exchange, send that same describe operation with
the single field `exchange=connection-bound/v1`. A capable listener echoes
`exchange=connection-bound/v1` alongside the contract and operation, then accepts
one account operation on that same connection. Read and validate this
declaration before sending the capability. No reconnect is permitted between
those frames; replacement of a TCP endpoint or namespace descriptor cannot
redirect the acquisition to an unnegotiated peer. An older listener closes this
read-only query without issuing credentials. The original fieldless describe
response and its EOF behavior are unchanged, as are single-operation clients.

`get-account-source-lease` uses the same exact fields and capability as
`get-account-token`. It acquires anew, rechecks policy/capability before delivery,
and returns the existing token fields plus `lease_contract`, a fresh `lease_id`,
and Unix-second `lease_issued_at` / `lease_expires_at` timestamps. Its deadline
is the earlier of request-start plus `source_lease_seconds` and grant expiry;
acquisition latency counts against it. Source failure, rotation or expiry
delivers neither a token nor a lease. The ordinary `get-account-token` response
is unchanged, including on a lease-enabled grant.

The receiving execution owner must independently enforce its auth-wait action
and retain durable native identity and uncertain operations. It must account
conservatively for request latency, suspension and clock uncertainty. This
source operation does not install that consumer, refresh a Copilot file,
pause/resume a process, create a feeder or authorize transport enrollment.
Provider-first expansion does not make an existing token-only consumer enforce
a lease. Both the ordinary token operation and declaration-less legacy
listener remain supported; no legacy operation is retired.

The standard-library client offers `acquire_source_lease(path, source=...,
account=..., host=...)`, and `exchange_source_lease(transport, fields,
capability)` for an already-admitted embedding. Every acquisition first reads
the fixed support declaration from the selected live endpoint, without sending
or reading a stored capability. An undeclared or incompatible peer is refused;
there is no ordinary-token fallback. Discovery and acquisition share one
finite timeout. The same socket descriptor, owner and peer checks used by the
ordinary token API apply to the connection carrying both frames. This SDK
requires the connection-bound capability, not merely the original
single-operation lease declaration.

The returned `SourceLease` exposes `token`, `lease_id`, `lease_issued_at` and
`lease_expires_at`. Its repr omits the token. `remaining_seconds()` uses the
earlier of the source's wall deadline and a local monotonic budget, deducting
the entire discovery/acquisition latency. Future-issued, non-finite, expired
or over-one-hour source evidence is refused. An observed clock rollback or
expiry permanently invalidates that object; fresh acquisition returns new
evidence. Source and consumer clocks must be sufficiently synchronized for
this conservative check. These objects are process-local evidence, not
persistable resume permissions or token-expiry attestations.

`exchange_source_lease_for(path, fields, capability, host=...)` additionally
checks the configured binding for an embedding. Like `exchange_for`, it uses
only the caller-supplied capability, never the worker's stored one.

The existing HTTP `/source-credentials` facade also accepts an explicit
`lease_contract=credential-relay.account-source-lease/v1` JSON field alongside
the ordinary request. The running core's `/health` advertises
`source_credentials_contract` with that `lease_contract` and `exchange=http/v1`.
Select this declaration from the intended installation before sending a lease
request; missing or incompatible support is not permission to fall back to a
token-only operation. Older cores reject the added request field. Unknown
lease contracts and a declaring-but-incompatible account listener return
`source-lease-unsupported` (HTTP 409) without ordinary-token fallback.

A lease response adds `lease_contract`, `lease_id`, `lease_issued_at`,
`lease_expires_at` and `lease_remaining_seconds` to the existing token/account/
host/installation fields. The remaining time is the native SDK's conservative
budget at serialization. Each forwarding/receiving owner must deduct its whole
exchange latency and apply the earlier wall deadline and local monotonic budget;
never restart the full source interval at a later hop or persist it as a new
authorization. Source unavailability, withdrawal or elapsed budget returns no
credential. Requests without `lease_contract` retain the exact token-only
response. No old form is retired. HTTP metadata declares provider support, not
a signed-in source, file renewal or execution-owner enforcement.

The Bridge publishes `account-relay-port.json` beside the policy, containing
only the actual loopback port, process ID and generation. This dedicated listener accepts
the account operations and their fixed support declaration described above;
legacy get/fill/token actions cannot bypass it. The
embedding must authenticate the selected local process and remote SSH peer,
and keep any forwarded listener private. Discovery is not identity by itself.
Bridge shutdown withdraws its discovery and cancels in-flight operations.
Generation cleanup never removes another process's published endpoint.
Publication and conditional withdrawal use the same cross-process OS file
mutex. That mutex is not a lease, role grant, service owner or ownership record;
the existing installed Bridge lifecycle retains custody. Account listener
activation remains inside the current private-cutover grant.

Each provider is explicitly pinned to its configured GitHub CLI account and
host. Account-bound acquisition ignores ambient token/host environment
overrides, never switches the global active account, and acquires anew for
every operation. No interactive login, account-token cache or persistent token
store is introduced. Use independent providers/grants for different accounts.

### Bridge rollout and recovery

Bridge's normal installer/update and private `StagedRelay` lifecycle own this
listener. Preparation reserves a new loopback socket with admission closed;
activation publishes its PID/port/generation only after the existing private
cutover grant. Compatible draining keeps the previous generation authoritative
until that boundary; no new relay owner or process-wide lease is introduced.

There is no connection-level handoff or promise of uninterrupted renewal.
Withdrawal first closes admission, cancels tracked acquisitions and conditionally
removes only its own publication. Shutdown allows at most 20 seconds for task
settlement, 20 for listener closure and 2 for publication withdrawal; unconfirmed
withdrawal remains a failure for Bridge's existing bounded takeover/recovery
owner, not permission to overlap an unknown owner. Legacy admission closes
before awaiting account shutdown, including its failure paths. Process termination remains attributable to the
existing Bridge lifecycle, never a port number or process name alone.

After endpoint replacement or crash, the embedding's installed transport owner
must observe the new source PID/birth/generation and reconcile its connection.
A worker rereads its selected transport/capability per operation; a namespace
descriptor is likewise read anew. An in-flight operation can fail closed during
replacement. It is not automatically replayed, made anonymous or supplied a
cached credential; a later normal caller operation can acquire through the
new endpoint. Recovery time depends on the embedding's bounded reconnect
schedule and valid source authentication, not a zero-interruption claim here.

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

`agent_index.source_account_relay.token_provider(path, source=..., account=...)`
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
The standard-library consumer ships inside the existing `agent-index` core
wheel. A standalone host keeps its ordinary `agent-index-service[native]`
profile and unchanged Requires-Dist closure. There is no extra SDK wheel,
classic broker import or private copied SDK.
The standalone HostConfig accepts the same relay reference and preserves its
existing loopback-only API/engine listener contract.
The maintained private core API also exposes a fixed `POST /source-credentials`
façade for an existing authenticated Gateway. It requires the actual installed
backend identity header and the caller's own exact capability; it never reads
the native worker's stored capability on that caller's behalf. Passive/draining
instances refuse. Gateway integrations retain their own frontdoor admission and
forward neither that admission token nor arbitrary URLs/REST paths.
Core corpus configuration also rejects explicitly null/invalid relay references
and simultaneous protocol declarations instead of falling back to native auth.
Index credential selection pins the intended connector destination before
acquisition: Git HTTPS uses port 443 and must match the approved provider host; the
public GitHub connector accepts only a `github.com` provider binding.
An enterprise-host binding is not silently sent to the public API.

`exchange(transport, fields, capability)` is the same fixed operation for a
trusted embedding. It does **not** authenticate the embedding's HTTP caller;
that caller must present its exact source/account/recipient capability.
An application role or ordinary search permission alone is insufficient.

Transport enrollment, durable Windows connection availability, deployment
roles and orchestrator configuration belong to their existing owners. This
SDK does not install a tunnel, launch a feeder, enroll an identity or claim a
desired declaration is a live service.
For a bridged container namespace, host loopback is not pod loopback. An
explicit `transport: {socket_descriptor: /run/credential-relay-channel/active.json,
owner_uid: <verified SSH owner UID>}` selects a read-only-mounted stream-local
endpoint descriptor. Its filesystem owner, socket inode/device and Linux peer
credentials must agree before a capability is transmitted. The transport owner
must publish that endpoint and admit access only to the declared consumer UID;
this client does not widen SSH listener policy, invent ownership or create an
ad-hoc proxy. A genuine namespace roundtrip remains deployment evidence.

Required unit tests cover descriptor owner/mode, stale or replaced socket inode,
and peer-UID denial before any capability bytes are sent. A separate non-root
Linux integration lane runs a real Unix-socket exchange when
`AGENT_INDEX_TEST_NAMESPACE_RELAY=1` is explicitly selected; it does not replace
an adopter's actual shared-pod transport qualification.
