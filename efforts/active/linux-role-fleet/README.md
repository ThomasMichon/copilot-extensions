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
  The common authenticated byte-stage prerequisite is merged in #6569.
  [Client lifecycle admission](client-lifecycle-admission.md) proposes the next
  serial durable-admission, construction and owned-activation/removal gates;
  durable admission is merged in #7459; construction and compatibility/local
  health are merged in #7804; physically attributed local runtime-selector
  activation and bounded forward retry are merged in #7847; explicit physically
  attributed client command registration is merged in #7866; physically proved
  registered/pending command withdrawal with durable retry is merged in #7882.
  Owned selector withdrawal after settled command unregistration is merged in
  #7891; it preserves every runtime and the admission floor.
  Torn-producer-journal recovery, completed-runtime removal, retained-good recovery
  and a managed installer remain open. None of these local prerequisites
  authorizes a live rollout.
  [Installation repair conductor](installation-repair-conductor.md) is the
  agent-recommended #6464 setup prerequisite proposal. Its ordinary-owner
  source implementation is merged in #6944; fresh-environment/native acceptance
  and actual consuming setup remain open under #6464. Downstream plugins are
  not required to create core installation cells, and active-core apply remains
  unsupported.
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
- [x] Supply explicit configuration-selected role/provider reference planning
  in the installed SDK, preserving custom declarations and bootstrap evidence
  requirements. This is not an external manager mutation or installed role.
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

### 2026-10-14 - Authenticated staging merged; managed admission remains open
- #6569 merged at `2026-10-14T03:30:07Z`, commit
  `fb06b7ae8b516cff4c432019603d598afc4e93cf`, after actual clean current-head
  review, all required checks and resolved threads. Its bounded workspace is
  finalized; that does not finalize this campaign or #6424.
- The delivered common stage returns currently authenticated immutable byte
  snapshots, not reusable public paths. Re-verification/consumption serialize
  with shutdown. POSIX cleanup captures into an exclusively owned private
  quarantine and retains identity mismatches with explicit errors; Windows
  admits and deletes consistent native full-width identities through handles.
  Same-user hostile-process isolation and actual ReFS-volume proof are not
  claimed by these contracts.
- Exact required native portfolio passed 489 cases with eight skips and seven
  full-build deselections in 106.14 seconds under the explicit 120-second
  deadline. Existing native Linux source/OS cleanup proofs passed; current-head
  hosted Linux/Windows checks passed before merge. Artifact execution,
  durable rollback state, candidate health and activation remain undelivered.
- #6499 is closed with explicit operator approval because #6497 substantively
  superseded its pointer-preserving inspection split. Source/backups/evidence
  remain retained, and no retired pointer contract was reintroduced.
- The next #6424 serial slice is the proposed
  [client lifecycle admission](client-lifecycle-admission.md) boundary.
  Implementation waits for this plan's review gate, retaining #6240 product
  release and #6443 Gateway/node ownership. Normal release remains blocked
  under the exact existing #6535 publication defect; a `dev` merge is not live
  installation, useful-service readiness or a completed fleet.
- Current-head review of #6624 identified a missing release-selection binding.
  The proposal now requires explicit attributable product-authority/adopter
  intent, bound to exact manifest, sequence and client closure before durable
  admission. A valid signature or higher sequence cannot select a release;
  validation must prove a signed but unselected release leaves state untouched.
  The minimal SQLite row retains that supplied decision's identity and digest,
  without adding a selector, ledger framework or runtime behavior before review.
- Re-review confirmed the selection finding resolved and identified one useful
  low-severity omission in the construction follow-on. The plan now names the
  established client-closure/API/data-schema compatibility gate before execution
  and activation, with an incompatible-candidate refusal case that preserves
  the prior active client/configuration and the monotonic admission floor.

### 2026-10-14 - Repair candidate and retained native execution gate
- The #6464 boundary reviewed in #6477 is implemented as a locally committed
  `agent-machines` candidate, not a published PR, released runtime or live
  consumer repair. It uses the current public source-neutral activation and
  installation-context APIs; ordinary downstream delivery neither creates nor
  requires core installation cells.
- A fresh native Linux clean-room run passed all six gates: real materialized
  payload installation and first-use CLI repair, 30 conductor cases and 138
  canonical packaging/source-guard cases. The Windows conductor portfolio also
  passed all 30 cases. These synthetic ordinary-owner proofs do not satisfy
  live setup, native WSL/macOS or unsupported active-core apply gates.
- Full Windows integration remains blocked. Serial native scenario matrices
  were separated without dropping assertions; measured cold-host workflow
  budgets and whole-tree fixture cleanup are explicit. A native ARM64
  interpreter comparison passed nine of ten selected integration cases, but a
  subsequent run failed four of ten. Shared host scheduling pressure remains
  observed, not a proven root cause or an accepted flake. An isolated pass is
  not permission to merge around the still-red native gate.
- The candidate observes validated already-active core cells but refuses
  undeclared core repair actions. #6464 retains native execution, actual
  consuming setup integration and real missing/stale-runtime repair before
  closure. #6424 retains managed client lifecycle; controller/connector,
  authorization, useful roles and adopter migration remain independently open.
  This campaign and routing foundation remain Active.

