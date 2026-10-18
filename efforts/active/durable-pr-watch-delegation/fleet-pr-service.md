# First-class PR service and fleet deployment

Back to the [canonical effort](README.md). Coordination: #7188 under #6007;
the installation-cell and governed-cutover prerequisite remains #6286.

This is a proposed scope amendment, not a shipped service, accepted deployment,
or waiver of the existing Windows receipt-read blocker.

## Operator-required outcome

Build out `agent-pull-requests` as the first-class PR-focused plugin and associated
service daemon. The fleet can place its centralized, primarily IO-bound PR
watcher and subscription registrar into containers on explicitly assigned fleet
machines.

The entire fleet belongs to exactly one operator. No service, controller,
connector, store or credential provider in it hosts another operator's state or
credentials. Future multiplayer means interactions between separately owned
fleets, never multiple operators sharing this fleet. Implementing federation is
outside this amendment.

Native PR use remains useful without adopting a fleet. Concrete inventory,
capacity, accounts, credentials and live activation stay in adopter configuration,
not this portable plan.

The design and sequencing below are **agent-recommended elaborations** of those
requirements. Review precedes implementation.

## Reuse and ownership

| Surface | Authority |
|---------|-----------|
| PR-domain client, provider operations, watch state, subscriptions and deliveries | Existing PR implementation, extended under this effort; reuse PR-conformance #7051 rather than replacing its owner |
| Worktree publication, source identity, claims and repository merge policy | `agent-worktrees`; compatible PR adapters do not transfer these obligations to the daemon |
| Task suspension, completion and generation fences | `agent-dispatch`; the PR registrar is not a task registry or queue |
| Gateway routes, service offers and connector authentication | Fleet routing #5789 and the integrated fleet contract owners |
| Fleet release, operation/receipt and credential-provider integration | #6236/#6240/#6241; no PR-owned controller, broker or release ledger |
| Container placement, capacity, role reconciliation and substrate actions | Linux role fleet #6030/#5861 and its selected deployment adapter |
| Concrete adoption and live changes | The fleet's sole operator through the approved deployment workflow |

Consume the same generic role contract as other standalone services. Index
#5768/#6383 and dispatch #6177 remain independently owned consumers, not work
this effort silently absorbs. Inventory current contracts and unmerged work with
their owners before extending shared interfaces.

## Plugin, service and placement boundary

The existing `agent-pull-requests` plugin supplies the PR-facing commands,
guidance and connections. Its service distribution must run independently of
Copilot, marketplace hooks and a client machine's interactive session. Extend
the existing watch core and provider contracts rather than creating a second
poller, registrar or forge stack.

Package the service and its explicit dependencies through the supported
standalone/artifact process. Supply a non-root container, declared configuration,
durable storage, readiness and graceful shutdown, plus a role manifest/adapter
that fleet-manager can consume. Do not make the thin client install Docker or
a controller. Container deployment uses the fleet's supervisor, not an
additional host service or per-session reconnect loop.

An explicit backend selection distinguishes native/local and fleet access.
Fleet clients resolve the configured logical PR service through the Gateway,
not a fixed machine name, arbitrary downstream URL or reused SSH alias.
Existing direct/local commands and worktree PR workflows retain their supported
semantics. Share domain/provider behavior with the conformance owner rather than
moving every worktree publication function into the remote daemon.

## Central observation and registration

The service shares an observation stream for the same forge, repository, PR and
authorized account scope. Independent subscribers keep their own criteria,
registration identity, deadline and delivery state. Sharing one operator's
fleet does not make all forge accounts, repositories or installation namespaces
interchangeable.

The registrar durably records subscription acceptance and terminal pending events
before reporting them. Reconnect/replay retains registration and event identity;
ACK commits only the matching delivery. Duplicate requests and lost responses
reconcile the accepted registration instead of creating two wait owners.
Timeouts retain their original absolute deadlines across persistence and moves.

Bound polling, in-flight IO, delivery work, memory and retry queues. One slow
forge, repository or subscriber must not stall unrelated registrations. Prove
that more subscribers for one authorized target do not create more observation
loops or one operating-system waiter per task. This is an IO-sharing goal, not
authority to add speculative replicas or a new scheduler.

## Remote delivery and credentials

A local callback executable is not runnable merely because a remote service
accepted its subscription. Preserve the existing local callback contract for
native use. The fleet path uses the fleet owners' typed subscription/replay/ACK
or named service-delivery contract, with dispatch committing its own
generation-fenced effect. The PR container never accepts arbitrary subscriber
shell commands, callback argv or arbitrary destination URLs.

