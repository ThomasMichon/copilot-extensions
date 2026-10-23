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

For the retained-good policy decision, the operator selected:

> explicit-retained-good

The selected option was presented as "Allow explicit retained-good recovery;
keep the admission floor unchanged." This selects a source contract, not a
live rollback or rollout. [The scoped recovery proposal](client-retained-good-recovery.md)
separates that choice from agent-recommended implementation details.

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
  Exact completed-runtime removal with durable physical custody, interrupted
  deletion recovery and reconstruction-generation fencing is merged in #7900.
  Owned pending command/selector withdrawal from intact unfinished producer
  proofs is merged in #7958; acknowledged-owner and execution proofs stay strict.
  Attempt-local metadata journals and staged namespace retry are merged in #8038.
  Complete producer headers before client pending ownership are merged in #8093;
  existing missing/malformed legacy proofs still refuse.
  Retained-good implementation,
  identity retirement/reinstall fencing and a managed installer remain open.
  [Explicit retained-good recovery](client-retained-good-recovery.md) is the
  operator-selected contract reviewed in #8106; executable implementation and
  its complete validation remain open.
  None of these local prerequisites authorizes a live rollout.
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

- [2026-10-22 - Client lifecycle](journal/2026/10.22-client-lifecycle.md)
- [2026-10-10 through 2026-10-21 - Foundation and client admission](journal/2026/10.10-foundation.md)
