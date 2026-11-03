# Explicit single-role Komodo workload execution

Back to [role/provider plans](role-deployment-plans.md) and the separate
[substrate owner](komodo-owner.md). This SDK closes part of #6030/#8352's
existing deployment-adapter intent, tracked in #10112. It is not a new
installer, service, credential producer, Docker reconciler or automatic planner.

## Code-selected consumer

`fleet_manager.komodo_workload.CONTRACT` is
`fleet-manager.komodo-workload.v1`. The selected executing runtime must supply
this contract before composition; an older runtime has no execution binding.
`KomodoRoleProvider.project()` and ordinary planning remain pure and inert.
Only explicit `KomodoRoleProvider.execution_binding(owner, slot, role=...,
installation=...)` registers the existing Gateway local-handler boundary.
Its sole route is `POST /converge` with the existing canonical
`OperationIntent`, command `manager.converge`, and
`parameters.deployment = selection.deployment_digest`.

The existing Gateway verifies the original TLS caller and passes its bound
identity to `AdmissionAuthority`, not a connector identity or HTTP label.
Its settled worker invokes `KomodoWorkloadOwner.converge(intent, permit)`.
An independently authenticated SDK caller can invoke that same method after
obtaining an exact content-bound permit from the existing admission owner.
No wire field selects Python imports, callbacks, endpoints, credentials or APIs.

The adopting application's trusted code constructs `KomodoWorkloadSelection`
and `KomodoWorkloadOwner(current=..., credentials=..., custody=..., journal=...,
admission=..., authority=..., clock=..., current_projects=...)`. These records authenticate nothing
themselves. Existing configuration, artifact, credential and physical-resource
owners must independently authenticate their inputs:

- Exact `ProviderInstance` and one `RoleBinding`, fleet, installation,
  generation, configuration/artifact references, current policy and expiry.
  Role dependencies must already have been satisfied outside this one-role API;
  declarations with unresolved dependency references refuse. Required capabilities
  must be supported by the selected built-in provider contract; bypassing the
  pure planner does not permit unsupported requirements to reach execution.
- Selected controller identity and TLS endpoint, or an independently
  authenticated loopback publication. Remote plaintext, redirects, ambient
  proxy configuration and cookie authentication are disabled.
- An **existing**, enabled, non-template Server's canonical full API record,
  including its immutable BSON ID, exact configuration and Periphery public
  key. Registration, name lookup and enrollment are not execution authority.
- Exact immutable image reference and image configuration ID. Both
  repository `@sha256:` references and already-imported local `sha256:` image
  IDs are supported. No registry selection, pull, build or artifact import occurs.
- Canonical full inspection records for two distinct **existing** local named
  volumes, mounted at `/home/vscode` and `/workspaces`. Volume creation time,
  mountpoint, labels, driver, scope and empty options remain bound.
- Existing Stack adoption/update additionally requires its exact BSON ID,
  canonical complete previous configuration and independently authenticated
  `mounts-v2:` native container fingerprint. A matching name is insufficient.

`custody(selection)` must acquire the existing owner's exclusion for the exact
physical controller/Server/project, Stack and retained storage. It yields a
callable that checks that custody and returns `None`; every API boundary,
selection/policy checkpoint and receipt publication invokes it. It must also
bind the canonical private NodeJournal location and exclude aliases/other
installations. A no-op context manager is not production fencing.
Komodo 2.3.4 has no conditional Stack-write/idempotency fence, so this SDK
cannot manufacture distributed exclusion. Missing exclusion is a deployment
prerequisite, not permission to proceed with names alone.

Creation additionally requires `current_projects(selection)` from the existing
authenticated physical-resource observer, returning `KomodoProjectCensus`:
the selected Server identity fingerprint, complete native project names,
observation time and an expiry at most five seconds later. The SDK rechecks
exact target/freshness and project absence before acceptance, CreateStack and
DeployStack. This is a read-only owner seam, not another Docker deployment
writer. The observer must acquire a current native census under that custody;
constructing a record or recycling Core cache data authenticates nothing.
Missing/unqualified observation refuses creation explicitly.

`credentials()` obtains the existing owner's current API key/secret tuple or
existing raw JWT string ephemerally for each HTTP request. The latter uses the
same raw `authorization` contract as the existing authenticated substrate-health
owner; it does not add a scheme or perform a login. This SDK issues no
credential, logs no backend response/error body and retains no secret in its
journal. JWT refresh/login remains with the existing credential owner.

## Fixed vendor operations and actual observations

