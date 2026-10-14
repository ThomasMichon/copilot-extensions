# Linux role fleet

- **Slug:** `linux-role-fleet`
- **Repo:** copilot-extensions
- **Branch(es):** Reviewed plan, then independent serial implementation PRs against `dev`
- **Created:** 2026-10-10
- **Status:** Active; plan reviewed, isolated adoption proof delivered
- **Vision:** [machine-fleet](../../../visions/machine-fleet/README.md):
  `repeatable-role-fleet-adoption`, `bounded-desired-state-control`,
  `role-placement-preserves-service-authority`
- **Umbrella issue:** #6030
- **Sub-issues:** #5861 (existing named reconciliation follow-on)

## Guiding Intent

Make an explicitly adopted fleet of Linux hosts run containerized roles from
the existing service ecosystem with repeatable setup and inspectable placement.
Use a lightweight existing deployment manager, not a new general scheduler.
Keep the core portable and the provider-specific provisioning private.
The standalone system is named `fleet-manager`. Ordinary harness/user machines
run only its authenticated API client; a designated remote service host owns
the controller/Gateway, deployment adapter and selected Komodo Core.

Komodo/Periphery is the first adapter to prove. SSH/Compose is the lower-footprint
static-host alternative. Swarm remains deferred until automatic cross-host
placement is an explicit requirement; no option is a mandatory core dependency.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Campaign owner | Intent, substrate synthesis, integration and PR stewardship | Managed upstream worktree |
| Deployment owner | Sequential adapter and fresh-environment proofs | Separate managed upstream worktree after plan review |
| Adopter | Capacity, inventory, trust and live activation approval | Provider-specific downstream flow |

## Coordination

