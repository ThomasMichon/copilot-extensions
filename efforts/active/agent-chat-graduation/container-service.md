# Chat container role, private templates and Komodo

Back to the [canonical effort](README.md),
[architecture](architecture.md) and
[Chat vision](../../../visions/plugins/agent-chat/README.md#templated-container-delivery).

**Status: proposed specification, not a delivered container or deployment.**
The container role, template-derived private configuration and Komodo-driven
deployment are operator-requested. The realization below is
**agent-recommended** and must clear the repository review gate before
container implementation or downstream configuration publication.

## Ownership and existing contracts

| Owner | Contract supplied to this composition |
| --- | --- |
| Chat | Independently built service image, portable role/configuration template, conversation storage compatibility, authenticated application health and bounded drain |
| Private knowledge/configuration repository | Selected template and revisions, actual named-machine allocation, overrides, persistent storage and provider/credential references |
| Fleet Manager | Loadout actualization, complete capacity/support closure, current role admission, prepared deployment and observable operation outcomes |
| Komodo deployment adapter | Realization of the admitted `container.service` role as an owned named resource, container lifecycle and restart |
| Gateway | Predeclared Chat slot and API/UX namespace, admitted outbound service channel and scoped client routing |
| Existing credential and storage owners | Secret resolution, current access, persistent-volume identity and fencing; references alone prove none of these |

Reuse the published [loadout template/registrar](../../../fleet-manager/docs/loadouts.md)
and [role/provider binding](../../../fleet-manager/docs/role-deployment-plans.md)
contracts. The current `KomodoRoleProvider` projects named `Stack` references;
it does not submit Compose, install Core or establish live readiness.
The actual effect executor, artifact/configuration resolution and independent
manager bootstrap remain dependencies of the owning fleet work, not functions
Chat may invent. Preserve the #6030/#6240/#6424 ownership boundaries.

The [PR service's container recipe](../../../services/pr/container/Dockerfile)
and [reference-only role binding](../../../services/pr/container/role-bindings.example.json)
are packaging and declaration prior art, not proof that Chat is deployed or a
reason to depend on that service.

## Image and process contract

- Ship an independently buildable OCI recipe and an admitted, digest-addressed
  image. Bind the selected Chat release, dependency closure, image architecture
  and base-image identity; do not use mutable tags as release selection.
- Build from approved wheel/lock inputs through the existing artifact and
  dependency-policy owners. No source checkout, Copilot runtime, sibling venv,
  host configuration, credentials or runtime package installation is required
  inside the resulting image.
- Use one foreground application lifecycle for Chat and its supported outbound
  Gateway integration. Komodo owns the container; existing service/SDK owners
  supply supported start, stop, health and connector seams. No Chat-specific
  supervisor, nested container manager or per-agent helper is added.
- Run as an explicitly declared non-root identity with a read-only application
  filesystem and bounded writable scratch. No privileged mode, host networking,
  container-engine socket or public backend port is an implicit requirement.
- Declare the qualified OS/architecture set and measured CPU, memory, disk,
  startup, drain and restart needs. An image recipe or a successful native
  Python test is not container-platform qualification.

The existing foreground host is an application primitive, not an already
complete Gateway-connected image. Publish any missing application/SDK lifecycle
binding as its exact owner dependency before claiming that composition ready.

## Configuration and persistence contract

Keep three separate inputs: immutable application/image content, non-secret
service configuration, and owner-resolved secret material. Mount rendered
configuration and credential references read-only; never bake private values
into the image, example, template or deployment logs.

The configured Chat installation identity survives image/container replacement.
Use a separately owned persistent conversation volume for the database and its
required journal files; endpoint/status/scratch state is disposable and separate.
Initial volume creation must be explicit. Missing or mismatched retained storage
must not silently become an empty replacement conversation installation.

Exactly one admitted writer may own a durable conversation volume. Local
singleton checks do not establish cross-host fencing or authorize rescheduling
a possibly live predecessor. Update/rollback must retain actual write custody,
declared storage compatibility and unresolved effects. Ordinary removal
withdraws owned routing and execution while preserving data and configuration;
purging either requires separately scoped authority.

## Portable template and private actualization

Deliver the Chat-owned container recipe, role/loadout contribution and
non-secret configuration example together. They are reusable inputs, not a
second fleet configuration schema or an automatically selected deployment.

The template exposes the following responsibilities through the existing
reference/integer slots and allocation fields:

| Input | Required meaning |
| --- | --- |
| Template provenance | Explicit contribution source, template ID and revision |
| Image/artifact reference | An independently admitted Chat image and compatible service release |
| Service-configuration reference | Non-secret installation identity, application settings and declared compatibility |
| Storage reference | The retained volume, access and current writer/fencing owner |
| Service-identity references | Credential-provider bindings, not token/key bytes |
| Gateway dependency | The selected expected Chat slot, scoped routes and connector requirements |
| Deployment-manager reference | The selected Komodo instance, named resource and independent bootstrap requirement |
| Named-machine allocation | Existing registrar target and current placement admission, not a host inferred from a template |
| Resource slots | Separate positive CPU/memory/disk reservations and limits, including support and retained-storage costs |

Map the admitted role to the existing `container.service` implementation and
`komodo` provider binding. Keep `artifact_ref`, `configuration_ref`, `key_refs`,
named resource, target and dependencies attributable to their real owners.
Raw commands, environment, mounts, URLs and secrets must not be smuggled into
the reference-only role-binding namespace. The selected artifact/configuration
resolvers produce the actual container specification through their supported
contracts.

"Dotfiles" means the operator's **resolved private knowledge/configuration
repository**, not a committed repository identity or machine path. After this
intent clears review, create/update its selected Chat allocation from the
portable template in a dedicated writable worktree. Use that repository's
declared fleet configuration location and publication flow; never edit its
anchor or guess a path from the word "dotfiles."

Retain existing overrides, custom roles and template provenance. Missing slots,
an incompatible template revision or unresolved references block preparation
instead of selecting a public/default backend. The private final composition,
not a second configuration uploaded by a roaming client, remains desired state.

## Gateway and deployment flow

1. Publish the portable template and qualified image through their existing
   source/release owners; publish the private allocation through its own gate.
2. Fleet Manager/Gateway acquire the selected repository pairing and revisions,
   resolve the complete support closure and prepare the current deployment.
   Keep manager bootstrap independent of the Core/Gateway being provisioned.
3. Confirm that exact prepared composition through the existing fleet control
   flow. A merge, source refresh or nonblocked offline plan is not activation.
4. The supported Komodo effect owner applies the named Chat resource under
   current enrollment, placement, storage, credential and capacity admission.
   Unknown submission outcomes retain their original recovery correlation.
5. Verify the actual application and its admitted outbound Gateway slot/route,
   independently of the initiating client. Report blocked, partial, failed or
   uncertain outcomes within the declared outfitting bound, not green status
   inferred from container creation.

Reserve the Chat service surface in desired Gateway configuration before an
instance connects. Do not publish a backend LAN port as an authentication
bypass. Cross-container access uses the existing authenticated proxy/channel
boundary; Chat does not introduce per-agent tokens or attestation inside an
already admitted single-operator coordination boundary.

## Required evidence

- Fresh independent image build/start without source, plugin or sibling runtime;
  explicit image/version/platform provenance and resolved resource limits.
- Default, overridden and upgraded templates preserve private choices and
  reject missing/incompatible slots without secret disclosure or deployment.
- Pure planning remains mutation-free; real Komodo application proves supported
  manager execution rather than merely a named-Stack projection.
- Missing/stale enrollment, credentials, storage, capacity, manager bootstrap
  or route prerequisites refuse before changing the serving allocation.
- Two genuine clients exchange a stable-ID message through the admitted
  Gateway route; health and drain remain responsive under held reads.
- Restart, caller loss, different-code replacement and compatible rollback
  preserve history, membership, cursors and uncertain requests. An uncertain
  predecessor cannot coexist as another writer.
- Ordinary removal preserves configuration and data, removes only owned
  exposure/execution, and keeps unresolved operations visible.

These are open acceptance obligations of the parent effort. No template
publication, private configuration write or production rollout is certified by
this proposed specification.
