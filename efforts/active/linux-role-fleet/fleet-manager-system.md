# Standalone fleet-manager system proposal

Back to [the effort](README.md). **Proposed distribution boundary, not a shipped
runtime.** This extends the existing routing foundation; it does not replace its
authentication/service-authority contracts.

## System topology

```text
harness / user machine
  fleet-manager client + supplied scoped credentials
                 |
         encrypted controller API
                 |
designated remote controller host
  fleet-manager controller / Gateway
  selected deployment adapter + Komodo Core / database
                 |
   approved manager / outbound connector operations
                 |
admitted worker hosts
  Periphery / fleet connectors + explicitly placed service roles
```

The user client is not a manager host, credential feeder or direct worker
deployment agent. It has no automatic Docker/Komodo/database prerequisite.
Host provisioning, network exposure and controller placement are explicit
adopter declarations; no private infrastructure implementation enters this
portable proposal.

## Distribution and existing ownership

The upstream `fleet-manager` home carries its CLI and independently installable
controller/connector components, plus role manifests/adapters that consume
existing service distributions. A unified entry point does not require one
monolithic dependency set: client installation must remain thin and must not
pull server dependencies or activate hosting roles.

Reuse the shared installer, version-slot, endpoint discovery, process
supervision and graceful cutover contracts. The independently owned standalone
index campaign (#5768) supplies the distribution contract and staged packaging
work; its complete install/activation lifecycle is a dependency to verify, not
a shipped role assumed by this proposal. `fleet-manager` orchestrates approved placement; it does not become
a second index installer, task queue, credential issuer or arbitrary package
manager. Komodo remains a selected deployment backend, not the public API
contract and not a mandatory installation on clients.

## Requested lifecycle surfaces

Each explicit role supports install, upgrade, inspect/diagnose and owned
uninstall. Client install configures a remote connection only. Controller
bootstrap explicitly selects its role, deployment adapter, authenticated API,
storage and startup; worker adoption selects connector/Periphery responsibilities.

Plan/inspect never installs or mutates hosts. Apply resolves only trusted named
role operations under controller policy. Separate provisioning and credential
approval precede first host activation. Uninstall removes only attributed
registrations/runtime, preserves documented data/config by default, and cannot
implicitly destroy an existing manager or worker workload.

Immutable artifacts and passive health/config checks precede activation.
Accepted work drains at the owning service's boundary. Failed activation keeps
the prior healthy configuration/runtime; post-commit recovery uses the shared
commit-forward rules. Container restart/manager acknowledgement is not evidence
that a stateful writer can safely move or that service data can roll back.

## Live configuration

Controller and connector own validated configuration revisions, effective
state and non-secret receipts. A submitted candidate is not effective until
schema, references, authorization and safety checks succeed. Invalid candidates
leave last-valid configuration untouched and return a bounded diagnostic.

Changes distinguish live-applicable, safe-transition and restart-required
categories. Trust revocation, role retirement, service placement and credentials
must not inherit generic reload permission. Persist the accepted revision before
reporting it effective, publish per-recipient application outcomes, and reconcile
partial/uncertain results rather than claiming fleet-wide convergence.

Local user configuration contains endpoint/trust and credential-provider
references, never server inventories or embedded secrets. Client-side config
reload cannot activate a server role.

## API and auth boundary

The harness CLI sends typed named requests through the configured encrypted
controller API with supplied auth. Operator control, access to an offered
service, connector enrollment and manager administration remain distinct
authorities. The routing proposal specifies these boundaries but does not itself
deliver an authentication implementation.

The operator separately selected external JWT/JWKS verification for configured
operator and service principals plus separate connector proof-of-possession
during the routing continuation. That is an explicit mechanism direction, not
an already shipped foundation: the authentication adapter remains unpublished
and needs its own reviewed validation/integration. Each principal's role and
grants are independently configured; sharing signature verification does not
merge operator and downstream service access. Manager admin/onboarding authority
is separate and never forwarded from arbitrary client headers.

Permission is scoped to fleet, target, service, operation and current generation.
The controller returns durable operation identity and acceptance/outcome receipts;
the actual service/manager remains authoritative for completion. Client logout,
sleep or disconnect does not kill remote work.

No arbitrary command, remote-authored shell/config payload or token-selected
backend URL is exposed. Direct service operation remains possible without
adopting the fleet, but an adopted thin client never silently switches to local
worker mutation when the controller is unavailable.

## First implementation sequence (agent-recommended)

1. Agree and review distribution/dependency boundaries and role-selective lifecycle.
2. Implement thin client config/request/error contracts and an authenticated
   synthetic controller endpoint; prove install/uninstall has no hosting effects.
   Resolve and review the selected authentication mechanism's issuer/audience,
   separate role/grant mapping, connector proof, revocation and replay/fencing
   contracts before treating synthetic verification as production authorization.
3. Implement controller configuration persistence and live-reload transactions
   with existing service lifecycle primitives.
4. Connect fixed-service routing and named Komodo deployment adapters through
   their existing bounded authority contracts.
5. Prove separate client/controller/worker processes and available controlled
   Linux venues; keep production inventory/source access and cutover downstream.

This proposal does not claim completed controller, auth-state persistence or
live-config runtime. The effort's Plan and Validation Plan remain completion
gates after this amendment merges.
