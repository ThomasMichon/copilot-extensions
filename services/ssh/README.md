# Gateway-registered SSH service

`agent-ssh-service` supplies authenticated machine enrollment, a persistent
public SSH directory and bounded onboarding notifications. It registers through
the existing Fleet `ServiceConnector` with channel-derived caller context;
there is no independent HTTP listener, issuer, fleet controller or scheduler.
It can run as a foreground process or inside a container without Copilot,
marketplace hooks, a source checkout or a knowledge repository.

This is the **control plane** of the SSH mesh. Native OpenSSH remains its data
plane. Registering this service does not make the HTTP Gateway a generic TCP
tunnel, install a target sshd or replace an existing reverse connector.
Native relay/container integration, managed installation, provider enrollment,
deployment and preserving migration require their separate qualification.

## Selected policy and automatic admission

The deployment owner supplies strict bounded schema-v1 `agent-ssh.service` JSON.
Fields are `revision`, absolute `data_dir`, `fleet`, `gateway`, `slot`,
`controller`, HTTPS `gateway_origin`, nullable absolute `gateway_ca`, absolute
`proof_key_file` and `access_token_file`, the existing `GatewaySSHPolicy`
document as `policy`, and at most 32 `bindings`.

Each binding has exact `machine`, `environment`, `principal`, positive
`generation`, operator-selected `alias`, native login `user` and nullable
`reverse_port`. Targets, principals, case-folded aliases and nonnull ports are
unique. Current Gateway authentication and caller grants still apply; a body
label cannot select another identity. Only the configured principal can enroll
its own declared machine/environment/generation under configured SSH policy.

An enrollment provides only canonical Ed25519 `public_key` and target
`host_key` (nullable without inbound service), plus its exact binding and a
stable `request_id`. No private key is received, minted, copied or distributed.
Vault provisioning and local private-key installation retain their existing
owner. Key changes require a separately selected higher enrollment generation;
retries cannot silently rotate an admitted key.

The local data directory must be dedicated and owner-private. The foreground
host holds the canonical single-instance lease before opening its SQLite
store. Configuration revision and per-target generation floors, exact service
ownership, public bindings and original request/content correlations survive
restart. Lower or equal-revision changed configuration refuses; removing a
binding does not reset its generation floor. A changed host-local volume or
cross-host writer transfer requires the deployment owner's separate admission.

## Gateway routes and notifications

Declare the service's namespace, for example `/ssh`, with `GET` and `POST` and
appropriate caller grants in the existing Gateway manifest. The Gateway strips
that namespace before dispatch:

- `POST /enroll` automatically admits only the authenticated configured binding.
  The enrollment and its new public-directory revision commit together.
  An onboarding announcement is not proof that the new machine's listener or
  reverse connection is ready. Conflicting content under the original request
  ID refuses; an uncertain reply is reconciled using that same request ID.
- `GET /directory?after=0&wait=0` returns the current complete public snapshot.
  A nonzero `after` waits at most 25 seconds for a newer revision; unchanged
  returns HTTP 204. At most six notification waits are admitted, reserving two
  of the existing channel's eight request slots for enrollment and finite reads.
  Additional watchers retry only those read-side availability refusals with
  bounded, logged backoff; they catch up from the same directory revision.
  No cursor or
  per-client event queue is stored; reconnect reads the current snapshot and
  cannot miss a machine merely because a transient notification was lost.

Responses use `agent-ssh.service-directory` version 1 with the existing
`policy` and `fleet-manager.gateway-ssh-directory` as `directory`.
They are bounded by the Gateway handler's 32 KiB response contract. Policy,
duplicate key, missing inbound pin and output-limit errors roll back enrollment
rather than publishing an unusable directory. Public bindings are not execution
or live-readiness permits.

`fleet_manager.ssh_directory.SSHDirectoryClient` uses the existing authenticated
`ServiceCaller` for enrollment and finite notification waits. Its `watch`
method belongs inside the machine's **existing Fleet lifetime owner**, not an
AI polling loop or a second daemon. It catches up first and advances only after
the explicit synchronous local consumer completes. Local convergence failures
stop the watch visibly; it does not hide a failed write or replay a remote
effect. The local owner still validates controller, policy, target, local key
and accepted revision before applying anything.

Profile rebuilding updates routes and pins. Target-local login authorization
must separately update sshd's admitted peer keys through the existing
agent-ssh authorization owner; a profile-only notification consumer is not a
completed SSH mesh.

## Foreground hosting

Install the wheel and its complete dependency closure through the selected
artifact flow, then the existing lifecycle/container manager invokes:

```text
python -I -m agent_ssh_service --config <absolute-service-policy.json>
```

Credential references are loaded through Fleet's existing custody adapter.
Current access tokens are reread for each actual admission; no provider login,
token inference or ambient-auth fallback is added. The existing connector owns
TLS, JWT/DPoP verification, attachment, bounded requests and reconnect backoff.
SIGINT/SIGTERM withdraw the channel and notification waits before releasing
the local writer lease. Cancellation waits for each owned disk transaction to
settle; it cannot release the writer while an orphaned worker still commits.
Do not run two hosts against one retained volume.

This entry point is not a bespoke install/cutover command. Standalone lifecycle,
immutable image construction, selected role placement, actual native SSH,
rollout and rollback remain distinct from a running control-plane channel.

`container/Dockerfile` consumes only an approved digest-pinned Python Linux
base, a complete wheel closure and an exact-version SHA-256 `requirements.lock`.
Build from a dedicated context containing those inputs, not a checkout or a
home directory. Installation is offline and hash-required; UID/GID 65532 runs
with a read-only image filesystem, bounded writable `/tmp` and an owner-private
persistent volume below `/data`. Mount configuration, trust and credential
references separately, read-only. No public port or Docker socket is needed
by this control-plane container. The example uses the existing
`container.service` role provider, not a new deployment mechanism.
The recipe and artifact tests are not an actual container execution witness
or proof of the separately required native OpenSSH relay image.
