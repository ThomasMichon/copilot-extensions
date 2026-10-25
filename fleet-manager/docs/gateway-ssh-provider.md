# Gateway SSH public-directory provider

`fleet_manager.gateway_ssh_provider.project_gateway_profiles` joins the existing
immutable `GatewaySSHPolicy` with an explicitly selected admitted public directory.
The installed `agent-ssh[fleet]` consumer exposes this as `gateway-provider`.
It feeds the existing normalized Gateway profile adapter and sole generic emitter;
there is no alternative renderer, registry, credential issuer or resident owner.

## Public directory

The bounded schema-v1 document contains `controller`, positive integer `revision`,
the exact selected `policy_digest`, and `machines`. Each row has:

| Field | Meaning |
|---|---|
| `machine`, `environment` | Exact existing policy target; Windows, Linux and WSL remain distinct |
| `alias` | Operator-selected exact SSH profile name, unique without case collisions |
| `user` | Target's declared native login user |
| `public_key` | Canonical ed25519 client authentication key, unique per logical target |
| `host_key` | Independently admitted canonical ed25519 server pin, or null without inbound service |
| `reverse_port` | Independently admitted finite Gateway port, or null without inbound service |

The schema identifier is `fleet-manager.gateway-ssh-directory`. This document
contains only public bindings, not private keys, tokens, arbitrary commands or
execution grants. The setup owner obtains it through its authenticated enrollment/
directory channel. Parsing, a digest or an `admitted` label cannot authenticate
that source; this API does not fetch or choose it.

Policy/controller mismatch, unknown targets, duplicate aliases/keys/ports,
malformed keys and missing pins/listeners for an effective inbound target refuse
the projection. The selected source must appear with the exact local public key.
A missing or different source key is an error, not initialization or replacement.

## Directional projection

The existing policy resolver remains the only direction/role/hop interpreter.
A route is emitted only when the source has effective outbound permission and
the peer has effective inbound permission under the same selected Gateway.
Peer outbound permission is not required for its inbound route. Client-only
sources need no server pin or reverse listener. Disabled/offline source policy
produces no peer routes; exclusions remain explicit.

Host-mediated source or otherwise eligible peer routes require their owning
mediation adapter and are refused here, not silently treated as native egress.
The existing reconciliation bound of32 projected profiles applies. Other
transports and controllers are not inferred or adopted.

Output includes a normal `agent-ssh.gateway-profile` configuration and its
target/controller, policy/decision/directory digests, directory revision and
exclusions. `execution_authorized` and `live_readiness_verified` remain false.
This closes provider projection, not the distinct enrollment, target-local
listener lifecycle, connector supervision or bidirectional SSH proof.

`project_gateway_authorization` selects the corresponding target-local login
keys from the same exact snapshot: target inbound and source outbound are
independent, disabled/offline targets yield no keys, and client-only sources
can authenticate without an inbound listener. It returns the selected login
user and admitted host pin for the target-local service owner to verify. The installed
[`gateway-authorization`](../../plugins/agent-ssh/docs/gateway-transport.md#target-local-login-authorization)
consumer explicitly converges its dedicated key file, with no global
authorization-file adoption, service restart or Gateway forwarding grant.
The optional installed native listener entrypoint verifies that login principal
and the actual private host key before consuming the authorization file; it
does not replace the lifecycle owner's endpoint custody or readiness contract.

## Installed consumer

Use the selected installed command, not a source-checkout helper:

```text
agent-ssh gateway-provider --policy policy.json --directory admitted.json --machine worker-a --environment linux --gateway chosen-gateway --identity-file <absolute-private-key-path> --public-key-file <absolute-public-key-path> --json
```

The default is read-only. `--apply` explicitly writes the managed local profiles
through the existing emitter. The local identity file must already exist;
the public key must match the directory. The consumer does not read/copy the
private bytes. Vault preparation/materialization stays with the selected Vault.

Before emission the consumer retains canonical public inputs and a per-target/
controller accepted directory revision outside the OpenSSH Include glob.
An older revision or equal-revision/different-directory input refuses without
replacing the current profile. Acceptance is not an applied-state receipt: if
emission fails, retry the same revision/bytes after repairing the real cause,
not by resetting the revision floor. Identical selections preserve public input
bytes and mtimes. The generic emitter still validates and replaces its fragment
using its existing syntax, permission and Include contract; profile bytes remain
the same, but the fragment's own mtime is not a no-op receipt.
Higher-revision policy withdrawal uses the same writer to remove its own peer
blocks, preserving other transports and native files.

The emitted fragment references retained registry/module/pin snapshots, never
the original directory/policy path, source checkout, anchor or plugin cache.
No automatic file sweep, network dial, enrollment, listener allocation, service
restart or private-key distribution occurs. Native authorization/revocation and
already-open sessions remain their existing owners' separate operations.

## Qualification and architecture

`fleet-manager/tests/test_gateway_ssh_provider.py` exercises the real policy
resolver, independent directions, exact controller/environment/local identity,
client-only records, strict public-directory validation and snapshot isolation.
`plugins/agent-ssh/tests/test_gateway_provider.py` runs the actual CLI/emitter,
revision rollback/mutation refusal, unchanged repeat, policy withdrawal and
native OpenSSH configuration consumption. These native filesystem cases belong
to the explicit integration tier, not a new exhaustive required-CI matrix.
The native authorization fixture additionally authenticates real SSH sessions
against an unchanged isolated server while admitted keys and inbound policy
change. Source parsing/selection still does not authenticate the directory
producer or qualify a production enrollment.

This realizes Machine Fleet's driver-selected reachability and the existing
Gateway/SSH onboarding provider phase. It preserves the transport-provider
contract, optional standalone SDK composition, existing setup/lifecycle custody
and separate execution authority. A configured local profile is not onboarding
completion, authenticated SSH or full-mesh convergence.