### 2026-10-17 - Repair review corrections; lifecycle integration retained
- #6944 remains open at `c82d93bdd9dd6926d80cfc69083cd2b5fcdfd149`.
  Its review corrections bind actual executable admission, re-admit initially
  ready and refreshed/post-install outcomes, propagate refused prerequisite
  blockers, and preserve process ownership through SIGINT/SIGTERM spawn and
  cleanup boundaries. Executable lookup through a relative `PATH` entry now
  produces an absolute admitted `argv[0]`; changing to the payload working
  directory at launch cannot reinterpret that executable path.

| Execution lane | Result | Scope |
|----------------|--------|-------|
| Linux binaries in WSL | 91 passed, three Windows-only skips | Complete repair/boundary/journal portfolio; installed WSL platform check also passed |
| Linux binaries in WSL | 166 passed | Packaging/source/trusted-reader portfolio |
| Windows-native processes | Nine passed, one POSIX-only skip | Executable admission, CMD and real plan/apply selection |
| Windows-native processes, preceding final-probe candidate | 14 passed | Cancellation/drift selection |

These bounded Windows selections are not a new full Windows-suite claim.

- Fresh-box attempts passed materialization and payload stamping but failed
  first-use provisioning with `TLS HandshakeFailure` fetching setuptools 84
  metadata from public `files.pythonhosted.org`. This reproduced in two
  disposable Docker attempts; it was not diagnosed as a universal feed outage.
  #6944 records that validation boundary, and #6464 retains its fresh-install
  follow-up. No TLS/feed waiver was used or passing result claimed. Native macOS,
  declared active-core apply and actual consuming setup remain under #6464.
  Current-head review and required CI still gate #6944; source publication
  does not establish a promoted release, installed repair or live deployment.
- The journal's first required Manager run hit the exact tracked #6490
  second-caller `RoutingLockTimeout` signature (CI run `39001166643`, job
  `117304199673`): one failed, 2,621 passed and 53 skipped. This is not a
  passing gate or permission to bypass it; the updated journal still needs
  real current-head CI success.
- The reviewed #6624 client-admission boundary and merged #6569 byte staging
  remain the managed-client prerequisites under #6424/#6240. Separate proposed
  Gateway SSH runtime/policy leaves are published in #7083/#7087, not yet
  accepted as merged dependencies or wired lifecycle behavior. Their owners
  may add the required canonical release entries for those leaves; shared
  configuration, admission/execution and lifecycle integration stays with its
  existing owner. An admission receipt is not an execution grant.
- Next serial implementation remains the validated client boundary followed by
  explicit configuration projection, provider adaptation and setup/reconcile
  integration against reviewed dependencies. Standalone controller/connector
  lifecycle, authorization, useful service roles, persistence and safe cutover
  remain unresolved. This campaign and routing foundation stay Active.

### 2026-10-17 - Ordinary repair merged; fleet completion gates retained
- #6944 merged into `dev` at `2026-10-17T14:41:24Z`, commit
  `b576e17ec2280c27866b0be550ff7b0604cfaa58`. The reviewed source head was
  `c246cd7a613925ed4f6859a9049d9a51eaaad7b0`; its actual Balanced review
  `5558255196` reported no open issues and CI `39040731281` succeeded.
- The final corrections independently bind declared action content, preserve
  declared interpreter semantics while attesting executable content, and
  confirm transient Windows/POSIX cleanup before releasing repair ownership.
  The runtime-owner census includes non-repairable and unresolved enabled
  peers, rejects linked/reparse runtime roots and ancestors, and refuses
  ambiguous ownership before probes or installers execute.
- Current source evidence includes 119 native Linux repair cases with four
  Windows-only skips, 160 Bridge authority cases and 167 packaging/scenario
  cases. The native Windows ownership/census/junction/fresh-apply selection
  passed 14 cases; this selection is not a new full Windows-suite claim.
  These are source-contract results, not a fresh Docker provisioning,
  native macOS, active-core apply or actual consuming-setup pass.
- No new fresh-box acceptance receipt was produced after the previously
  recorded package-feed TLS failure. #6464 retains that follow-up and the
  remaining native/consuming setup gates; no TLS/feed exception or passing
  first-provision result is inferred from the merge.
- The independently owned Gateway SSH runtime leaf #7083 merged into `dev`
  at `2026-10-17T11:53:40Z`; policy leaf #7087 remains open. This records
  source availability only: neither shared lifecycle wiring nor live
  automatic recovery has been delivered by those leaves.
- Current stewardship is limited to completing owned upstream PRs. The
  campaign and routing foundation remain Active; broader implementation and
  activation stay paused. Managed client admission, controller/connector
  lifecycle, authorization, useful roles, persistence and safe cutover remain
  unresolved. No plugin/runtime update, enrollment, capacity acquisition or
  service migration is authorized or performed by this journal update.

### 2026-10-21 - Explicit role/provider planning boundary
- Added strict optional role bindings to the preserved adopter configuration,
  immutable provider contracts and an explicit installed-package module CLI.
  Providers are registered by trusted code, never imported from configuration.
  The default client entry point and dependency set remain unchanged.
- The first Komodo projection names a Stack and its selected references.
  It does not contact Core, create resources, construct artifacts, acquire keys
  or execute deployment. Manager bootstrap remains an independently owned,
  unverified requirement rather than a fabricated installation receipt.
