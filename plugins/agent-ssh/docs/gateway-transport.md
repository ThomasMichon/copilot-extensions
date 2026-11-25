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

On Windows, a concurrent reader may temporarily deny replacement of the key
file. The authorization owner retains its existing writer lock and rechecks the
managed content before each retry, allowing up to one additional second of
backoff for native access/sharing errors from replacing that exact file.
Changed content, another destination, staging errors and other failures are
not retried; persistent refusal remains an error. No permissions are repaired
and no publication or directory floor is reset.

The explicit native test starts an isolated, non-elevated/current-user OpenSSH
server with synthetic keys and strict host pinning. It proves real login,
forwarding denial, old-key rejection, re-admission and inbound withdrawal under
the same server process, then stops its owned fixture. This is local native
consumer evidence, not a live fleet enrollment or installed connector proof.

## Local native identity preparation

The independently acquired first-login entry can prepare its receiver's client
and host keys through `agent_ssh.gateway_identity.prepare(directory, ssh_keygen,
machine=..., user=...)`. Both paths are explicit local references selected by
trusted code, never executable or private-key references accepted from a
synchronized handoff. The dedicated directory must have an existing parent.

The same module has a bounded local command:

```text
python -m agent_ssh.gateway_identity --directory <absolute-private-directory> --ssh-keygen <absolute-executable> --machine worker-a --user your_user
```

It creates distinct Ed25519 keys, bound to the named machine and current login.
The existing authorization owner protects the directory and validates all
reused files. Newly generated Windows key files have their ssh-keygen-assigned
ACLs replaced with the current-user/SYSTEM-only ACL before publication.
Existing nonprivate, linked, foreign or mismatched files refuse without
rotation or permission repair. Interrupted generation preserves its private
key; a missing public sidecar is derived from that key, never replaced with a
new identity. Each completed role's public pin is persisted before proceeding
to the next role; loss of that established private key, even together with its
public sidecar, refuses instead of generating a replacement. Interrupted first
creation may finish a role that has not yet been pinned. Invalid partial keys
remain an explicit refusal. Content validation and generation share the writer
lock, so overlapping preparations wait rather than inspect intermediate files.

The return value supplies local file references and a separate public
`{machine,user,public_key,host_key}` descriptor. The command prints only that
descriptor; callers may exchange it through their admitted public-data owner.
Private files remain local. This operation does not select a Gateway, enroll,
install an application, start a listener, register startup or create outbound
profiles. Those actions retain their existing owners and approval boundaries.

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

An owner already consuming the authenticated `SSHDirectoryClient.watch` may
instead call `prepare_update(load(config_path), update)` with its
`DirectoryUpdate`. This inbound-only path does not stage a second directory
file, create outbound profiles, or read a target-login private client key.
It requires the notification's policy to match the selected local policy and
retains the native login-user, host-pin, dedicated-state and accepted-revision
checks before changing authorization. The native server and its configuration
remain in place as later-admitted controller keys arrive or are withdrawn.
The typed update does not authenticate its own origin: the embedding retains
the existing authenticated directory client and selected lifetime. This API
does not install that lifetime, select credentials or perform enrollment.
The selected installed `agent_ssh.gateway_sshd` advertises
`DIRECTORY_UPDATE_CAPABILITY = "gateway-native-ingress-directory-v1"` for
service-directory version 1. Resolve that declaration and the `prepare_update`
callable from the runtime chosen for execution before activating the embedding.
An older package without them is unsupported
for direct notification convergence; do not stage a remote-selected file or
start another watcher as a fallback. The original `prepare(config)` file-based
interface and native entrypoint remain unchanged.
Before starting or restarting a listener, the embedding still obtains its
current authenticated snapshot and supplies the native entrypoint's initial
directory through its existing owner. Notification preparation does not update
that input file or make a stale initial snapshot safe to replay.

Native authentication fixtures use `ssh -N -T`: the SSH client proves
public-key authentication without requesting a shell, command or subsystem.
Forwarding-denial cases use direct forwarding requests, not command shells.
This avoids Win32 OpenSSH delegating an unnecessary test `cmd.exe` to the
operator's default terminal. These fixtures do not claim headlessness of
arbitrary remote command shells.

### Embedding the Windows foreground listener

