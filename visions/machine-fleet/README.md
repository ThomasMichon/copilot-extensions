# Machine Fleet - Vision

- **Subject:** A driver-based fleet of machines and services coordinated through an optional central Gateway controller.
- **Scope:** leaf (cross-cutting child of agent-fabric)
- **Status:** Active
- **Last revised:** 2026-10-10
- **Reality docs:** [`docs/architecture.md`](../../docs/architecture.md), [`docs/configuration.md`](../../docs/configuration.md)

## Purpose & Intent

Make a declared set of machines a coherent, reachable, observable control fabric.
An operator can discover offered services, inspect work, reconcile deployments
and configuration, and control supported sessions without first obtaining a
remote shell. A central Gateway provides rendezvous and authorized routing,
while each existing service remains the authority for its own state.

Fleet adoption is optional and additive. Standalone local services, direct
connections, and existing SSH-based workflows retain their value without a
Gateway. A roaming operator client is not required to keep remote work alive.
The same fabric can grow into a scalable fleet of Linux hosts running explicit,
containerized roles from the existing service ecosystem, without making one
container manager or a private infrastructure provider its required substrate.

## Concepts & Components

### Harness-declared fleet

Harness-side configuration declares the selected fleet driver, machine membership,
roles, desired service placement, permitted operations, and credential-provider
references. Private inventories, accounts, and deployment policy remain with the
adopter, not the portable controller. Effective configuration and its provenance
are inspectable; installing connection tooling alone does not enroll a machine
or enable background services.

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

### Existing service authorities

`agent-worktrees` owns durable worktree agency and source-control obligations;
`agent-dispatch` owns task/claim state; `agent-bridge` owns coordination;
`agent-machines` owns declared machine-state reconciliation; session-host
providers own their execution/session substrate. Fleet adapters route supported
operations to these owners rather than building competing stores or supervisors.
Index/search and other harness-side services can be registered independently of
interactive agent sessions.

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

## Features

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

## Behaviors

### one-owner-per-kind-of-state

The controller owns enrollment/connection registration within its fleet scope,
desired reconciliation intent, and
delivery/observation receipts. It derives worktree/task/session/service state
from its existing authority. A Gateway acknowledgement does not replace the
owning service's acceptance or completion record.
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
explicit configuration choices, not consequences of tool installation.
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

- No required provider, cloud substrate, tunnel product, identity vendor, or
  adopter-specific inventory in the portable controller.
- No mandatory Gateway dependency for standalone services or existing direct/
  SSH workflows, and no automatic migration of unrelated fleets.
- No second task queue, worktree registry, credential issuer, session host, or
  package manager replacing existing authorities.
- No arbitrary shell/script payloads hidden inside deployment broadcasts,
  automatic capacity expansion, or destructive provider actions on mere enrollment.
- No claim of transparent exactly-once side effects from transport retries.
- No dependency on an unreleased session protocol before basic control is useful.
- No pinned wire protocol, schema, port, package layout, or rollout plan in this
  intent document.

## See Also

- Parent: [agent-fabric](../agent-fabric/README.md)
- Related: [host-resource-providers](../host-resource-providers/README.md),
  [venue-parity](../venue-parity/README.md), [session-hosting](../session-hosting/README.md)
- Lifecycle: [plugin-services](../plugin-services/README.md), [installer](../installer/README.md)
- Cutover: [graceful daemon cutover](../../docs/patterns/graceful-daemon-cutover.md)
- Auth-relay reality: [credential-relay](../../libs/credential-relay/README.md)
- Reality: [architecture](../../docs/architecture.md), [configuration](../../docs/configuration.md)
- Realization planning: [routing foundation](../../efforts/active/machine-fleet-routing-foundation/README.md)
- Deployment planning: [Linux role fleet](../../efforts/active/linux-role-fleet/README.md)
