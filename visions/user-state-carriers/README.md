# User-State Carriers - Vision

- **Subject:** Optional user-owned synchronization carriers for independently
  governed Vault artifacts, machine heartbeats and portable preferences
- **Scope:** leaf (cross-cutting capability)
- **Status:** Draft
- **Last revised:** 2026-11-05
- **Reality docs:** [Configuration](../../docs/configuration.md),
  [agent-vault](../../plugins/agent-vault/README.md)

## Purpose & Intent

A user should be able to reuse an explicitly selected personal synchronization
carrier across their approved machines. OneDrive should be a supported carrier
choice for encrypted Vault artifacts, high-latency health broadcasts and
nonsecret portable preferences. Reusing an established carrier should avoid
requiring a second always-reachable fleet service for these exchanges.

The carrier moves data; it does not own the meaning, authority or activation
of everything it carries. Sharing a storage provider never joins the trust
boundaries or audiences of credentials, health observations and configuration.
Other supported carriers and local-only use remain first-class choices.

## Concepts & Components

- **Explicit binding.** The user selects an account-qualified carrier,
  approved location, participating machines and permitted data classes.
  Bindings resolve on each machine; a repository does not bake in host paths.
- **Separate domain owners.** Vault owns credential custody, heartbeat
  publishers own their observations, and configuration owners govern portable
  preferences. A shared carrier is not a shared database or control plane.
- **Eventually replicated material.** Local production, local publication and
  peer receipt are different facts. A synchronized folder offers asynchronous
  exchange, not remote durability acknowledgements or fleet consensus.
- **Explainable adoption.** A reader can distinguish received preferences,
  accepted configuration and actual running state, including local overrides,
  conflicts, unsupported capabilities and unavailable synchronization.

## Features

### one-carrier-separate-responsibilities

An adopter already using OneDrive for encrypted Vault material can explicitly
select it for heartbeats or preferences without creating a competing sync
service. Each data class retains separate access, ownership, retention and
conflict policy. Permission to share one class does not enable another or
broaden its audience. No provider becomes a mandatory suite dependency.

### encrypted-vault-artifact-carriage

Only the Vault owner's approved encrypted material participates in Vault
replication. Keys, unlock mechanisms, authentication transactions, rotation,
recovery and concurrent-write correctness remain with that owner. Copying an
artifact is neither an unlock nor proof that credentials are usable or safe to
serve. Health publication does not require unlocking the Vault and does not
copy its secrets into another data class.

### out-of-band-health-carriage

The carrier distributes
[hourly machine-health history](../telemetry/machine-heartbeats/README.md)
without depending on the SSH or Gateway paths being observed. Multiple
machines publish only their own observations; peers consume bounded,
age-qualified replicas rather than writing one contested fleet summary.

### supported-portable-preferences

User-side preferences can follow the user across approved machines, including
supported presentation choices and suggested setup defaults. Preference scope,
source and revision remain visible. Each configuration owner validates its own
supported settings and applies its established precedence, preserving local
bindings, machine-specific choices and non-overridable policy.

Preferences carry nonsecret data, not credentials, executable commands or
instructions that authorize actions. Receiving a preference does not grant
installation, login, repository creation, deployment, access expansion or a
binding change. Effects still use the owning capability's existing explicit
adoption and approval rules.

## Behaviors

### opt-in-without-a-new-authentication-owner

Carrier adoption is explicit and uses the selected provider's established
installation and account flow. Discovery never installs a sync client,
starts login, searches unrelated storage or substitutes another account.
Unavailable carrier support remains an optional unavailable capability, not
permission to invent a new credential path.

### domain-specific-conflicts-and-recovery

Delayed, reordered, duplicate, deleted or conflicting replicas are expected
conditions. Each domain preserves last-known accepted state and applies its
own validity and recovery rules. A provider's conflict suffix or newest file
timestamp is not authority to replace credentials or overwrite preferences.
Unresolved preference conflicts remain visible rather than silently choosing
a winner or reverting an explicit local choice.

### complete-minimized-publication

Consumers admit complete, bounded records from their configured participant
and data scope. Partial files, unexpected writers and unsupported formats are
not valid updates. Health and preferences expose only necessary, nonsecret
material under their selected audience; encryption of a neighboring Vault
artifact does not protect their plaintext or admit sensitive diagnostic data.
Carrier access is not a substitute for the domain's validation.

### offline-use-with-honest-freshness

Permitted local work and accepted local preferences remain usable during sync
outages. Last-known material is identified as such; an old copy does not become
current merely because it was downloaded again. Local writes are not reported
as received by all peers. Retention and deletion claims distinguish active
replicas from provider backups, version history and recycle bins.

## Non-Goals / Boundaries

- No distributed lock, lease, leader election, work queue or remote-command
  channel built from synchronized files.
- No implicit configuration authority, preference application, secret export
  or transfer of a domain owner's accepted responsibility.
- No mandatory OneDrive, cloud account, Vault, Gateway or online connection
  for otherwise independent local capabilities.
- No universal merge strategy or atomicity claim spanning multiple machines.
- No prescribed directory layout, record schema, provider API or installation
  sequence in standing intent.

## See Also

- [Vision index](../README.md).
- [Machine health heartbeats](../telemetry/machine-heartbeats/README.md) and
  [portable telemetry](../telemetry/README.md).
- [Vault](../vault/README.md).
- [Configuration workbench](../installer/configurator/README.md).
- [Plugin services](../plugin-services/README.md).
