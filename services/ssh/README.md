# Gateway-registered SSH service

`agent-ssh-service` supplies authenticated machine enrollment, a persistent
public SSH directory and bounded onboarding notifications. It registers through
the existing Fleet `ServiceConnector` with channel-derived caller context;
there is no independent HTTP listener, issuer, fleet controller or scheduler.
It can run as a foreground process or inside a container without Copilot,
marketplace hooks, a source checkout or a knowledge repository.

The registered channel is the **control plane** of the SSH mesh. An optional
Linux native OpenSSH relay runs as an owned child of this same foreground host.
It is a separate TCP listener, not a generic TCP extension to the HTTP Gateway.
Target sshd, reverse connectors, managed installation, provider enrollment,
deployment and preserving migration retain their existing owners and require
separate qualification.

## Selected policy and admission

The deployment owner supplies strict bounded schema-v1 `agent-ssh.service` JSON.
Required fields are `revision`, absolute `data_dir`, `fleet`, `gateway`, `slot`,
`controller`, HTTPS `gateway_origin`, nullable absolute `gateway_ca`, absolute
`proof_key_file` and `access_token_file`, the existing `GatewaySSHPolicy`
document as `policy`, and at most 32 `bindings`.

Each binding has exact `machine`, `environment`, `principal`, positive
`generation`, operator-selected `alias`, native login `user` and nullable
`reverse_port`. Targets, principals, case-folded aliases and nonnull ports are
unique. Current Gateway authentication and caller grants still apply; a body
label cannot select another identity. Only the configured principal can enroll
its own declared machine/environment/generation under configured SSH policy.
Without `enrollment_policy`, configured-binding automatic admission retains
its existing request and directory-response contract. The optional explicit
peer policy below adds a separate approval requirement; it does not replace
Gateway authentication or declared bindings.

An enrollment provides only canonical Ed25519 `public_key` and target
`host_key` (nullable without inbound service), plus its exact binding and a
stable `request_id`. No private key is received, minted, copied or distributed.
Vault provisioning and local private-key installation retain their existing
owner. Key changes require a separately selected higher enrollment generation;
retries cannot silently rotate an admitted key.

Native-relay deployments additionally require a distinct canonical Ed25519
`relay_public_key` in enrollment. It authorizes only the native relay, never
target login. Reusing another relay key or any admitted target-login key refuses
the whole enrollment transaction. Relay keys are stored under the same
principal/target/generation and original-request correlation; adding or rotating
one on an existing enrollment needs a new generation. They are not added to the
public target-login directory.

The local data directory must be dedicated and owner-private. The foreground
host creates it atomically with a protected current-account ACL on Windows,
and validates existing ownership, access grants and retained state before
reading credentials or acquiring the writer lease. Shared state is refused,
never silently adopted or repaired. New database and lease files explicitly
bind that same account even under an elevated Windows token. SQLite-created
recovery files may keep the OS's privileged default owner, but must retain
the same closed current-account/System/Administrators access grants.
The foreground host holds the canonical
single-instance lease before opening its SQLite
store. Configuration revision and per-target generation floors, exact service
ownership, public bindings and original request/content correlations survive
restart. Lower or equal-revision changed configuration refuses; removing a
binding does not reset its generation floor. A changed host-local volume or
cross-host writer transfer requires the deployment owner's separate admission.
A first-start interruption may leave only the validated canonical lease file.
That otherwise empty directory can retry through the same exclusive OS lease;
a live holder or unrelated retained contents are never displaced or adopted.

## Optional explicit peer approval

Select this additional nonsecret configuration only on a capable service:

```json
{
  "enrollment_policy": {
    "mode": "peer-approval",
    "recovery_principals": ["operator-recovery"]
  }
}
```

Recovery principals must be distinct from ordinary machine principals and
retain their existing Gateway authentication and route grants. An empty list
disables bootstrap/recovery; the service never invents an authority or acquires
a credential. A deployment can compose its selected Vault/provider owner for
that identity without giving this service private-key custody.

The running `GET /enrollment` contract identifies
`agent-ssh.enrollment-contract` version 1 and the selected policy.
`fleet_manager.ssh_approval.PeerEnrollmentClient` requires this actual peer to
advertise `peer-approval` before sending an effectful request. Its enrollment
body also carries `enrollment_policy: "peer-approval"`, enforced at the store
boundary, so a provider/configuration rollback after discovery cannot silently
admit the request under the automatic policy. There is no compatibility replay.
An older strict configuration reader rejects the optional policy; an old
service without this API or a current service in automatic mode gives an
explicit unsupported/not-selected result before the client submits anything.

For this selected mode:

| Operation | Authority and result |
|-----------|----------------------|
| `POST /enroll` | The authenticated declared requester submits its own public keys under the original request ID; HTTP 202 is durable pending state, not admission |
| `GET /enrollment/requests/<request_id>` | The requester inspects its original pending/completed outcome |
| `GET /enrollment/pending?after=<principal>` | An admitted peer or configured recovery authority inspects byte-bounded pages of pending public candidates; an unadmitted requester sees only its own |
| `POST /enrollment/approve` | An already enrolled, currently configured peer approves exact `requester`, `request_id` and `fingerprint`; self-approval and caller-supplied approver fields refuse |
| `POST /enrollment/recover` | Only a configured recovery principal may decide the exact candidate, additionally supplying `confirm: true` and a nonempty bounded `reason` |

The candidate fingerprint is SHA-256 over canonical public candidate data:
service identity, authenticated requester, complete target binding, policy
digest, and client/host/optional relay public keys. Request IDs correlate
delivery/retries separately; they do not change the public identity fingerprint.
An approval covers that exact candidate, including equivalent pending request
IDs, not another target or changed generation/key. The service derives the
approver from authenticated caller context, never from a body label.

Only activation commits public directory and relay authorization eligibility.
Pending submission does neither and sends no directory-change notification.
The decision, original request and directory update share the existing SQLite
transaction, with caller withdrawal checked before commit. Native relay writes
retain their existing post-commit reconciliation and original-request retry
semantics. A successful retry wakes directory readers to recheck a previously
committed revision; it does not mint another revision. Authentication, admission
and actual native reach remain distinct.

`agent-ssh.enrollment-result` version 1 reports `pending`, `active`, or
`superseded`. Pending state has no admission decision. An active result
identifies current public admission; a superseded completed decision remains
inspectable without claiming current admission.
An incumbent admitted before selecting this policy is reported as `retained`,
not falsely relabeled peer-approved. Selecting the policy does not rotate or
erase those existing keys. Changed pending bindings/policy refuse stale
decisions; changing an admitted key still requires a new selected generation.
Observing an unchanged admitted incumbent durably records that derived retained
outcome in the existing transaction; it does not grant admission. Equivalent
request IDs reuse an existing candidate decision instead of inventing another
revision. Caller withdrawal before commit rolls back any such receipt repair.
An inbound-enabled enrollment requires its host pin before the original request
or pending candidate is journaled, so a corrected retry can use that same request
ID. Outbound-only clients may still omit a host pin.
A relay key equal to that candidate's client key is likewise rejected before
journaling; approval cannot make that intrinsically conflicting identity valid.

Enrollment receipts include the decision's original `directory_revision`, not
an embedded global directory. Read the current directory through its existing
API. The complete receipt is byte-checked before its transaction commits, and
later directory growth cannot make that original outcome too large to retrieve.
Recovery reasons are nonblank and at most 512 characters including padding;
control characters are refused.

Pending records share the existing bounded original-request journal. Listing
returns the latest valid unapproved candidate per declared principal in
principal order, within the existing 32 KiB response bound. A nonnull `next`
cursor is the last returned principal; pass it as `after` (or
`PeerEnrollmentClient.pending(after=cursor)`) until `next` is null.
Each page reflects current authorized state; refresh from the first page for
concurrent changes. No cursor grants authority or consumes a request, and
earlier original IDs remain queryable.
Unavailable approvers, missing credentials, transport errors and locked
credential owners never trigger recovery automatically. Recovery is a separate
explicit action, not a fallback in the client.

## Gateway routes and notifications

Declare the service's namespace, for example `/ssh`, with `GET` and `POST` and
appropriate caller grants in the existing Gateway manifest. The Gateway strips
that namespace before dispatch:

- `POST /enroll`, in the default automatic mode, admits only the authenticated configured binding.
  The enrollment and its new public-directory revision commit together.
  An onboarding announcement is not proof that the new machine's listener or
  reverse connection is ready. Conflicting content under the original request
  ID refuses; an uncertain reply is reconciled using that same request ID.
- `GET /directory?after=0&wait=0` returns the current complete public snapshot.
  A nonzero `after` waits at most 25 seconds for a newer revision; unchanged
  returns HTTP 204. At most six notification waits are admitted, reserving two
  of the existing channel's eight request slots for enrollment and finite reads.
  Additional watchers retry only those read-side availability refusals with
  bounded, logged backoff until their lifetime owner stops them; they catch up
  from the same directory revision. Capacity pressure does not permanently
  retire a connected machine's notification consumer.
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
AI polling loop or a second daemon. Fleet's explicitly selected
[`serve --role ssh-client`](../../fleet-manager/docs/ssh-machine.md) supplies
that foreground consumer with fixed local agent-ssh writers and external
credential references; installation alone starts none of it.
It catches up first and advances only after
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

The foreground entry point retains one daemon-instance admission through the
registered service and any unsettled native-relay retirement. Production has
no expiry; explicitly test-owned instances use their caller's lease. The native
`sshd -D` process remains a supervised child under its original
`NativeChildCustody`, not a second daemon owner. Container images must provide
the selected UID's real passwd/home identity and a writable private state
location; changing `HOME` alone does not select the instance registry.