- Unknown implementations/capabilities, aliased controller references, duplicate
  resource writers, dependency cycles and unadmitted/stale placement refuse or
  block. Unbound roles/providers remain preserved without new bootstrap work.
- The focused role/provider and retained planner portfolio passed 138 cases,
  including a real built wheel installed without dependencies and exercised
  with source and ambient site packages excluded. Network/process effects were
  forbidden in that installed consumer proof.
- The complete required fast SDK portfolio passed 1,829 cases with three
  platform skips and sixteen existing full-portfolio exclusions in 98.49 seconds,
  under its unchanged 120-second boundary; owned cleanup completed. These are
  planning/packaging contracts, not manager or production adoption evidence.
- Existing artifact publisher/assembly, release authentication, client
  construction/admission, shared CLI, operation and credential owners retain
  their paths. Actual Core/Gateway execution, installed health, useful real
  services and every live activation gate remain open under this campaign.
- Review exposed diagnostic expansion beyond the shared collection bound for
  valid high-cardinality inputs. Blockers now group every distinct code by
  fleet/provider/role subject, fitting at most 513 groups without increasing
  shared limits or truncating diagnoses. The 172/256-provider cases and actual
  CLI serialization preserve blocked output and saved-plan refusal.
- Updated only the explicit package-module allowlist in the existing
  exact-source Gateway witness, admitting the new SDK modules and the
  already-merged client-admission source. A committed-tree regression and all
  89 retained runner/native-selection/probe guards pass. This fixes package
  closure, not a new native container or production qualification claim;
  Docker definitions, privileges and deadlines remain unchanged.

### 2026-10-21 - Durable selected client admission merged
- #7459 merged into `dev` at `2026-10-21T02:25:47Z`, commit
  `ad37a0df743b8121e4811b2f7c0a4f9e0e235378`. The actual current-head Balanced
  review approved `a6aa69e611dc0a20741da233327c5ca9a0ed1d3b`; its required CI
  and source-attribution checks succeeded.
- The delivered `client-state initialize/inspect/admit` operations reuse the
  shared `ReleaseSelection` and authenticated immutable stage. They persist a
  monotonic installation-owned floor, not an execution grant. Reports retain
  constructed, healthy, active, activation-authorized and live-readiness fields
  as false. No runtime, service, enrollment, credential or selector was changed.
- Native Windows and Linux source/installed-consumer contracts cover actual
  signatures, owner privacy, interrupted initialization and admission, hot
  rollback-journal CLI recovery, concurrent transactions and durable reopen.
  Linux additionally proves retained reader locks, WAL refusal without sidecar
  mutation, publication/ownership sync ordering and actual quarantine failures.
  Complete selected Linux files passed 43 admission, 60 staging, 51 planning,
  33 diagnostics and 82 shared-selection cases, with two explicit platform skips.
  These are not a full development-suite or fresh Docker qualification claim.
- Full local development preparation remains refused by the configured index's
  per-file age metadata under the unchanged dependency policy. Equivalent bytes
  carrying different source-index timestamps do not authorize an index switch,
  metadata override, lower floor or local waiver. The previously recorded
  fresh-box package-feed failure remains uncredited; qualification and release/
  installed adoption are distinct from the source merge.
- The next retained #6424 slice is authenticated candidate construction,
  explicit compatibility and actual client health through the canonical
  versioned-runtime owner before any selector activation. Preserve the active
  client, accepted admission floor and unrelated configuration on every failure.
  The merged #7083/#7087 SSH leaves remain policy/runtime dependencies, not
  managed-apply hooks. Shared construction/lifecycle stays with this campaign;
  Chat architecture and artifact work remains separately owned under #7238.
- The campaign and routing foundation remain Active. Reviewed source work
  continues in dependency order; live installation, enrollment, migration and
  activation still require their actual source, identity and resource gates.

### 2026-10-21 - Construction source qualification; publication remains gated
- The retained #6424 continuation implements explicit client construction,
  current API/config/retained-data compatibility and actual installed console
  health as local source WIP. It does not advance admission or activate a
  runtime. Owner-side `construction` tooling remains an explicit optional
  dependency; the candidate receives only the dependency-free client wheel.
- Standard canonical slots, construction/retention ownership and completion
  are reused. Candidate intent is stored outside the removable tree so an
  interrupted or partial removal cannot erase the sole ownership receipt.
  Reuse proves actual bytes and fresh health without rewriting completion;
  failed health preserves the accepted floor, prior selectors and user config.
- Current bounded Linux selections passed 26 construction, four independent
  process/installed-console, 35 diagnostics, 60 staging, 43 admission and 18
  selected wheel/sdist/resource cases; the staging/admission selections each
  have one explicit Windows-only skip. Native Windows separately passed the
  real construction/reuse pair, retry/partial-removal, independent installed
  owners, actual console initialize/admit/construct, failed-doctor preservation
  and caller-death cleanup fencing. Windows regressions passed 55 staging
  with six platform skips, 39 admission with five platform skips, 35
  diagnostics and 18 selected distribution cases. These are separate
  portfolios, not a combined full development-suite result.
