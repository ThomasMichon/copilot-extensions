# Gateway SSH profiles

`agent-ssh gateway-profile selected.json` is the supported outbound profile
adapter for an existing SSH Gateway. It is part of the installed `agent-ssh`
package, not the prototype under `examples/`. It uses the ordinary profile
writer, managed Include and source audit; there is no additional Gateway,
installer, proxy process or supervisor.

The caller explicitly supplies an already-approved Gateway SSH alias, the
selected peers' finite Gateway loopback ports, their login users and their
approved public host keys. Normal `ssh <peer-name>` then uses OpenSSH
`ProxyJump` through that Gateway and authenticates the actual peer behind its
reverse listener. The Gateway's own provider/profile remains responsible for
reaching and authenticating the Gateway.

## Explicit input

The input is strict JSON, at most 1 MiB and 256 machines:

```json
{
  "schema": "agent-ssh.gateway-profile",
  "version": 1,
  "gateway": "my-gateway",
  "identity_file": "~/.ssh/gateway-identity",
  "machines": [
    {
      "name": "my-build-machine",
      "user": "builder",
      "reverse_port": 23107,
      "host_key": "ssh-ed25519 <approved-base64-public-key>"
    }
  ]
}
```

Replace the key placeholder with the target's independently approved complete
ed25519 public host key. This command never obtains a pin from an unauthenticated
scan, accepts a changed key implicitly or reads a private key. `identity_file`
must identify an existing local file, with an absolute path after `~`
expansion; the existing key's permissions and authentication are checked by
OpenSSH when it is eventually used. Fetching that key from an approved Vault is
a separate prerequisite, not a side effect of rendering profiles.

Gateway and peer names must be distinct exact aliases, including case-insensitive
comparison. Peer ports must be distinct positive TCP ports already admitted by
the forwarding/enrollment owner. Port zero, wildcard aliases, shell arguments,
duplicate JSON fields, unknown fields and malformed keys are refused.
Each supplied peer is an explicitly selected outbound route; this adapter
neither infers inbound permission nor invents undeclared peers.

An explicitly empty machine list withdraws this transport's peer blocks. It
does not revoke keys, close listeners, terminate connections or remove any
other transport's profiles.

```text
agent-ssh gateway-profile selected.json --json
agent-ssh doctor --json
ssh my-build-machine
```

The first command emits `state: configured`, `connection_attempted: false` and
`enrollment_performed: false`. It performs OpenSSH's local `-G` syntax check,
but makes no SSH connection. Syntax/configuration/I/O failure exits 2 with an
explicit diagnostic, not a successful empty mesh. `doctor` separately checks
managed-source currency and shadowing; neither command proves live reachability.
`--config-d` and `--ssh-config` select isolated destinations for testing.

## Identity and durable inputs

Each peer uses its operator-selected name as `HostKeyAlias`, strict host-key
checking and a transport-owned pin file. Global known-hosts and DNS trust do not
add another acceptable key. Host-key updates, passwords, keyboard-interactive
authentication and agent forwarding are disabled. The peer's explicit identity
file and `IdentitiesOnly yes` preserve the separate peer-authentication boundary.
The jump connection uses the existing Gateway profile rather than copying its
private keys, provider configuration or tunnel recipe.

Before publication the adapter retains content-addressed `registry.json`,
`module.json` and `known_hosts` together under
`~/.ssh/.agent-ssh-sources/gateway-<digest>/`, outside the Include glob.
An isolated `--config-d` uses that directory's parent instead. Identical
selections reuse the same snapshot; changed selections retain the predecessor
and use a new snapshot. Changed or foreign bytes in an existing snapshot refuse
replacement of the current profile. Interrupted unpublished staging is not a
selected source.

The generic emitter remains the only writer of
`50-agent-ssh-gateway.conf` and the shared Include. After emission, the original
input, source checkout, anchor and plugin cache are not referenced by the
profile or its source audit. The installed package can likewise run without
those locations. Retained snapshots are public-key/configuration data, never
copies of the private identity file. No automatic source cleanup is performed.

## Setup boundary

The optional [Fleet provider](../../../fleet-manager/docs/gateway-ssh-provider.md)
now supplies this adapter from the existing directional policy and an explicitly
selected admitted public directory. Its read-only default and explicit
`--apply` entry point preserve local identity, directory revision and the
existing emitter; raw public data still cannot grant enrollment or execution.

This implements the outbound transport/profile part of
[Gateway/SSH onboarding](../../../efforts/active/gateway-ssh-fleet-onboarding/README.md),
not its remaining setup executor. Gateway selection, Vault key preparation,
public-key enrollment, reverse-connector ownership, target sshd readiness,
forwarding grants, bidirectional proof and automatic admitted-key catch-up
remain with their existing setup and lifecycle owners. Do not call a machine
onboarded merely because its profiles render.

The adapter serves the machine-fleet vision's
[driver-selected reachability](../../../visions/machine-fleet/README.md) and
the agent-ssh [transport-provider contract](transport-provider-contract.md):
one generic renderer, independently selected providers, explicit effects,
preserved peer identities and no duplicate resident owner.
