# Composable Fleet Loadouts - Vision

- **Subject:** A capacity-aware design surface for repository-backed role allocations
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-23

## Purpose & Intent

Let an operator design a fleet as a grid-based loadout of capabilities on named
machines. Reusable role templates become concrete allocations with inspectable
CPU, memory, storage and other supported resource requirements. The designer
shows what fits, what is already reserved or used, and why a proposed allocation
is blocked rather than reducing unlike resources to one misleading capacity
score.

Fleet-manager authors expectations and produces the final desired composition
in the adopter's knowledge/configuration repository. The Gateway-side
controller independently pulls published sources, prepares a deployment against
current admitted state, notifies the designer and waits for confirmation of
that exact plan. The client is not the runtime deployment owner.

## Concepts & Components

- **Role template:** a reusable capability declaration contributed by an upstream
  plugin or repository, with configurable slots, container/configuration
  composition, supported operations and resource requirements.
- **Adopter registrar:** the knowledge-repository-owned selection and
  actualization of templates, overrides, named targets and private bindings.
- **Allocation:** a concrete instance of a selected role on a specific admitted
  machine, including its required support capabilities and capacity reservation.
- **Loadout grid:** the common designer view of desired allocations, observed
  utilization, reservations, limits, headroom and unmet requirements.
- **Prepared deployment:** the controller's current, version-bound realization
  of published desired composition, distinct from approval and observed readiness.

## Features

### contributed-templates-and-configurable-slots

Plugins and repositories can contribute discoverable role templates without
forking the fleet designer. Their slots compose supported container
specifications and service configuration, not arbitrary remote shell commands.
Template provenance, compatibility and selected versions remain visible.

Actualization preserves adopter overrides and custom roles. Missing templates,
unsupported slot values and incompatible combinations produce explicit
diagnoses rather than dropping allocations or selecting an undeclared fallback.
Changing an upstream recommendation is not permission to overwrite private
choices or retire an existing resource.

### knowledge-owned-final-composition

The registrar records template selections, completed slots and named-machine
allocations in the knowledge/configuration repository. Fleet-manager validates,
previews and commits the final desired composition through that repository's
configured publication flow. Runtime observations do not silently rewrite it.

The controller acquires the published repository pairing itself. A client can
request refresh and approve the prepared result without uploading a private
configuration document. Temporary diagnostic adjustments remain distinct from
persistent source changes.

### multidimensional-capacity-grid

The designer presents each named machine's allocations against its admitted
CPU, memory, disk and other declared resource dimensions. Reservations,
observed utilization, hard limits, permitted sharing and operating headroom
are distinguishable. Idle use is not proof that a reservation can be reassigned,
and excess capacity in one dimension cannot satisfy another.

Persistent storage, expected growth and state ownership remain visible alongside
ephemeral execution space. Unknown or stale capacity cannot make a placement
appear safe. Dependencies and shared infrastructure consume capacity too;
the view neither hides their cost nor counts the same owned shared instance
once for every consumer.

### complete-support-closure

Selecting a capability expands its required service and control composition:
credential-provider bindings and relays, permitted tunnels, API/UX namespaces,
persistence and control-side services. Each dependency has an explicit owner,
placement and supported readiness contract.

The final plan exposes those requirements before confirmation. A reachable
application without its required authentication, route or control surface is
not a completed loadout. Missing credentials remain an owner-resolved
prerequisite; templates and committed composition never carry secret bytes.

### configurable-actions-runner-capacity

Self-hosted GitHub Actions execution is an optional first-class role.
An adopter can select a named eligible machine, repository or organization
scope, supported execution profile and bounded ephemeral runner capacity,
with explicit image/toolchain, resource, trust, egress and credential bindings.

The chosen runner controller retains individual runner registration,
job execution and autoscaling within the admitted ceiling. Its deployment
manager does not become a competing runner scheduler. Workload placement and
workflow routing remain distinct reviewed decisions; available self-hosted
capacity does not imply hosted overflow or automatic fallback.

### confirmed-bounded-outfitting

The Gateway prepares and publishes the complete deployment proposal and its
outfitting bound for inspection through fleet-manager. Confirmation applies to
the exact source selection, composition, placements and material admission
assumptions. A changed proposal needs a fresh decision.

After confirmation, deployment and verification remain owned independently
of the client connection. Within the admitted duration, the controller exposes
verified end-to-end readiness or a precise blocked, failed, partial or uncertain
outcome. It does not leave an indefinitely green-looking deployment merely
because containers started. Recovery preserves accepted work, unresolved effect
custody and the last valid serving state where supported.

## Behaviors

### compose-existing-owners

The designer and controller compose existing substrate, lease, deployment,
service, credential and transport authorities. Configuration selection,
source publication, deployment confirmation and effect-side authorization are
separate decisions. Neither templates nor confirmation create new machines,
credentials or broader access implicitly.

### explain-and-preserve

Every allocation can explain its template origin, selected configuration,
target, capacity cost, dependencies and current outcome. Invalid or conflicting
desired state preserves the previous valid configuration. Removing a slot
from source is not permission to destroy retained work, data or a machine.

## Non-Goals / Boundaries

- No private host inventory, account, credential or deployment policy embedded
  in shared templates.
- No replacement container scheduler, credential issuer or service installer
  merely to actualize a loadout.
- No mandatory Kubernetes or particular deployment manager for unrelated roles.
- No unconditional readiness guarantee during source, credential, manager or
  target outages, and no success inferred from a submission receipt.
- No automatic live deployment from a source merge without its declared
  confirmation and current admission.

## See Also

- Parent: [machine fleet](../README.md)
- Reconciliation: [declarative Gateway](../gateway/README.md)
- Reality: [fleet-manager](../../../fleet-manager/README.md)
- Realization: [Linux role fleet](../../../efforts/active/linux-role-fleet/README.md)
