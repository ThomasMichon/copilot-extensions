# Machine fleet routing foundation

- **Slug:** `machine-fleet-routing-foundation`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, serially landed per-slice PRs against `dev`
- **Created:** 2026-10-08
- **Status:** Draft; operator-approved scope, architecture review pending
- **Vision:** [machine-fleet](../../../../visions/machine-fleet/README.md):
  `driver-based-fleet-adoption`, `shell-independent-service-control`,
  `discoverable-service-routing`, `standalone-service-installation`
- **Umbrella issue:** #5789
- **Sub-issues:** None yet; implementation slices remain under this umbrella

## Guiding Intent

Deliver the minimum portable Gateway route to independently hosted services:
an explicitly selected static-machine driver, authenticated outbound machine
connectors, and authorized fixed-service discovery, health and search. Useful
daemon access must not wait for interactive session protocols or fleet-wide
configuration control.

The Gateway is optional. Existing local/direct/SSH workflows and service
authorities remain usable. This campaign neither migrates a live service nor
automatically enrolls machines.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign owner | Planning, integration, validation and PR stewardship | Managed upstream worktrees |
| Implementation owner | Serial contract and runtime slices after plan review | Separate managed upstream worktree |
| Adopter | Explicit enrollment, target configuration and live rollout authority | Downstream deployment flow |

## Coordination

