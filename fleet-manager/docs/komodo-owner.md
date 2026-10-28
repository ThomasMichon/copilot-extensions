# Fixed Komodo substrate owner

Back to [fleet-manager](../README.md) and
[role/provider plans](role-deployment-plans.md).

`fleet_manager.komodo_owner.KomodoOwner` is an explicit Linux host SDK owner.
It executes a fixed official Core, Periphery and MongoDB Compose profile,
independently of Core and Gateway availability. It adds no CLI activation,
resident controller, credential producer or general workload executor.
The [lifecycle plan](../../efforts/active/linux-role-fleet/komodo-substrate-lifecycle.md)
retains actual adopter construction and official-image qualification.
Synthetic subprocess/HTTP tests are not evidence of an installed production
Komodo service.

## Selected configuration and existing owners

The optional `extensions.role_bindings.substrates` map holds independently
selected declarations. `select_substrate(config, name)` validates only the
named declaration and its exact existing Komodo provider, instance, target
and configuration reference. Ordinary planning and role projection remain
inert; their bootstrap/readiness flags do not change.

The canonical `fleet-manager.komodo-substrate`, version 1 declaration contains:

| Fields | Contract |
|---|---|
| `provider`, `instance`, `target`, `configuration_ref`, `revision` | Bounded non-secret references; provider tuple must match the selected role provider |
| `project`, `architecture`, `server_name` | Bounded native project, `amd64` or `arm64`, selected Periphery registration name |
| `references` | Exactly `artifact`, `credentials`, `host`, `runtime`, `storage`; existing-owner references, not paths or inline credentials |
| `images` | Exactly `core`, `mongo`, `periphery`, each with `image` and `version`; official digest-pinned repositories, matching Komodo 2.x and MongoDB 8.0.x |
| `resources` | Per-service `cpu_millis` (100-64000) and `memory_bytes` (128 MiB-128 GiB) |
| `core_port`, `outfitting_seconds` | Loopback port 1024-65535 and total finite operation deadline 1-1800 seconds |
| `desired_state` | Optional `present` (default) or explicit `absent`; absence is a separately content-admitted withdrawal |

No shell, executable, arbitrary Compose/environment/mount/module override,
mutable image tag or inline secret is accepted. An image version declaration
is not artifact provenance: the existing release owner must authenticate the
chosen official digest and version.

The adopted host composition constructs `KomodoSelection` from its existing
authenticated configuration, release, host, storage and credential owners.
Record construction and matching hashes authenticate nothing by themselves.
`current()` must return a fresh selected record on every checkpoint, with
finite `valid_until` and current policy revision. No callback/module path
is selected by a control request.

Selection binds fleet/installation/generation, physical Docker daemon/project,
release digest, fixed profile, executable/client configuration, materialization
paths and their current device/inode identities, credential fingerprint,
explicit data-compatibility assertions and any incumbent adoption provenance.
Freshness/policy renewal alone does not rewrite the original operation.

`KomodoTooling` requires an admitted absolute Docker executable, existing
private inert client configuration (`config.json` containing `{}`), physical
daemon identity and the existing
`agent_procutil.owned_process.run_caller_owned` adapter. `KomodoMaterialization`
requires an existing native Unix socket, disjoint persistent directories for
Mongo data/config, keys, backups and Periphery, plus private credential files
for admin/database usernames/passwords, JWT secret and webhook secret.
Credential files cannot be inside service-writable storage. Missing, changed,
unsafe or unavailable prerequisites refuse; this API installs neither Docker
nor images and never changes filesystem permissions to accommodate an image.
The client directory is owner-private, inode-bound and contains only that
configuration file. Extra plugin/context directories refuse before any child;
the native child receives the OS default executable path, never ambient `PATH`.
The existing host authority still authenticates the Docker binary and its
system-provided plugins; constructing a tooling record does not do that.

Install the SDK's `komodo-host` extra and the separately selected identity
profile's dependencies. The host owner is Linux-only; parsing and ordinary
thin-client planning remain platform-neutral.

## Native profile, adoption and custody

The three services use immutable images, bounded CPU/memory, fixed file-based
credential inputs, persistent bind mounts and one explicit bridge network.
Only Core publishes a loopback port. Mongo and Periphery publish none.
Periphery receives the selected native Docker socket; that is a powerful host
capability requiring the host owner's independent admission.