Agree the exact supported delivery and credential-provider interfaces with their
owners before building against them. Mocks may unblock adapter tests, but
unavailable runtime contracts remain explicit dependencies, not a fabricated
ready Gateway. The service may consume renewable, scoped credentials from its
operator's admitted providers; query/subscription permission is not credential
issuance, fleet administration or permission to mutate a PR.

Keep account, resource, audience, expiry and revocation boundaries intact.
Tokens do not enter images, manifests, durable subscriptions, callback argv or
diagnostics. Multiple accounts of the same operator remain separately scoped.
No foreign operator credential or private state is imported as a tenancy shortcut.

Before accepting a delegated wait, negotiate compatible registration and
delivery capabilities. Once acceptance is uncertain, reconcile that identity
before any process fallback. An explicitly selected but unavailable fleet backend
must not silently route through a different account or local owner.

## Lifecycle and state

The service owns durable watch/registration/delivery state independently of its
image and code slot. Exactly one active writer owns a given service namespace.
The fleet lifecycle composes the service's drain, writer transfer, readiness,
commit and retirement boundary; container restart or manager acknowledgement
alone is not proof of safe promotion.

Keep storage placement and any movement explicit. Do not concurrently mount a
live native state root into a container or infer cross-host fencing from a
host-local file lock. The first placement may use one declared persistent volume
on its assigned host; movement or failover requires an agreed storage-continuity
and writer-fencing contract before activation. Image rollback does not imply
state-schema rollback is safe.

Clients' local installation receipts do not become authority over a remote role.
Bind selected fleet/service identity, admitted installation namespace and scoped
account separately. Preserve the reviewed cell-isolation and exact-instance
upgrade contracts where those runtimes are used.

## Implementation sequence (agent-recommended)

1. Land this vision/effort amendment; agree service-role, transport, delivery and
   credential interfaces with their retained owners. Reconcile the saved dispatch
   integration against the broadened contract without enabling it prematurely.
2. Finish the existing governed owner prerequisite and original reader diagnosis.
   Extend the PR-facing client/service seam and provider conformance without
   regressing native behavior or worktree publication policy.
3. Build the independent service artifact/container and fleet role consumer.
   Validate the same core in native and isolated container deployments.
4. Add explicit Gateway client and remote delivery integration. Preserve native
   callbacks and safe process fallback where appropriate; no remote argv relay.
5. Exercise an explicitly approved fleet placement, real credential renewal and
   client/Gateway/service/dispatch path. Native-only delegation does not satisfy
   the expanded completion gate. Any unavailable owner contract or venue stays
   a named blocker; it is not silently marked complete.

## Validation plan (agent-recommended)

- [ ] Contract: agree the supported role lifecycle, Gateway route, credential
  provider and event delivery interfaces with their current owners; unsupported
  dependencies fail explicitly instead of inventing a parallel implementation.
- [ ] Unit: one operator owns the fleet; wrong fleet/service/account/namespace
  refuses, PR read/watch grants cannot mutate PRs or administer the fleet, and
  no other operator's state or credentials can be enrolled into this service.
- [ ] Observation: two independent subscribers share one authorized target poll;
  distinct account scopes do not share unauthorized results. Slow requests,
  provider outages and retrying deliveries do not starve unrelated targets.
- [ ] Durability: registration ambiguity, disconnect after commit before ACK,
  duplicate/stale ACK, unsubscribe/re-register, expiry, daemon crash and restart
  preserve original identities, deadlines and dispatch generation fences.
- [ ] Credential lifecycle: real provider refresh, expiry, revocation and provider
  disconnection produce truthful scoped outcomes without secret persistence or
  a more privileged fallback. Tests use synthetic credentials.
- [ ] Native compatibility: existing local CLI/watch callbacks, ordinary waits,
  installation-cell isolation and supported worktree PR workflows retain their
  behavior without requiring a Gateway.
- [ ] Simple E2E: separate client/service processes demonstrate register, observe,
  consume and ACK using the real selected transport, not only mocked calls.
- [ ] Fresh container: install/start without Copilot, register and complete a
  synthetic watch, restart with a persistent volume, gracefully terminate and
  verify no lost pending registration or second writer.
- [ ] Lifecycle: exercise update, failed-candidate rollback, interrupted cutover
  and owned uninstall. Refuse unsafe cross-host movement and incompatible data
  rollback; do not touch shared live owners or unrelated volumes.
- [ ] Available live venue: on explicitly approved capacity, prove actual
  fleet-manager role placement, Gateway client access, scoped credential renewal
  and a dispatched task waking through durable remote delivery. Record a genuine
  unavailable lane as an unresolved blocker, not successful mocked deployment.
- [ ] Release/documentation: owning tests, scoped fast CI, manual/exhaustive
  container lanes, changefiles, source-independent service packaging, provider
  conformance, current review, promotion and authorized installed proof; document
  the supported backend and failure/recovery semantics.