- **Topology:** Reviewed architecture plan, then serial implementation PRs.
- **Host (owns PRs):** Campaign owner.
- **Delegates:** None assigned; do not give two actors the same slice.
- **Handoff:** Resume the first unresolved Plan item from this canonical README.
- **Adjacent ownership:** [agent-index standalone service](../agent-index-standalone-service/README.md)
  (#5768, proposal/recovery #5779) owns index distribution, queue and warm-engine
  compatibility. This campaign supplies a route, not a second index installer.
  [Machine transport convergence](../machine-transport-convergence/README.md)
  owns shared named-machine resolution; reuse its landed contracts and coordinate
  any overlapping adapter edits rather than superseding another open PR.
- **Downstream boundary:** Provider-specific substrates, identities, credential
  feeders, inventories, resource placement and cutover remain adopter-owned.
  Public artifacts do not link to private coordination state.

## Context

The [machine-fleet vision](../../../../visions/machine-fleet/README.md) landed in
#5771 after the vision-only claim #5767. It establishes the controller/driver
ownership boundary, not a runtime implementation.

Existing `agent-ssh` owns static target identity and transport selection.
Existing services own their state and supported APIs. Shared versioned-runtime,
endpoint-discovery and graceful-cutover patterns are lifecycle foundations.
The controller must not grow a competing registry, task queue, credential issuer
or process supervisor for those services.

The [architecture proposal](architecture.md) specifies requirements and
implementation proof obligations; it does not claim a package, protocol or
deployment already exists.

## Request

Public-safe scope presented for approval:

> Approve the following first slice: static-machine driver contract + outbound
> connector registration + authenticated fixed-service discovery/health/search
> routing; leave named update/config reconciliation for the next slice.
> Provider-specific transport and credential feeders stay downstream.
> No live migration, fleet enrollment, or source-host cutover is included.

The operator selected `approve-routing-foundation` and confirmed the slug
`machine-fleet-routing-foundation`. Deployment-specific names are omitted from
this portable capture; no deployment authority is inferred from that redaction.
The detailed sequencing and validation below are agent-recommended.

## Plan

### Phase 1 - Reviewed contract and ownership
- [ ] Land this effort and [architecture proposal](architecture.md) through
  actual upstream review before runtime implementation.
- [ ] Confirm package/executable placement against existing shared libraries,
  service authorities and the standalone-index contract. Add no marketplace
  plugin merely to supervise the services.
- [ ] Define versioned driver, connector registration, service offer and routing
  schemas, including capability-honest unavailable/unsupported outcomes.
- [ ] Bind the static driver to an explicit `agent-ssh` target selection with
  canonical identities and provenance; preserve provider-owned reachability.

### Phase 2 - Standalone controller and connector
- [ ] Implement independently installable controller and connector runtimes,
  explicit role/enrollment configuration and owned reversible registration.
- [ ] Establish encrypted, authenticated outbound registration with scoped
  principal/target/service authority, expiry/revocation and generation fencing.
- [ ] Implement bounded connection/request state, readiness, stale/disconnected
  transitions and reconnect without taking ownership of downstream work.
- [ ] Wire immutable version slots, passive candidate validation, routing flip,
  safe drain, rollback/commit-forward and generation-bound retirement through
  the established lifecycle authorities.

### Phase 3 - Fixed-service discovery, health and search
- [ ] Implement authorized service discovery and fresh backend identity/health
  verification for locally declared service offers.
- [ ] Route typed, allowlisted health/search operations over the connector to
  the fixed offered backend. Preserve source/service authorization and existing
  response semantics; never expose arbitrary URLs, paths or HTTP methods.
- [ ] Add the initial index adapter against the existing supported query API,
  without coupling routing to an unreleased standalone package extraction.
- [ ] Preserve direct client access and report driver, connector, credential,
  route and backend failures separately.

### Phase 4 - Proof and completion
- [ ] Prove a clean standalone controller/connector install and real search from
  a separate client against a real service, with no Copilot/marketplace process
  required to keep the route alive.
- [ ] Prove restart, disconnect, expiry/revocation, update failure and graceful
  controller/connector replacement under the validation contract below.
- [ ] Document portable adoption, diagnostics, lifecycle and compatibility;
  journal actual evidence and transfer remaining work to named objectives.
- [ ] Mark Done only after every Plan and Validation Plan item is resolved.
  A reviewed plan or mocked routing result is not runtime completion.

## Validation Plan

- [ ] Driver/config/schema regressions: explicit selection, canonical target
  identity, ambiguous/missing identity rejection, capability honesty and no
  implicit creation, transport fallback or capacity mutation.
- [ ] Authentication/authorization: enrollment versus operator versus service
  authority, cross-fleet/target/service isolation, revocation, expiry, rejected
  offers, duplicate principals and old-generation reconnect/cleanup.
- [ ] Routing: real backend identity/readiness, supported query/response parity,
  source restrictions, deny-by-default operations, no SSRF/open proxy/header
  credential forwarding, malformed/oversized responses and unavailable backend.
- [ ] Resource bounds: request/body/response limits, per-principal and global
  concurrency, queue/backpressure, reconnect backoff, timeouts and slow-service
  isolation. Record numeric limits and measure them in implementation tests.
- [ ] Real process integration: outbound connector and separate client/controller/
  backend, reconnect after lost responses, stale offers, controller/connector/
  backend restart and failed route publication. No mocked-success substitutes.
- [ ] Lifecycle: complete/incomplete slots, failed candidate preserving the old
  runtime, passive ownership, atomic promotion, accepted-request drain,
  updater exit/crash, generation-fenced cleanup and supported data rollback.
- [ ] Fresh install and uninstall preserve unrelated configuration/state,
  remove only owned registrations, and never enable a retired role.
- [ ] Windows and Linux installation/daemon/transport behavior; macOS is either
  separately proven or explicitly unsupported, not silently called POSIX parity.
  Automated processes produce no visible terminals or unattended login prompts.
- [ ] Existing local/direct service clients and SSH workflows remain functional.
  Missing Gateway configuration never activates a service host or connector.
- [ ] Unit, simple end-to-end, clean-room and available live-venue tiers are
  recorded per implementation PR, with explicit reasons for unavailable tiers.
  Adopter live rollout remains separately approved, not an upstream test shortcut.
- [ ] Touched-code lint, relevant suites/guards, changefiles where required,
  documentation-impact review, actual automated review and required CI before
  each merge; confirm provider merge state and settle worktree obligations.

## Proposal

[Architecture proposal](architecture.md). Named deployment/config reconciliation,
credential feeder implementation, durable observation replay, AHP/TTY forwarding
and live service migration are intentionally excluded from this first campaign.
They remain vision deltas, not features claimed by this routing foundation.

## Journal

### 2026-10-08 - Scope approval and planning
- The operator approved the routing-foundation scope and confirmed the slug.
- Searched existing public issues, effort index and active dispatch work before
  claiming #5789. Reused #5768/#5779 for index service distribution.
- Verified target effort adoption and created an isolated upstream worktree.
- No portable runtime, live enrollment, provider lease or service migration was
  performed by this plan-authoring slice.