The Mongo image's normal entrypoint drops to its database UID before reading
`*_FILE` settings. Its fixed command therefore uses the image's Bash to load
only the two selected read-only credential mounts before re-entering the
unchanged official entrypoint with `mongod`. Values enter only that process's
environment, never the rendered profile, Docker command arguments or static
container environment. The vendor still owns database initialization, data
ownership and privilege dropping; no credential mode/owner changes or forked
entrypoint are used. Compose dollar escapes are rendered explicitly and native
inspection checks the resulting literal command after interpolation.
Periphery's native private key is explicitly selected at
`file:/config/keys/periphery.key` in the same retained identity directory that
Core reads; the image's root-directory default must not create another key.
Tagged source checks cover
[Komodo 2.3.3 key configuration](https://github.com/moghtech/komodo/blob/780ac68b992094a9fccd5fffb760e0c84fd3c3d1/client/core/rs/src/entities/config/periphery.rs)
and the
[MongoDB 8.0.32 entrypoint](https://github.com/docker-library/mongo/blob/5808abcbf79f662b7f0ac597dcbdda606d9ac4e9/8.0/docker-entrypoint.sh).
Actual selected
official-image startup and authenticated readiness remain adopter qualification,
not consequences of a shell or configuration test.

The owner interrogates the actual daemon, Compose 2 or stable Compose 5 version, installed image
digests/platform/configuration, container IDs/configuration/mounts and physical
network. It scrubs ambient Docker context/auth controls. Native output is
bounded and private; credential-bearing container configuration never enters
receipts or error details. Only fixed `compose up --no-build --pull never`
and receipt-owned container withdrawal are available. No orphan/volume removal,
build, automatic pull, network cleanup or workload modification is issued.
Docker Hub's familiar Mongo repository spellings are equivalent only for the
same selected official repository and exact digest; other registries and the
Core/Periphery repository names remain exact. Network discovery matches the
complete expected name, not similarly named neighboring networks.

Compose 5 retains the Compose Specification and the fixed `up --no-build
--pull never --wait --wait-timeout` interface used here. Stable 5.x is accepted
alongside the existing 2.x contract; other majors and 5.x prereleases refuse.
The contained native-protocol lifecycle matrix exercises both 2.39.4 and 5.1.4.
This is not qualification of an actual Docker installation or official service
images. The selected host authority must still authenticate its executable and
system Compose plugin; no plugin download, replacement or global configuration
change is performed by this owner.

A matching Compose project name does not authorize adoption. First adoption
requires exact independently authenticated fingerprints of all three native
containers, existing public-key identity and physical network. The network
fingerprint covers its full stable inspection record, excluding dynamic
container membership; membership must contain only the exact owned container
IDs. Foreign endpoints, a replaced bridge or additional container attachments
refuse. An existing empty network also requires explicit network adoption.
An adopted bridge remains external in all subsequent fixed profiles, preserving
its identity rather than relabelling or recreating it.

Preserving adoption records an unchanged healthy incumbent without native
writes. Its legacy configuration remains bound to the exact original native
fingerprints, not a permanently relaxed validator. Once an admitted managed
update actually invokes the fixed profile, `fixed_profile` evidence requires
strict image environment, entrypoint, command, labels, storage and isolation.
Original adoption provenance remains immutable across later updates.

The private owner root is canonical to physical daemon/project and guarded by
the existing private-path primitives and a kernel lock. Its same `NodeJournal`
owns the physical external resource; changing an installation/target alias or
generation cannot acquire an unsettled resource. It retains immutable profile
and outcome files, current/previous good, failed candidate, original attempt,
network-before evidence and whole-native-process settlement. No garbage
collector or second scheduler is introduced.

## Admission, convergence and recovery

`manager.converge` uses the existing canonical `OperationIntent` scope with
exactly `parameters: {"deployment": selected.deployment_digest}`.
The existing current-policy `OperationGrant` must include the complete
original intent's `request_digest`, not merely command/target/generation.
The selected fleet, actor/account, authority, target and generation must match.
There is no serialized root grant, enrollment database or cached-allow fallback.

`komodo_control.bootstrap(owner, actual_verified_identity, original_intent,
role=..., installation=...)` consumes the existing real verifier's identity
through common admission before Core/Gateway exists. Direct SDK embeddings
may likewise use the exact owner-issued permit. Neither a DTO nor an SDK
constructor grants execution authority.

The owner rechecks current selection and admission before persisting UNKNOWN
and immediately before each fixed native command, including between stop and
remove. Post-inspection expiry or withdrawal refuses the next effect without
replay. A settled or never-started native invocation retains settled process
evidence; unconfirmed cleanup does not. A long accepted native run may outlive
its permit, requiring fresh original-operation proof for later reconciliation.
Bounded authenticated readiness then checks
current Core version, selected registered server, Periphery connection/version,
Docker connection and actual retained public keys before publishing good.
Container existence, a zero native exit or old good evidence is not readiness.
Current native identity/network are checked again after readiness.
Convergence, successful repeats and status use the same final observation:
all three services must still be running, unpaused and non-restarting with
positive process IDs and unchanged start identities. A stop/restart during HTTP
readiness cannot publish or reuse a successful current-health result. Public
identity is reread after health and bound to the returned evidence.
Each original attempt also retains its pre-effect public-key fingerprints.
Known keys cannot change across admission, readiness, failure classification or
restart reconciliation; initially absent keys may be generated by the admitted
fixed apply. Missing original key evidence refuses rather than becoming an empty
baseline. These checks describe an observed readiness interval, not a promise
that a process cannot subsequently fail.

A timeout, lost response or process-settlement uncertainty leaves UNKNOWN.
An original settled attempt may reconcile exact observed resources without
replaying native effects. An unconfirmed whole-process settlement blocks
reconciliation even after owner restart. Only an original attempt whose
recorded network-before was positively absent may recognize a newly created
fixed owned bridge. An existing proved bridge may never be silently replaced.
There is no TTL takeover, operation-ID substitution or implicit rollback.

Updates/recovery require explicit compatibility with the actual retained data
candidate, including a failed candidate that may already have modified data.
Unsupported database major transitions refuse. `settle_failed` is an explicit
original-operation decision after exact settled native evidence and actual
readiness failure; it retains the failed selection and compatibility decision.
It does not restore code, database or credentials. A subsequent recovery must
be independently admitted and data-compatible with that failed candidate.
An original settled fixed-profile attempt can also classify positively absent
or strictly attributable partial native results as failed, without replay.
Every present container still needs its exact fixed configuration and network
custody. Partial failure retains which public keys actually existed; a later
compatible fixed apply may create missing keys but cannot replace known ones.
Short positive outfitting budgets remain valid, with proportionate time reserved
for final native inspection.
Failure-only reconciliation also accepts mixed updates when every service is
either the exact retained pre-update incumbent or a strictly validated selected
replacement. It retains the actual mixed fingerprints and candidate compatibility;
success still requires the complete selected profile. Mixed native state never
authorizes credentialed health probes to an unproved selected endpoint, including
subsequent status calls over its retained failed receipt.

A new admitted convergence can restore missing owned Core/Periphery from
retained selected storage/configuration while Core is unavailable; native
restoration precedes final authenticated health. Credential/native replacement,
unsettled previous work and ambiguous custody refuse rather than wipe data.

## Status, withdrawal and Gateway binding

`owner.inspect(original_intent, permit)` reports desired, accepted, observed,
installed, currently healthy and converged separately, with retained good/
previous-good/failed candidate, drift blockers and unresolved effects.
`None` means unavailable/unproved, not false. A healthy old deployment need
not converge a new desired selection. Inspection never mutates native state.
Only the actual current accepted UNKNOWN candidate can supply candidate
evidence; changed legacy containers are not blessed through a fallback.
Unresolved effects also reflect the retained original attempt's journal
receipt, even when the admitted status request has a new operation ID and no
receipt of its own. That resource fence prevents convergence without allowing
the new request to validate or reconcile the original candidate.
Status also reads the physical resource's current receipt directly from the
same journal. Missing attempt metadata cannot hide an unsettled claim or permit
state reconstruction; the original missing evidence remains a recovery refusal.
Stopped Core containers never authorize credentialed HTTP probes to their
released loopback port. Status reports health unavailable; original failure
settlement treats stopped or incomplete native state as readiness failure.
An interrupted ACCEPTED attempt retains its original deployment and native
fingerprints rather than adopting changed bytes on retry.
Missing or unsafe public identity evidence is an explicit health-blocking status
drift, including complete-but-unstarted failures and adopted installations.
Status preserves the failed candidate and does not probe HTTP without that evidence.

Explicit `desired_state: absent` uses the same content-bound operation and
requires a recorded installation, unchanged declaration apart from desired
state, storage, keys, credentials, native fingerprints and network custody.
It stops/removes only the exact proved container IDs; it never removes the
bridge, data, credential files, named volumes, retained evidence or workloads.
Lost replies reconcile complete absence for the original settled attempt.
Partial withdrawal remains UNKNOWN and does not replay stop/remove commands.
Reinstallation can reuse retained data and the same proved network through
another admitted present selection.

`komodo_control.gateway_binding(owner, exact_slot, role=..., installation=...)`
registers with the existing service Gateway's explicit `local_handlers`.
It accepts only POST `/converge`, `/settle-failed` and `/status` beneath that
slot's configured prefix, with canonical original operation bytes and no
queries. The Gateway passes its original retained verified caller into the
same independent operation admission. Default JWT and explicitly selected
enrolled-PoP profiles remain separate; identity headers/cookies grant nothing.
The existing settled worker joins finite native work across request shutdown;
no service connector or new daemon is necessary.

The opt-in API contract is `fleet-manager.komodo-substrate-owner.v1`.
Embeddings must establish support from the selected installed SDK before
constructing this owner or selecting the declaration/command. Expand providers
first; older strict readers may reject new substrates/operations before effects.
The ordinary CLI never automatically invokes this API, and missing support
must not fall back to a manual installer, generic command or weaker grant.
Actual adopter controller construction, official-image qualification and
authorized production rollout remain separate lifecycle gates.