Credential references are loaded through Fleet's existing custody adapter.
Current access tokens are reread for each actual admission; no provider login,
token inference or ambient-auth fallback is added. The existing connector owns
TLS, JWT/DPoP verification, attachment, bounded requests and reconnect backoff.
SIGINT/SIGTERM withdraw the channel and notification waits before releasing
the local writer lease. Cancellation waits for each owned disk transaction to
settle; it cannot release the writer while an orphaned worker still commits.
Repeated cancellation also waits for those transactions and channel cleanup.
Do not run two hosts against one retained volume.

This entry point is not a bespoke install/cutover command. Standalone lifecycle,
immutable image construction, selected role placement, actual native SSH,
rollout and rollback remain distinct from a running control-plane channel.

## Optional native relay

Select `relay` explicitly in the service policy with exact fields `sshd`,
`ssh_keygen`, `host_key_file` (literal absolute references), `host_public_key`
(canonical Ed25519 pin), `listen_host` (`127.0.0.1` or explicit `0.0.0.0`),
nonprivileged `port`, and `user`. It requires Linux, a non-root account matching
the actual login user, readable Linux pidfd/process/socket custody and an
owner-private host-key reference whose derived public identity matches the pin.
The deployment owner supplies OpenSSH and its platform prerequisites; startup
does not download it, create accounts, change platform sshd or mint host keys.
Omitting `relay` preserves the control-plane-only host, including Windows.

The child uses only its dedicated generated configuration and key file below
`data_dir/relay`. No platform `authorized_keys` is modified. The selected
listener must belong to the actual unreaped child before Gateway registration;
a foreign listener or an exited child never counts as readiness. Native failure
withdraws the Gateway channel. Normal stop retires and confirms the complete
owned child group before releasing the host's writer lease. Unconfirmed cleanup
retains the original process and lease in an explicit error, not a success.
The CLI stays in the foreground and retries retirement through that original
kernel custody. SIGINT/SIGTERM or repeated task cancellation cannot release
the writer early; the process reports failure only after cleanup settles.
The withdrawal handlers and original-handler state travel with unsettled
custody across the CLI handoff. Original handlers are restored only after
native settlement, never in the interval before the CLI catches that outcome.
The outer lifecycle owner must also contain descendants on abrupt host death;
for a container, that is the existing container process namespace/lifecycle.

Each admitted relay key can listen only on that machine's exact loopback
reverse port and open only currently admitted loopback peer ports.
Session channels are disabled (`MaxSessions 0`), including shell, exec and
subsystems; PTY, agent/X11, Unix-socket forwarding and tunnels are disabled too.
The current shared-account native adapter supports **bidirectional configured
bindings only**, without host-mediated guests. It refuses an unsupported
one-way or host-mediated binding rather than inventing an allowlist destination
or silently enabling a prohibited direction. The policy engine's other modes
remain available to their existing adapters.

Enrollment commits before regenerating relay authorization. A local write
failure returns an explicit availability error; retrying the original request
reconciles that same committed enrollment without rotating a key or incrementing
its revision again. Reconciliation reads the current committed snapshot under
one local writer lock, so an older concurrent callback cannot republish stale
keys. Owned-file drift refuses. A failed write between atomic key replacement
and its receipt can recover only the exact current committed projection.
Changing keys affects new authentications, not an already authenticated
connection; explicit connection retirement remains the lifecycle owner's job.
An admitted public directory still does not prove a target/reverse route is live.

`container/Dockerfile` consumes only an approved digest-pinned Python Linux
base, a complete wheel closure and an exact-version SHA-256 `requirements.lock`.
Build from a dedicated context containing those inputs, not a checkout or a
home directory. Installation is offline and hash-required; UID/GID 65532 runs
with a read-only image filesystem, bounded writable `/tmp` and an owner-private
persistent volume below `/data`.
The image provisions the selected UID's real passwd home as private writable
`/data`; an existing UID 65532 retains its name and prior-home contents.
The native-relay prerequisite check still runs first and requires its approved
base to supply the selected account and OpenSSH prerequisites already.
Mount configuration, trust and credential references separately, read-only.
No public port or Docker socket is needed
by the control-plane-only container. Native deployments use the same recipe
with `NATIVE_RELAY=true` and an approved base already containing OpenSSH,
`/bin/false`, `/run/sshd` and the selected non-root account (UID 65532).
The build refuses missing native prerequisites; no online OS-package install
is added. Mount the host-key reference read-only with the selected UID's private
permissions and configure only the explicitly selected native TCP port mapping
through the existing container owner. Never expose the reverse loopback ports.
The example uses the existing
`container.service` role provider, not a new deployment mechanism.
The recipe and artifact tests are not an actual container execution witness.
Image construction, actual native authentication/forwarding, normal Fleet
placement and preserving migration/rollback need their own evidence.
