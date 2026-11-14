# Linux role fleet

- **Slug:** `linux-role-fleet`
- **Repo:** copilot-extensions
- **Branch(es):** Reviewed plan, then independent serial implementation PRs against `dev`
- **Created:** 2026-10-10
- **Status:** Active; plan reviewed, isolated adoption proof delivered
- **Vision:** [machine-fleet](../../../visions/machine-fleet/README.md):
  `repeatable-role-fleet-adoption`, `bounded-desired-state-control`,
  `role-placement-preserves-service-authority`,
  `convention-discoverable-diagnostic-logs`
- **Umbrella issue:** #6030
- **Sub-issues:** #5861 (existing named reconciliation follow-on)
  and #8377 (diagnostic logging audit and retention)
  and #7192 (first-class configurable Actions runner loadouts)

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
| Runner loadout participant | Vision-first #7192 template, placement and declaration-to-managed-owner flow; existing release/lifecycle/authentication owners retained | Existing #7192 coordination and separate managed upstream worktree |
| Trusted base-image participant | Bounded #8352 image recipe, contract, docs and tests; no controller or credential ownership | Separate managed upstream worktree; campaign owner integrates |
| Diagnostic logging contributor | Bounded #8377 shared Bridge/Dispatch application logging, discovery and retention source; no installer/native capture or deployment ownership | Managed source worktree `spinning-sprocket` |
| Telemetry attribution planner | Bounded #8377 public intent and source-backed attribution proposal; no implementation, collector or deployment ownership | Managed source worktree `gleaming-gasket` |
| Adopter | Capacity, inventory, trust and live activation approval | Provider-specific downstream flow |

## Coordination