A Windows lifecycle owner can call `start(config, update=current_update)` and retain the returned `ForegroundIngress`. This uses the same native preparation and strict caller-owned Job as the command entrypoint. The current authenticated update avoids replaying an older staged directory. Omitting `update` retains the file-based preparation contract.

The selected runtime declares `FOREGROUND_CAPABILITY = "gateway-native-ingress-foreground-v1"`. An embedding must check this declaration and the `start` callable in that runtime, not a newer plugin payload. A runtime without them cannot support this embedding. The existing command syntax and configuration version remain supported; POSIX retains its direct `exec` entrypoint and does not support this Windows handle API.

The embedding calls `ready()` to observe a running child, one exact IPv4 loopback listener attributed to that child, and a bounded SSH banner exchange. It checks attribution again after the exchange. A wildcard, duplicate or foreign listener, lost process, unavailable native observation or failed banner returns false. This proves local listener readiness only. It does not prove Gateway transport, login authorization, authenticated SSH, or survival after the embedding's cleanup boundary.

The embedding calls `close()` to settle the exact retained Job and child. Failed Job settlement preserves the handle for reconciliation. After the Job settles, a failed or interrupted child wait can resume without using the closed Job handle. Calling `close()` again after confirmed cleanup is harmless. No detached worker, registration, supervisor, retry loop, directory watcher or outbound profile is installed. The existing lifecycle owner still supplies those selected responsibilities and the independent post-caller SSH proof.

## Existing native Gateway compatibility

`agent_ssh.gateway_native.NativeGateway` adapts an already selected native
OpenSSH Gateway's public command baseline. It is independent of the optional
Fleet SDK and does not migrate that Gateway to the Fleet HTTPS service.
The installed module declares `CAPABILITY = "gateway-native-public-commands-v1"`;
an embedding selects this module and its `NativeGateway` callable from the
runtime actually chosen for execution.

The embedding supplies the previously approved native `export-client`
descriptor and a `call(tokens, input_bytes)` function bound to the selected
Gateway launcher. The callback owns authenticated management access, host
verification, process containment, deadlines, output limits and native exit
checking. An existing forwarding-only relay key cannot execute these commands.
This API does not upgrade that key, choose another credential or make normal
provider sign-in unattended. Neither a public descriptor nor a callback result
authenticates its own source.

Only the existing commands are used: `export-client --machine <selected-name>`,
`export-directory`, `status`, and, for explicit admission, `import-peers` with
one public peer on JSON stdin. The documented legacy baseline is the
schema-1 `openssh-reverse` descriptor containing `machine`, `gateway_user`,
`host_key`, `server_port: 22`, `relay_port` and a `peers` topology of
`machine`/`relay_port` rows. The directory is exactly `{"peers": [...]}`,
with each row containing `machine`, `user`, `public_key` and `host_key`.
Status must report an installed, enabled owner and an enrollment-enabled
boolean consistent with its public membership. Extra status fields are ignored.
Malformed/unsupported descriptors or directories refuse before admission;
there is no speculative capability command or effectful syntax fallback.

`inspect()` reads current public membership and verifies the selected Gateway
host pin, user, target and existing relay bindings. Additive topology is accepted;
changed or missing prior bindings are not. It validates the entire directory,
including duplicate names, undeclared members and conflicting client/host
identities. Unavailable, disabled or malformed data is an error, not empty
membership. An enabled empty Gateway can admit its first peer.

`enroll(peer)` is an explicit admission operation, not a notification handler.
The embedding must first journal its original request and obtain the existing
Gateway owner's admission authority. An identical existing peer returns a
read-only observation; different login/key bindings refuse without rotation.
A new peer is imported once and then read back with the previous members
preserved. Failure after submission raises `AdmissionUncertain`, retaining
the cause. Preserve that original request and reconcile with
`enroll(peer, existing_only=True)`; this form never submits an admission and
refuses an absent identity. The adapter starts no automatic retry, watcher,
credential flow, daemon or second membership registry.

The returned `Observation` contains independent copies of public descriptor
and directory data plus `admission_submitted`. Submission does not claim that
the native owner changed state: a concurrent identical admission can make its
import a no-op. This legacy directory has no
revision counter, generation or revocation protocol: the adapter fabricates
none and does not relabel it as a Fleet `DirectoryUpdate`. Separate command
reads are not an atomic directory transaction. The native provider still owns
its mutation transaction; the embedding retains request correlation, durable
unknown-outcome fences and consumer trust/rotation policy. Observation alone
proves neither target authorization convergence nor authenticated inbound SSH.
Connecting this baseline to the minimal receiver's lifetime and automatic
membership consumption remains setup integration, not a delivered journey.

