# Machine Fleet - Vision

- **Subject:** A driver-based fleet of machines and services coordinated through an optional central Gateway controller.
- **Scope:** branch (cross-cutting child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-10-19
- **Reality docs:** [`docs/architecture.md`](../../docs/architecture.md), [`docs/configuration.md`](../../docs/configuration.md)

## Purpose & Intent

Make a declared set of machines a coherent, reachable, observable control fabric.
An operator can discover offered services, inspect work, reconcile deployments
and configuration, and control supported sessions without first obtaining a
remote shell. A central Gateway provides rendezvous and authorized routing,
while each existing service remains the authority for its own state unless an
explicit ownership migration replaces it with the integrated coordination core.

Fleet adoption is optional and additive. Standalone local services, direct
connections, and existing SSH-based workflows retain their value without a
Gateway. A roaming operator client is not required to keep remote work alive.
The same fabric can grow into a scalable fleet of Linux hosts running explicit,
containerized roles from the existing service ecosystem, without making one
container manager or a private infrastructure provider its required substrate.

A fleet belongs to exactly one operator. Its machines, services, state and
credentials form that operator's control boundary, not a multi-tenant hosting
environment. Collaboration between operators means explicit interaction between
independently owned fleets, never placing another operator's state or credentials
inside this fleet.

## Concepts & Components

### Declarative service Gateway

The [Gateway child vision](gateway/README.md) defines repository-owned role
slots and namespaces, authenticated outbound service channels, live desired-state
reconciliation and reusable container/edge-adapter boundaries.

### Standalone fleet-manager system

`fleet-manager` is the standalone entry point and distribution home for the
fleet's CLI, controller/Gateway and machine connectors. Its services are usable
without Copilot or marketplace hooks, while optional harness integrations
provide authenticated client access.

Ordinary user machines install only the thin CLI client. Commands reach the
explicitly configured remote controller API using supplied scoped authority;
client installation never installs Komodo Core, starts a controller or directly
mutates worker container managers. A designated service host runs the
controller/Gateway and its selected deployment-manager integration. Worker hosts
run only their explicitly adopted connectors and service roles.

Before a Gateway exists, the local CLI provides explicitly authorized bootstrap
and recovery through the selected substrate owner. Once ready, that Gateway is
the central service for CLI, workspace-manager and harness clients; ordinary
clients do not become competing coordinators when it is unavailable.

### Harness-declared fleet

Harness-side configuration declares the selected fleet driver, machine membership,
roles, desired service placement, permitted operations, and credential-provider
references. Private inventories, accounts, and deployment policy remain with the
adopter, not the portable controller. Effective configuration and its provenance
are inspectable; installing connection tooling alone does not enroll a machine
or enable background services.

### Logical machines and physical association

Windows and WSL on one physical host are separately addressable logical
machines, conventionally `<base>` and `<base>-wsl`. Machine and SSH owners
resolve their independent identities and exact connection profiles; downstream
worktree, bridge and dispatch clients consume that authority rather than adding
an environment dimension to machine addresses or selecting a first SSH profile.
An explicitly declared logical key remains canonical regardless of naming
convention.

Physical association supports host grouping and Win/WSL presentation in
Worktree Manager, without collapsing source/target identities, registrations,
settings, sessions or ownership. Existing identity evidence stays intact;
ambiguous legacy ownership is not permission to migrate or write a sibling's
state.

### Substrate drivers

The choice of machine substrate belongs to a driver. Drivers contribute
discovery, stable target identity, lifecycle/lease capabilities, bootstrap and
reachability information through a bounded, capability-honest contract. The
controller does not import provider implementations or embed their platform
management rules. Unknown or unavailable capabilities remain explicit.
Managed drivers preserve their provider's admission, capacity, lease/fencing,
and destructive-action gates; joining a fleet is not permission to bypass them.

The default driver adopts an explicitly selected set of static machines through
`agent-ssh` and its existing registry/transport authority. It does not claim to
create or delete those machines. Other drivers may offer managed or ephemeral
capacity and source their lifecycle, credentials, and placement policy from
their owning providers. No particular cloud, tunnel product, or identity system
is required by the fleet contract.

### Gateway controller and machine connectors

The Gateway is a shared rendezvous, policy boundary, and coordination service.
Connectors establish authenticated outbound reach, advertise approved services,
report freshness and outcomes, and reconcile locally permitted desired state.
A logical controller may have a recoverable deployment without making its
current network connection the lifetime owner of remote work.

### Integrated coordination product

An explicitly adopted integrated profile uses fleet-manager's Gateway as the
coordination core, not a broker above a second control system. The same core
supports machine-local and multi-machine deployments through capability-honest
transport boundaries. Worktree Manager and Copilot integrations are clients of
that authority, not independent fleet writers.

Canonical agency, task, claim and operation relationships can share a
transactional owner after their previous authorities are explicitly migrated.
External or unmigrated services retain their own state; enrollment alone never
creates a replacement registry or queue. Nodes preserve effect receipts and
accepted execution without becoming another global allocation authority.

### Existing service authorities

`agent-worktrees` owns durable worktree agency and source-control obligations;
`agent-dispatch` owns task/claim state; `agent-bridge` owns coordination;
`agent-machines` owns declared machine-state reconciliation; session-host
providers own their execution/session substrate. Fleet adapters route supported
operations to these owners rather than building competing stores or supervisors.
Index/search and other harness-side services can be registered independently of
interactive agent sessions.
The PR service owns its observation, subscriptions and delivery state; fleet
placement and routing do not create a second PR watcher or registrar.

### Shared fleet state backing store

An adopted fleet has a durable central current-state view behind its selected
Gateway. Authorized machine and container origins push their committed
observations into that view; Worktree Manager, other UX surfaces, CLI tools and
API clients share its scoped query and subscription contract rather than each
maintaining a separate remote polling view.

The central projection retains provenance, freshness and coverage without
becoming another writer of its sources' worktree, task, session or resource
authority. Centralizing an actual authority requires its separate explicit
migration; centralizing observation alone does not perform that migration.

### Role deployments and container managers

A role describes a supported service responsibility and its resource,
placement, persistence and availability needs, not an arbitrary executable.
Role instances may run directly or in containers on explicitly admitted hosts.
The chosen deployment adapter composes an existing lightweight container manager
or authenticated host deployment mechanism rather than building another
scheduler. Fleet routing, container placement, service-state ownership and
credential issuance remain separate authorities.

Replicable roles may scale within declared capacity and authorization.
Stateful or singleton roles declare storage and writer ownership before
placement or movement; a healthy replacement process is not proof that its
data and authority are safe to activate. Adopter-specific provisioning remains
outside the portable fleet core.

### Scoped credential relay

The fleet composes the existing auth-relay provider model. Machine enrollment,
operator control, access to a registered service, and borrowing resource
credentials are distinct authorities. Providers own issuance, account/audience
selection, expiry, renewal, and any interaction required to acquire credentials.
The Gateway is not a blanket credential pool or an implicit source of authority.

Explicitly enrolled, logged-in provider machines maintain the authenticated
channel needed to answer fresh-token requests. This responder is an installed
credential-provider service, independent of an interactive client invocation.
Reuse the credential owner's lifecycle rather than adding a fleet-local duplicate
custodian or requiring a roaming client to keep remote work alive.

### Recommended fleet templates and owned configuration

A harness supplies a recommended fleet shape; the adopter's own configuration
records choices, custom capabilities and managed identities. Setup compares both
with authoritative live state and admitted quota rather than treating a template
as permission to overwrite the fleet. Recommended service capacity and optional
execution capacity are separate, inspectable dimensions.

Each adopted fleet supplies central management services and connections: Gateway,
coordination, task state, indexing and declared communication capabilities.
An unavailable future capability is an explicit requirement awaiting a supported
implementation, not a fabricated ready service. Minimal capacity prioritizes
these services; more capacity adds agent/build containers and product venues.
Authenticated, operator-enrolled external execution hosts can supplement a small
fleet without becoming controller hosts implicitly.

## Features

### shared-operator-surfaces

Conversational agents, thin CLI clients and web surfaces operate the same
qualified fleet relationships and authorized controls. An at-a-glance index
connects objectives, tasks and dependencies, efforts, worktrees, bridge sessions,
contributions and service health, exposing current responsibility, blockers,
progress, freshness and coverage. Operators and agents can locate and steer the
rightful owner without opening each console or maintaining their own remote
polling inventories.

### portable-service-and-presentation-extensions

The Gateway offers general-purpose service API adapters and reusable UX
components through explicit capability and authorization boundaries.
Adopter-owned drivers supply forge integrations, identity policy, branding,
interaction presentation and custom service views without forking the portable
coordination core. A wrapper preserves its service's supported semantics,
ownership and unavailable capabilities; registration is not permission to
invent control or widen data access.

Generalized capabilities can move from an adopter implementation into the shared
product, while private policy, inventories and presentation remain extensions.
Convergence replaces duplicated responsibilities deliberately, preserving
accepted work and existing consumers rather than installing a second authority.

### single-operator-fleet

The entire fleet serves one operator, including its controller, service roles,
storage and credential providers. Multiple machines, clients or provider accounts
do not create additional operator tenants. Future multiplayer connects separate
fleets through explicitly authorized interactions while each retains its own
state and credential custody.

### unified-coordination-product

The integrated profile has one selected product release and lifecycle contract,
with thin client, Gateway and node roles and independently isolated execution
where lifetime, cost or privilege requires it. Local use does not require remote
fleet adoption; a fleet client does not silently become a local controller when
its selected Gateway is unavailable.

Copilot adapters provide guidance and typed invocation, not installation,
updates or persistent supervision. Independently useful guidance and existing
standalone service adoption remain available outside this profile.

The product's release authority is the single owner of release selection and
rollout outcomes for the hosts that adopt it. It decides which coordinated
release each role runs, which optional heavy environments a role includes,
and when a per-service last-known-good hold applies. It records each target's
own outcome from attributable installer evidence. Role closures are the unit
of installation. A client gets only its thin closure. A Gateway or node adds
only the control, execution, and optional environments that its role and
configuration select. A host's installer realizes the selection, its
supervisor keeps authorized residents running, and neither one selects a
release (plugin-services
[*one-authority-per-lifecycle-decision*](../plugin-services/README.md#one-authority-per-lifecycle-decision)).
A delegated venue's runtime is observed and reported in its own right. A
central host's evidence never stands in for it.

Accepted work survives controller/connection replacement. Authority is fenced at
the resource/effect owner; stale observations, grant expiry or healthy replacement
processes alone never establish safe reassignment. Rolling updates report
individual compatibility/readiness outcomes rather than fictitious fleet-wide
atomic convergence.

### driver-based-fleet-adoption

An adopter can choose static-machine or provider-managed membership without
changing the controller's purpose or its consumers. Driver selection is explicit;
there is no silent fallback to another substrate, account, or resource pool.
Existing provider registries remain authoritative for identities and reachability.
Exactly one selected driver/provider owns each target's substrate reachability;
Gateway route health is a derived connection observation, not a competing registry.

### shell-independent-service-control

The minimum useful fleet exposes authorized discovery, health, task interaction,
worktree operations, and bridge communication without a working remote shell.
Full remote Copilot driving, a tunneled shell, and interactive applications are
additional declared capabilities, not prerequisites for service control.

### discoverable-service-routing

Clients reach named, explicitly offered services through one discoverable
Gateway surface. Routes preserve the service's supported API and authorization
semantics; a service listener does not become generally exposed merely because
its machine enrolled. Gateway routing is an alternative to supported direct
access, not an unannounced replacement for it.

### resumable-observation

Clients subscribe to machine, service, task, worktree, and session changes.
Connection recovery restores a coherent view through replay or an authoritative
snapshot, with explicit stale/disconnected states. Subscription machinery
augments the owning services' observation contracts rather than replacing their
durable records.

Fleet activity is incrementally consumable through resumable cursors without
repeated full-fleet scans. Events retain origin and work relationships, and
coverage gaps remain visible; a combined feed does not invent global causal
ordering or treat silence from an offline origin as inactivity.

The shared backing store accepts attributable push updates and serves coherent
current queries and subscriptions from the same committed view. Machines and
containers have independently fenced origin identities. Duplicate, delayed,
discontinuous or disconnected reporting remains diagnosable without rolling
back newer facts or manufacturing completeness. Bounded recovery uses replay
or an authoritative snapshot, and a slow client cannot stall producers or
other readers. Observation expiry is not evidence that accepted work stopped.

### bounded-desired-state-control

The controller can request named deployment and basic configuration
reconciliation for authorized target machines. Connectors resolve these requests
through trusted local declarations and the existing installer/update authorities.
Broadcast means an explicit set of recipients with individual acknowledgements
and outcomes, not arbitrary command execution or a claim that every host updated.

### capability-scoped-credential-access

Registered consumers can borrow only the credentials appropriate to their
declared identity, account, audience, resource, and lifetime. Multiple eligible
providers may improve availability without widening authority or conflating
their identities. Missing/expired access is visible and fails closed; source
unavailability does not silently select a more privileged credential.

### standalone-service-installation

Controller and connector runtimes are independently installable and operable
without Copilot or marketplace hooks. Their lifecycle includes immutable runtime
slots, attributable readiness, safe updates/rollback, and owned uninstall.
Copilot plugins provide the agent-facing skills/tools and connection surface;
they are not the service host's required process supervisor.
Resident controller/connector updates follow the shared graceful-cutover
contract: validate the replacement before activation, transfer connection and
registration ownership explicitly, drain accepted operations, then retire the
predecessor. Failed promotion preserves the last healthy runtime and its work.

### repeatable-role-fleet-adoption

An adopter can bootstrap an explicitly chosen Linux role fleet through
repeatable, noninteractive operations after approving its capacity and trust
inputs. Initial container-manager setup and machine enrollment expose supported
automation rather than requiring undocumented browser ceremonies.
Unavoidable attended identity or administrative decisions are explicit
prerequisites, not hidden manual repairs.

The deployment adapter is replaceable: adopting one manager does not bake its
resource model into service contracts or require it for direct/standalone use.
Inspection and planning do not install a manager, enroll hosts or start roles.

### role-selective-system-lifecycle

Client, controller and connector installations have explicit, independently
usable install, update, diagnostics and owned-uninstall flows. The client has
no heavy server dependencies or automatic hosting side effects. Server roles
use existing service distributions and runtime activation authorities rather
than introducing competing installers for the same service state.

Live configuration changes are validated and applied atomically by the owning
controller or connector. Invalid candidates preserve the last valid effective
configuration and report why they were rejected. Changed trust, placement or
roles require their own authorization and safe transitions; accepting a config
file does not silently create capacity or activate a previously retired role.
Restart-only changes are reported explicitly, not disguised as successful reload.

The same configuration-directed lifecycle supports cold installation,
incremental in-place migration, interrupted-operation recovery and
data-compatible rollback. Rerunning an unchanged request does not duplicate
capacity, storage, services or writers. Unsafe data downgrades fail explicitly
instead of presenting an old executable as successful rollback.

### safe-template-and-live-refresh

"Set up my fleet" resolves the adopter's existing configuration before planning
recommended placement against live resources and available quota. Empty fleets
can be outfitted within approved capacity; inadequate quota produces an explicit
smaller proposal or unmet-capacity outcome, never silent quota expansion.
Refresh shows additions, drift and proposed changes while retaining custom roles,
novel capabilities and work. Unknown live resources remain observed, not deleted.

Containers and code venues require inspection and source-control finalization
before cleanup. Configuration removal, resource retirement and data destruction
are separate approved operations; a template difference alone cannot authorize
them. Stale inventory or uncertain ownership cannot produce a cleanup plan
presented as safe convergence.

An adopted Gateway can subscribe to the user's versioned configuration and
reconcile approved changes and releases without continuous client supervision.
Private configuration repositories need not provide CI: local validation and
explicit user approval gate major reconfiguration. Approval binds the actual
change, not arbitrary later revisions. Invalid or unapproved desired state
preserves the last valid deployment.

### user-controlled-fleet-observation

Gateway-backed telemetry and monitoring belong to the operator's fleet, with
explicit collection, retention, access and export choices. Fleet-wide health
separates services, connectors and execution capacity and remains diagnosable
when a target is offline. Generic telemetry does not depend on an external
analytics vendor or send data outside the fleet without consent.
Organization-governed telemetry adapters are separate policy-owned integrations;
they cannot silently widen the portable collector's scope.

## Behaviors

### one-owner-per-kind-of-state

The controller owns enrollment/connection registration within its fleet scope,
desired reconciliation intent, and
delivery/observation receipts. It derives worktree/task/session/service state
from its existing authority. A Gateway acknowledgement does not replace the
owning service's acceptance or completion record. An integrated canonical module
may replace that owner only through an explicit state/obligation migration;
the old and new owner never both authorize the same work.
Enrollment does not transfer the selected driver's target identity, substrate
reachability, capacity, or lease authority to the controller.

### recoverable-not-connection-owned

Machine and controller restart, network transitions, sleep/resume, and
logout/login are ordinary events. Reconnect is bounded, instance-owned, and
restores registration and subscriptions without duplicating remote work.
Accepted operations reconcile uncertain outcomes instead of blindly replaying
side effects. Offline recipients remain visibly unresolved.

### explicit-availability-roles

Auto, on-demand, client-only, service-host, and credential-provider roles are
explicit configuration choices, not consequences of tool installation or of a
role's code being present in the selected release. Updates, rollbacks, and
release changes keep a disabled or retired role disabled.
An operator client may maintain one logical Gateway connection without becoming
a feeder or per-target keepalive owner. Required attended credential-provider
dependencies are reported honestly rather than implying unattended availability.

### authenticated-scoped-and-revocable

Routing and control are encrypted and authorized per principal, service,
operation, fleet scope, and target. Reconnect rechecks expiry/revocation.
Membership grants neither arbitrary shell access nor the authority to alter
another fleet's configuration. Secrets do not enter images, manifests, command
arguments, event logs, or durable operation receipts; any bounded cache follows
its credential provider's explicit custody contract.

### diagnosable-without-the-failed-path

Local status and bounded diagnostics remain usable when the Gateway or remote
shell is unavailable. Reports separate connector, driver, route, identity,
credential-provider, and downstream service failures. Registered, installed,
connected, ready, and converged are distinct states.

### headless-owned-and-resource-bounded

Installers and daemons, not per-session agent instructions, own startup and
reconnect. Automated discovery cannot create interactive terminals or prompt
unattended sign-in. Backoff, queues, buffers, workers, and tunnel resources are
bounded; slow recipients and heavy services do not starve unrelated control.
Uninstall and role retirement remove only owned registrations/processes and
cannot silently restore a retired feeder role on a client.

### role-placement-preserves-service-authority

Desired role placement has explicit per-instance acceptance and observed
readiness. Host loss, a container restart, or manager outage does not turn
uncertain placement into successful convergence. Replica expansion remains
within admitted capacity; automatic movement of stateful writers requires
verified storage continuity and fencing rather than generic rescheduling.
Updates preserve the owning service's safe activation, drain and data
compatibility contract, including when an external manager performs container
replacement.

## Non-Goals / Boundaries

- No multi-operator tenancy inside a fleet and no hosting another operator's
  state or credentials. Cross-fleet collaboration does not enroll that
  operator into the local fleet or merge the fleets' private stores.
- No required provider, cloud substrate, tunnel product, identity vendor, or
  adopter-specific inventory in the portable controller.
- No mandatory Gateway dependency for standalone services or existing direct/
  SSH workflows, and no automatic migration of unrelated fleets.
- No competing task queue or worktree registry beside an existing authority;
  integrated replacement requires explicit migration. No replacement credential
  issuer, session host or package manager merely from fleet enrollment.
- No arbitrary shell/script payloads hidden inside deployment broadcasts,
  automatic capacity expansion, or destructive provider actions on mere enrollment.
- No claim of transparent exactly-once side effects from transport retries.
- No dependency on an unreleased session protocol before basic control is useful.
- No pinned wire protocol, schema, port, package layout, or rollout plan in this
  intent document.

## See Also

- Parent: [agent-fabric](../agent-fabric/README.md)
- Related: [host-resource-providers](../host-resource-providers/README.md),
  [venue-parity](../venue-parity/README.md), [session-hosting](../session-hosting/README.md),
  [PR service](../plugins/agent-pull-requests/README.md)
- Lifecycle: [plugin-services](../plugin-services/README.md), [installer](../installer/README.md),
  [runtime assembly & artifact admission](../plugin-services/runtime-assembly/README.md)
- Cutover: [graceful daemon cutover](../../docs/patterns/graceful-daemon-cutover.md)
- Auth-relay reality: [credential-relay](../../libs/credential-relay/README.md)
- Reality: [architecture](../../docs/architecture.md), [configuration](../../docs/configuration.md)
- Realization planning: [routing foundation](../../efforts/active/machine-fleet-routing-foundation/README.md)
- Deployment planning: [Linux role fleet](../../efforts/active/linux-role-fleet/README.md)
