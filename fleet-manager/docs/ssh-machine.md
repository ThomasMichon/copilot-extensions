# Foreground SSH directory consumer

An explicitly selected Fleet lifetime can consume the registered SSH service:

```text
fleet-manager serve --role ssh-client --config <absolute-local-config.json>
```

The selected artifact closure includes `fleet-manager[ssh-client]`, the normal
agent-ssh distribution and their declared dependencies. This is a foreground
role managed by the existing user-logon/service lifecycle owner, not a new
installer, daemon launcher, controller or AI polling task. Ordinary thin-client
installation, planning and diagnostics remain inert. General-purpose node
hosting, automatic setup selection and managed install/update/uninstall are not
implemented by this role.

An explicitly selected [installed host](gateway-host.md#selected-ssh-participant)
can embed this same consumer through the existing Source application lifecycle.
It requires both `gateway-host` and `ssh-client`; this does not change the
foreground command or make ordinary thin-client installation active.

## Configuration and authority

Supply strict bounded `fleet-manager.ssh-client` JSON in one explicitly selected
version. Version 1 remains token-only. Version 2 opts into `machine-key-v1`;
missing tokens never select it implicitly. All fields in the selected form are
required; optional references use explicit `null`, not ambient defaults.

| Fields | Meaning |
|--------|---------|
| `state_dir` | Dedicated absolute owner-private directory containing only this role's canonical lease |
| `gateway_origin`, `gateway_ca` | Selected canonical HTTPS Gateway origin and nullable absolute CA reference |
| `namespace` | Selected SSH service namespace, such as `/ssh`; no query or trailing slash |
| `proof_key_file` | Absolute existing provider-owned P-256 private PEM reference, read through Fleet's credential-reference owner |
| `access_token_file` (v1 only) | Absolute existing access-token reference; required in v1 and forbidden in v2 |
| `authentication` (v2 only) | Exact record containing `profile`, `principal`, `audience`, `resource`; forbidden in v1 |
| `policy_path`, `machine`, `environment`, `controller` | Absolute local policy and exact selected logical machine/environment/controller |
| `gateway` | Existing SSH Gateway alias, not a new endpoint or an enrollment grant |
| `identity_file`, `public_key_file` | Absolute existing target-login key references; no key acquisition, export or rotation |
| `authorization_dir` | Absolute existing agent-ssh local authorization scope, or `null` only without inbound SSH |
| `config_d`, `ssh_config` | Explicit absolute local managed-fragment and root-config destinations |

Linked/reparse path references, unknown fields, relative paths and incompatible
local policy refuse. The selected target must have a configured direct Gateway
policy; offline, blocked, other-transport and host-mediated paths are not silently
turned into a direct caller. An inbound target requires its explicit local
authorization owner.
All output destinations must be outside the entire lease-only `state_dir`
tree, including normalized path aliases. Equality or nesting refuses before
credentials or state creation, so a successful first write cannot make the
same configuration invalid on restart.
Before policy/credential reads or lease creation, the existing agent-ssh adapter
also checks the writers' derived source and lock directories. The lease state
cannot contain or sit inside any profile, authorization, source or lock scope;
no second copy of those writer-owned path names is maintained by Fleet.
Installed hosting additionally excludes the entire application installation
root from those writer scopes. Local public-key bytes are bounded and parsed
through the existing canonical ed25519 validator before watch or lease creation;
they are still reread and matched to the authenticated directory at each write.
Preflight checks existing lease-state directory privacy, its lease-file shape
and absence of unowned contents without creating or acquiring anything. A missing
state directory requires an existing ordinary parent; regular files and invalid
lease state refuse before the watch or an installed selection can change.
No policy, credential, identity, CA or host configuration reference may occupy
that lease-only tree, including its canonical lock filename.
Existing output kinds and path ancestors are checked too: configuration and
authorization roots must be directories, the SSH configuration must be a file,
and no selected path may traverse an existing regular-file parent. Runtime
writers retain their own permission, lock and content checks.
The selected adapter must expose `validate_writer_destinations`; an older
partial adapter refuses before credential reads rather than skipping this check.
Writer scopes must not overlap each other or the root configuration; the
canonical fragment/root lock directories may share their existing lock scope.
An existing authorization directory must already satisfy the authorization
owner's privacy and dedicated-content checks. Preflight neither creates missing
authorization directories nor repairs existing ownership or grants.
Existing authorization bindings must match the configured target/controller,
and retained key-file receipts must remain internally consistent. Fresh
directory admission, remote login identity and monotonic revision checks remain
with the authenticated update and its original writers.
Preflight also reuses their static alias/path renderers and existing root-config
decoder, and validates the retained revision record's shape without advancing
its floor. It does not manufacture a fresh directory or an admission receipt.

Configuration selects local write scope; Gateway authentication alone does not.
The fixed code adapter is `agent_ssh.gateway_directory.apply_update`, never an
import path, executable or command supplied by a remote message. It rereads the
local policy and public identity before each write, requires the remote policy
and controller to match, and reuses the existing profile and target-login
authorization writers. No global `authorized_keys`, sshd or service settings are
changed. Profiles retain the existing route/pin and unrelated-fragment guards.

In v1, the external PEM proof key and access-token callback retain their existing
meaning. Access-token contents are reread on each actual admission through the
existing bounded credential reference.
Preparation validates that same token's readability and bounded text format,
without caching it or treating token claims as admission authority.
After trimming surrounding file whitespace, both preparation and fresh
callbacks require the caller's visible-ASCII token format, excluding internal
spaces, non-ASCII characters and DEL.

In v2, replace `access_token_file` with this explicit record:

```json
{
  "profile": "machine-key-v1",
  "principal": "caller-a",
  "audience": "https://fleet.example.test",
  "resource": "ssh-service"
}
```

`principal` and `resource` are bounded identifiers; `audience` is a nonempty
visible-ASCII string of at most 512 characters. These must match the external
Gateway enrollment, audience and selected service resource; local configuration
does not create those bindings. The reference must already contain an
unencrypted private P-256 PEM key (PKCS8 or SEC1), not a public key, JWK,
other curve or key type. Fleet imports that selected PEM as a JOSE `ECKey`
locally, after local policy/output preflight and before lease or profile effects.
It never opens an access-token source in this mode or falls back to tokens.
The existing `ServiceCaller` owns TLS and peer-profile validation, fresh Gateway
challenges and proofs binding the principal, audience, resource, actual method,
full target including query and body.

Both modes consume caller-explicit, externally provisioned references. No
provider login, enrollment, key generation/materialization/export, token minting,
cache or credential renewal is supplied. In particular, private continuity
bootstrap state is not a PEM provider interface. Key provisioning and rotation
remain external; replacing the selected signing key requires restarting the
foreground consumer.

## Lifetime, catch-up and failure

One native single-instance lease owns this configured state directory.
An incumbent is never displaced. Windows creation uses the shared protected
current-account ACL before publication, including under an elevated token;
retained private state is validated, not repaired or adopted.
The target-login writer separately retains its protected user/SYSTEM access
grants; native privileged ownership of a newly created Windows file does not
turn those closed grants into a shared authorization directory.
Windows compares the descriptor with the native SDDL spelling of the exact
current account SID, so an OS-rendered alias such as `LA` does not become a
foreign identity. This does not admit another account, group grant or owner.

The existing `SSHDirectoryClient.watch` first reads the current authenticated
snapshot, then waits for revision changes. Transport availability retries and
bounded backoff remain owned by that client. It advances only after the
synchronous local consumer completes. Existing local writers retain monotonic
revision/content floors and idempotence. Restart begins with a fresh snapshot
and revalidates those same retained floors; no second notification queue,
cursor database or topology registry is introduced.

Local convergence retains the canonical writers' filesystem and permission
operations, including their native Windows ACL helper processes. The host adds
no new per-notification process launcher; initial or changed snapshots still
pay that existing local-writer cost. This source does not claim a qualified
cold-start or tail-latency budget.

SIGINT/SIGTERM request withdrawal. Cancellation stops and settles the original
watch before restoring signal handlers or releasing its lease. Repeated
cancellation cannot detach that cleanup. The synchronous local writer cannot
be orphaned as a background thread. Embeddings can call
`ssh_machine.run(config, stop=event)` inside their existing lifetime; signal
ownership stays with the embedding unless it explicitly selects
`install_signals=True`.

An embedding may pass `on_applied=callback` to observe the completed directory
revision. This synchronous notification runs only after the existing local
transaction succeeds, including a successful unchanged snapshot on restart.
Starting the watch or a failed writer never emits it. An observer failure stops
the watch without advancing its notification position; the existing lease and
cancellation cleanup still settle. The callback observes convergence, not
enrollment, current connection health or SSH route readiness.

Replacement is explicitly stop-before-start, not a side-by-side/ZDD claim.
The existing lifecycle owner stops the old foreground role; the OS lease
excludes a replacement until exit, and the next selected role catches up from
the current directory against retained writer floors. There is no local
listener or accepted remote mutation to transfer. Temporary notification
unavailability is explicit. This role neither implements an updater nor
authorizes terminating an incumbent; compatible artifact/data selection and
rollback remain with the existing construction/lifecycle owners.

Authentication/policy refusals and local write failures remain visible and stop
this role. An unavailable dependency is an explicit missing-extra error.
The role performs no enrollment POST or uncertain-effect replay. It does not
prove a native listener, reverse tunnel, bidirectional route or full-mesh
readiness merely because a directory update was applied.

## Compatibility and selection

The selected installed `fleet_manager` package carries
`ssh-client-contract.json`: contract `fleet-manager.ssh-client-contract` v1,
configuration `fleet-manager.ssh-client` versions 1 and 2 and directory
`agent-ssh.service-directory` v1. The existing package-resource API reads this
data without discovery, credential acquisition or hosting. Read it from the
same immutable runtime selected for execution, never a newer payload or a
root-wide version stamp.
Select v2 only when this selected provider declares configuration version 2;
v1 support alone does not imply machine-key authentication. Older v1 readers
reject v2 before credential access or profile effects. New readers keep v1's
token semantics. A rollback without v2 support must refuse the machine-key
requirement before invocation, not translate it into token credentials. Neither
version is retired, and no per-request compatibility subprocess is required.

This is a provider-first expansion. Existing Gateway serving, planning,
diagnostics and default installation retain their commands and effects; no
existing setup caller automatically emits the new role. A setup/lifecycle
consumer must establish this optional contract before invoking it. An older
runtime without the descriptor has unknown/unsupported SSH hosting, not proof
that its ordinary thin-client commands are broken. Keep those commands usable
and refuse this optional role before effects; do not try it and blindly replay
some older setup command. An older parser rejects the unknown role without
reading its configuration. No legacy command is retired by this source change.
Provider rollback must retain the same pre-effect unsupported outcome rather
than silently starting another implementation.
The selected `ssh-client` closure also requires the current agent-ssh adapter's
local writer-scope preflight. An older partial dependency that lacks it refuses
this role before policy/credential reads, lease creation or any writer call.

## Qualification

`fleet-manager/tests/test_ssh_machine.py` covers strict local scope, real
profile/authorization convergence, repeated cancellation/lease custody,
restart/no-op behavior and explicit failures. The existing isolated standalone
runner prepares the declared dependency closure.
The same focused file also exercises v1 fresh-token reads, v2 mixed/incomplete
configuration and invalid-key refusals, and the real v2 consumer, TLS Gateway,
`MachineIdentityVerifier`, directory watch and local writer with synthetic PEM
keys. It verifies the directory request's full query and empty body and a finite
request through the composed caller with a nonempty body and opaque query.

`services/ssh/tests/test_machine_consumer.py` runs the installed Fleet module
outside the checkout against the real disposable TLS/JWT/DPoP Gateway and
registered SSH service. A later join updates a connected machine; an offline
consumer catches up after restart, and a second configured consumer receives
the admitted peer key. It explicitly checks that Fleet and agent-ssh resolve
from the selected runtime's installed packages, not their source trees.
The normal development preparer may still use canonical shared-library source
references, so this is not a source-free complete production-artifact proof.
POSIX uses real graceful SIGTERM; Windows deliberately
uses abrupt process termination to exercise kernel-lease recovery, not to claim
graceful Windows signal delivery.

These fixtures use synthetic credentials and isolated local destinations.
They are not a complete source-free artifact closure, adopted installation,
live rollout, provider interoperability or preserving migration/retirement
witness.