- Earlier native timeout and post-read stability failures were not converted
  into a deadline, dependency-policy or integrity waiver. Fixed helpers retain
  one owning-SDK RECORD snapshot and recheck their bytes; SDK discovery stays
  qualified to the imported package. The unexplained earlier snapshot refusal
  remains an investigation, not an accepted flake: strict checks now name a
  bounded reason, and the owned-wheel 64-read stability contract passes.
  Qualification detail and the outstanding source gate are recorded on #6424.
- The canonical shared child-custody draft remains frozen and unpublished
  pending the actual retained source owner's interface agreement on #5472.
  Consumer success is not that grant. No vendored propagation, signing,
  enrollment, selector publication, live install, updater or service migration
  was performed. Fresh-box/full-profile acceptance and normal release/adoption
  remain separate gates.
- The read-only install-contract audit refuses publication on precisely the
  two canonical/shared-process differences from the agent-worktrees vendored
  engine. The existing #5472 coordination request has no response as of this
  audit. No sync, source publication or gate waiver was used to clear it.
- The reviewed Machine Fleet amendment #7513 and #6681's handback preserve the
  existing ordered lifecycle/setup-consumer integration. They require an
  explicit broader artifact-publisher/module-integration assignment, not a
  parallel controller or an implicit promotion of these client prerequisites
  into an executor. The campaign and routing foundation remain Active.

### 2026-10-22 - Use actual claims for PR ownership
- Correct the prior coordination interpretation: an issue or PR comment is
  not a source-custody lease, and waiting for a reply does not establish a
  publication hold. Discover competing PR ownership from actual worktree
  claim records on the relevant machines over SSH, then trace their owner
  references and liveness. Cross-check provider state independently; local
  tracked "open" records can outlive the actual PR.
- The accessible claim inventories identify other live owners for the
  matching currently open PRs. Those PRs remain with their owners; this
  campaign does not adopt, supersede or merge them. An unavailable machine
  or an unsupported query is unknown coverage, not evidence of abandonment
  or a repository-wide lock.
- The client implementation worktree has no outstanding PR claim: its plan
  and admission claims are released. Construction remains unpublished WIP.
  The real install-contract drift still needs ordinary source integration,
  propagation and qualification before publication; the absence of a comment
  reply is no longer treated as a separate authority gate. Review, required CI,
  release, authorized adoption and the campaign's remaining lifecycle gates
  are unchanged.

### 2026-10-22 - Construction integration and native qualification
- Integrated the client constructor with current shared child-launch/timeout
  outcomes and propagated the canonical engine to both vendored consumers.
  The finite Windows guardian retains the existing candidate lease through
  native Job settlement, including caller death. Success, child failure,
  launch refusal and settled deadline outcomes have native Windows contracts.
  No resident service or alternate installer framework was introduced.
- Current isolated Linux selections passed 26 construction, four installed/
  independent-process, 35 diagnostics, 60 staging and 43 admission cases,
  with the two existing Windows-only skips. Native Windows ARM64 passed the
  construction/reuse, retry, failed-doctor, installed-console, independent-owner
  and caller-death selections; shared guardian contracts passed 11 cases with
  one POSIX-only skip. Diagnostics passed 35 and admission passed 39 with five
  platform skips.
- The complete Windows staging membership passed 55 cases with six platform
  skips in explicit bounded groups. A prior whole-file attempt exceeded its
  aggregate deadline; it is not credited as a pass. No item or group deadline,
  integrity check, dependency age or configured index was weakened.
- Earlier emulated-interpreter timeouts and executable cleanup errors were
  not treated as native qualification. Restart Manager identified the Windows
  translation-cache process holding a failed fixture executable. Qualification
  used an already installed, signed native interpreter; no OS service was
  stopped or reconfigured. Its admitted binary dependencies remained within
  the declared ranges and unchanged seasoning policy.
- A real pinned Gateway package build exposed missing newly bundled process
  resources. The existing Docker recipe, closed-world source materializer and
  regression now carry the exact companions and new client module. Both
  repository and pinned-context wheel builds and a repository-independent
  sdist rebuild pass. This repairs the shared package closure, not Gateway
  deployment or native container qualification.
- The client remains construction-only and unpublished at this journal point.
  Current-head review/CI, actual merge, release and authorized installed
  adoption remain separate. Activation/removal and the broader campaign's
  useful-service, persistence and continuity obligations remain open.
- Review exposed a parent-only teardown injection that no longer crossed the
  new guardian boundary. The regression now injects inside the actual Job
  owner and records frozen inputs, construction/retention exclusion, live
  writers before the first accounting failure and settled descendants before
  release. The recovered teardown still refuses publication. Complete shared
  engine membership passed 43 cases with one POSIX-only skip in bounded groups.
- The opt-in native desktop regression passed timeout and owner-cancellation
  cases from the real windowless Python image, with two console/descendant
  cycles each. Native window/focus observation found no visible or Default
  Terminal windows and no foreground transitions; process handles and cleanup
  custody prove settlement. This remains outside the fast required lane.
- A further review found two missed consumer boundaries: normal standalone
  preparation omitted the process companions, and the guardian omitted the
  explicit Windows application path. Both failures were reproduced before
  correction. The preparation regression now exercises the actual entry point
  and retained wheel bytes after original-input mutation; the interpreter
  regression executes from an owning venv beyond `MAX_PATH`.
