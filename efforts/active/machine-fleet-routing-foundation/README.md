# Machine fleet routing foundation

- **Slug:** `machine-fleet-routing-foundation`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, serially landed per-slice PRs against `dev`
- **Created:** 2026-10-08
- **Status:** Active; architecture reviewed, runtime implementation pending
- **Vision:** [machine-fleet](../../../visions/machine-fleet/README.md):
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

The [machine-fleet vision](../../../visions/machine-fleet/README.md) landed in
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
- [x] Land this effort and [architecture proposal](architecture.md) through
  actual upstream review before runtime implementation.
- [ ] Confirm package/executable placement against existing shared libraries,
  service authorities and the standalone-index contract. Add no marketplace
  plugin merely to supervise the services.
  Shared contracts and the static adapter now have their canonical locations;
  standalone controller/connector distribution placement remains Phase 2 work.
- [x] Define versioned driver, connector registration, service offer and routing
  schemas, including capability-honest unavailable/unsupported outcomes.
  Driver/registration/offer/request records and native index response DTOs are
  implemented. Live backend adapters and service authorization remain Phase 3
  work, not implied by validating a response body.
- [x] Bind the static driver to an explicit `agent-ssh` target selection with
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

### 2026-10-08 - Architecture gate passed
- #5792 received an actual Copilot APPROVED verdict for head `eb1113ba0`.
  The first provider merge was refused while the required
  `workflow-lockdown-guard` was queued; it was not bypassed.
- After the required check passed, #5792 squash-merged into `dev`. Verified
  provider state is MERGED at `2026-10-08T23:39:36Z`.
- Only the plan-publication item is closed. Package placement, schemas, static
  driver integration and every runtime/validation item remain actionable.
  The implementation umbrella #5789 stays open.

### 2026-10-08 - Contract implementation started
- Campaign owner transferred the remaining Phase 1 contract slice to
  Implementation owner under #5789, in a separate managed implementation
  worktree. The post-merge approval journal was carried into that worktree.
- Selected a standard-library-only `agent-fleet-contracts` shared package.
  The static driver remains owned by `agent-ssh`: it describes explicit
  selections from the profile emitter's normalized source records and never
  imports another provider, probes SSH, enrolls or offers managed lifecycle.
- Request envelopes retain the existing index search parameters; response
  adapters, authentication and resident controller/connector lifecycle remain
  later implementation work, not implied by this contract slice.
- Implemented immutable, bounded records with strict schema/version/type/key
  checks, duplicate rejection, UTF-8/JSON/query limits, expiry and current-offer
  identity/generation matching. Parsing does not authenticate or authorize.
- Added a real `fleet-targets` CLI roundtrip and no-probe/no-profile/no-process
  checks. Canonical library tests are collected in the consumer's CI lane.
- Validation so far: 86 pure contract tests and 14 targeted static-driver tests
  pass; standalone stdlib-only import and self-contained release materialization
  pass. Lint, install-contract, headless-launch, changefile and vendoring guards
  pass.
- Broader SSH validation exposed the new dependency missing from first-use
  snapshot staging; wired it through both installers, snapshot completeness and
  fixtures. Waited behind other live host-admitted test runs rather than clearing
  their locks or processes. The repaired first-use/contract selection passes
  (103 passed, 2 platform skips).
- The full suite exposed two POSIX manifest harnesses selecting Windows'
  WSL launcher with unconverted native paths. Kept these shell-specific cases
  on native POSIX and retained their real PowerShell counterparts on Windows.
  First-use snapshot builds now carry an explicit 120-second test budget instead
  of being killed by the unrelated 30-second default.
- Final full SSH suite: 304 passed, 31 expected platform/optional skips using
  freshly installed shared-library code and normal default test budgets.
  Opaque source labels retain spaces; route checks accept the same explicitly
  configured TTL policy as registration acceptance.
- Validation tiers: pure unit, actual CLI roundtrip, self-contained release
  materialization and Windows snapshot-only first-touch install exercised.
  Live external-venue tests are not applicable to this no-network description
  command; CDE/provider admission and migration remain separately blocked.
  Native POSIX installer execution remains the Linux CI lane's obligation,
  not a claim made from the Windows run.
