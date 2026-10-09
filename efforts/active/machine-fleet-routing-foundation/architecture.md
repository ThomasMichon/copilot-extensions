# Portable Gateway routing foundation - architecture proposal

Back to the [effort](README.md). **Proposed; no runtime is implemented here.**

## Minimum useful boundary

```text
operator client -- encrypted authorized request --> optional Gateway controller
                                                        |
                                      authenticated outbound connector session
                                                        |
                                          locally declared service adapter
                                                        |
                                          existing authoritative service
```

This is fixed-service routing, not a general HTTP proxy or remote shell. The
initial operations are service discovery, health and index search. A connector
is not authorized to install packages, change machine configuration, launch
arbitrary commands or supply resource credentials merely by enrolling.

| Owner | Authority | Not transferred by enrollment |
|---|---|---|
| Selected substrate driver | Stable target identity, reachability, lifecycle/leases it actually offers | Capacity/destructive approval and provider credentials |
| Controller | Scoped enrollment/connection registration and routing policy | Provider registry, service data or task/session state |
| Connector | Its instance connection and locally approved service offers | General process execution or remote-authored configuration |
| Service adapter | A typed route to a fixed trusted backend and its API contract | Source/account grants or downstream acceptance semantics |
| Existing service | Supported requests, health, source access and durable state | Queue supervision, indexing/model lifecycle or work ownership |
| Credential provider | Issuance, identity/audience, renewal and custody | A blanket shared token pool |
| Lifecycle authority | Install/update/activate/drain/retire of its own runtime | Lifetime ownership of independently hosted service work |

## Driver contract

Start with explicitly selected static machines through `agent-ssh`. Resolve
canonical target identity and transport through its existing authority; do not
copy aliases into a second authoritative mesh. Gateway connection freshness is
a derived observation, never evidence that substrate admission succeeded.

The versioned contract must expose provider identity/provenance, stable target
identity, supported capabilities and explicit unavailable/unsupported results.
The static driver does not create/delete machines or invent a lease. Future
managed drivers preserve their own capacity, distributed admission and fencing;
no generic fallback may weaken those gates.

Bootstrap is an explicit local installation or supported provider operation.
Discovery/readiness cannot cold-start an interactive connection or sign-in.
Selecting a driver does not enroll every target it can discover.

## Standalone programs and configuration

Controller and connector are ordinary service distributions with their own
executables and lifecycle, not mandatory Copilot plugins. Final package names
and shared-contract placement are decided against the import/dependency graph
before code is added. A thin future plugin is a connection surface only.

Service configuration is explicit and validated without a harness checkout or
knowledge binding. It includes fleet scope, target/principal bindings, selected
driver provenance, approved service offers, credential-provider references,
resource limits and roles. Secret material is injected by its provider and is
absent from images, manifests, argv, diagnostics and durable receipts.

Client-only, on-demand connector and resident service-host roles are separate
choices. Installation does not imply enrollment, auto-start or credential
feeding. Native controller endpoints follow local-first OS socket/pipe
discovery; crossing a machine boundary is an encrypted opt-in through the
selected trusted transport. Any required TCP listener is OS-assigned and
discovered, not a portable fixed port.

## Registration and instance ownership

An authenticated connector initiates an outbound session and advertises only
locally approved services. Controller authorization independently checks fleet,
target and service binding; self-asserted identity is not enrollment proof.
Operator access, connector enrollment and downstream service access are distinct
authorities. Expiry and revocation are rechecked on reconnect and requests.

Versioned offers carry backend installation identity, adapter/API compatibility
and freshness. Connection/offer generations fence duplicate connections and
cleanup: an old instance cannot replace or remove a successor's registration.
Disconnect makes the offer stale/unavailable until renewed and health-verified.
Neither a cached offer nor a connected socket is proof of service readiness.

Persistent enrollment/policy remains separate from ephemeral connection state.
Controller restart requires authenticated re-registration and fresh health
proof before routing. This first slice provides authoritative current snapshots,
not durable event replay or task/session subscriptions.

## Fixed-service route

The controller maps an authorized logical service/operation to an approved
connector offer; the connector resolves that offer through trusted local config.
Clients cannot choose a backend URL, executable, filesystem path, arbitrary
method/header or unrestricted query syntax.

Adapters validate typed request parameters, backend identity/readiness and
response shape/size. The index adapter preserves the existing query contract
and corpus/source access restrictions. Gateway membership is not permission to
search every source. Do not forward client-supplied Authorization headers or
mint broader service authority. A single-corpus deployment still needs an
explicit binding, not an inferred trust-domain ACL.

Bound request/body/response sizes, timeouts, per-principal/global concurrency,
pending work and reconnect resources. Propagate cancellation where supported;
otherwise bound and account for downstream work until it ends. Slow or failed
services cannot starve registration, health or unrelated routes. Errors identify
the actual failing layer without raw credentials, unbounded logs or
success-shaped empty results.

Health/liveness distinguish driver reachability, connector connection, route
freshness, backend instance identity, service readiness and model readiness.
Compatibility is explicit: negotiate supported contract/API versions or reject;
never assume any remote HTTP listener is an interchangeable service.

## Lifecycle and failure boundary

Use [immutable runtime slots](../../../../docs/patterns/durable-vs-versioned-runtime.md)
and [graceful cutover](../../../../docs/patterns/graceful-daemon-cutover.md).
Controller and connector are resident daemons and have no blanket exemption.
Validate a passive replacement before activation, transfer registration/routing
ownership explicitly, stop new requests on the predecessor, drain accepted
requests to a safe boundary, then retire only its proven generation.

Failed pre-commit activation leaves the old route healthy. Post-commit failure
uses the established commit-forward/recovery contract, not two active owners.
No passive instance may seize a predecessor's connection or singleton resources.
Updater termination must not kill the active successor or the hosted service.
Standalone data/config compatibility, including rollback limits, is validated
independently of executable-slot completeness.

Connector/controller outage interrupts access, not the independently owned
backend's indexing or task lifetime. Idempotent reads may be retried within
bounds; no exactly-once side effects are inferred from lost transport replies.
Uninstall removes only owned startup/connection registrations and preserves the
documented data/config policy, with no fallback that re-enables a retired role.

## Sequencing and proof

Land this plan before contract/runtime code. Implement driver and wire schemas,
then standalone lifecycle and authenticated connector registration, then
typed service adapters. Prove a real index search through a separate client,
controller and connector against a real backend; mocks cover only local edge
cases. A useful route is not gated on new index packaging: consume its existing
supported API while #5768 owns the independent distribution.

The effort's Validation Plan is binding implementation evidence. Numeric limits,
wire schemas, package boundaries, auth-provider integration and persistence
details must be resolved and tested in those slices before rollout. This plan
does not waive clean-room, update/crash, revocation or cross-platform proofs.
No live migration or adopter enrollment is authorized by publication.

Named reconciliation, credential feeders and interactive session/shell protocols
are later independently reviewed campaigns. Do not hide them in an unbounded
generic message/execute API introduced by this routing foundation.