- **Direct role cohort receipt:** the [client/onboarding/ARC amendment](cohort-receiving.md)
  preserves #6424/#6681/#7192's complete distinct Plan/Validation and current
  owners. Actual parent/Active-effort readback precedes administrative tracking
  intake; #7192's named-owner transfer condition remains independently open.
  The #5861-only KEEP convention is not generalized, and no active source,
  provider, native, release or resource custody transfers.

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
- **Retained Komodo substrate:** The [canonical substrate contract](komodo-substrate-lifecycle.md)
  keeps #8860 under current campaign/deployment/adopter roles, the original
  appointed `komodo_*` owner and existing operation/admission/node-journal,
  client-release/installer, SSH/onboarding, credential/storage and workload
  custodians. #9274 SDK source and #9338 fixed-profile source credit do not
  complete actual official-image/adopter/Linux-runtime acceptance. The earlier
  continuity SDK lane is not Manager/Core installation. No new implementer,
  claim transfer, coordinator or competing workload writer is appointed.
  Generic Taking is not a new ACK ceremony, and responsibility does not expire
  by age. Normal reviewed delivery and full receiving readback precede any
  separately supported source judgment.
  #9735 continues that same substrate appointment with focused identity,
  native-observation and same-original SDK repair. Its
  [repair amendment](komodo-substrate-lifecycle.md#original-attempt-sdk-repair-amendment)
  was reviewed in #9738 and its qualified SDK source merged in #9752; it does
  not transfer journal, admission, client-release, credential or workload
  ownership. Release/acquisition, original UNKNOWN custody and actual adopter
  qualification remain open.
  The [fresh checkpoint-authorization proposal](komodo-repair-checkpoint-authorization.md)
  continues #9735 within those same owners. Distinct freshly authenticated
  permits must retain each 30-second ceiling and exact-original custody; no
  common-admission change or ordinary convergence fallback is proposed.
- **Logging slice:** `spinning-sprocket` owns Dispatch application diagnostics,
  local discovery and existing-owner idle maintenance under #8377. It does not
  modify #8370's installer-attempt capture or #6812's progress formatter.
  Native capture, telemetry configuration adoption, controller/container mappings
  and actual released/running qualification remain explicit integration acceptance.
  The bounded application source merged as #8415 after approval and required
  CI; the [logging sub-plan](logging-conventions.md) records its exact merge,
  validation and transferred remaining interfaces. None of the broad logging
  amendment's checkboxes is closed by that partial source delivery.
  The continuation reuses its policy in the existing shared library and adopts
  Bridge application logging with bounded producer work; native capture and
  installer/lifecycle/deployment owners remain separate. Its source validation
  and acceptance are tracked in the same logging sub-plan, not a competing effort.
  Shared policy and Bridge application source merged as #8495 after exact-head
  approval and required CI. #8577 subsequently accepted the additive retained
  configuration loaders, worker-owned quiet maintenance and local discovery.
  Adopter helper/configuration migration, release/install verification and live
  qualification remain undelivered. The sub-plan records the exact source
  contract and remaining owners; the parent and #8377 remain open.
- **Trusted image slice:** #8352 owns the bounded
  [trusted harness container plan](trusted-harness-containers.md) under #6030.
  Its proposed `.devcontainer/trusted-harness-base/` reservation does not transfer
  controller, credential, publication/signing or Gateway/SSH source custody.
  Campaign owner retains integration and publication; implementation awaits
  the plan's merged review and an explicit execution instruction.
- **Manager substrate slice:** #8860 owns the bounded
  [declarative Komodo substrate lifecycle](komodo-substrate-lifecycle.md).
  Its independent install/adopt/update/recovery source appointment does not
  transfer client lifecycle, credential/release, SSH, workload or ARC ownership.
  An offline role projection is not accepted as manager installation.

- **Finite onboarding continuation:** F6030 retains the existing campaign,
  deployment/adopter, #6240 release/construction, #6241 operation/admission,
  Gateway/SSH, agent-ssh profile/registry, credential-provider and native/resource
  owners. It neither takes the broader constructor reservation nor makes that
  whole PR a prerequisite for the independently scoped installed consumer.
  Existing source appointments, original-operation custody, source/release/
  installed distinctions and separately authorized effects remain unchanged.
  No enrollment, credential, service, network, capacity, migration or retirement
  authority follows from this planning amendment.

- **Finite conductor continuation:** #6464 keeps its existing single writer,
  current required-selection and selected-project readiness contributors, exact
  source reservations, and the complete direct repair Plan/local/global Validation
  below. #1886 uninstall, #6274/#6389 serving, #6240 release, configuration and
  native/resource custodians retain their separate scopes. Do not revive the
  superseded #6499 pointer-publication approach or duplicate the conductor.
  Broad Taking is not a new ACK requirement. The strict repair annex's explicit
  #6464-OPEN-until-implementation-and-real-consumer-proofs condition remains:
  receiving publication does not waive it or authorize NOT_PLANNED retirement.
  Normal reviewed merge and whole-current ROOT/necessary-annex readback precede
  any later separate source judgment; this amendment supplies neither actual
  implementation acceptance nor resource transfer.

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

For installation generations, the operator directed:

> Always reinstall forward. We never want to block a new install unless the new
> generation fails to start; graceful handoff from the old generation is a
> "nice to have"

The [client lifecycle proposal](client-lifecycle-admission.md) must therefore
make new-generation installation/start independent of old-generation retirement,
cleanup and graceful handoff. This is source design direction, not authorization
to deploy, force-stop an old owner, bypass release authentication or erase the
accepted floor. The isolation and fencing details remain agent-recommended.

## Plan

- [ ] Complete the [direct role-lifecycle cohort Plan](cohort-receiving.md#direct-plan)
  through existing client, onboarding and ARC owners, preserving their
  distinct requirements, current source reservations and original limits.

### Capacity-grid loadouts and configurable runner amendment

The operator clarified the existing designer/controller split and requested
upstream realization. Public-safe summary: fleet-manager designs expectations,
actualizes contributed role/slot templates into the knowledge repository's
final composition, and publishes named-machine allocations. The Gateway-side
controller pulls published sources, prepares the deployment, notifies
fleet-manager and waits for exact-plan confirmation. A grid-based view must
compare CPU, memory, disk and other supported allocations against capacity.
The confirmed operation includes required auth relays, tunnels, API/UX exposure
and control-side deployments within an inspectable outfitting duration.

The latest execution request is:

> Make sure the vision upstream accounts for this. Then, see if you can wire in a flow for this into the grand system, building out the pieces needed to make GitHub Actions Runners for copilot-extensions configurable via fleet-manager

The operator subsequently prioritized adoption of the existing runner pool:

> Let's focus on getting the ARC runners formally "adopted" by out fleet-manager and Gateway controller system, so nothing interferes with them. Then we can cease manual tweaks and leverage a formal kubernetes-style control plane for them.

This prioritizes durable existing-pool adoption and controller ownership before
further manual tuning or graphical presentation. It does not select another pool,
replace ARC's pod lifecycle, or turn a session/task lease into permanent service
ownership. The [adoption-first sequence](runner-loadouts.md#adoption-first-priority)
retains the existing release, provider, custody, credential and activation gates.

The operator subsequently accepted reusing the existing canonical Git-backed
coordination store for a distinct permanent service-custody record, rather than
another store or an extended task lease. The
[custody realization proposal](runner-service-custody.md) preserves provider
admission, exact accepted transfer and incumbent-safe reconciliation. Its
protocol details cleared source review in #8931; live adoption
and temporary-obligation discharge remain separately gated.

The [runner-loadout realization plan](runner-loadouts.md) extends this
existing campaign and #7192; it is not a second controller or a replacement
for #6240/#6424/#6241 and #6681 ownership. Its decomposition and validation
are **agent-recommended**. Existing client, Gateway/SSH, trusted-image and
credential-provider work stays with its current owners.

- [x] Land the loadout/Gateway vision and this implementation amendment
  through ordinary review before changing runtime behavior.
  #8434 merged after exact-head Copilot approval and required CI; this closes
  reviewed intent only, not runner provisioning or live fleet acceptance.
- [x] Implement contributed template/slot composition and capacity-aware
  named-machine runner allocations, preserving custom registrar state.
  #8465 supplies the installed design command and JSON resource grid. It does
  not supply a graphical loadout surface, controller execution or live runners.
- [ ] Connect the committed desired composition to controller-owned source
  acquisition, preparation, notification and exact-plan approval.
  #8535 supplies the published-source and authenticated Gateway SDK path with
  durable scoped proposals, notifications, bounded plan inspection and exact
  review decisions. Production controller binding, selected deployment-version
  admission and managed-owner execution remain open; review is not a grant.
- [ ] Carry the typed runner declaration through the real supported deployment
  owner with complete support closure, bounded outcomes and no raw command API.
  #8597 supplies fixed admitted Helm convergence with canonical operation,
  exact-content grants, durable local resource custody and live owned-spec
  checks. Production controller binding, actual support-owner authentication
  and complete lifecycle/adoption remain open; this is not an adoption receipt.
- [ ] Formally adopt the existing runner pool into durable fleet/controller
  ownership, prove noninterference and session-independent reconciliation, then
  retire manual tweaks and the temporary agent-held protection through the
  existing ownership flow.
- [ ] Prove real convergence, update/failure recovery, drain/removal and one
  authorized job; planner-only acceptance cannot close this amendment.

### Repository-led preparation and local telemetry amendment

The operator's 2026-10-23 direction extends this existing campaign, not a parallel
controller or deployment implementation:

> Let's focus on adding deployment-control modules to the Gateway/Komodo system. We need the Gateway or dedicated controller to pull config from the repo(s), determine the expected layout and package versions, and then be ready to activate deployment and updates. We want to be able to use the fleet-manager on a dev machine to say "go", but not have to feed the config manually to the controller.

The operator then narrowed immediate execution (private machine/agent identities
redacted, surrounding direction retained):

> Actually let's not overcomplicate this: extend the vision to cover what we discussed, extend the existing Gateway/Fleet control effort to cover it, but then just give me a simple jsonc-bucket dump sink to a folder somewhere on each machine, so worst-case we can SSH into each machine and view data. Then, let's debug-activate it here on [selected local machine] so we can validate what we're collecting. Defer Grafana until after [the existing Fleet Management owner] can get more Fleet Management properly deployed.

The operator clarified the dump format as **JSONL**. The
[preparation and observation sub-plan](repository-config-preparation.md) records
the future controller/Grafana requirements and their validation. Current adopter
execution reuses the already-shipped local spool seam only; it does not implement
controller modules, enroll a fleet or deploy Grafana. Existing Fleet Management
owners retain those changes.

- [ ] Implement controller-owned acquisition of explicitly adopted configuration
  repositories and exact desired module/package selection.
- [ ] Produce an inspectable prepared generation, then admit thin-client
  activation without client-supplied config blobs.
- [ ] Prove stale/changed source, source/credential outage, uncertain activation,
  unchanged repeat and independently bootstrapped manager cases.
- [ ] Add the optional operator-owned telemetry service role and explicit
  multi-destination delivery after the deployment foundation is usable.
- Local JSONL adopter/debug capture is useful independent evidence, not a
  completion claim for any of these controller or hosted-sink items.

### Diagnostic logging audit and retention amendment

The operator extended this existing effort:

> We should also do an audit of existing logging while we in this effort and ensure all logging goes to clearly by-convention discoverable and predicable locations, with timestamps, attribution, and 30-day retention

The [logging audit and implementation plan](logging-conventions.md) records
source-backed coverage, existing owners and measurable acceptance. It includes
native installer/cutover output, not just optional telemetry. #8377 owns
integration; #8370 retains installer-attempt logging and #6812 retains
install/update progress formatting. No competing implementation or live fleet
mutation follows from this amendment.

- [ ] Complete the writer/consumer inventory with explicit disposition for
  every diagnostic logging category.
- [ ] Implement owner-resolved log discovery, timestamps and attribution
  through the existing logging/launch and deployment owners.
- [ ] Enforce elapsed 30-day retention for active, archived, quiet/restarted
  and container-owned logging; preserve explicit incomplete-attempt protection.
- [ ] Validate concurrent writers, native Windows/POSIX handles, protocol
  output/privacy and truthful failed/rolled-back/committed outcomes.
- [ ] Deliver the [persistent isolated spawn reproducer](persistent-isolated-reproducer.md)
  through a reviewed source plan, bounded synthetic implementation and separately
  approved real persistence/fault qualification. Existing restricted-provider
  invariants, ephemeral tests and native/installer/deployment owners stay unchanged.
  The operator selected ordinary persistent volumes with monitored storage
  thresholds, not hard quotas; #9101 reviewed the linked amendment before
  implementation of that source policy.
  The selected [retained experiment lifetime](retained-experiment-lifetime.md)
  behavior and concrete API proposals cleared review in #9176 and #9183;
  existing-owner integration settlement remains before service/peer wiring.
  Ordinary disposable-test expiry and production lifetime remain unchanged.
- [ ] Review and realize the [keyed attribution continuation](telemetry-attribution.md)
  through the [existing logging sub-plan](logging-conventions.md), preserving
  internal diagnostics, truthful subject identities and user-held-key self-lookup.
  Plan publication precedes separately admitted implementation; shared deployment
  remains blocked on the complete ingestion/query identity-separation review.

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
- [ ] Deliver the bounded #8352 [trusted base-image slice](trusted-harness-containers.md);
  then integrate its separately owned build, deployment and venue-validation
  dependencies without claiming the broader fleet proof complete.
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
  are the first prerequisite under #6424/#6240; complete product setup integration
  and controller/connector components remain open.
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
  [Explicit retained-good recovery](client-retained-good-recovery.md) is the
  operator-selected contract reviewed in #8106; its client source implementation
  and focused Windows/Linux qualification merged in #8185. The accepted floor
  remains unchanged. The operator-directed
  [forward-first generation contract](client-lifecycle-admission.md#forward-first-installation-generations)
  has source primitives merged in #10439: independent construction/start,
  generation/floor fencing, explicit incoming default-command publication and
  separate exact-generation withdrawal/removal. Native Linux/Windows installed
  admission, construction, publication, withdrawal and removal consumers are
  qualified in #10469; that source also corrects Windows private-state creation,
  rollback-journal ordering and held-handle runtime-directory custody.
  Their exact-source evidence and earlier artifact installation are recorded in the
  [incoming-selection journal](journal/2026/11.04-client-incoming-selection.md).
  The broader lifecycle matrix and complete managed installer remain open;
  these consumers are not a fresh-OS or adopter receipt.
  #10496 composes explicit client-generation install/forward update and exact
  uninstall through those existing owners. Both native hosts passed the original
  six and distinct managed six installed consumers at the reviewed head, before
  actual merge. Its original incoming observation is durable across retries.
  This closes that explicit client composition, not the broader packaging,
  controller, fresh-OS, release-acquisition or adopter gates.
  #10509 composes the existing explicit legacy-floor import with managed install.
  Both native hosts passed the original six and seven managed consumers at the
  implementation head; the final documentation-only correction retained those
  byte-unchanged runtime/tests and passed renewed review and required CI.
  Import/install and exact retry preserve the original floor and do not require
  an already imported predecessor to remain present. Complete role packaging,
  installed-client self-update, first-touch and adopter duties remain open.
  Its [authoritative replacement reconciliation](client-lifecycle-admission.md#authoritative-replacement-reconciliation)
  keeps incoming readiness plus complete current launcher/registration selection
  as the completion gate. Retained generation work is not complete installation
  when missing old proofs or stale installer state can veto that selection;
  strict old-object deletion custody remains separate.
  None of these local prerequisites authorizes a live rollout.
  [Installation repair conductor](installation-repair-conductor.md) is the
  agent-recommended #6464 setup prerequisite proposal. Its ordinary-owner
  source implementation is merged in #6944; fresh-environment/native acceptance
  and actual consuming setup remain open under #6464. Downstream plugins are
  not required to create core installation cells, and active-core apply remains
  unsupported.
#### Finite installation-repair continuation (#6464)

These unchecked rows receive the complete current conductor acceptance under
its existing machine-setup owner. They refine the existing
[repair contract](installation-repair-conductor.md), not a new executor or
execution appointment. Ordinary-owner source delivered in #6944 is credited;
remaining first-touch, active-core and actual consuming-setup duties are not
completed by this tracking amendment.

- [ ] **R6464-P1.** Keep default discovery/readiness/plan nonmutating and the
  installer-readiness graph library execution-free. Resolve enabled payloads
  through existing attributable activation/source admission, with ordinary or
  supported legacy ownership distinct from already-active validated core cells.
  No cache/PATH/root guessing, cross-cell capture, cell creation or automatic
  session-start installation follows.
- [ ] **R6464-P2.** Resolve the reported root-free ordinary-owner admission case:
  identify the supported attributable protocol for a declared external-tool
  owner OR coordinate the narrowly reviewed conductor/declaration correction.
  Do not fabricate a runtime root, silently disable the declaration or use an
  invented ownership model to block unrelated selected repair. Preserve actual
  root-collision/source controls; honest refusal is not fulfillment of this case,
  and the reported consumer finding is not an independently reproduced diagnosis.
- [ ] **R6464-P3.** Retain explicit qualified module selection plus real
  prerequisites; optional modules enter only as actual prerequisites. Preserve
  the additive required-module selection reservation and complete enabled-owner
  census: missing payloads or malformed declarations cannot be silently omitted.
  An incomplete census refuses, without changing existing explicit selection.
- [ ] **R6464-P4.** Bind plan/apply to exact platform, source/content/provenance,
  enablement, declarations, graph, actions and applicable receipt generations.
  Re-admit before effects and each selected action; changed input refuses rather
  than replaying or silently replacing the accepted plan. Invoke only exact
  absolute manifest-owned actions through existing bounded lifecycle primitives.
- [ ] **R6464-P5.** Preserve the selected-project readiness-context correction
  under its existing contributor: bind the requested project into qualified
  admission and the plan, and use that context for read-only owner probes.
  Installer cwd/authority, absolute action admission and each owner's readiness
  judgment remain unchanged. Actual Index/Vault configuration refusals are not
  reclassified as inactive or configuration-empty to obtain success.
- [ ] **R6464-P6.** Independently observe each owner's readiness after its action;
  report observed-before, selected, attempted, installer and observed-after
  outcomes, blockers and restart obligations. Keep failed, unknown, timed-out,
  unsupported, blocked, configuration-empty and ready states distinct. Only
  explicitly dependency-satisfying empty results unblock dependents; independent
  branches may continue. A successful build or installer exit is not readiness.
- [ ] **R6464-P7.** Preserve bounded probes, action/output budgets, cancellation
  and owned transient-descendant cleanup, while durable service lifetime,
  convergence and recovery remain installer/lifecycle-owned. Unknown cleanup or
  mutation is not successful completion or permission for duplicate operations.
  No raw command API, credential discovery/escalation or wider retry is added.
- [ ] **R6464-P8.** Preserve policy-denied absent/unconfigured runtime outcomes
  without bypassing machine gates. When enabled payloads coexist with stale
  selected/serving incumbents, expose unresolved state and the supported explicit
  repair owner rather than implying whole-set convergence from payload success.
  Authorized repair preserves configured owner/profile, state and live handlers,
  and observes selected and serving generations independently where applicable.
- [ ] **R6464-P9.** Complete the original supported Windows/Linux/WSL/macOS
  first-touch and repeat-repair/config-preservation tiers, plus the separate
  already-active core-cell fixture and declared-action support. Current refusal
  of undeclared active-core actions is correct safety, not positive repair proof.
  Keep unavailable lanes and the later fresh-environment TLS failure explicit;
  an older clean-room pass does not replace that failed attempt.
- [ ] **R6464-P10.** After the retained prerequisite gates, wire an actual
  consuming setup through the same authority and prove a real missing/stale
  enabled runtime becomes ready. Preserve the original serial delivery boundary;
  source merge, release, graph planning or local installation alone does not
  complete this consumer. No dormant capability, provisioning, uninstallation,
  durable-data cleanup or live rollout is authorized by this amendment.

##### Finite installation-repair local Validation

- [ ] **R6464-LV1 (P1).** Prove whole-fixture nonmutation on default invocation,
  supported ordinary/legacy and validated active-core admission, exact source
  distinction, foreign-cell rejection and no implicit cell/installer creation.
- [ ] **R6464-LV2 (P2).** Through the owning admission consumer, qualify the
  reported synthetic root-free external-tool declaration plus a separately
  selected ordinary runtime, preserving root-collision/source controls and
  explicit malformed/unsupported/unknown refusals without fabricated roots.
- [ ] **R6464-LV3 (P3).** Cover explicit and required-module selection, complete
  enabled-owner census, actual prerequisite closure, optional exclusion,
  missing/malformed payloads and malformed/cyclic graph refusal.
- [ ] **R6464-LV4 (P4).** Exercise changed source/declaration/receipt/content and
  executable/containment controls, digest-bound re-admission, exact Windows/POSIX
  argument boundaries and per-action binding checks before any effect.
- [ ] **R6464-LV5 (P5).** Strengthen the actual caller control to require the
  selected project's cwd for configuration-aware read-only probes, keeping
  installer context unchanged. Preserve genuine Index/Vault configuration
  refusal and distinguish real backend discovery from activation readiness.
- [ ] **R6464-LV6 (P6).** Exercise independent post-readiness, partial failures,
  blocked dependencies with independent progress, unsatisfied/satisfying empty
  outcomes, unknown probes, restart-required state and honest aggregate results.
- [ ] **R6464-LV7 (P7).** Cover slow probes/actions, output limits, repeated
  cancellation, interruption and retained descendants through existing owners;
  keep durable services and unresolved custody distinct from transient cleanup.
- [ ] **R6464-LV8 (P8).** Qualify policy-denied absent/unconfigured and enabled
  stale-selected/serving cases without granting deployment authority from an
  incumbent. Record owner/profile preservation and separately observed readiness;
  historical authorized recovery is not continuing live permission.
- [ ] **R6464-LV9 (P9).** Record exact native first-touch/repeat and fresh-source
  evidence separately for Windows, Linux, WSL and macOS and the active-core
  fixture. Credit established Windows/WSL and earlier Linux proofs only at their
  actual revisions; retain later TLS failures and pending positive action/native
  gates. Mock dispatch or another POSIX host is not a missing native receipt.
- [ ] **R6464-LV10 (P10).** Prove the actual consuming setup's missing/stale-to-ready
  flow only after the retained serial prerequisites, preserving independent
  owner judgment, configuration and source/release/native/installed distinctions.
  No test, build, setup or live action is run by this documentary intake.

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

#### Normal first-touch and installed SSH continuation

These unchecked obligations refine the existing onboarding lane; they do not
appoint another implementer or activate a fleet.

- [ ] **F6030-P1.** Compose normal first touch through the existing supported
  interpreter, explicitly activated configured-Vault and configuration-selected
  transport/provider setup owners, including prerequisites and key enrollment.
  Distinguish runtime installation, database registration, unlock, key availability
  and readiness. Preserve explicit attended sign-in/unlock prerequisites, existing
  keys and host trust, user-logon versus service lifetime, standalone use and
  generic shared-owner tunnel/reconnect machinery; provider provisioning remains
  behind its adapter. Do not assume a system agent or require a private executable
  onboarding provider, second supervisor or implicit activation.
- [ ] **F6030-P2.** Retain v1 token/proof, v2 external PEM and explicitly selected
  v3 provider-owned continuity references through the existing finite ServiceCaller
  and host lifetime. The provider owns protected-context validation, original
  key/operation, requested machine/origin/principal/audience/service scope,
  reference revision and selected CA comparison. Separate credential/reference
  scopes from lease/writer ownership before key reads. Unsupported capability,
  mixed forms or unsafe state refuses before effects; no private JWK parsing or
  export, alternate issuer, PEM export, reenrollment, identity rotation or change
  to enrollment/storage, SDK proof semantics or existing local writers.
- [ ] **F6030-P3.** Complete the explicit selected Source/profile application and
  SDK closure through the existing constructor and actual noneditable normal
  Manager/selected-engine/Fleet SSH front door. Defaults remain inert; missing or
  wrong selected sources/profile/capabilities refuse before identity or lifecycle
  effects. Incoming readiness AND all applicable current selections are
  authoritative: old-state provenance/recovery, missing predecessor proofs,
  predecessor liveness or cleanup cannot veto them. Preserve current release
  authentication/admission, the monotonic admitted floor, existing good state and
  strict exact old-object deletion custody independently. No alternate installer,
  test preinstall or development-support surrogate qualifies this lane; the whole
  broader constructor PR is not its prerequisite.

##### Normal first-touch and installed SSH local validation

- [ ] **F6030-LV1.** Qualify the selected installed first-touch prerequisites and
  explicit attended boundaries, key/trust preservation, configured versus ready
  states and interactive/service lifetime through the original owners and native
  tiers. Capability enablement or database discovery is not readiness.
- [ ] **F6030-LV2.** Exercise v1/v2/v3 compatibility, protected provider-reference
  revision/CA and scope checks, unsupported/mixed forms, original-operation
  retry/UNKNOWN reconciliation and secret-free refusal without credential export,
  issuer replacement or alternate fallback.
- [ ] **F6030-LV3.** Exercise the actual noneditable normal Manager-to-selected-engine
  Fleet SSH consumer with its selected application/SDK closure: source/payload
  denial, inert default, first touch, failed-candidate preservation, repeat/update,
  retained identity/operation, exact readiness and stop/cancellation. Prove healthy
  incoming selection without predecessor-state veto while retaining current
  authentication/admission and separate physical cleanup custody. Record native,
  feed/source-independent installed, release and adopter tiers separately.

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

### Direct retained named deployment and configuration reconciliation

Retain the complete finite #5861 follow-on directly under this #6030 effort.
This is generic named deployment AND basic configuration reconciliation, not
only runner loadouts or the Komodo adapter. #5789 remains the deliberately
narrower discovery/health/search and fixed-service routing foundation; this
amendment does not put deployment or configuration effects into its protocol.

The following is an agent-recommended tracking amendment. It appoints no
implementer, effect authority, source writer, resource owner or new coordinator.
The admitted #5861 metadata records no current assignee/source claim; absence
of a claim is not an execution appointment. Existing checked items and source
credit remain unchanged.

#### Plan

- [ ] Resolve only explicitly authorized, typed named deployment and basic
  configuration operations from trusted local declarations through the
  existing installer/update/configuration and actual service authorities.
  Refuse unsupported or untrusted declarations. No arbitrary shell, executable,
  URL or remote-authored script/configuration payload may be hidden in a name,
  broadcast, connector request or configuration operation.
- [ ] Bind each request to its explicitly selected recipients, declared
  availability roles and actual current operation/target authority. Preserve
  provider identity/admission, destructive-action gates, machine-local
  deployment boundaries, operator settings and existing direct/SSH operation.
  Routing or an available connection alone supplies none of those authorities.
- [ ] Obtain and retain each selected recipient's own acceptance and outcome,
  with attributable operation/target correlation. Transport acknowledgement is
  not downstream acceptance, installation, configuration application or
  completion. Report unsupported, refused, stale and mixed recipient outcomes
  independently; do not infer whole-fleet convergence from one success.
- [ ] Keep offline, disconnected and otherwise unobserved recipient outcomes
  unresolved/UNKNOWN as appropriate. Reconcile lost responses and interrupted
  communication against the same original operation and its actual effect
  owner; uncertainty never authorizes blind replay, duplicate side effects or
  a successful completion claim.
- [ ] Compose named operations with the existing package, configuration,
  lifecycle and service owners. The Gateway is not a second package manager,
  configuration authority or service supervisor. Preserve supported direct/SSH
  paths and last valid operator state; do not require fleet adoption merely to
  keep existing direct service operation usable.
- [ ] Qualify the complete finite contract below using synthetic refusal and
  uncertainty controls PLUS real controlled target/process boundaries and
  separately governed actual installer/configuration outcomes. A routing,
  planner, runner-only, Komodo-only or receipt-shape result cannot substitute
  for the applicable named deployment/configuration effect-owner evidence.
- [ ] Record exact evidence, remaining unsupported cases and existing owners
  for this finite acceptance. Live rollout remains a separately authorized
  operation after qualification. No adopter enrollment, migration, capacity
  acquisition, implementation or live operation is authorized by this tracking
  amendment.

#### Local Validation

- [ ] Prove authorized typed names resolve only trusted local declarations and
  the intended existing installer/update/configuration authority. Denied,
  unsupported and stale declarations fail explicitly, including attempts to
  smuggle shell, executable, URL or remote-authored script/configuration data.
- [ ] Prove exact recipient selection: no unselected recipient receives an
  effect request; each selected recipient's declared availability and role/auth
  scope are honored. Admission and destructive refusals preserve operator
  settings, local deployment boundaries and unrelated targets/state.
- [ ] Prove per-recipient acceptance and actual outcome remain separate from
  transport acknowledgement and from each other. Mixed success/refusal/
  unsupported/stale outcomes remain complete and attributable, without
  inventing global success or silently dropping a selected target.
- [ ] Prove offline/disconnected targets and lost responses retain unresolved
  or UNKNOWN outcomes. Reconnection and same-original reconciliation through
  the actual owner must not duplicate effects, relabel uncertainty as success
  or silently dispatch a replacement operation.
- [ ] Exercise real controlled target and process boundaries, including
  interruption/outage and lost-response cases, rather than only callbacks,
  serialized receipts or mocked acceptance. Preserve accepted-work custody
  through the existing effect owner's supported settlement/recovery.
- [ ] Prove actual named installer/update and basic configuration outcomes
  through their separately governed owners, including supported failure/
  recovery and preservation of existing operator state. Evidence distinguishes
  requested, accepted, installed/applied and observed outcome; no single
  adapter's success qualifies all generic operations or recipient roles.
- [ ] Prove existing direct/SSH operation remains usable without the Gateway
  becoming a package manager, configuration owner or service supervisor.
  Provider identity/admission and destructive-action requirements remain
  effective; qualification does not itself enroll or migrate any adopter.
- [ ] Record the exact source/release/native/effect evidence tier and retained
  unsupported gates. Actual controlled qualification precedes any separately
  authorized live rollout; source merge or a released artifact alone is not
  installed, running or live acceptance.

#### Continuing Coordination

This subsection receives tracking under #6030 without changing its Participants,
existing source reservations, checked credit, resource custody or activation
claims. #5789 retains fixed-service routing. Existing installer/update/config,
provider/admission, credentials, machine-local deployment and service lifecycle
owners retain their actual effects and source leaves. Completed release-assembly
source is credited as completed, not reopened or reassigned; operational
publication and proof-method interoperability remain independently governed.

Coordinate a concrete overlapping source/interface or resource transition with
its existing owner. Broad parent custody, age, a generic Taking notice or an ACK
is not a new signoff requirement, veto or custody transfer. This finite #5861
acceptance does not require completion of every unrelated #6030 child or other
parent task, and no unrelated task can silently discharge its residual duties.

Keep #5861 open unless and until the original campaign owner confirms actual
normal-reviewed delivery of this complete direct intake at the real receiving
revision, whole-current receiving Plan/Validation/Coordination and applicable
history, and current original-contract/source/custody qualification. Reconcile
any current incumbent source appointment without taking or retiring it.
Only then may the authorized owner decide administrative tracking supersession
into #6030 while retaining every unresolved acceptance obligation and original
evidence. Draft text, a proposed link, a partial merge, theme overlap or this
subsection's presence alone is insufficient. Otherwise KEEP #5861.
Administrative consolidation, if separately decided, is not implementation
completion, resource release, runtime activation or permission for live rollout.

### Direct retained Komodo substrate acceptance

- [ ] Directly retain and resolve every original/refined/inherited #8860 Plan
  duty in [the canonical substrate contract](komodo-substrate-lifecycle.md#plan):
  versioned adopter-owned selected references; independent actual first
  installation and Core-unavailable recovery; preserving adoption and unchanged
  repeat; health-gated update/failed candidate/data-compatible recovery; exact
  native identities and UNKNOWN operation/resource responsibility. Preserve
  delivered SDK/profile credit and every existing source appointment. This is
  an **agent-recommended tracking amendment**, not a new runtime assignment.

## Validation Plan

- [ ] Satisfy the [direct cohort Validation](cohort-receiving.md#direct-validation)
  under actual installed/native/provider owners. Planning receipt or tracking
  retirement is not implementation, live adoption, release or resource proof.

All entries are **agent-recommended implementation gates**.

- [ ] Loadout acceptance follows the [runner matrix](runner-loadouts.md#validation):
  resource fit/refusal, template/registrar preservation, published-source pull,
  exact confirmation, complete support readiness and bounded partial/unknown
  outcomes through actual owner adapters. No secret, routing, new-host or
  live-pool action is inferred from a plan or this source amendment.
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
- [ ] Logging inventory and convention discovery cover all selected roles and
  every native/structured output category without terminal-output dependence.
- [ ] Synthetic clock and real writer tests prove the 30-day boundary,
  continuous rollover, idle/startup cleanup, concurrent append safety and
  protected incomplete-attempt handling; size/count-only rotation is not proof.
- [ ] Direct retained #5861 named deployment AND basic configuration acceptance:
  prove authorized typed names from trusted local declarations with no hidden
  shell/executable/URL/remote-authored payload; exact selected-recipient,
  declared-availability and role/auth scope; each recipient's own acceptance
  and actual outcome, not transport acknowledgement; denied/unsupported/stale/
  mixed outcomes and offline/disconnected/UNKNOWN/lost-response reconciliation
  without duplicate effects. Use synthetic controls PLUS real controlled
  target/process boundaries and actual separately governed installer/config
  outcomes. Preserve provider identity/admission/destructive gates, local
  deployment/operator settings, existing direct/SSH operation and existing
  package/config/service authority. This generic gate is not runner-only or
  Komodo-only, adds no unrelated parent prerequisite and precedes separately
  authorized rollout; tracking/source delivery is not live qualification.
- [ ] Pure contracts: explicit provider/adapter selection, fixed named operations,
  untrusted/ambiguous input refusal and no raw remote script/URL/executable API.
- [ ] Reproducer acceptance follows its [isolation and fault matrix](persistent-isolated-reproducer.md#validation-plan):
  actual exclusive persistent volumes, monitored usage/free-space refusal,
  no production reach, literal unknown
  outcomes and independently retained evidence. Fake peers do not qualify native
  CLI, actual task START, review completion or released/running adoption.
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
- [ ] Directly retain the COMPLETE matching [substrate Validation Plan](komodo-substrate-lifecycle.md#validation-plan)
  for #8860 at its original evidence tier: actual selected official images and
  authenticated Core/Periphery readiness, preserving existing identities/
  credentials/database/volumes/workloads, unchanged rerun, compatible update/
  candidate failure and independent recovery without a working Core. Source
  fixtures, a count, SDK construction or old code slots alone do not prove
  these native/adopter outcomes.

- [ ] **F6030-G.** Resolve the normal first-touch, credential-reference and
  noneditable installed-frontdoor obligations above at their original selected
  consumer/native tiers, including explicit attended prerequisites, keys/trust,
  v1/v2/v3 refusal/UNKNOWN compatibility, authoritative incoming readiness AND all
  applicable current selections, preserved good state and exact stop. Existing
  lifecycle/provider/source owners and strict physical removal custody remain;
  bounded source/native prerequisites are not containing release, installed
  rollout, provider/adopter, mesh, migration or retirement acceptance.

- [ ] **R6464-G.** Resolve the entire R6464-P1-P10/R6464-LV1-LV10 conductor
  union at its original finite tiers: default nonmutation and qualified ordinary/
  active-core admission; supported root-free ownership; complete selected/required
  closure; exact source/digest/action and selected-project probe context;
  independent readiness/dependency/restart/partial outcomes; bounded original-owner
  process custody; machine-policy/stale-serving distinctions; each originally
  supported native first-touch/repeat lane and positive declared-action fixture;
  then actual consuming setup. Preserve credited source/native partials, failed
  fresh-environment attempts, current contributors and the strict annex's OPEN
  condition. No unrelated fleet/adopter programme or universal host gate is added;
  planning, source delivery and separately authorized operational proof remain
  distinct.

## Proposal

[Safe fleet setup and refresh](fleet-setup-reconciliation.md).
[Standalone fleet-manager system](fleet-manager-system.md).
[Substrate evaluation and proposed adapter boundary](substrate-evaluation.md).
Runtime package/API details belong to the implementation contract after review,
not to the vision. This effort does not acquire or migrate live capacity.

## Journal

### 2026-11-13 - Finite installation-repair continuation proposed

The complete #6464 original and current discussion retain the existing default
no-write/explicit qualified apply contract, ordinary-versus-core admission,
source/digest and action fences, independent post-readiness and original serial
native/consumer gates. Reported #6944 source and Windows/WSL progress stay
credited at their recorded tiers; later fresh-environment TLS failure and
pending active-core/native/consuming setup remain unresolved. The root-free
ordinary-owner report, required-module census reservation, machine-policy/
stale-serving distinction and selected-project read-only probe correction are
now directly represented in Plan, paired local Validation and actual global
Validation rather than only an annex/topic pointer.

All twenty-one new technical/validation rows remain unchecked. Every incumbent
row, check state, first unchecked item, Status, strict-annex condition and source/
resource custodian remains unchanged. This is an agent-recommended unfinished
tracking amendment, not implementation, acceptance, resource transfer, execution
appointment or issue disposition. In particular #6464 remains OPEN under the
annex's actual implementation-and-real-consumer-proof condition; publication
alone cannot silently replace it with an administrative-fold condition.

### 2026-11-13 - Finite normal first-touch and installed SSH intake

The existing #6030 refinements through comment6702057494 retain normal
first-touch setup, provider-owned v1/v2/v3 credential-reference composition and
the actual noneditable Manager/selected-engine/Fleet SSH consumer. The additive
Plan, local Validation and actual global Validation rows preserve the complete
existing fleet/cohort contract, checked credit, incoming-first selection policy
and current owners; no incumbent row or status changes.

Bounded #10929/#10954/#11009/#11023/#11041/#11204/#11227 source and prerequisite
credit remains credited, not reopened as missing implementation. Those results
do not establish the containing release, installed rollout, provider/adopter,
mesh, migration or retirement. The older development-supported witness is not
the normal source-independent product cohort, and completion of the whole
broader constructor PR is not this independent consumer's prerequisite.

This is planned unfinished work, not an execution appointment, live
qualification, accepted resource transfer or issue retirement. Existing #7192
named-owner transfer and #5861-specific receiving conditions remain scoped to
their actual contracts.

### 2026-11-09 UTC - Provider-effect review fixes under the existing source gate

The resumed bounded outcome remains source/provider-effect enforcement under
#7192/#6030/#6236, followed by separately authorized adoption. The next observable
completion is #11458's reviewed, CI-qualified merge, not a rollout. Its current
review found two concrete blockers: inspecting-client skew could invalidate an
effect's task prefix, and whole-second rounding could admit an effect after its
actual safe deadline. The source fixes freeze each effect's own predecessor
prefix while preserving subsequent task policy, and compare the precise clock
before encoding. Focused cross-margin recovery and fractional-boundary
qualification is in progress. The return path is the same canonical resource
owner and existing Gateway/provider owners; no new workstream is introduced.
Live adoption, rollout, credentials, controller changes and temporary-obligation
discharge remain separately gated. No Plan or Validation checkbox closes.

### 2026-11-08 UTC - Canonical provider-effect source prepared

The [custody checkpoint](runner-service-custody.md#2026-11-08-utc---canonical-in-flight-effect-source-prepared)
records the same-slot in-flight extension and 128 distinct selected passing
controls, including real separate-process effect/adoption races. Unsettled
native responsibility cannot expire into borrowing or adoption; explicit
original-owner settlement preserves the original task generation and retained
receipt. The failed initial worker fixture and passing corrected selections
are recorded separately.

This remains source preparation, not provider integration or adoption. The
local admission authority and node journal are not a distributed resource
fence. Actual provider enforcement, current/prior installed compatibility,
Gateway/Fleet and incumbent-preserving ARC binding, fresh live authorization
and verified obligation discharge remain open. No broad Plan or Validation
item closes, and no controller, resource, credential, capacity or routing
change was made.

### 2026-11-08 UTC - Permanent custody receiver source accepted

#11267 merged at `72aae10da1dd53ff06eb1f3d797152aeeba441e6` after
current-head Copilot approval, resolution of both findings and required green
CI, including the PR gate. The receiver recognizes strict permanent custody
in the existing resource CAS slot, freezes its historical admission margin,
refuses task mutations and preserves mixed history during task compaction.
No adoption or retirement writer is enabled.

The [custody qualification checkpoint](runner-service-custody.md#local-implementation-checkpoint)
records all 126 passing individual outcomes, separate native Git/process
partitions, actual return-proof GC and retained failed aggregate attempts.
Recorded margins prevent inspecting-client policy from invalidating permanent
history; ordinary task admission and newly added return-evidence checks remain
unchanged. The documentation correction keeps current invariants separate from
review discussion.

The full Request, Plan and Validation Plan were reread. No broad checkbox or
resource obligation closes. The next stretch integrates actual provider
admission and competing-effect settlement with the existing canonical owner,
then binds the existing Gateway/Fleet and incumbent-preserving ARC owners.
Installed-peer compatibility, native effect races, controller-independent
protection and live adoption remain open. Eventual live acceptance requires
fresh valid ownership admission; source merge is not deployment or discharge.

### 2026-11-04 - Managed legacy client integration accepted

#10509 merged at `be98510a8f4e98b4377de29f756111a05f89027f` after both native
platforms passed the original six plus seven managed installed consumers,
the valid retry-documentation finding was fixed, and the final owner-PR verdict
and latest required CI passed. The
[existing incoming-selection journal](journal/2026/11.04-client-incoming-selection.md#managed-legacy-client-integration-accepted)
retains the exact revisions, carried native evidence, failed local attempts
and accepted limits. The whole Plan and Validation Plan were reread; no broad
checkbox, release promotion, installed rollout or existing owner is discharged.

### 2026-11-04 - Explicit managed client composition accepted

#10496 merged at `f6734831513d256475b64c2a41bac16ae0560d12` after exact-head
native Linux/Windows qualification, a clean owner-PR Copilot verdict and required
CI. The [existing incoming-selection journal](journal/2026/11.04-client-incoming-selection.md#explicit-managed-client-composition-accepted)
records the actual twelve-consumer per-host result, preserved timing failures,
boundaries and remaining source integration. No broader Plan/Validation checkbox,
release promotion, installed rollout or existing owner's duty is closed.

### 2026-11-04 - Native client generation consumers accepted

#10469 merged at `b01544dc89c9532273ddaf401b192851da532ce1` after its exact
head passed all six installed consumers on both native Linux and Windows,
the strict report oracle, a clean Copilot Comment on the owner's PR and current
required CI. The [qualification journal](journal/2026/11.04-client-incoming-selection.md#native-consumer-source-accepted)
records the Windows ownership/journal corrections, private-directory refusal,
evidence limits and accepted review. The Plan and Validation Plan were reread:
managed lifecycle composition, the broader matrix, fresh-OS and adopter gates
remain open under their existing owners. No broad checkbox or rollout closes.

### 2026-11-04 - Incoming client generation source accepted

#10439 merged at `b83b688711334b62e2b9b9fd7a4c0c57f0ceb323` after exact-head
review, all required CI and isolated Linux/Windows artifact installation passed.
The [source qualification journal](journal/2026/11.04-client-incoming-selection.md)
retains the native Windows consumer evidence and remaining native Linux,
managed-installer, clean-room, release and adopter gates. No broad Plan or
Validation checkbox closes, and no deployment follows from this merge.

### 2026-11-04 - Direct retained named reconciliation tracking

This amendment places the complete finite #5861 named deployment and basic
configuration contract directly in this effort's Plan, local Validation and
Continuing Coordination, with a matching actual global Validation row.
It preserves typed trusted-local names, no hidden remote payload, exact selected
recipients and declared availability, per-recipient acceptance/outcome,
offline/UNKNOWN/lost-response responsibility without duplicate effects, and
existing direct/SSH, provider/destructive/local-settings boundaries. Synthetic
controls do not replace real controlled target/process and actual installer/
configuration evidence before separately authorized rollout.

This is tracking intake, not implementation or an appointment. Existing #6030
Participants, checked credit, source/resource/native custody, activation claims
and narrower #5789 routing remain unchanged. Completed release-assembly source
is not reopened; operational publication/proof interoperability remains separate.
No whole-current source/custody qualification, reviewed merge receipt, live
operation or #5861 closure is asserted by this entry.

The original campaign owner must confirm actual normal-reviewed direct delivery,
whole-current receiving readback and current original-contract/source/custody
qualification before deciding any administrative tracking supersession. Until
then KEEP #5861. All finite implementation and validation residuals remain open
under their existing authorities; unrelated parent tasks are not new blockers.

### 2026-11-04 - Client authoritative replacement acceptance reviewed

#10415 merged the existing client-lifecycle plan's authoritative replacement
reconciliation at `7275048aab45fd77ceaa09569e5a4df93c4d2d4f`, after actual
current-head approval and required green checks. GitHub publication and merge
server errors were reconciled before bounded retries; neither a failed response
nor an approval was credited as merge evidence.

The retained forward-generation candidate remains preserved and unpublished.
Its construction isolation and physical publication evidence are useful, but
historical receipt recovery cannot veto complete incoming invocation selection.
The next client-owned integration reuses the common publication owner, preserves
strict old-object deletion custody and proves the real default-command consumer
alongside incoming health. No broad Plan or Validation item closes from this
reviewed intent. Controller, connector, substrate, credentials, actual adopter
qualification and authorized rollout remain with their existing owners.

### 2026-11-01 UTC - Keyed attribution source plan accepted

#8795 merged the [keyed attribution continuation](telemetry-attribution.md)
after current-head review, resolution of all three findings and required green
CI. It preserves truthful producer/subject/collector identity, explicit
mixed-version writer support, and compatible final-storage privacy/retention
gates. The sub-plan records exact source acceptance. This parent remains
Active: implementation admission, #8377, existing installer/formatter owners
and all runtime, shared-export and privacy acceptance remain open.

### 2026-10-31 - Released SDK and embedding composition qualified locally

See the [authenticated release, existing-owner integration and remaining actual adopter gates](komodo-repair-checkpoint-authorization.md#2026-10-31---released-sdk-and-embedding-composition-qualified-locally).

### 2026-10-31 - Installed checkpoint SDK compatibility merged

See the [real noneditable compatibility boundary and remaining release/adopter gates](komodo-repair-checkpoint-authorization.md#2026-10-31---installed-checkpoint-compatibility-merged).

### 2026-10-31 - Checkpoint authorization proposal reviewed

See the [reviewed opt-in proposal and remaining implementation gates](komodo-repair-checkpoint-authorization.md#2026-10-31---checkpoint-authorization-proposal-reviewed).

### 2026-10-31 - Original-attempt repair SDK source merged

See the [source qualification and remaining release/adopter gates](komodo-substrate-lifecycle.md#2026-10-31---original-attempt-repair-sdk-source-merged).

### 2026-10-28 through 2026-10-31 - Retained source and acceptance boundaries

See the [preserved source history and unresolved acceptance boundaries](journal/2026/10.28-retained-boundaries.md).

- [2026-11-04 - Incoming client selection qualification](journal/2026/11.04-client-incoming-selection.md)
- [2026-10-23 - Forward client generations](journal/2026/10.23-forward-generations.md)
- [2026-10-22 through 2026-10-23 - Client retained recovery](journal/2026/10.22-client-recovery.md)
- #8931 merged the permanent service-custody protocol proposal after actual
  current-head Copilot approval, complete conversation inspection and required
  source checks. The managed provider confirmed merge using the authorized
  exception to an additional code-owner vote, without a CI bypass.
  Re-read this full Request, Plan and Validation Plan and the custody sub-plan.
  The next stretch implements distinct canonical custody records and provider
  admission interlocks in the existing resource owner, then binds the existing
  Gateway and incumbent-preserving ARC owner. No permanent record or live
  transition exists yet; all provider, installed, native and adoption acceptance
  remains open. Temporary protection and the parent's active responsibility
  are retained. Controller deployment, credentials, capacity and routing did
  not change.
- [2026-10-22 - Client lifecycle](journal/2026/10.22-client-lifecycle.md)
- [2026-10-10 through 2026-10-21 - Foundation and client admission](journal/2026/10.10-foundation.md)
