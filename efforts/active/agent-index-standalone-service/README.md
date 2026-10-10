# agent-index standalone service

- **Slug:** `agent-index-standalone-service`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice PRs against `dev`
- **Created:** 2026-10-08
- **Status:** Active
- **Vision:** [agent-index](../../../../visions/plugins/agent-index/README.md):
  `lightweight-client-and-declared-host-service`,
  `independently-packaged-indexer-service`,
  `released-version-controller-contract`, `deployment-backend-parity`
- **Umbrella issue:** #5768
- **Sub-issues:** #5769 (existing host recovery)

## Guiding Intent

Give the hosted indexer its own portable service-package and lifecycle boundary,
without making a Copilot plugin or a particular container controller the service
owner. Preserve lightweight clients, native host deployment, the durable
embedding engine, corpus state and task sequencing.

Phase 1 established the reviewed proposal and repaired the existing native host.
Implementation now proceeds in incremental reviewed slices, preserving the
native instance as a health-gated canary. No slice authorizes an unreviewed live
deployment or destructive migration of durable data.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Integration, implementation and canary rollout | Managed upstream worktree |
| Service implementer | Bounded independently packaged service slices | Separate managed worktree when delegated |

## Coordination

- **Topology:** independent per-slice PRs.
- **Host (owns PRs):** Coordinator.
- **Delegates:** bounded read-only packaging and vision evidence; no delegated git changes.
- **Handoff:** this README and [architecture proposal](architecture.md) are the
  canonical portable plan. Deployment-specific elaboration remains downstream;
  the public plan never links to private state.

## Context

- The independently installable `agent-index-engine` is the embedding RPC
  program, not the complete hosted indexer. The HTTP/query surface, queue,
  source ingestion and detached workers still live in the plugin's base package.
- Reuse the existing durable-engine and server-package-split work rather than
  recreating their model/runtime boundary.
- `worktree-manager` supplies the standalone version-slot installer precedent.
  Existing versioned-runtime and zdd contracts supply candidate completeness,
  activation and active/passive service cutover.
- The existing native host has two recovery defects tracked in #5769:
  installer activation ignores effective configuration layers, and a detached
  successor can remain inside a Windows management Job.
- The architecture proposal is future design, not a description of a service
  distribution already shipped.

## Request

Operator request, with deployment-specific names redacted; the portable intent
and qualifications are preserved:

> Help fix the routing for the index service locally first. We want to make sure
> it self-heals. We'll want to start an effort to get agent-index's indexing
> service build out in the [operator's container platform] container.
>
> I think the correct approach will be to define agent-indexer's indexer service
> as its own service package, not a plugin, and install and update it the same way
> as worktree-manager (though with a container spec). The [deployment controller]
> device will need to check upstream periodically for `main` releases, and then
> pull down and deploy the updated service version.
>
> Ideally, we'll maintain back-compat with the lightweight non-container install
> if possible.
>
> Help build out a vision and architecture plan stream in copilot-extensions.
> Ideally what's upstream is agnostic to the factory-specifics for now, any
> mostly just defines the standalone service, with its version-slot installer
> and config support, and (maybe) a generic dockerfile.

Subsequent operator decision: do **not** enable the optional Windows Scheduled
Task tier for the current host. The recovery repair and proposal must not
silently introduce that tier.

Subsequent operator request:

> Great. Continue driving. Make sure our instance stays healthy as we upgrade it
> as a guinea pig during this work

Architecture clarification, verbatim:

> I mean, arguably the system decomposes into four pieces:
> 1. API surface and master controller
> 2. Indexing/embedding engine
> 3. Actual DB
> 4. Client wrapper
> 1-3 could run in independent containers if needed. We should break it down in a
> reasonable manner.

After the merge-only pause, the operator authorized resumption:

> Okay, we may resume work now

## Plan

### Phase 1 - Existing recovery and reviewed proposal
- [x] Restore the existing host using its durable launcher; verify live routing
  and preserve the warm engine and queue.
- [x] Fix effective-config activation parity and management-Job survival (#5769);
  source regressions and isolated real-process checks pass; deployed in `0.10.10-dev1`.
- [x] Revise the vision and review the [architecture proposal](architecture.md).
- [x] Land the proposal/recovery PR, observe release promotion and deploy the repair.
- [x] Record which recovery is event-driven and which requires a continuously
  running lifecycle authority; do not claim unattended restart from logon
  registration alone. _(agent-recommended validation clarification)_

### Phase 2 - Independent service distribution
- [ ] Align component contracts with the API/controller, execution, persistence
  and client decomposition before moving or distributing code.
- [ ] Define the service package, executable and portable release descriptor
  outside the plugin marketplace. The normal package/executable landed in
  #5863; pinned bundle validation is the next descriptor slice.
- [x] Extract or compose the existing hosted query, indexing and worker code
  without rebuilding a parallel implementation (#5863, local adapters).
- [ ] Implement the version-slot installer and durable configuration/state
  contract, including health-gated activation and rollback.
- [ ] Preserve existing CLI, configuration and non-container host compatibility.
- [ ] Keep the live native canary healthy across reviewed upgrades; retain a
  validated previous slot, verify worker adoption and protect the warm engine.
- [ ] Define and test schema/queue rollback limits explicitly rather than assuming
  immutable executable slots make data rollback safe. _(agent-recommended)_

### Phase 3 - Deployment adapters and release reconciliation
- [ ] Supply the portable discover/install/activate/status/rollback contract for
  an external released-`main` reconciler; keep its deployment platform downstream.
- [ ] Decide whether a generic Dockerfile materially improves the reference
  deployment; if included, keep model/cache/data and controller ownership explicit.
- [ ] Integrate standalone-service versioning and release validation without
  declaring a second Copilot plugin.
- [ ] Prove native-host and optional-container parity before any downstream
  placement/routing migration.

## Validation Plan

- [x] Unit regressions for layered designation, unconfigured/client nonactivation
  and daemon detachment while retaining contained-test ownership.
- [x] A real management-process-exit check and stale-route recovery check, not
  just mocked spawn flags.
- [ ] Lightweight client install/import tests: no store, server or model stack
  acquired by ordinary read commands.
- [ ] First-touch native installation, interrupted/incomplete candidate rejection,
  configuration preservation and idempotent reconcile.
- [ ] Existing-client/new-service and new-client/existing-service compatibility,
  with explicit protocol/config schema expectations.
- [ ] Queued full/incremental sequencing, worker adoption and corpus preservation
  through update, failed activation and supported rollback.
- [ ] Optional container restart, persistent volumes and graceful termination.
- [ ] Independently hosted API/controller, execution and persistence contracts,
  without cross-container database-handle or shared-SQLite assumptions.
- [ ] Authenticated inter-component transport and component/job/source-scoped
  authorization: reject unauthenticated, invalid-identity, cross-role and
  cross-scope operations without mutating queue or corpus state.
- [ ] Released snapshot/artifact pinning, digest/version verification, controller
  outage, repeated poll idempotence and concurrent-updater exclusion.
- [ ] Live target deployment belongs to a separately authorized downstream slice;
  no private venue provisioning in the portable proposal.

## Proposal

[Architecture proposal](architecture.md). It separates intended package
boundaries from implementation decisions and deployment-specific policy.

## Journal

### 2026-10-08 - Kickoff
- Operator confirmed the slug. Public coordination opened in #5768; the bounded
  current-runtime recovery defects are tracked in #5769.
- Existing packaging and vision evidence was reviewed. The embedding program
  already has an independent package; the hosted indexer does not yet.
- Operator declined Windows Scheduled Task supervision. No task registration
  is part of this effort's current repair.
- Recovery validation: 20 focused regression tests passed, followed by 221
  adjacent activation/configuration/installer/deploy tests (23 platform skips).
  The real layered resolver selects the same host role as the deployed CLI.
- Isolated Windows integration: a deployed successor remained healthy after its
  real kill-on-close management Job was closed. After terminating that owned
  test instance and retaining its stale routing advertisement, a second
  deployment replaced the route and survived another Job closure. Temporary
  data and both test processes were cleaned up.
- Validation tiers: unit and simple real-process integration exercised.
  Full installer clean-room and live external-venue execution are deferred to
  the standalone implementation slices; the current recovery was reproduced
  and checked on a Windows native host. No external deployment was provisioned.
- #5779 merged after an approving Copilot review and passing required CI.
  Review corrected the lifecycle note to reflect the active installer `ensure`
  hook and added explicit fail-closed resolver-error handling with a bounded
  diagnostic on both installer platforms. The contribution landed on `dev`;
  release promotion and local deployment remain part of Phase 1.
- The proposal is reviewed, not an implemented standalone distribution.
  Phases 2 and 3 remain future service-package and deployment-adapter slices;
  no downstream host-routing migration has started.
- Release #5794 merged on `main`. Its activation-helper content matched the
  validated source exactly; contribution-SHA ancestry was not used as proof.
  Unified update deployed agent-index `0.10.10-dev1` through a clean, non-forced
  active/passive cutover.
- Post-deployment: the successor served after the updater exited. Two actual
  installer `ensure` invocations selected the layered host configuration and
  preserved the healthy instance/route; configured CLI retrieval returned
  populated hits. The original indexing worker remained alive and adopted,
  and the warm embedding engine remained loaded at background priority.
- No Scheduled Tasks were added. Recovery through session-start `ensure`,
  explicit `ensure` and update is event-driven; continuous unattended crash
  supervision remains a separately selected lifecycle-authority contract.
- Phase 1 is complete. Remaining service distribution and container/controller
  implementation belongs to Phases 2 and 3, tracked by #5768, not to a claim
  that the reviewed proposal already shipped that implementation.

### 2026-10-08 - Four-piece implementation and native canary
- Operator authorized continued implementation and explicitly selected the
  current native instance as a canary.
- Operator refined the architecture to API/master controller, indexing/embedding
  execution, actual database and client wrapper. Independent containers for the
  first three are optional deployment choices, not a mandatory first migration.
- Implementation will reuse existing queue/query/store/engine behavior while
  making those boundaries explicit. The existing embedding package alone is
  not the hosted indexer, and a renamed embedding executable would not satisfy
  the requested split.
- Baseline native service and warm engine are healthy, active indexing remains
  adopted, and a complete previous native version slot is retained. A bounded
  development health check is active; it is not new permanent service supervision.
- Four-piece/authenticated-boundary amendment #5830 merged after review and
  passing required CI. Component/job/source-scoped authorization and rejected
  unauthenticated/cross-role operations are acceptance requirements for remote
  adapters.
- Initial code slice adds a normal `agent-index-service` API/controller program
  with explicit repository-independent local component composition, preserving
  existing client and core behavior. The embedding program is not renamed.
  The existing standalone-consumer release mechanism is extended; no second
  plugin or changefile schema is introduced.
- Installed-core contained standalone suite: 68 tests passed, including two
  real isolated zdd deployments, persisted query compatibility and synthetic
  worker adoption. Core boundary/explicit-source/lazy-import selection: 37
  passed with 2 platform skips. Release/materialization/rollback regressions
  pass after repairing fixture dependency closure and a Windows symlink-target
  identity assertion. Code is still awaiting publication and canary eligibility.
- Canary API and warm engine remain healthy through existing fleet maintenance.
  An incumbent indexing run ended partial after embedding read timeouts on two
  sources; a successor incremental worker is active. A short warm-engine query
  remains responsive. Live rollout changes are paused while that CPU/batch
  timeout risk is classified; no worker is cancelled or engine cold-restarted.

### 2026-10-09 - Merge-only pause boundary
- Operator narrowed the current objective to landing existing PR #5863, then
  pausing. No additional implementation, native canary migration or deployment
  is authorized by this slice; the development health-check schedule is stopped.
- The first review identified unconditional exhaustive PR CI and a stale
  documentation limitation. Required Linux/Windows CI now path-gates the
  controller contract smoke; full hosted/deployment coverage remains in
  promotion and manual execution. CI integration is no longer documented as
  deferred. The contained smoke passed 62 tests in 4.27 seconds; runner/workflow
  policy regressions passed 30 tests.
- Phases 2 and 3 remain open under #5768. This API/controller composition is
  not the version-slot installer, a separated database/execution adapter, a
  release reconciler or an authorized live service migration. Resume those
  tracked slices only after the operator lifts the pause.
- The next review found two containment defects in the new test surface.
  Non-finite/nonpositive wall limits now fail before admission. POSIX hosted
  children stay attached to the outer runner instead of creating escape
  sessions; an abrupt-worker-exit regression runs in Linux CI. Windows retains
  nested kill-on-close Job teardown. The full contained Windows standalone
  suite passed 68 tests; runner/workflow regressions passed 35 with the Linux
  process-group test explicitly deferred to its Linux CI lane.
- Follow-up review identified the pre-seam core dependency floor and the new
  registry's missing version-bump test path. Both base/native wheel requirements
  now exclude the currently released `0.10.11-dev1` core and require
  `0.10.12-dev1` or newer; the runtime seam check remains fail-closed. Test
  preparation uses an explicit same-checkout core override (including native
  extras) because `dev` freezes its source version. Actual preparation plus the
  full contained suite passed 68 tests; wheel assertions reject the old core.
  Registry-only changes now trigger the existing version-bump-engine suite.
- Review also required smoke preparation itself to stay lightweight, not just
  test selection. A genuinely fresh smoke environment passed 63 contracts in
  2.32 seconds and contains no FastAPI/uvicorn/NumPy/pyarrow/LanceDB/tree-sitter
  packages. Full preparation retains native extras. Runner/workflow regressions
  passed 36 tests with one Linux-only process-group regression deferred to CI.

### 2026-10-10 - Resuming the independent lifecycle
- Operator explicitly lifted the pause. #5863 has reached released `main` as
  `agent-index-service` `0.1.1-dev1`; native deployment is not inferred from
  promotion. The retained implementation worktree was safely advanced after
  preserving a backup of its already-merged private branch.
- Native process/read-admission health, detailed status and existing warm-engine
  inference were verified without restarting either service or cancelling work.
  A timed-out CLI status alone did not establish failed API health. The host
  currently has only its active executable slot; any upgrade must first establish
  a retained eligible rollback target rather than assume an older slot exists.
- The next bounded slice is a pinned wheel-bundle descriptor and verification
  contract before installation/activation. It supplies integrity, exact version
  selection and compatibility evidence; it does not claim a digest authenticates
  a release or proves its build provenance. Trusted released-source acquisition
  and lifecycle/schema-safe activation remain separate gates.
- Development-only read-only monitoring is rearmed at a long interval. No
  Scheduled Tasks, persistent supervisor, factory-specific integration or live
  migration is introduced by this resumed slice.
- Pinned-bundle verification is implemented without wheel extraction, native
  stack imports or host writes. The descriptor binds five normal distributions
  to exact source/version/digest selections; the independently supplied expected
  revision and dependency checks reject tampered or incompatible candidates.
  Unit/CLI smoke passed 176 tests; the full contained standalone suite passed
  182 before the additional real-wheel build test. That integration test builds
  actual package source copies, rejects the frozen pre-seam core version and
  verifies a temporary release-version fixture. No test version is published.
- Validation tiers: unit and contained native hosting/cutover regression lanes
  exercised. Real wheel-build/descriptor integration also passed. No installer
  or fresh service activation is implemented by this slice, so installer
  clean-room and live external-venue deployment remain explicitly deferred to
  the installation/activation slices. The native production instance is intact.
- Complete contained suite, including the actual wheel-build integration and
  hosted deployment cycles, passed 183 tests. Pinned-release pure contracts are
  part of the base-only path-gated smoke lane; native deployment/build coverage
  remains in the full lane.