- Complete native guardian/preparation regression membership passed 79 cases
  with seven platform skips; guardian plus real windowless cases passed 14
  with one POSIX skip. The refreshed installed CLI and caller-death consumer
  cases passed. Normal full Windows x64 SDK preparation and 35 diagnostics
  passed; default native ARM64 full-development preparation still requires
  OpenSSL for its selected cryptography source build. That broader prerequisite
  remains under #6424; targeted native binary qualification is not a full
  fresh-platform readiness claim. No toolchain or dependency-policy override
  was installed to conceal it.

### 2026-10-22 - Admitted client construction merged
- #7804 merged into `dev` at `2026-10-22T08:48:10Z`, commit
  `668d96fbb4f7d4db309dbd04c05a35d534ae97e5`. Current-head Copilot review
  `5611508962` reported no open issues on
  `5ef1bd7ce2183180f4554fe9cb9d94ed4c5edff9`; all four concrete findings
  were fixed and resolved, and required CI/source attribution succeeded.
- The delivered slice constructs only the supplied durably admitted portable
  client, freshly checks compatibility and local installed health, and binds
  immutable completion to actual bytes. Canonical Windows child retention,
  explicit long-path execution and complete retained build-resource closure
  are included; this is not selector activation or a full installer.
- Normal standalone and Index test preparation share the same declared build
  resource closure. Native Windows and Linux qualification, windowless/caller
  death and retained-source mutation witnesses remain distinct from full
  fresh-platform, release and installed-adoption readiness.
- The next serial source obligation is owned client activation/removal under
  the reviewed lifecycle plan: reacquire canonical ownership, revalidate current
  selection/policy/compatibility and preserve foreign registrations, retained
  state and the accepted floor. No live client activation, enrollment, signing,
  service migration or updater invocation follows from this merge.
- The campaign and routing foundation remain Active. No broad Phase 2 or
  Validation Plan checkbox is closed by this construction prerequisite alone.

### 2026-10-22 - Owned client runtime selectors merged
- #7847 merged into `dev` at `2026-10-22T10:18:49Z`, commit
  `7675c529ace83933c9af01a2cfdbd300525ff708`. Current-head Copilot review
  `5612639052` approved `087a2e65436c2753194b921b4785831214ecc281`
  with no open issues; required CI and source attribution succeeded.
- `client-state activate-runtime` reacquires the existing installation,
  candidate and publication custody, freshly authenticates the exact admitted
  selection, checks compatibility and actual completed bytes, and runs installed
  local health before publishing canonical, physically attributed selectors.
  Durable intent and complete producer receipts permit bounded forward retry
  of the same candidate/target; foreign or incomplete evidence refuses.
  The accepted floor, previous candidate and user configuration are preserved.
- Native Windows ARM64 passed 57 ownership/recovery/target/diagnostics controls,
  real independent installed-owner serialization, and the actual installed
  console lifecycle outside the checkout. The exact published Linux source
  passed 21 activation, 37 diagnostics, two package-context builds and 40
  pinned-source controls. Constructor regressions and repository-independent
  wheel/sdist proofs remain distinct from fresh-OS qualification.
- The command publishes only local runtime selectors, not a frontdoor, PATH
  registration, service, controller or fleet role. Frontdoor attribution and
  owned removal are the next serial client obligations under #6424; retained-good
  rollback and whole-install identity retirement still need their own supported
  policy/fencing proof. No shared publisher or installer is replaced.
- The campaign remains Active with every broader setup, useful-service,
  persistence and continuity gate retained. Release promotion and authorized
  installed adoption are unobserved; no updater, live activation, credential
  change, enrollment or service migration follows from this source merge.

### 2026-10-22 - Owned client commands qualified for review
- The next #6424 slice implements `client-state register-commands` for an
  explicit existing private command directory. It requires the exact admitted,
  completed and physically selected client, fresh policy and compatibility,
  real local console health and canonical created-file publication custody.
  POSIX and Windows command families select a fixed candidate and run the
  canonical slot-local retention bootstrap before site imports. No PATH,
  profile, service, controller, credential or selector is modified.
- Registration retains physical producer proofs and an installation-bound
  receipt. Exact reruns revalidate without rewriting; updates replace only
  owned commands in the same directory. Foreign same-byte replacements and
  incomplete evidence refuse. Interrupted publication or acknowledgement keeps
  pending evidence and reports uncertainty; automatic crash recovery, owned
  unregister/runtime removal and identity retirement remain separate work.
- Native qualification reproduced the previously investigated snapshot
  instability: direct kernel evidence showed Windows read-side metadata-change
  time advancing while creation time, write time, size and named identity
  stayed stable. The integrity/staging reader now denies existing and new
  write/delete handles while permitting readers, checks stable creation/write
  metadata and retains every byte bound, identity and authenticated hash check.
  POSIX retains its change-time check. Native controls prove the actual sharing
  fence, released custody and interrupted descriptor cleanup; no integrity,
  dependency-age, feed or timeout waiver was used.
