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

## Target-local login authorization

The same installed `agent-ssh[fleet]` consumer exposes:

```text
agent-ssh gateway-authorization --policy policy.json --directory admitted.json --machine worker-a --environment linux --public-key-file <absolute-public-key-path> --state-dir <dedicated-absolute-directory> --json
```

Preview reads only public inputs and writes nothing. `--apply` explicitly
publishes `authorized_keys.gateway` inside that dedicated directory. The
selected target/controller/login user remain bound to the directory; it cannot
be reused for another identity. A separately installed target-local sshd must
explicitly select the returned path as its `AuthorizedKeysFile` and verify its
login-user/environment binding. This command neither configures nor starts
that service and never modifies the user's conventional `authorized_keys`.

The target needs effective inbound permission, and each admitted source needs
effective outbound permission under the same controller. The target's outbound
permission and a source's inbound permission are independent. Client-only
sources need no listener/host pin. Unsupported host mediation refuses for a
selected target; an ineligible source grants no key. Every login entry uses
`restrict,pty`: normal commands and terminals are permitted, but TCP, agent and
X11 forwarding and user RC execution are not. This is not the Gateway's
forwarding-only enrollment adapter or a host-mediated WSL grant.

Publication requires an ordinary owner-private directory and unlinked files.
Windows creates the final protected user/SYSTEM DACL before publishing public
authorization data and verifies native descriptors on reuse; POSIX requires
current-user ownership and modes700/600. Foreign files, changed owned bytes,
missing owner binding, links and unsafe permissions refuse without repair.
The existing accepted-directory revision helper fences rollback and
equal-revision mutation before atomic replacement. An interrupted write retains
the floor and permits the exact accepted retry. Identical authorization leaves
file bytes and mtime unchanged. Newer admitted keys or withdrawal take effect
on the sshd's next authentication without restarting it; existing sessions are
not revoked by a key-file replacement.

The explicit native test starts an isolated, non-elevated/current-user OpenSSH
server with synthetic keys and strict host pinning. It proves real login,
forwarding denial, old-key rejection, re-admission and inbound withdrawal under
the same server process, then stops its owned fixture. This is local native
consumer evidence, not a live fleet enrollment or installed connector proof.

## Native listener integration

`agent_ssh.gateway_sshd` is an installed internal service entrypoint for an
existing target-local lifecycle owner. That owner invokes the selected immutable
interpreter with `-m agent_ssh.gateway_sshd --config <absolute-config-path>`.
This is a foreground OpenSSH listener, not a second supervisor, installer,
Gateway, connector, or public cutover verb. It is installable as part of
`agent-ssh[fleet]` without a Copilot plugin host.

The protected local JSON configuration has exactly these fields:

```json
{
  "schema": "agent-ssh.gateway-native-ingress",
  "version": 1,
  "policy": "/absolute/durable/policy.json",
  "directory": "/absolute/durable/admitted.json",
  "machine": "worker-a",
  "environment": "linux",
  "public_key_file": "/absolute/local/identity/id_ed25519.pub",
  "host_key_file": "/absolute/local/host/id_ed25519",
  "state_dir": "/absolute/dedicated/native-ingress",
  "sshd": "/absolute/native/sshd",
  "ssh_keygen": "/absolute/native/ssh-keygen",
  "port": 24107
}
```

Paths are platform-native literal absolute references, not command templates;
use the host's native environment (`windows`, `linux`, or `wsl`). The port is
an example of the existing owner's explicitly selected nonprivileged loopback
endpoint, never a default or an allocation by this adapter. The owner still
owns endpoint selection/custody, stable installed launchers, startup/update,
readiness, cutover and rollback. Startup refuses an already-bound endpoint
before changing authorization. This bounded native bind preflight is **not an
atomic reservation**: the owner must serialize selection and verify the actual
native listener before publishing it. In particular, Windows OpenSSH can allow
two native processes to bind the same loopback endpoint; successful process
creation or an SSH banner alone cannot qualify endpoint ownership.

Before changing authorization, preparation verifies the actual OS login user,
the native environment, the selected private host key's derived public value
against the admitted pin, and `sshd -t`. Windows additionally requires a
non-elevated token and binds `AllowUsers` to the actual qualified Windows login
principal; it does not guess a domain from environment variables. POSIX requires
the current non-root account. The private state directory remains bound to that
target, controller, principal and host key. Changed/foreign configuration is
preserved and refused, not silently adopted. Conventional user key files,
system sshd configuration and system PID files are untouched.

The native config permits only public-key login for the selected principal,
with strict modes and all forwarding disabled. A POSIX entrypoint execs native
sshd; Windows retains the native child and descendants in the shared
kill-on-owner-close Job with breakaway prohibited. Native execution changes
working directory to durable state, never the source checkout. Only an
ancestor Windows OpenSSH descriptor carrier is removed from new native child
environments; unrelated identity and transport variables remain unchanged.
Windows inetd-over-pipes is not a supported substitute for this listener.

The same lifecycle owner calls `prepare(load(config_path))` when it accepts a
new admitted directory. That uses the existing monotonic authorization writer
without restarting the native server. This module does not fetch the directory,
create a poller, or claim that event delivery is already integrated. The native
fixture runs the actual package entrypoint with synthetic keys,
authenticates through strict host pinning, proves key replacement and inbound
withdrawal under the same server, refuses stale input and a changed host pin,
preserves repeat mtimes/unrelated files, and confirms listener exit after its
owner stops.

Native authentication fixtures use `ssh -N -T`: the SSH client proves
public-key authentication without requesting a shell, command or subsystem.
Forwarding-denial cases use direct forwarding requests, not command shells.
This avoids Win32 OpenSSH delegating an unnecessary test `cmd.exe` to the
operator's default terminal. These fixtures do not claim headlessness of
arbitrary remote command shells.

## Remaining setup integration

This implements the outbound transport/profile part of
[Gateway/SSH onboarding](../../../efforts/active/gateway-ssh-fleet-onboarding/README.md),
and target-local login-key convergence/native consumption, not the full setup executor.
Gateway selection, Vault key preparation,
public-key enrollment, reverse-connector ownership, target sshd readiness,
forwarding grants, bidirectional proof and automatic admitted-key catch-up
remain with their existing setup and lifecycle owners. Do not call a machine
onboarded merely because its profiles render.

The adapter serves the machine-fleet vision's
[driver-selected reachability](../../../visions/machine-fleet/README.md) and
the agent-ssh [transport-provider contract](transport-provider-contract.md):
one generic renderer, independently selected providers, explicit effects,
preserved peer identities and no duplicate resident owner.