- **Topology:** One reviewed proposal, then serial independently reviewable slices.
- **Host (owns PRs):** Campaign owner.
- **Delegates:** Bounded read-only substrate evidence; no shared edit or Git ownership.
- **Handoff:** Resume this README's first unresolved Plan item, preserving its gates.
- **Adjacent ownership:** [Routing foundation](../machine-fleet-routing-foundation/README.md)
  (#5789) owns authenticated outbound registration and fixed-service routes.
  This effort realizes #5861 without hiding deployment in its routing protocol.
  Existing installer, `agent-machines`, credential-provider and service lifecycle
  authorities retain their responsibilities. The external manager owns container
  deployment; the fleet does not duplicate its database or scheduling loop.
- **Privacy:** Public artifacts contain no private provider name, inventory,
  credentials, organization policy or downstream coordination links.
- **Activation:** Reviewed upstream code is not permission to enroll a live fleet,
  acquire capacity, change operator credentials or move an existing service.

## Context

The reviewed machine-fleet vision already separates driver reachability,
Gateway routing and existing service state. Its routing foundation has delivered
portable records and explicit static SSH description through #5824; that is not
a controller, connector or role-deployment runtime.

[Substrate evaluation](substrate-evaluation.md) records the current primary-source
comparison and its proof gaps. Official Komodo initial-admin configuration and
onboarding APIs indicate a headless setup path; an actual fresh-install receipt
is required before treating that path as demonstrated. Static Compose supplies
per-host deployment rather than fleet scheduling. Swarm supplies orchestration
but introduces quorum/network/storage obligations beyond this initial adapter.

## Request

Public-safe operator direction, with private deployment identifiers removed:

> Continue driving. Peer alignment permits the core vision and key effort pieces
> to move upstream. The specific managed infrastructure provider stays private.
> The general idea of a scalable fleet of Linux boxes running roles from the
> copilot-extensions service system, containerized using Komodo and Periphery,
> can stay. Evaluate something easier as well: these tools were selected for
> their light footprint, but Komodo Core required manual operator setup.

The surrounding intent is preserved; only private deployment identifiers and
the private example repository are omitted. No deployment permission is inferred.

The operator explicitly selected:

> Komodo/Periphery first; SSH/Compose alternative; Swarm deferred

The operator confirmed `linux-role-fleet`. Detailed sequencing and proof
requirements below are **agent-recommended**, not additional operator mandates.

Standalone-system direction (public-safe capture, private host substrate
identifier removed):

> Let's focus on giving this whole system its own standalone entry point, and
> home for services and CLI tools. "fleet" or "factory" are reasonably generic.
> Do we want "fleet-manager" to complement worktree-manager? We'll need something
> with proper install/uninstall, live-config support, etc. One designated machine
> will be the controller/Gateway, controlled via its API to manage the rest of
> the fleet; the harness-side installed CLI just sends commands with supplied
> auth. The user's machines won't directly have the Komodo controller or anything.

The operator confirmed **`fleet-manager`**. This replaces the proposed
workstation-local deployment CLI ownership with a remote-controller system and
thin client. The private hosting substrate remains an adopter choice, not a
public hard dependency. Controller-host selection is explicit and does not
authorize acquiring or migrating a live machine.

## Plan

### Mission amendment - Setup, refresh and first useful services

Public-safe summary of the operator's 2026-10-10 direction; full private wording
remains with the adopter and is not linked from public artifacts:

- Deliver "set up my fleet" through the private provider adapter and generic
  `fleet-manager`, using a harness recommendation and existing adopter config
  before diffing authoritative live resources and quota.
- Outfit empty eligible fleets within quota; offer a smaller/scaled proposal
  when quota is insufficient, never silently expand capacity.
- Keep essential central services distinct from optional agent/build/product
  venues. One-host fleets prioritize services and can use explicitly enrolled
  external execution hosts; login/manual adoption remains required.
- Refresh preserves work and custom capabilities. Containers/code venues require
  inspection/finalization before cleanup, not template-driven deletion.
- Integrate user-controlled fleet telemetry/monitoring at Gateway. External
  organization-governed analytics is a separate later private policy adapter.
- Initially prove index and dispatch on the Gateway host, then centralized
  worktree/bridge observation and a full harness in a provider-hosted container.
  Bare-host harness installation is not a prerequisite for this validation.
- The name remains `fleet-manager`; the operator explicitly reconfirmed it.

Implementation sequencing below is **agent-recommended**:

- [x] Review [safe setup/refresh proposal](fleet-setup-reconciliation.md) and
  amended core intent before provider/service changes.
- [ ] Implement bounded template/adopter/live/quota planning and version-bound
  apply receipts with no implicit provisioning/cleanup.
  The initial standalone offline JSON planner produces preservation-safe
  config suggestions, aggregate capacity proposals and revision-bound digests;
  apply receipts, YAML/default harness integration and placement remain open.
- [x] Prove empty, existing-custom, no-quota, one-host and stale/partial states
  in the offline planner. Live application and service deployment remain gated
  separately below.
- [x] Supply harness default-template and paired-config integration downstream;
  keep the portable runtime name-free and the private capacity adapter private.
- [ ] Prove first useful index/dispatch service roles and full managed harness
  container on an explicitly admitted/leased controller host.
- [ ] Add central worktree/bridge monitoring and declared future communication
  roles without inventing ready capabilities that are not implemented.
- [ ] Implement explicit Gateway telemetry scope/retention and preservation-safe
  finalization/retirement contracts before fleet-wide refresh/cleanup.

### Phase 1 - Reviewed intent and substrate contract
- [x] Land the machine-fleet vision amendment and this effort through automated
  review before expanding runtime/deployment behavior.
- [x] Record primary-source substrate evidence, required operator inputs and
  the fresh-install experiment that falsifies the claimed headless Komodo path.
- [x] Define the deployment adapter boundary against existing machine transport,
  installer/update, role/service and credential authorities; retain #5789 scope.

### Phase 2 - Repeatable first adoption
- [x] Land the standalone `fleet-manager` boundary amendment and
  [system proposal](fleet-manager-system.md) before implementing system packaging.
- [ ] Establish standalone client/controller/connector packaging and explicit
  install/update/diagnostics/uninstall, with a client-only default and no local
  Komodo/controller activation on user machines.
  Client artifact-contained lifecycle primitives and local resource diagnostics
  are the first prerequisite under #6424/#6240; managed lifecycle execution and
  controller/connector components remain open.
  [Installation repair conductor](installation-repair-conductor.md) is the
  agent-recommended #6464 setup prerequisite proposal; it does not implement
  execution or require downstream plugins to create core installation cells.
- [ ] Define controller-owned live configuration validation, atomic activation,
  last-valid-state recovery and explicit restart-required outcomes.
- [ ] Implement explicit inspect/plan/apply behavior for the chosen adapter:
  no mutation on discovery or mere plugin installation.
- [x] Prove initial Core deployment, configured first admin, authenticated API
  access, bounded onboarding and Periphery enrollment without browser steps.
  Use supported APIs and provider-injected secrets, never embedded credentials.
  Isolated synthetic-credential proof; production credential-provider adoption
  is still required before live deployment.
- [ ] Prove rerun, partial-failure recovery and owned uninstall; preserve an
  existing manager, unrelated roles, settings and data unless separately approved.
- [ ] Capture a fresh-environment receipt with exact release/images, commands,
  first readiness, remaining attended prerequisites and non-secret outcomes.

### Phase 3 - Named role deployment
- [ ] Publish independently runnable role artifacts using existing service
  distributions and lifecycle contracts, without a Copilot process dependency.
- [ ] Resolve named role placement through the manager's declared deployment
  resources; report per-target desired, accepted, installed, ready and converged
  states separately, including stale/mixed/uncertain outcomes.
- [ ] Declare budgets, persistence and replica/singleton behavior. Pin stateful
  writers until data continuity and fencing are proven; do not infer safe
  movement from generic restart/scheduling behavior.
- [ ] Prove credential revocation/rotation and role update/drain/rollback through
  existing authorities; manager success cannot substitute for service readiness.

### Phase 4 - Alternate adapter and completion
- [ ] Prove static SSH/Compose adoption against the same role/receipt contract
  without assuming cross-host scheduling, automatic failover or native rollback.
- [ ] Document the decision boundary for adopting a scheduler later; Swarm
  remains unimplemented until its scheduling requirement is separately approved.
- [ ] Land truthful adoption, diagnostics and ownership docs; resolve or transfer
  every Plan and Validation Plan item before marking this effort Done.

## Validation Plan

All entries are **agent-recommended implementation gates**.

- [ ] Three-input reconciliation preserves user overrides/unknown roles and live
  unowned resources; stale config/inventory/quota invalidates apply.
- [ ] No quota cannot trigger creation; one-host service-only placement reports
  unmet roles honestly and preserves external host enrollment requirements.
- [ ] Refresh and retirement inspect unfinished work, claims, PRs and volumes;
  no data/resource loss from template drift or process idleness.
- [ ] Initial index/dispatch and full harness container proofs use actual
  processes, managed worktrees/plugin loading and configured source auth.
- [ ] User-controlled monitoring is bounded/redacted and off-fleet analytics
  export remains disabled without separate explicit policy/consent.
- [ ] Pure contracts: explicit provider/adapter selection, fixed named operations,
  untrusted/ambiguous input refusal and no raw remote script/URL/executable API.
- [ ] Client-only fresh install and uninstall on Windows/Linux: no controller,
  Komodo/database, host daemon, worker mutation or local secret issuer; API
  credentials are supplied through the existing provider custody contract.
- [ ] Controller API scope/authorization and per-target receipts, distinct from
  manager credentials and machine enrollment; client disconnect cannot own work.
- [ ] Live config invalid-candidate rejection, last-valid-state persistence,
  atomic replacement, role retirement and restart-required reporting.
- [ ] Real first-touch Core/Periphery installation and authenticated enrollment;
  no pre-existing admin/API key, host config or browser state hiding setup work.
- [ ] Repeat and interrupted adoption, rejected enrollment, stale credentials,
  duplicate target identity, old-generation cleanup and no implicit capacity growth.
- [ ] At least two controlled Linux targets, per-target role outcomes and manager/
  connector/service outages; receipts come from actual processes, not mock success.
- [ ] Secret custody: provider-owned acquisition/renewal, restricted injection,
  no secrets in images, repo files, argv, logs, receipts or exported backups.
- [ ] Manager/service update and safe drain, failed candidate preservation,
  storage ownership and an explicit rollback/data-compatibility boundary.
- [ ] Stateful singleton fence and storage continuity; replicated-role scaling
  respects admitted resource budgets and does not steal writer authority.
- [ ] Static SSH/Compose parity for the supported contract, including honest
  unsupported scheduling/failover and application-specific secret reload behavior.
- [ ] Clean-room and available live tiers recorded per implementation PR.
  An unavailable lane remains explicit rather than substituted with stubs.
- [ ] Existing native/direct service operation and route foundation remain usable
  without the manager; no new mandatory daemon or plugin merely for deployment.
- [ ] Relevant tests, changefiles, documentation-impact review, automated review,
  real required CI and confirmed merges for each implementation slice.

## Proposal

[Safe fleet setup and refresh](fleet-setup-reconciliation.md).
[Standalone fleet-manager system](fleet-manager-system.md).
[Substrate evaluation and proposed adapter boundary](substrate-evaluation.md).
Runtime package/API details belong to the implementation contract after review,
not to the vision. This effort does not acquire or migrate live capacity.

## Journal

### 2026-10-10 - Direction and substrate selection
- Resumed the existing routing objective and retained its unpublished response
  and authentication work rather than replacing the foundation.
- Compared three independent primary-source evidence tracks and corrected
  rootless/secret-update overclaims before synthesis.
- Operator selected Komodo/Periphery first, SSH/Compose alternative, Swarm
  deferred, and confirmed the slug. Claimed #6030 under existing #5861.
- Public capture preserves core intent; private substrate identity and example
  deployment stay downstream. No runtime or live enrollment is claimed here.

### 2026-10-10 - Reviewed intent and isolated first-adoption proof
- #6032 merged after genuine current-head Copilot approval and real green CI.
  Reconciled only an effort-index addition conflict, retaining both campaigns.
- The operator approved starting the existing local Linux Docker engine and
  separately approved a privileged disposable nested-Docker target. Neither is
  live fleet enrollment or a claim of hostile-container isolation.
- Digest-pinned Core 2.3.3 created a synthetic initial admin, authenticated an
  isolated API client, created a short-lived nonprivileged onboarding key and
  enrolled a second real Periphery without browser use.
- The controlled nested daemon deployed the fixed harmless test role through
  Komodo's execute API. The update completed and independent Docker inspection
  confirmed running state; the role survived Core/enrolled-Periphery outage.
  Restart and repeated Compose/API operations retained exactly two server rows.
- Corrected fixture shortcomings: database readiness, writable generated key
  ownership, release-specific tagged login response, digest-to-image-ID loading,
  and teardown of every Compose profile. Final reusable runner passed on a fresh
  project with no host Docker socket or published ports; all owned resources and
  synthetic secrets were removed.
- [Proof harness](../../../tools/clean-room/komodo-role-fleet/README.md) is
  opt-in integration tooling, not a production installer or finished fleet.
  Two logical Periphery instances in one local test engine do not satisfy the
  two independent Linux host, service-role packaging, credential rotation,
  stateful placement or safe-update gates.

### 2026-10-10 - Proof merged and standalone boundary selected
- #6056 merged after current-head Copilot approval and all actual CI gates.
  The proof remains opt-in and is not a production fleet deployment.
- Operator confirmed `fleet-manager` as the standalone system and selected
  remote controller/Gateway ownership with thin authenticated user clients.
  No ordinary user machine should host Komodo Core as a side effect.
- Install/uninstall and live-config are requested system requirements, not
  deferred optional wrappers. This amendment precedes runtime packaging.

### 2026-10-11 - Standalone system amendment merged
- #6094 merged at `2026-10-11T01:15:18Z` after genuine current-head approval,
  corrected dependency/auth status descriptions and successful required CI.
- Public canonical topology is now thin user clients plus a designated remote
  controller/Gateway and explicitly adopted connectors. The private hosting
  provider is not a portable dependency or client-side controller.
- Next implementation slice establishes the standalone distribution/dependency
  boundary and client-only lifecycle. Server hosting, live-config runtime,
  authentication persistence and controller APIs remain unchecked plan gates.
- Native response #6034 remains held on confirmed partial-publication recovery
  #6038. No lease was hand-edited and the final test-only follow-up stays local;
  this does not transfer publisher ownership from the existing #5835 campaign.

### 2026-10-11 - Safe setup gate and first offline planner
- #6119 merged after actual current-head approval and successful CI. Its
  review-request workflow hit the shared account's API rate limit; waited for
  reset and reran that failed workflow rather than bypassing it.
- Established the standalone `fleet-manager` package and release/test registry
  entry. The first CLI accepts explicit non-secret JSON inputs and preserves
  adopter roles/extensions while proposing aggregate quota/budget-bound capacity.
- The CLI performs no provider, server, credential or cleanup operations.
  Review-required output is not fleet readiness; YAML/harness default integration,
  detailed role placement, installer/uninstaller and apply receipts remain open.
- Installed contained-package tests cover existing custom state, stale/unknown
  quota, unadmitted resources, one-host budget, future unsupported roles,
  duplicate/oversized inputs and plan identity/freshness. An actual installed CLI
  process proves no input/config writes or implicit hosting.

### 2026-10-11 - Planner review bounds and portability
- Separated bounded plan output from individual input limits. Near-limit
  preserved config, the union of custom/recommended roles and nested extensions
  now fit the larger explicitly bounded output envelope. Hashing, saved-plan
  verification and CLI serialization share its output-specific diagnostics.
- The standalone preparation fixture uses the runner's native interpreter
  layout; file errors retain input-kind diagnostics without an unreachable
  generic I/O fallback.
- Installed contained planner selection: 40 passed, including real CLI
  subprocesses. Runner/CI-selector contracts: 40 passed, one platform skip.
  These are offline contracts, not provider deployment, clean-room service
  hosting or live capacity receipts. #6133 still requires current review and CI;
  none of the fleet activation or lifecycle gates are completed by this fix.

### 2026-10-11 - Recorded provenance revalidation refusal
- The next review identified that explicit stale/different recorded template
  provenance remained only a suggestion. Added a plan blocker so saved-plan
  verification rejects that unresolved discrepancy while retaining the original
  provenance and custom roles. Optional missing provenance remains supported
  without inventing a previous template identity.
- Installed contained planner selection: 43 passed, including stale revision,
  different template and optional absent-provenance regressions. Current review
  and CI still gate #6133; this correction is not fleet activation.

### 2026-10-11 - CLI syntax outcome separation
- A subsequent current-head review identified argparse's default exit 2 for
  invalid invocation syntax, colliding with valid blocked-plan output. Parser
  failures now return the documented invalid-input code 1; help/version retain
  exit 0. Eight actual installed CLI subprocess regressions cover missing and
  unknown arguments, invalid integers, help/version and subcommand help.
- Installed contained planner selection: 51 passed. Shared review-request
  workflow import failure is tracked in #6191; no current workflow failure or
  review finding is bypassed to land the planner.

### 2026-10-11 - Offline planner merged; activation gates remain open
- #6133 merged at `2026-10-11T22:06:38Z`, commit
  `9aa8a7c6adace00fd2a38b9046936bf97095ca4c`, after current-head approval,
  resolved findings, green code CI and a successful repaired review-request
  workflow. The merge used the supported flow without bypassing review.
- The shared workflow fixes #6219/#6225 were released through #6233; the owning
  #6191 repair also has a real successful live request receipt. A fresh planner
  synchronization event exercised the repaired workflow instead of replaying
  the old failing snapshot.
- The bounded offline planning and pure scenario proofs are delivered. The
  mixed planning/apply item remains unchecked because apply receipts, role
  placement and live re-admission are not implemented. This effort stays Active.
- Next gates are actual planner release, downstream recommendation/config
  adoption, client/controller lifecycle and useful service roles. A merged
  planner is not a deployed fleet or authorization to acquire capacity.

### 2026-10-12 - Released planner and downstream read-only adapter
- The planner is released as `0.1.1-dev1`. Its downstream recommendation and
  paired-workspace adapter cleared the adopter's required review/CI and merge
  flow. Non-secret input preservation, stale/quota blockers and unsupported
  central roles remain explicit; no live capacity or service activation occurred.
- A local publisher partial-success/rebase bug was fixed through reviewed
  #6288 and subsequent release fix-forwards. The installed runtime's real
  affected-PR publication retry now succeeded with independently verified
  remote/tracked source head and both original and recovered exact lease heads.
- Next work remains client/controller/connector lifecycle, live configuration
  and separately admitted useful service roles. The mixed apply-receipt and all
  live deployment/migration gates remain unresolved; this effort stays Active.

### 2026-10-12 - Client lifecycle artifact prerequisite
- Coordinated #6424 with #6240 and the service-layout migration owner. Reused the
  index service's canonical resource-delivery pattern as one shared build-only
  helper instead of introducing a competing installer or maintained primitive.
- The thin client wheel contains the unchanged versioned-runtime primitive and
  the sdist can rebuild it outside a checkout. Nested project layouts consume
  the same canonical helper. Linked, missing and conflicting inputs fail before
  publishing a wheel; no hosting component is added to runtime dependencies.
- Read-only client diagnostics bind local resource evidence to the exact imported
  package's installed RECORD. This is neither signed release authentication nor
  controller readiness, and it cannot select a controller/node role.
- Real fresh native client installation, entry-point invocation, resource-tamper
  refusal and package-manager removal preserve separate user configuration.
  Installation uses a fresh target and isolated Python without ambient site
  packages, not a temporary copied interpreter or a simulated uninstall.
  Required native selection passed 268 tests in 56 seconds; the full additional
  artifact selection passed 9 tests in 22 seconds, and resource/runner selectors
  passed separately. Owned test sandboxes were removed without permission edits.
  Native Linux CI remains the second OS proof; no provider capacity or host
  activation is required or inferred by this artifact prerequisite.
- Managed install/update/rollback/receipt-owned uninstall execution, live config,
  useful service roles and migration remain open. This effort stays Active.

### 2026-10-13 - Client prerequisite review corrections
- #6432's existing index wheel fixtures now supply the shared helper. Further
  review corrections reject linked/non-directory resource ancestors before
  diagnostics read bytes and distinguish required canonical build inputs from
  optional included comparison copies. A missing canonical file cannot fall
  back to an included copy; a standalone sdist requires both included resources.
- Native installed-client proof also refuses a real linked target ancestor.
  The exact required 120-second local selection passed 363 tests in 63 seconds;
  the full changed client artifact/diagnostic selection passed 37, and the
  shared index artifact portfolio passed 8. Earlier overlapping native runs
  exhausted their aggregate budgets; sequential measurements confirmed the
  unchanged deadline, without weakening containment or child limits.
- The previous published head's actual Linux/Windows test-runner jobs exercised
  the client portfolio (354 each), not merely the separate index-service jobs.
  The corrected head still needs its own review and CI. The independent Windows
  console-root failure is the exact existing #6266/#6274 owned blocker; no
  competing repair, assertion relaxation or CI bypass is proposed.
- #6437's role-selected artifact preflight is merged and can be consumed by the
  subsequent managed-client slice. No lifecycle activation, release rollout or
  private migration gate is claimed complete here.

### 2026-10-13 - Client prerequisite merged; next runtime obligations retained
- #6432 merged at `2026-10-13T06:26:04Z`, commit
  `ca7b3e5630673bf2dee2c7519c950575ad91b825`, after actual current-head approval,
  resolved findings and every required check passed. The earlier independent
  Windows console-root failure remains owned under #6266; a subsequent passing
  native run is not a diagnosis or closure of that issue.
- The artifact/resource prerequisite is delivered to `dev`, not yet evidence
  of release promotion or an installed managed lifecycle. #6424 stays open for
  explicit install/update/rollback and receipt-owned uninstall, consuming #6437
  integrity preflight and #6240's separately reviewed release authentication.
- The generic machine-setup installation repair conductor is claimed under
  #6464, retaining #1886 uninstall and #6274/#6389 serving-convergence ownership.
  Discovery remains read-only. A reviewed implementation plan must precede
  executor implementation; no provider-specific composition is added upstream.
- The fleet and routing campaign worktrees were safely pulled forward while
  retaining recovery refs. Both efforts stay Active; controller/connector,
  useful service roles, authorization, provider admission and adopter migration
  completion gates remain unresolved.