- Implementation PR #5824 review identified two real integration gaps: raw-byte
  YAML decoding could admit a different source encoding than the emitter, and
  the projected fast suite lacked guard-lane attribution. Decode both bounded
  sources as UTF-8 before YAML parsing; add UTF-16/32 rejection regressions for
  both files and guard markers to the projection and static-driver contract tests.
- Fresh guard-lane verification: 116 passed, 4 native-platform skips. This
  collects the canonical pure suite as well as static-driver/CLI contracts.
- Copilot APPROVED the corrected production head. Linux CI then exposed two
  isolated pip-fallback fixtures still omitting the newly required library
  resolver and source directory (330 passed, 7 skips, 2 fixture failures).
  Update both local-vendored and canonical-source harnesses and assert that
  `fleet-contracts` reaches pip's arguments. Do not merge around this failed CI.
  Local supported fallback checks pass; native shell proof is the next CI gate.

### 2026-10-09 - Contract/static-driver slice merged
- #5824 received current-head Copilot APPROVED for `33ee4ee02`. Waited for
  actual CI success after repairing the two native shell fallback fixtures;
  no test/review/hook/CI gate was bypassed.
- Verified provider state MERGED at `2026-10-09T04:43:17Z`. The public
  implementation umbrella #5789 is still OPEN.
- Only portable records and explicit read-only static SSH description are
  delivered. Request/offer matching is not authentication or persisted fencing.
  Controller/connector package placement, standalone lifecycle, encrypted
  registration, response adapters and real routed search remain the next slice.
- Consumer payload refresh started immediately after merge; release promotion
  and installed feature availability must be verified, not inferred from `dev`.
- Next-slice bounded evidence found the native index cell identity is
  `<marketplace-id>/agent-index`, which the initial logical-ID character
  restriction rejected. Treat backend installation identity as bounded opaque
  UTF-8 comparison data, never a path/endpoint, and retain control/empty/size
  rejection. Add native identity roundtrip/offer-matching regressions before
  implementing the fixed-backend response adapter.

### 2026-10-09 - Runtime authentication boundary selected
- The operator selected `separate-authorities`: externally verified JWT/JWKS
  operator/service access plus separate per-connector proof-of-possession
  identity supplied by the adopter's credential provider. This authorizes
  implementation and synthetic proofs only, not live trust changes/enrollment.
- The existing relay is a credential-borrowing mechanism, not verified peer
  identity. Do not reuse its process-local bearer registry or unverified JWT
  expiry extraction as enrollment authentication.
- The portable Gateway verifies and authorizes; it does not mint resource
  credentials, create a blanket pool or implicitly enroll a machine. Preserve
  distinct issuer/audience/principal, fleet/target/service/operation grants,
  expiry/revocation, connection/generation binding and replay protection.
- Coordinator response-contract slice preserves native index health/search
  output, strips process control tokens, distinguishes read admission from
  model readiness, preserves unmodified hit content and rejects explicit
  backend-unavailable results/oversized or malformed bodies. No live HTTP
  transport, authentication grant or routed-search proof is implied.

### 2026-10-09 - Operator pause checkpoint
- Operator requested merging pending PRs and then pausing. #5792 and #5824
  are verified merged; no new PR is opened for the unfinished next slice.
- Native installation-ID compatibility correction and typed native response
  contracts are preserved locally. The latter passes pure and consumer guard
  checks but remains unpublished and has no HTTP transport/runtime.
- Separate authentication-adapter work is retained in a dedicated managed
  workspace. Synthetic crypto/fresh-wheel evidence exists, but host integration,
  durable authorities, cross-platform/CI wiring and publication remain open.
- Resume only on explicit operator continuation. Do not mark #5789 Done or
  finalize an active effort based on the merged static-driver artifact.

### 2026-10-10 - Continuation and native response boundary
- Operator resumed upstream capability work. Generic Linux containerized role
  adoption is coordinated separately by #6030 under #5861; the fixed-service
  routing boundary and #5789 completion gate are unchanged.
- Revalidated native index health/search response shapes against current service
  source. Health strips process-control tokens/PID and distinguishes read
  admission from model readiness. Search preserves hit fields and content,
  rejects explicit unavailable results and has a separate response-byte budget.
- Targeted static-driver/canonical contract selection: 135 passed, one
  native-platform skip. Touched-code Ruff and install-contract checks passed.
  These are pure DTO/CLI proofs, not authenticated routed search or enrollment.