Only the selected Komodo **2.3.4** Core and Periphery contract is supported.
Source verification uses its tagged
[resource/BSON shapes](https://github.com/moghtech/komodo/blob/v2.3.4/client/core/rs/src/entities/resource.rs),
[Stack configuration](https://github.com/moghtech/komodo/blob/v2.3.4/client/core/rs/src/entities/stack.rs),
[write API](https://github.com/moghtech/komodo/blob/v2.3.4/client/core/rs/src/api/write/stack.rs),
[read APIs](https://github.com/moghtech/komodo/blob/v2.3.4/client/core/rs/src/api/read/docker.rs)
and [asynchronous execution router](https://github.com/moghtech/komodo/blob/v2.3.4/bin/core/src/api/execute/mod.rs).
The [vendor content normalizer](https://github.com/moghtech/komodo/blob/v2.3.4/client/core/rs/src/deserializers/file_contents.rs)
requires a trailing newline on nonempty file contents; generated Compose JSON
includes it before content admission. The
[Core project-list implementation](https://github.com/moghtech/komodo/blob/v2.3.4/bin/core/src/api/read/docker.rs)
uses a cache and can return empty without Docker data; it is never absence proof.
`x-api-key` and `x-api-secret` follow the
[vendor client](https://github.com/moghtech/komodo/blob/v2.3.4/client/core/rs/src/request.rs).

The finite sequence authenticates version, exact Server/Periphery, existing
volumes and installed image. New resources additionally require absence of the
exact Stack name and a current authenticated native Compose-project census.
The only mutations are
`CreateStack` or `UpdateStack`, then `DeployStack`. Existing Stack configuration
must match the independently admitted baseline before any write.
Updates cannot change Server, project, mounts, commands or profile; the
baseline permits only the selected immutable image replacement. No deletion,
volume reset, cleanup, arbitrary Compose/command or Server mutation is exposed.

The fixed single-service Compose JSON binds CPU/memory/swap limits, image and
external home/workspace volumes. Optional explicit `trusted_systemd=True`
uses the existing trusted image's SYS_ADMIN/TTY/container-environment/tmpfs
profile. It adds no blanket privilege, socket bind or host security change.
That profile still requires native host qualification and prior replacement
drain/data compatibility admission by its existing owners.

Success requires actual current authenticated Stack/configuration/action-state,
service-set and direct container readback: exact image ID/reference, effective
image command/environment/user, process state, owner/project/service labels,
limits, effective `unless-stopped` restart policy and complete persistent mounts. A paired container observation and
repeated Server/image/volume/configuration checks detect drift during readback.
An unchanged running workload issues no backend mutation. Repeating a succeeded
receipt still performs current readback; a saved receipt alone is not success.
Every response is bounded to 256 KiB; the caller selects 1-90 total seconds,
with at most five seconds per request. Backend operations can outlive that
deadline; timeout is not cancellation or failure proof.

## Original-attempt persistence and recovery

The existing NodeJournal serializes named external operations by physical
resource. It persists UNKNOWN **before** the first mutation. Its generation-6
extension retains bounded immutable original-attempt facts in the same journal,
not another queue or operation store: selected content, returned Stack ID,
returned deployment Update ID, and terminal outcome.

`DeployStack` returns an asynchronous Update, not completion. The owner retains
its exact BSON Update ID and reads only that original `GetUpdate` until
Complete. A completed unsuccessful Update yields `failed`, never success.
Lost polling/publication can resume original-ID reads and current readback.
Lost create/update/deploy responses, or a crash before retaining the returned
ID, remain UNKNOWN: no name-based adoption, inferred success or fresh mutation.
Even an apparently healthy desired container cannot settle that missing-ID case.
An unsettled original operation blocks replacement by a fresh operation.

An unchanged running incumbent retains an immutable **read-only decision**
before UNKNOWN/readback. If that readback is interrupted, the same original
attempt can resume only readback of its original Stack/native fingerprint.
This does not infer a no-deploy decision for an attempt that already mutated.

Generation 4 journals migrate transactionally to 6 without changing original
intents/receipts. Generation 5 remains incompatible and rejected; its separate
read-only observer is unchanged. Older writers/readers refuse generation 6.
Qualify and select the new runtime before opening a journal for migration;
rollback to an older writer requires its existing storage/recovery owner.

## Validation and remaining qualification

`tests/test_komodo_workload.py` uses a finite local HTTP backend and a real
TLS/enrolled Gateway consumer. It covers exact create/update/deploy/poll
sequences, no-op/replay, wrong target/Stack/image/storage, selection/policy
withdrawal, response bounds, UNKNOWN and original-ID recovery. Servers and
threads are closed by the fixtures; run under repository test containment.
`test_node_journal.py` covers immutable evidence bounds and migration; its
isolated child processes select the same imported SDK source as their parent.

These are source/protocol tests, not actual Komodo, Linux/systemd/image startup,
artifact transfer, cross-host exclusion, installed rollout or live adoption.
Those tiers remain with the existing integration/deployment owners. No live
external mutation is part of this source qualification.