- Final Linux qualification passed 35 artifact controls, 60 staging controls,
  19 frontdoor tests, 27 construction tests, six native-process/installed-CLI
  cases, 37 diagnostics, all 27 wheel/sdist/context tests and 40 pinned-source
  controls, with three explicit platform skips. Native Windows ARM64 passed
  34 artifact controls, 55 staging controls, 98 combined client safety controls,
  real independent installed-owner serialization through both Windows launchers
  and the actual installed command-registration CLI. Native platform/privilege
  skips were explicit. The shared install-contract check passed all 13 plugins.
- These are fresh isolated SDK/fixture and native-consumer proofs, not a fresh
  OS, full fleet or deployed-service claim. Fresh-host qualification remains
  open under #6424; live fleet activation is not authorized by this source
  slice. The broader Phase 2 and Validation Plan remain open, and this
  implementation still needs current-head review, CI and a real merge before
  it is delivered. Release promotion and authorized rollout remain separate.

### 2026-10-22 - Preserve non-ASCII Windows launcher paths
- Current-head review of #7866 identified a real code-page dependency:
  Windows PowerShell 5.1 can decode BOM-less script paths as ANSI, and a batch
  wrapper containing literal UTF-8 paths depends on its active code page.
  The PowerShell command now has a UTF-8 BOM; the batch command is ASCII-only,
  using `%SystemRoot%` and its `%~dp0` sibling rather than embedded paths.
- Native Windows proved independent installed owners and both entry points
  with non-ASCII installation and command directories, the actual installed
  lifecycle CLI with those directories, and 18 command ownership/encoding
  controls. Linux passed all 19 frontdoor, 27 constructor and six native-process
  cases with the Unicode fixture, plus both package-context builds. The
  implementation and broader campaign still await their own remaining gates.

### 2026-10-22 - Scope Windows wrapper execution policy to the child
- The next #7866 review found the missing process execution-policy option in
  the PowerShell registration probe and batch compatibility command. Both now
  pass `-ExecutionPolicy Bypass`, matching the shared installer wrapper without
  changing persistent host policy or overriding Group Policy.
- Native coverage injects `Restricted` into the actual returned sanitized
  probe environments, not just the parent session. A real no-option primary
  control refuses with `UnauthorizedAccess`; the explicit primary and batch
  invocations pass, including Unicode paths, independent owners and verified
  reuse. All 19 native frontdoor cases passed within the existing 120-second
  portfolio budget; all 19 Linux cases passed too. No policy or timeout
  waiver, live activation or completed-campaign claim follows.

### 2026-10-22 - Owned client command registration merged
- #7866 merged into `dev` at `2026-10-22T12:31:28Z`, commit
  `e7109dca14223df0bb49b2f4622ba16c47606cd7`. Current-head Copilot review
  `5614097873` approved `a93a3aa0c86ba2e69e6779a3e77f13a3efc2468c`
  with no open issues. Both Windows findings were fixed and their threads
  resolved; required CI, source attribution and authorized completion passed.
- This delivers the explicit client command-registration transition and the
  native read-custody correction described above, not a complete installer.
  No PATH/profile/service or live fleet role was changed. Promotion and
  authorized installed adoption are unobserved; merging source did not invoke
  an updater, enroll a host or authorize activation.
- The next serial client work is owned pending-command recovery and removal,
  using the existing installation and physical publication receipts. Preserve
  the accepted floor, user configuration, foreign artifacts, backups and retained
  runtimes. Restoring an older executable is not ordinary removal and still
  requires the separately scoped retained-good compatibility/rollback policy.
- The campaign remains Active. Every broader Phase 2, useful-service,
  persistence, continuity and Validation Plan obligation remains open; this
  worktree continues the owning effort rather than finalizing after one slice.

### 2026-10-22 - Owned command withdrawal qualified for review
- The next serial source slice adds explicit `unregister-commands` against an
  existing installation identity and the same private command directory.
  It verifies registered/pending intents and canonical physical producer
  receipts, then uses the shared native recovery primitive with a separate
  empty-backup removal context. It does not restore the copy backups or an
  older executable. A durable removal marker blocks registration and lets a
  fresh owner retry after process death or partial metadata settlement.
- Present foreign replacements, corrupt/truncated producer proofs and changed
  installation scope refuse. Complete caught-failure recovery receipts permit
  withdrawal of an interrupted update whether it left new commands or restored
  physically proved old commands. Torn/incomplete producer-journal repair and
  forward completion of a pending registration are not supplied by this slice.
- Native Windows and isolated Linux ownership/fault suites pass, including an
  abrupt exit after the first unlink followed by a distinct installed owner.
  Actual installed CLI registration/withdrawal/no-op flows pass on both
  platforms with non-ASCII paths. A separately installed owner with no optional
  dependencies withdraws commands without executing a candidate. Native
  wheel/RECORD and repository-removed sdist rebuild controls pass.
- The first withdrawal test exposed a failed registration acknowledgement's
  shared `.tmp` filename blocking later registration. Acknowledgement and
  removal-marker staging are now attempt-local; old staging/evidence is retained
  rather than guessed away. Rerun, pending-update and foreign-replacement
  controls pass after that correction.
- Validation tiers: unit/fault and simple installed end-to-end contracts pass.
  The Linux lane creates an isolated fresh source/SDK and new private client
  state, not a fresh-OS clean-room or live fleet receipt. No live deployment
  lane is authorized for this local withdrawal prerequisite; controller/node
  provisioning, credentials, service migration and shared updaters remain
  outside its scope. Required source guards and current-head review/CI remain
  publication/merge gates, not claimed by these local results.
