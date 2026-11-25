# Entra Gateway user authority - Vision

- **Subject:** Public signing trust and access-token caller policy for an explicitly selected Entra Gateway application.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-11-24
- **Reality docs:** [Entra Gateway driver](../../../../libs/gateway-entra/README.md)

## Purpose & Intent

Ordinary Gateway user access should retain the operator's existing Entra
application and login-scoped credential producers. A reusable adapter should
connect that authority to the common Fleet verifier without creating an issuer,
re-signing tokens or requiring a separately enrolled machine signing key.

Application selection, caller mappings, permission policy and activation should
remain adopter-owned configuration. Making the adapter available should perform
no discovery, login, policy publication or service action.

## Concepts & Components

The adapter should own tenant-pinned public signing-trust acquisition and
provider-specific access-token constraints. The common Fleet engine should own
signature verification and current caller/operation admission. Existing
credential producers should retain issuance, refresh and attended challenges;
the Gateway and deployment manager should retain hosting and rollout ownership.

## Features

### original-access-token-authority

Only original access tokens for the selected API, tenant, delegated permission
and mapped user should reach caller admission. ID tokens, unrelated resources,
application-only substitutes and forwarded identity should not acquire grants
by matching a role or user identifier.

### bounded-public-trust

Public signing keys should come from the selected tenant's authenticated
endpoint, with bounded response size, finite freshness and an interruptible
total acquisition deadline. Failed acquisition should preserve the existing
bounded serving selection without extending its lifetime.

### explicit-provider-composition

Provider preparation should return only immutable public trust. Broader token
or permission shapes should evolve through explicit common-verifier contracts,
not provider-created identities or silent fallback into another profile.

## Behaviors

### independent-grants-and-lifetimes

User authentication, route access, operation permission, service identity and
machine enrollment should remain independent. Provider-permitted refresh should
not introduce a new human-presence countdown; withdrawal and expiry should
remain visible and effective under their actual owners.

### attributable-cancellation-and-evidence

Acquisition should terminate and settle only its own finite work on timeout.
Failure evidence should be value-free and distinguish source, artifact,
installation, authenticated consumer and actual deployment qualification.

## Non-Goals / Boundaries

- The adapter should not create directory applications, grant consent, store
  credentials, replace user-side pumps or become a general Azure client.
- It should not deploy or restart the Gateway, reconcile containers, transfer
  writer custody or authorize work from successful discovery.
- Shared source should contain no operator identity, selected application,
  private topology, credential values or operational history.

## See Also

- Parent vision: [Gateway](../README.md)
- [Explicit user bearer callers](../../../../fleet-manager/docs/gateway-user-callers.md)
- [Portable Gateway human sessions](../README.md#human-sessions-and-machine-enrollment)