### Native membership in the existing ingress owner

The selected `agent_ssh.gateway_sshd` runtime additionally declares
`NATIVE_MEMBERSHIP_CAPABILITY = "gateway-native-ingress-membership-v1"`.
An embedding checks this declaration in the runtime it will execute, then uses
the existing `load`, `prepare_update` and Windows `start` interfaces.
Configuration version 2 replaces the version-1 `policy` and `directory` paths
with `native_gateway`, the independently selected schema-1 native descriptor
described above. Every other field retains its version-1 meaning. Version 1
and its Fleet accepted-revision semantics remain supported unchanged.

The native embedding supplies the current `NativeGateway.inspect()` observation
as `update`; it does not save that public data and call the file-based CLI later.
Version 2 refuses preparation without an observation. Neither this typed value
nor synchronized configuration authenticates its own origin: the embedding
must preserve its selected authenticated management transport and descriptor.
No Fleet SDK, Fleet HTTPS credential or fabricated directory revision is
required for native preparation.

`gateway_authorization.projection_from_native` binds the descriptor and admitted
target to the selected local machine, environment and public client key.
The existing native preparation additionally verifies the actual login user
and private host key. It uses the same dedicated authorization file, private
namespace, writer lock, atomic publication and foreground listener as Fleet.
It does not create outbound profiles, modify conventional user keys, enroll
a peer or restart an already running listener.

The authorization owner retains `gateway-native-membership.json` as its local
accepted-input receipt, not a second admission directory. The receipt binds
the selected Gateway and preserves accepted peer identities and relay ports.
Its combined envelope uses the existing 4 MiB owned-file bound; the descriptor
and directory each retain their separate 1 MiB response limit.
An identical observation is a no-op; an additive admitted peer updates the key
file for the native server's next authentication. Missing or changed accepted
members, changed ports, a missing receipt or a Fleet/native namespace switch
refuse while preserving the previous authorization. This unversioned contract
cannot distinguish a stale observation from deliberate revocation or rotation,
so it cannot perform those changes implicitly. Their separately authorized
provider/consumer protocol remains future integration, not a claimed native
revision guarantee. An interrupted publication retains the accepted input;
the same or an additive observation may finish it, but an older one cannot
rewind it. These rules do not terminate existing sessions.

The embedding still owns authenticated refresh, reconnect policy, durable
startup and reverse-transport custody. This adapter does not install a minimal
receiver or complete the controller-prepared one-command bootstrap.

## Windows native executable prerequisites

The dtssh transport's portable OpenSSH preparation is also callable without
installing dtssh, logging in, changing PATH, registering startup or starting a
listener. From an independently acquired agent-ssh payload:

```powershell
. .\transports\dtssh\scripts\openssh-prerequisites.ps1
$tools = Install-AgentSshOpenSSH -Destination (Join-Path $env:LOCALAPPDATA 'my-receiver-openssh')
```

The existing parent must be an ordinary local directory. The result names
`Ssh`, `Sshd` and `SshKeygen` explicitly; callers select those paths rather than
relying on a PATH update. Missing tools are prepared from the architecture's
SHA-256-pinned Win32-OpenSSH `10.0.0.0p2-Preview` archive in private staging,
with bounded download/extraction and atomic directory publication. Existing
complete selections are reused offline, not upgraded. Incomplete directories
and reparse paths refuse without removal or replacement. The dtssh installer
uses this same prerequisite owner for its listener and identity-validation
tools instead of maintaining separate download/extraction paths.

Reuse checks the portable shared library and shell/SFTP companions. The server
version resource selects the split-server requirements: `sshd-session.exe` for
9.8 or newer and `sshd-auth.exe` for 10.0 or newer. Older layouts do not acquire
requirements for newer split-server binaries. Paths must be drive-qualified;
UNC spellings, including mixed separators, are rejected before filesystem work.

This is executable preparation only, not a native identity, admission or
service installer. The trusted receiving entry still owns local key preparation,
the existing application installer and the selected lifecycle/authentication
owners. Synchronized handoff data must not supply this script or choose code,
release URLs, executable paths or their trust policy.

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