- Documentation-impact review updated the existing client-command reference,
  its README entry and this lifecycle plan; no top-level hub, adoption/runtime
  policy, independent installer or new pattern is introduced. This closes
  another part of machine-fleet `unified-coordination-product` using the
  reviewed standalone-system lifecycle and existing versioned-runtime
  ownership/publication patterns.
- The accepted floor, runtime selectors, retained candidates, user settings,
  backups and foreign artifacts remain intact. Runtime removal, retained-good
  rollback, identity retirement/reinstall fencing, the full managed installer
  and broader fleet Plan/Validation obligations remain open. Source qualification
  and a future merge do not authorize promotion, rollout or live activation.

### 2026-10-22 - Owned client command withdrawal merged
- #7882 merged into `dev` at `2026-10-22T13:29:53Z`, commit
  `7d42f525c5f99f09f4a1d4eb9660faf24e5e31ff`. Current-head Copilot review
  `5614965766` approved `f3c6c08b8477babb7a5ca0297f4218cf1b38236c`
  with no open issues or inline findings. Required CI, source attribution
  and the repository-authorized managed completion path passed.
- This closes only physically attributed command withdrawal and retry, including
  pending attempts with complete durable producer proofs. It does not repair
  torn producer journals, withdraw runtime selectors, remove retained runtimes,
  retire installation identity or implement the full installer. No live machine,
  service, enrollment, credential, PATH/profile or updater was changed.
  Promotion and authorized installed rollout remain unobserved.
- Re-read the canonical Plan/Validation Plan after merging. The parent campaign
  remains Active; client runtime removal and the remaining lifecycle gates are
  the next serial work, with retained-good compatibility/rollback still separate.
  Preserve the accepted floor and retained evidence through that work. Broader
  controller/node assembly, useful services, persistence and continuity remain
  unresolved; no phase or effort completion is claimed.

### 2026-10-22 - Owned selector withdrawal qualified for review
- The next serial source slice adds explicit `deactivate-runtime`. It refuses
  registered/pending/unsettled command state, verifies the installation owner
  and complete native selector producer proofs, and uses an empty-backup
  native removal context. Present foreign replacements and corrupt/torn
  producer receipts refuse before selector effects.
- A durable intent snapshots active and pending activation references before
  removal. It blocks activation/registration until a fresh owner settles both
  absent selectors and clears the acknowledgements. Mixed pending upgrades,
  first activation without previous ownership, partial metadata settlement,
  abrupt owner death and foreign replacement after interruption are covered.
  It does not restore older selectors, stop a client or release live retention.
- Unit/fault suites pass natively on Windows and in isolated Linux source/SDK
  environments. A distinct installed process retries after abrupt death; an
  independently installed dependency-free owner performs withdrawal. Real
  installed Unicode-path console flows cover construction, activation, command
  registration/unregistration, selector deactivation and exact no-op on both
  platforms. Wheel/RECORD and repository-removed sdist rebuild controls pass.
- The first implementation nested the selector target lease inside the same
  non-reentrant publication lease. The bounded test caught the contention;
  withdrawal now takes the canonical grouped target lease once. Test files run
  in separate bounded portfolios rather than enlarging aggregate timeouts or
  bypassing qualification-environment ownership.
- Validation tiers: real local installed-consumer and fresh isolated source/SDK
  checks were exercised. A full fresh OS and live fleet/controller venue were
  not exercised: this is a local selector transition, not host bootstrap,
  runtime deletion, fleet enrollment or deployment authority. Full hosting
  extras and live readiness are not claimed.
- Documentation-impact review: the standalone owner README, exact activation/
  deactivation contract, frontdoor sequencing and this lifecycle plan are
  updated. The suite architecture already delegates product detail to that
  owner; no new plugin, service or generic installer mechanism is introduced.
- The campaign remains Active. Completed-runtime removal still owes exclusive
  construction/retention/reference/liveness fencing and durable partial-delete
  recovery; it must not clear completion evidence to call failed-candidate
  cleanup or raw-GC an installation. Retained-good rollback policy, identity
  retirement/reinstall fencing, full installer, useful services and all broader
  unresolved Plan/Validation items remain open. Merge, promotion and authorized
  rollout remain distinct.

### 2026-10-22 - Owned selector withdrawal merged
- #7891 merged into `dev` at `2026-10-22T14:34:47Z`, commit
  `490d9f3ef20a13c55de4989e0428ec8c8b2ade86`. Current-head Copilot review
  `5615729290` approved `030ece9d581fa204a23ceecba94a0657d02bf0d8`
  with no open issues or inline findings. Required checks and the actual CI
  unit/coverage and guard jobs passed. The repository-authorized managed
  Maintainer completion path was used; no CI bypass was used.
- Some PR check summaries still reported pending after the Actions run had
  completed. The exact head's REST check-run records and the Actions jobs
  confirmed success before merging; the separate nonrequired reviewer check
  did not replace the already-delivered current-head approval.
- Re-read the canonical Plan and Validation Plan after merging. This closes
  only owned selector withdrawal/retry, not completed-runtime removal,
  retained-good rollback policy, installation retirement/reinstall fencing or
  the complete managed installer. The campaign and all broader unresolved
  fleet gates remain Active. Promotion and authorized rollout remain
  unobserved; no live machine, service, enrollment or updater was changed.
- Confirmed the merged PR through `pr-status` before the supported pull-forward;
  retained recovery refs and synchronized the owning worktree onto the merged
  `dev` commit. Continue with completed-runtime removal under canonical cleanup
  ownership, preserving external construction/admission and recovery evidence
  through partial deletion rather than mislabeling completed bytes as failed.

### 2026-10-22 - Exact completed-runtime removal qualified for review
- The next serial client slice supplies `remove-runtime --candidate` after
  settled command/selector withdrawal. The shared runtime owns exclusive
  construction/retention/publication cleanup custody and reference/liveness
  fencing; the installation owner supplies original completion/input validation,
  durable external physical tree evidence and exact native deletion. There is
  no raw root GC, completion downgrade, forced lease release or second installer.
- A retained intent precedes deletion. Whole-tree preflight and per-object
  checks preserve new/changed/same-byte replacement objects; partial retry
  tolerates only missing captured objects. Windows reuses pinned native handles
  for hashing and disposition; POSIX uses no-follow descriptor-relative
  traversal and deletes proved links without following them. Completion files
  are removed last. Outcomes precede acknowledgement; other runtime slots,
  configuration, admission floors and historical receipts remain.
- Windows qualification passed 39 removal tests (one POSIX-only skip), the real
  installed Unicode-path lifecycle, two wheel/RECORD and repository-removed
  sdist controls, 21 activation and 26 construction tests (one platform skip).
  Shared staging cleanup passed 55 tests (six platform skips). Linux passed
  40 removal tests, the real installed lifecycle, both packaging controls,
  21 activation tests and 40 closed-source runner tests. Real native lifecycle
  checks refuse deletion while a candidate process runs, then remove, no-op,
  reconstruct through current admission/health and remove the new generation.
- Separate installed owners prove abrupt-death retry and dependency-free
  removal. Partial deletion, corrupt intent, foreign root/directory/file
  replacements, registration/selector state, retained custody and interrupted
  outcome/acknowledgement are exercised. A real Linux venv exposed excessive
  nesting in the first tree receipt; shallow bounded pages fixed the actual
  consumer contract without relaxing JSON bounds. A stale archive caused by a
  rejected staging command was rebuilt from the checked current index before
  qualifying the Linux candidate; stale-source results are not evidence.
- Validation tiers: native unit/fault tests and actual installed consumers on
  Windows/Linux; fresh isolated Linux source/SDK/HOME and independent no-deps
  installed owners. No full fresh OS, full hosting extras or live fleet venue:
  the slice is local removal, and no live enrollment/deployment/retirement is
  authorized. Lint, canonical consumer synchronization and all 13 plugin
  install contracts pass; production dependency/timeout policies are unchanged.
- Documentation impact: standalone README and lifecycle contract, shared
  cleanup API documentation and this reviewed plan are updated. This closes a
  prerequisite of the existing lifecycle intent, not a vision extension.
  Retained-good recovery, identity retirement/reinstall fencing, the managed
  installer, controller/node assembly, useful services and all broader
  unresolved fleet Plan/Validation items remain open. The effort stays Active;
  review/merge, release promotion and authorized rollout remain separate.
- The first source CI caught growth beyond the canonical entry module's
  shrink-only size ceiling. Both exact owned removal and the existing
  failed-candidate cleanup are extracted into the already-shipped construction
  companion; the entry point shrinks instead of widening its baseline. Their
  typed operation bindings retain the same canonical leases and protection
  checks. The original failed-construction matrix qualifies that extraction.
- The first review found a real POSIX name-to-unlink race and crash windows in
  deterministic temporary journal names. POSIX now claims each object into a
  private, identity-bound receipt namespace before validation/deletion; a
  foreign claim winner remains there, and original-name replacements survive.
  Whole remaining source/claim preflight precedes effects. Journal attempts
  use unique exclusive temporaries and preserve abandoned empty/truncated or
  complete attempts. Focused tests cover replacement during the final hash,
  link and directory validation, partial native outcome writes and the initial
  marker interrupted before rename. No equally privileged hostile-process
  isolation or automatic restoration of a foreign claimed name is asserted.
- The review fixes qualify with 46 Linux removal cases and 42 Windows cases
  (four POSIX-only skips), plus the actual installed lifecycle, wheel/RECORD
  and repository-removed sdist controls on both platforms. The Linux closed
  source runner's 40 cases pass. Module-size ceilings and all 13 install
  contracts remain unchanged and pass. The prior original failed-construction
  extraction matrix remains qualified; no shared cleanup semantics changed
  while fixing consumer claim/publication recovery.
- Follow-up review found that a prior outcome could incorrectly certify
  unexplained absence after reconstruction of the same selection. Construction
  now validates and durably invalidates only the current per-candidate outcome
  pointer before cleaning/creating a new generation under custody. Historical
  receipts remain. The ordered regression and real installed console on both
  platforms prove refusal after a reconstructed root disappears outside owned
  removal. Final removal suites pass 47 Linux and 43 Windows cases (four
  POSIX-only skips); the actual native lifecycle passes on both platforms.
