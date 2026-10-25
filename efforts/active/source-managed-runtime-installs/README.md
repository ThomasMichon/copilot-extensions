# Source-Managed Runtime Installs

- **Slug:** `source-managed-runtime-installs`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-slice PRs against `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** [Plugin Service Model](../../../visions/plugin-services/README.md#managed-runtime-deployment), [Runtime Assembly](../../../visions/plugin-services/runtime-assembly/README.md#selected-development-source-assembly), [Installer](../../../visions/installer/README.md#independent-runtime-source-selection), [Clean-room Validation](../../../visions/clean-room-validation/README.md#independent-source-installation)
- **Umbrella issue:** #7028
- **Sub-issues:** #3905 (existing source-materialization gap), #7029 (source selection and immutable generations), #7030 (deployment boundary and adapters), #7758 (durable installation execution driver)
- **Future objective:** #7031 (exclusive local plugin-content sourcing; outside this runtime-only campaign)

## Guiding Intent

Separate Copilot plugin delivery from service-runtime deployment without
disabling ordinary hook behavior. An opted-in installation can deploy clean,
merged development source directly, retain a stable release or exact pin, and
iterate through immutable generations without waiting for marketplace payload
updates or rewriting a live environment.

Prove the source install on a disposable container before attempting a native
Windows runtime-only pilot and completing the adopter's installed service/tool
transition. Keep the dedicated source healthy and current through the existing
maintenance owner and canonical install flow. Ordinary plugin self-provisioning
remains the default; managed deployment is a local choice, not adoption of
another product or a change to service-supervision ownership.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation coordinator | Owns the plan, integration, public PRs and completion gate | Managed source worktree |
| Manager maintenance implementer | Owns the bounded background-maintenance enrollment, lifecycle and ownership-handover slice | Managed source worktree; public coordination on #7028 |
| Installation execution implementer | Owns durable suite-update requests and exclusive transaction execution, separately from recurring enrollment | Managed source worktree; public coordination on #7758 |
| Linux clean-room runner | Reproduces and verifies actual source installation and activation | Existing Docker clean-room rig; WSL is a supported host |
| Windows validation runner | Verifies native installer, resolver and non-deployment hook behavior | Isolated Windows fixtures, then an explicitly authorized runtime-only pilot |
| Independent reviewer | Reviews each coherent proposal or implementation change | Repository PR review |

## Coordination

The complete #4015 family below continues through this existing Active ROOT's Plan,
actual global Validation and Journal under the SAME declared implementation, source,
adopter and native custodians. Closed source trackers are historical provenance, not
required live claim tokens. Every incumbent scope, alternative, hold and resource
obligation remains; this intake grants no implementation, deployment, process action or
new blanket approval gate.

The complete #4010 retained finite family continues through this existing Active ROOT's
reviewed Plan, actual global Validation and Journal/open bounded-source process under
the SAME declared and source-specific custodians. Preserve every incumbent alternative,
native/source/resource reservation and independent acceptance gate. Closed source
trackers are historical provenance, never required live claim tokens; no assignment,
claim expiry or new blanket ACK is created.

The direct #2822 installed-output/LKG family continues under the same #5472/#7028/#7029
source-managed, adopter, native, and deployment owners. Candidate-health and
retained-good-generation qualification remain distinct from best-effort handoff and
separately authorized rollout.

- **Topology:** serial, coherent PRs against `dev`; this plan clears review before implementation.
- **Host (owns PRs):** Implementation coordinator.
- **Delegates:** bounded evidence, implementation or validation scopes only; no shared mutable git ownership.
- **Handoff:** the next owner reads this README and the current slice evidence before continuing. A merged PR resolves only its actual Plan and Validation Plan items.
- **Existing owners:** preserve #3654 (vendor pointers), #5472 (immutability), #4876 (artifact admission), #5851 (source isolation), and #6812/#6820 (update outcomes/sequencing). Reuse their mechanisms; do not fork their campaigns.

## Context

Manager has independent runtime-source policy and owned acquisition diagnostics,
separate from self-update configuration, plus a shipped trusted canonical toolkit
that prepares plugin and standalone Manager payloads. The normal source-backed
suite caller now qualifies the exact executing engine, acquires one source
commit/tree and prepares its runtime portfolio before either update stage.
Fresh legacy suite repair now retains that same independent service selection
through native engine recovery and subsequent suite preparation. Targeted and
non-repair bootstrap service-source paths still refuse rather than silently
deploying marketplace code. Plugin bootstrap paths still need complete
publication-boundary qualification.
The collected updater now preserves Containers' existing dependency profile
through installed-owner target planning, native construction and selected
source-qualified currentness, separately from the original source pin. Its
native same-root witnesses do not qualify the complete eligible portfolio or an
adopter's production data/serving transition.
Worktrees now retains its original global command owner through collected
prepared-source updater execution, native publication and installed source-unavailable
restoration. Those bounded providers do not qualify an adopter's actual
data/service cutover. The engine now
advertises and accepts a complete prepared-source suite packet through its
existing inventory and durable update process boundary; Manager's normal caller
uses that capability without coupling its independent self-source or replacing
the existing driver. Immutable Manager recovery and actual adoption remain open.

Promotion composes versions and expands development vendor pointers into a
self-contained `main` tree. Raw-dev non-editable installs have an existing
tracked gap (#3905). Canonical runtime helpers already provide immutable
completion, activation, retention and claim-owned editable development
generations (legacy interactive behavior). The
[Immutable Dev Slot effort](../immutable-dev-slot/README.md) replaces that
editable target with frozen, non-editable source-hashed generations under
#5472. This campaign remains the canonical deploy-from-Dev home and extends
the same building blocks, not a second installer or mutable environment.

The [source-owned release vision](../../../visions/release-pipeline/README.md)
keeps the verified development commit as the release identity. Generated main
commits, materialized payload digests, package versions, profiles and dependency
locks describe different facts; no new competing artifact authority is needed.

## Request

**Immutable development alignment (2026-10-22), operator verbatim:**

> So, mutable dev slot should probably be superseded by "immutable dev slot", and the "deploy from dev branch" effort. Basically, always deploy using an immutable venv, tagged with the hash of the source. Just set the new dev slot as current and handle cutover normally by giving the incoming version a start command and letting the old version shut off. We should endeavor to support baton-passing both ways; a version knows it's "old" simply because it's "not current", not via any ordering relationship between the active and owner versions.

> Great. Update relevant visions and efforts to align; close out and abandon superseded efforts and off-course work.

**Accepted alignment:** retain this effort's clean merged Dev source, Manager,
maintenance, driver and installed-adoption owners/gates. All deployments use
immutable venvs identified by complete source and admitted build inputs.
Current selection starts the incoming owner through ordinary best-effort handoff;
full identity inequality, not version ordering, identifies the displaced owner.
Reverse baton-passing prefers compatible health-gated drain, but permits bounded
identity-checked old-owner shutdown and cold recovery when handoff contracts
are incompatible or fail. This documentation revision neither
restarts completed Phase 2 work nor claims remaining cutover/adoption proven.

**Best-effort follow-on (2026-10-22), summary:** the operator permits outgoing
daemons to quick-terminate their owned scope, and the selected newcomer to
force-terminate an identity-verified predecessor when handoff cannot complete.
Clients tolerate transient network-like outages/stream cut-offs; child daemons
tolerate power-loss-like interruption and recover retriable durable work.
A breaking development contract must not veto replacement. The
[canonical verbatim capture](../immutable-dev-slot/README.md#request) preserves
the complete operator input. Existing source/maintenance/driver and native
acceptance owners remain; no live shutdown or rollout is authorized here.

**Settled request summary:** add a local gate only around service payload
publication and installed runtime redirect updates; retain all other hook
behavior and ordinary Copilot marketplace delivery. Add an independent managed
runtime source supporting main, stable/exact older pins and merged dev.
Materialize dev dependencies and install immutable, hash-qualified,
non-editable generations from clean merged source, never worktrees or dirty
checkouts. Keep adapters thin and version-skew compatible. Begin with a
clean-room container on WSL, then prove a native Windows runtime-only pilot.

The operator confirmed the slug and explicitly selected **runtime-only** for
the Windows pilot. Local plugin-content sourcing is long-term intent tracked
separately in #7031, with exclusive source replacement rather than shadowing.

**Wholesale same-installation cutover clarification (2026-10-24), summary:**
replace Main/release code with immutable merged-Dev install slots in the
**existing global installation**, for every installed service/tool including
Manager. Existing global binstubs and service registrations select the Dev
generation; that generation uses the **same existing runtime configuration,
databases and durable state** that the Main installation used. This is not a
custom-root/cell deployment, a parallel installation or a data-copy migration.
Installation-cell/custom-root capability is separate work, not a prerequisite
for this wholesale transition. The [verbatim clarification](inception-transcript.md#wholesale-same-installation-cutover)
preserves all three operator statements. Exact native change/rollback consent,
candidate health and data-format compatibility remain required.

**Execution follow-through summary:** the requested outcome covers all installed
services and tools, including their management tools, rather than ending at a
successful canary. A regular flow must keep the dedicated merged-dev source
healthy and updated and rerun canonical installation at a bounded interval,
using the selected maintenance owner. The
[maintenance slice](maintenance.md) proposes the portable integration and
validation; it must clear review before implementation.

**Maintenance ownership revision (2026-10-20):** automatic suite installation
remains off by default, but an explicitly run trusted consuming-harness setup
may opt in for the user or local installation. Manager owns that background
capability. Windows enrollment uses current-user Autorun for a headless
maintenance process rather than requiring Task Scheduler registration. A
harness's approved setup can enable maintenance after installing Manager.
An external update controller remains an exclusive alternative. Agent Machines
retains general machine/dependency convergence rather than another concurrent
suite-update loop. The operator selected **implement-and-adopt**, following
review of this ownership amendment; source-backed installation prerequisites
and adopter-specific change gates remain intact.

The [inception record](inception-transcript.md) retains the operator's words,
with private host identifiers redacted. Implementation choices below marked
**agent-recommended** are not additional operator requirements.

**First-installation decision (2026-10-22):** the operator selected
`driver-foundation`: "Cover first installation too: establish a standalone
driver foundation before plugin provisioning". Python-free standalone genesis
is not exempted from the execution-owner objective. The foundation must remain
independent of Manager and other plugins. The detailed packaging, bootstrap
admission and adapter sequence below are **agent-recommended implementation
work**, not additional operator-selected mechanisms.

**Execution-owner clarification (2026-10-22):** the operator requires one
background installation driver, not merely independent per-installer locks.
Ordinary callers submit durable requests; waiting observes the operation and
does not become its installer. Direct execution is reserved for supervised
debugging under the same exclusive authority. The operator directed:
"Do what you need to do to get unblocked that doesn't involve waiting on
someone else's PR". The [execution-driver slice](execution-driver.md) therefore
proceeds against the existing canonical updater, independently of open staging,
watchdog and recurring-maintenance changes, without superseding their owners.

**Repair evidence clarification (2026-10-23), summary:** audited, request-bound
pre-launch recovery is allowed, but an explicit operator order to repair,
clean-install or force-reinstall does not require historical cleanup evidence.
Operator authorization is distinct from native-confirmed settlement. Preserve
the actual prior failure, addressed installation scope, one active writer and
unrelated services/data; do not require the broken owner to approve recovery.
The [request record](inception-transcript.md#explicit-repair-authorization-and-legacy-pre-launch-audit)
retains the exact words. This approves source implementation, not a concrete
repair run on a host.

## Plan

### Phase 0 - Review the intent and plan
- [x] Land the vision amendments and this canonical proposal through repository review.
- [x] Confirm issue/slice ownership and preserve the existing dependency campaigns.

### Phase 1 - Reproduce direct-dev installation in the clean room
- [x] Extend the existing Tier-P rig with a credential-free, source-install scenario using a recorded clean merged dev commit.
- [x] Reproduce #3905 through an actual non-editable installer path and record the first classified failure.
- [x] Establish observable source identity, runtime generation, installed command and source-checkout isolation assertions.

### Phase 2 - Assemble immutable merged-source generations
- [x] Reuse canonical source materialization for the complete declared vendor closure without modifying the input checkout.
- [x] Add source-qualified non-editable generation identity while preserving package versions, profiles, dependency policy and completion evidence.
- [x] Make installer, runtime resolver and installed command redirects agree on the exact selected generation on Linux and Windows.
- [x] Prove identical reuse, distinct same-package-version commits, source removal, interrupted build and failed-candidate preservation.

### Phase 3 - Separate plugin-triggered service deployment
The [deployment-policy slice](deployment-policy.md) maps the existing trigger,
publication and command-ownership boundaries; it does not disable whole hooks.

- [ ] Add a local managed-deployment policy at the shared publication boundary, retaining standalone defaults and installation scope.
- [x] Land and qualify the shared installation-owned decision on Python,
  no-Python POSIX and PowerShell 5.1 before wiring every publication caller.
- [x] Gate each plugin's reconciliation plan and in-process execution without suppressing marketplace refresh or independent targets.
- [x] Qualify shared legacy lifecycle publication admission and explicit management purpose on the existing source-qualified lease.
- [ ] Cover session bootstrap and indirect first-use publication without disabling other hook or extension behavior.
- [x] Qualify Logger's direct native installer admission before staging, preserving standalone stamping and canonical management custody.
- [x] Gate the common CodeSpaces/Logger/Pull Requests/Vault bootstrap family's first stamping and drift reconciliation before lock/reaping/dispatch, retaining independent hook behavior and healthy dispatch.
- [ ] Gate generated first-use wrappers before boot trace, provision status and orchestration-lock writes, preserving admitted provisioning and healthy runtime invocation.
- [x] Qualify Logger's generated primary, payload-local and auxiliary first-use commands, selected-cell policy, deferred tracing and explicit helper-skew readiness before closing the broader wrapper item.
- [x] Qualify the shared Pull Requests/SSH/Vault generated-wrapper owner and Vault payload gates with actual adopter no-write refusal, standalone provisioning, healthy dispatch and failure-safe snapshot selection.
- [x] Qualify CodeSpaces' bespoke generated-wrapper and payload gates against the selected installation without treating context validation alone as deployment admission.
- [x] Qualify Worktrees' native bootstrap and payload-local early first-use
  publication gates, retaining standalone reconciliation and healthy dispatch.
- [x] Qualify Worktrees' cell-installer pre-staging and retained-backend
  publication gates using existing governance and lock custody.
- [x] Authenticate Worktrees' explicit managed-cell installation through the existing
  genesis/install custody across native staging, candidate handover and callbacks.
- [x] Gate Worktrees' pre-launch and staged runtime-update planning without suppressing marketplace refreshes, healthy reuse or unrelated repository bootstrap actions.
- [x] Revalidate cached launcher plans immediately before native execution, including older standalone compatibility and policy changes after planning.
- [ ] Complete the remaining adopter hook/extension publication map and qualify their retained non-deployment behavior and explicit readiness outcomes.
- [x] **Agent-recommended decomposition:** qualify remaining Bridge/Dispatch/Containers/MCP/Machines/Budget Guidance bootstrap first-stamp and drift paths with actual no-write policy refusal, standalone positives and retained independent hooks.
- [ ] **Agent-recommended decomposition:** qualify remaining payload/global first-use command routes separately from bootstrap, distinguishing selected-context validity from publication permission and preserving healthy dispatch and explicit helper-skew outcomes.
- [x] **Agent-recommended bounded slice:** qualify Bridge command-side first use before provision trace/status/root/lock writes, with actual standalone reachability, healthy managed invocation, helper skew and independently selected installation scope.
- [x] **Agent-recommended bounded slice:** qualify Dispatch command-side first use through the canonical required-context dispatcher contract, preserving legacy defaults for unrelated version-1 manifests and actual selected-cell scope.
- [x] **Agent-recommended bounded slice:** qualify Containers payload/global first-use publication before state writes, exact canonical root and retained-cell binding, fresh-provision publisher binding, healthy dispatch and cross-platform lock custody under #7030; do not infer native adoption or installation-driver migration.
- [x] Deferred to `#7030`: **Agent-recommended capability follow-up:** qualify Dispatch's native scoped staging/service publication before replacing explicit cold-cell/custom-root command readiness with provisioning. This separate installation-cell/custom-root capability is outside the wholesale same-global-installation cutover and is not its readiness gate; its explicit unsupported outcome remains intact.
- [x] **Agent-recommended bounded scoped-publication slice:** preserve Dispatch's existing global primary/board commands, or their absence, during actual custom-root/cell stamping while retaining scoped helpers and healthy selected/ordinary-shell invocation; do not infer native scoped staging or service readiness.
- [x] **Agent-recommended review follow-up:** restore and qualify serialized no-flock stale-lock recovery in Bridge's retained command gate under #7030, separately from its merged publication-admission slice.
- [x] **Agent-recommended retained-binding follow-up:** qualify Bridge's actual scoped stamp preserving existing global commands byte-for-byte or leaving them absent, plus ordinary-shell active-cell dispatch and publisher/pointer-loss/deactivation controls under #7030; do not assume its scoped installer emits cell-bound globals.
- [x] **Agent-recommended cross-platform contention follow-up:** qualify Dispatch's PowerShell/POSIX provisioning-lock contention and healthy-owner reuse under #7030; preserve explicit refusal of unrelated I/O failures and the separate scoped-publication capability gate.
- [ ] Keep adapters thin and enforce drift-compatible service interaction without implicit runtime replacement.

### Phase 4 - Integrate Manager-owned runtime source selection
- [ ] Replace Main/release code through the existing global installation roots, binstubs and service registrations with immutable merged-Dev slots, preserving the same existing runtime configuration, databases and durable state; do not introduce custom roots, installation cells or a parallel data store for this cutover.
- [ ] **Agent-recommended alignment qualification:** prove forward/reverse current-generation replacement, including same-package/different-source selection, with preferred compatible handoff and bounded identity-checked cold takeover when contracts break or drain fails; use identity inequality, not version ordering.
- [ ] Maintain a separate installation source with main, stable/exact pin and opt-in merged-dev selection resolved once per run.
- [x] Land Manager's independent default-off source policy and owned exact-commit acquisition diagnostics, refusing unconnected source-backed updates rather than silently installing from marketplace.
- [x] Ship the trusted canonical preparation toolkit inside Manager's own payload and qualify standalone Manager preparation, exact retained assets and fetched-tools-as-data isolation before connecting source-backed execution.
- [x] **Agent-recommended integration prerequisite:** preserve Worktrees' canonical command-binding receipt at Python-owned manifest publication and qualify a separately prepared source artifact through the real native global installer, retaining its existing owner, data root and global command.
- [x] **Agent-recommended updater provider slice:** qualify collected prepared-source runtime steps against the installed owner, exact source pin, actual selected/completed slot and original global data, preserving native execution, module fallback, machine gates and explicit older-helper refusal before connecting Manager's whole-suite source orchestration.
- [x] **Agent-recommended process-boundary slice:** qualify the executing engine's advertised prepared-source suite contract, durable exact input and complete eligible-portfolio admission before enabling Manager's capability-aware caller.
- [x] **Agent-recommended normal caller slice:** qualify Manager's capability-aware, once-acquired service source and trusted preparation through the existing suite process, preserving the exact provider argv, independent Manager self-source and explicit unsupported bootstrap/repair/targeted paths.
- [ ] Keep service-source selection independent of Manager self-update and Copilot plugin-content selection.
- [ ] Route the selected source through the same assembly, readiness, activation and observable update-outcome path.
- [ ] Preserve exact selection across retries and source failures; never fall back to an ambient checkout or marketplace payload.
- [ ] Qualify Manager's own clean-source, immutable non-editable installation and recovery independently of the selected service-runtime source.
- [x] **Agent-recommended prepared Manager slice:** qualify exact-source dry-run/currentness, distinct installed-health repair and reuse of its healthy source generation, preserving the existing default config/data and global command through compatible-state recovery. Keep clean-source self-update acquisition, production migration compatibility and actual adoption separate.
- [x] **Agent-recommended independent Manager acquisition slice:** route explicit Manager `[source] ref = "dev"` through the existing clean merged-source acquisition and trusted preparer, independently of service policy, into the same immutable installer and cutover owner. Qualify native Windows/Linux acquisition, source refusal, exact repair/reuse and fixture-default data continuity without changing Main/custom-ref/Git-optional behavior. Keep standalone bootstrap acquisition, production data compatibility and actual adoption separate.
- [x] **Agent-recommended standalone Manager bootstrap slice:** reuse that acquisition and trusted preparation from the isolated base-interpreter bootstrap before Manager construction. Preserve explicit prepared inputs without reselecting Dev, recovery-only/adopted recovery, the original ownership SDK and all service-source repair refusal guards. Qualify actual native Windows/Linux construction, refusal, installed health and fixture-default continuity; keep shell prerequisite/download transport, service-source repair and production rollout separate.
- [x] **Agent-recommended bounded service-source repair slice:** retain the incumbent legacy engine owner and default data root while preparing its independent service source for native runtime-only repair. Hold one original commit/tree and acquisition lease through repaired-engine inventory and suite preparation/execution; preserve targeted/non-repair bootstrap refusal and reject unsupported scoped source repair before native refresh. Qualify actual Windows/Linux construction, installed/global commands, fixture data continuity and an upstream advance between repair and inventory. Keep final portfolio execution, production migration compatibility and rollout separate.
- [x] **Agent-recommended bounded existing-profile qualification:** preserve an installation's existing Containers dependency profile through prepared-source updater planning, native construction and source-qualified currentness. Qualify the actual selected profile/generation, unchanged-source reuse and explicit unsupported/foreign/incomplete outcomes without changing its root, enabling an unused role or substituting a base-profile runtime. Retain the full source pin and canonical native profile owner; existing profile support in a standalone installer is not collected-updater qualification.
- [ ] **Agent-recommended bounded portfolio follow-through:** qualify source-backed updater readiness and actual execution across the eligible runtime owners rather than inferring a complete portfolio from the engine/profile witnesses. Preserve each installed command owner, canonical lifecycle tooling, existing default root, configured role and independent Manager/service/plugin selections. Use the existing collected updater and driver, retain one exact service commit and complete packet, and keep production data/serving cutover and recurring-maintenance consent separate.
- [x] **Agent-recommended bounded source-owner SDK readiness:** ship the existing canonical lifecycle SDK in every current required-context Python runtime payload and qualify actual materialization plus the installed owner's source validator. Preserve command/catalog bytes and legacy compatibility; this finite prerequisite does not close whole-portfolio execution or adoption.
- [ ] **Agent-recommended bounded service-less execution witness:** qualify actual collected prepared-source updates through SSH's native install/update and Machines' native init frontends, retaining their original command owner, default fixture root/state, installed consumers, selected-source currentness, healthy reuse and source-unavailable global dispatch. Preserve Machines' CMD-only Windows contract and actual persistent-environment containment; this pair does not close the complete eligible portfolio or production adoption.

### Phase 4a - Manager-owned opt-in background maintenance
The [maintenance slice](maintenance.md) owns the detailed proposal and evidence
matrix; no schedule or cadence is enabled by this plan.

- [x] Land the portable source-backed maintenance intent and implementation plan through review before adding that capability.
- [ ] Review the ownership revision, default-off policy, trusted-harness setup consent and current-user Windows Autorun lifecycle before implementation.
- [ ] Implement Manager-owned enrollment, background lifetime, bounded recurring invocation and status through the existing canonical update path, without requiring Agent Machines or an open Picker.
- [ ] Upgrade the resident maintenance worker safely when Manager changes, preserving durable update requests and exclusive scheduling through best-effort handoff or bounded cold recovery without waiting for another logon.
- [ ] Preserve explicit local choice and support an exclusive external-controller owner; hand over legacy suite-update responsibility without disabling general machine/dependency convergence.
- [ ] Add the portable setup enrollment seam and qualify an explicitly consenting adopter after Manager installation; keep private adopter implementation and evidence in its own repository.
- [ ] Prove default-off, trusted-setup enrollment, no-elevation Windows startup, durable headless operation, failure visibility and unchanged/advancing cycles on native Windows and Linux.
- [ ] After the Phase 3 and Phase 4 prerequisites, extend that same maintenance owner for bounded source health, selected-ref synchronization and canonical source-backed installation; do not add another updater.
- [ ] Preserve independent Manager/service/plugin-content selections, configured roles and exact per-run source identity across scheduled operation and retries.
- [ ] Prove unchanged-source reuse, a merged-source advance and explicit dirty/unavailable/overlapping/failed-candidate outcomes on Linux and Windows.

### Phase 4b - Durable installation execution driver
The [execution-driver slice](execution-driver.md), tracked by #7758, separates
transaction execution from the existing recurring-maintenance assignment.

- [x] Review the execution-owner clarification and bounded implementation plan.
- [ ] Review and implement [normal and operator-approved repair modes](execution-driver.md#normal-and-forcerepair-operation): central desired targets, same-version debounce, one active installer, clean normal rollback and bounded repair takeover without stale-marker vetoes. Preserve the [same-boot recovery requirement](execution-driver.md#live-cutover-recovery-correction), older-record repair and unrelated services/data; no host/kernel restart prerequisite.
- [x] Implement durable requests, compatible pending-request coalescing and attributable read-only status/results.
- [x] Land pinned-target active-request sharing through the existing queue and fixed first-use adapter; preserve later execution for unresolved moving targets. This does not close central desired-target selection, worker fan-out, same-boot recovery or repair takeover.
- [x] Land native-confirmed same-boot failure settlement, preserving interrupted/no-exit-status outcomes and older request history; qualify strict native cleanup, participant refusal, schema-2 compatibility and subsequent normal execution on Windows/Linux. Owner death without durable proof and legacy/approved repair remain separate open work.
- [x] Implement explicit operator-authorized legacy fence recovery without a historical cleanup-proof prerequisite, retaining original failed outcomes and recording authorization separately from native proof. Keep audited pre-launch recovery optional, normal admission conservative and active-installer takeover within the bounded repair contract.
- [x] Qualify an intact-source recovery-only bootstrap for older installed clients that cannot expose the new repair command. Preserve the canonical driver, existing custody and failed history without constructing Manager, replaying work or requiring the old owner to cooperate.
- [ ] Route ordinary supported suite-update entry points through one caller-independent execution owner over the complete canonical transaction.
- [x] Migrate the public Manager/engine update commands, staged acquisition and hook `reconcile-plugins --apply` without replacing their canonical executors.
- [ ] Migrate native launchers' direct validated plan-action execution and remaining suite-adopted first-use publishers while preserving standalone defaults.
- [x] Land and qualify the native launcher phase adapters independently of remaining first-use publishers.
- [x] Review the standalone foundation amendment before implementing cold first-use execution.
- [x] Qualify the Python-free Windows process and operation-custody prerequisites independently of foundation construction.
- [x] Qualify native construction progress, queue-fence interoperability and explicit restart-evidence recovery before enabling a cold bootstrap producer.
- [x] Qualify the fixed standalone construction participant, immutable reuse, failed-candidate preservation and installed source independence on hosted Windows and Linux before native genesis integration.
- [x] Qualify the owner-bound native Windows bridge to the existing participant protocol, retaining owner-thread mutation, cross-language identity, revocation and malformed/stalled/excess-peer isolation.
- [x] Qualify Python-free Windows source-inventory admission and durable progress under that same custody, preserving existing queues and canonical malformed/absent-state behavior.
- [x] Qualify the finite Windows producer with real private prerequisite acquisition, immutable construction, submitting-Job loss, concurrent cold callers, source-independent queued execution and protected native-root creation.
- [x] Qualify native Windows upgrades through retained queue-aware admission, preserving active requests, pending old-slot workers, immutable selections and later source-independent execution; refuse legacy/unsupported authority without mutating its queue.
- [x] Qualify Linux native whole-file custody and exact-descriptor exec transfer independently of a cold producer, including path loss, foreign-descriptor refusal and failed stream construction.
- [x] Qualify direct unreaped Linux child adoption across native exec and whole-group settlement before integrating pre-Python acquisition.
- [x] Qualify Python-free Linux source-inventory admission and durable progress under that same custody, preserving canonical schema, queue fences and restart-evidence recovery before native genesis integration.
- [x] **Agent-recommended bounded integration:** retain a positively owned native acquisition child through deliberate private-Python exec, reuse the canonical whole-group settlement and fixed construction participant, and prove caller-independent Linux genesis/upgrade before ordinary first-use publication.
- [ ] **Agent-recommended next integration:** route fixed owning-plugin first-use requests through the selected foundation's canonical queue and durable receipt/result, preserving publication admission, healthy dispatch, source identity and explicit unsupported/recovery outcomes on Windows and Linux.
- [x] **Agent-recommended bounded first-use handoff:** qualify the fixed legacy Vault direct-provision recipe's native cold supervisor, retained foundation, canonical ordinary queue receipt/result and frozen-input execution on Windows and Linux before adopting any production publisher. Namespaced recipes and actual canonical plugin deployment remain separate acceptance.
- [x] **Agent-recommended bounded first-use qualification:** exercise that fixed handoff against the actual materialized Vault installer and installed CLI/immutable generation in isolated native Windows and Linux client-only lanes, preserving canonical publication/source admission and healthy selection on failure. Qualify existing-owner/nested invocation with explicit no-write refusal before any production wrapper can recursively enqueue behind its own custody. Ordinary publishers remain unadopted; service-enabled deployment is not credited from client-only evidence.
- [x] **Agent-recommended bounded publisher adoption:** package the fixed standalone foundation through the existing canonical source/materialization owner and route the legacy Vault cold primary/payload command's admitted provisioning through its durable queue before holding any outer provisioning lock. Preserve healthy dispatch, managed/pinned/no-self-provision no-write refusal, exact owning source/root, ordinary service-enabled defaults and explicit nested/unsupported/recovery outcomes. Qualify the actual generated commands and applicable service effects in isolated native Windows/Linux before claiming production-path acceptance; do not infer adoption or namespaced support from the client-only adapter tests.
- [x] **Agent-recommended bounded bootstrap adoption:** route Vault's legacy session bootstrap's first stamp and drift provisioning through the qualified fixed actions under the same execution authority. Preserve the native staging, source/publication admission, healthy and non-deployment hook behavior, originating project, normalized runtime identity and explicit unsupported outcomes. Qualify the actual hook deadline and output-free launch acknowledgement without treating it as durable acceptance; do not recursively submit behind existing custody, retain the age/PID installer guard, or reintroduce a foreground fallback. Explicit installer publishers remain separate.
- [x] **Agent-recommended bounded stamp prerequisite:** qualify Vault's fixed canonical stamp action through the existing native supervisor and ordinary queue before routing its session bootstrap. Preserve provision request identity, distinct/coalesced stamp identity, source/publication/no-self/pinned/nested refusal and real native publication without a Vault runtime or service. Recipe support is not bootstrap or explicit-installer adoption.
- [x] **Agent-recommended bounded next-adopter prerequisite:** qualify Pull Requests'
  fixed legacy stamp/provision recipes for its canonical installed payload and
  default legacy root through the same native supervisor,
  canonical queue and frozen-input owner. Preserve its actual installer options,
  own context, publication/no-self/nested refusal, source-independent installed
  command, failed-candidate selection and native caller-loss outcomes before
  adopting any ordinary Pull Requests publisher. Do not infer service options,
  namespaced support or production command/bootstrap adoption from recipe proof.
- [x] **Agent-recommended bounded source handoff:** qualify Pull Requests'
  exact selected legacy snapshot through the same fixed recipe and native
  staged-origin reader before adopting its ordinary primary command. Preserve
  canonical snapshot provenance, live publication policy, exact queued source,
  ordinary-copy refusal and unavailable/reselected-source outcomes; do not
  replace the retained snapshot with the currently installed payload.
- [x] **Agent-recommended bounded publisher adoption:** route Pull Requests'
  actual generated legacy primary/payload cold commands through the packaged
  foundation and canonical queue before cold trace or outer-lock writes.
  Qualify generated commands, exact selected snapshot, caller loss, healthy
  dispatch and no-write refusals on native Windows/Linux. Refuse unsupported
  cold custom roots and cells without changing healthy dispatch or adopting
  bootstrap/explicit installer publishers.
- [x] **Agent-recommended bounded bootstrap adoption:** route Pull Requests'
  legacy session bootstrap's first stamp and drift provisioning through those
  qualified native actions and the same canonical queue. Preserve publication,
  exact source/root/project, killswitch, healthy and independent hook behavior,
  the actual hook deadline and output-free acknowledgement. Refuse nested and
  unsupported outcomes before state writes; remove its age/PID installer guard
  rather than retaining another execution owner. Explicit installers and cells
  remain separate acceptance.
- [x] **Agent-recommended bounded next-adopter prerequisite:** qualify SSH's
  existing legacy stamp/provision actions through the same native foundation,
  canonical queue and frozen-input owner before changing any ordinary SSH
  publisher. Preserve its own payload declaration, source/root/project,
  context/control semantics, native staging and actual service-less installer
  options; do not infer Vault service flags or Pull Requests' snapshot layout.
  Prove materialized cold install, installed CLI/source independence,
  failed-candidate preservation, caller loss and no-write source/publication/
  nested/unsupported refusal on native Windows/Linux. Command/bootstrap,
  explicit installer, namespaced and retained-source #5379 qualification remain
  separate unless directly exercised.
- [x] **Agent-recommended bounded bootstrap adoption:** route SSH's legacy
  session bootstrap's first stamp and drift provisioning through its qualified
  fixed native actions, packaged foundation and the same canonical queue.
  Preserve the actual service-less installer, source/root/project and control
  semantics, killswitch, healthy no-op and independent companion/guidance hooks.
  Remove the age/PID installer guard rather than retaining another execution
  owner; no foreground fallback or recursive submission behind existing custody.
  Qualify actual materialized hooks against the existing 15-second deadline,
  exact output, caller loss, ordinary receipt/result and no-write publication,
  source, nested and unsupported refusals on Windows/Linux. Initial runtime
  preparation may use the already-qualified fixed recipe; it does not credit
  unadopted command publishers. Retained-source #5379, primary/payload commands,
  explicit installers and namespaced/custom-root publication remain separate.
- [x] **Agent-recommended bounded source handoff:** qualify SSH's exact
  selected legacy snapshot through the same fixed native recipe and canonical
  staged-origin reader before adopting its ordinary primary command. Retain
  SSH's actual snapshot provenance, payload selection/version and complete
  dependency graph, publication controls, request-bound source and service-less
  options. Prove native Windows/Linux materialized stamp, selected-snapshot
  provision with the installed payload unavailable, installed CLI/metadata/module
  identity and failed-candidate preservation. Refuse missing, malformed,
  unselected, substituted or ordinary-copy sources before acquisition/queue/
  installation writes; do not replace the retained source with a newer payload.
  Preserve the bootstrap's already-qualified canonical-installed-source boundary
  rather than incidentally admitting a new bootstrap publisher. Ordinary
  primary/payload commands, explicit installers, custom roots/cells, rollout
  and the separately bounded network-backed #5379 acceptance remain open.
- [x] **Agent-recommended bounded publisher adoption:** route SSH's actual
  generated legacy primary and payload cold commands through the packaged
  foundation and canonical queue before cold trace/status or outer provision
  locks. Preserve the existing stamp-to-selected-snapshot/provision semantics,
  exact owning source/root/project, native staging, service-less options and
  original command arguments/exit outcomes. A primary command delegates to its
  exact selected snapshot's qualified dispatcher, never today's unrelated
  installed payload. Keep healthy dispatch unchanged; refuse managed,
  no-self-provision, pinned, nested, missing/skewed driver or unsupported cold
  custom-root/cell publication before state writes, without a foreground or
  recursive fallback. Qualify actual materialized commands, Python-free first
  use, caller loss/concurrency, source independence, failed-candidate
  preservation and no-write controls on native Windows/Linux. This does not
  reopen the qualified bootstrap, adopt explicit installers, exercise remote
  SSH, authorize rollout or close the separate network-backed #5379 gate.
- [x] **Agent-recommended bounded next-adopter prerequisite:** qualify CodeSpaces'
  fixed legacy `stamp` and `provision` recipes through the same native
  foundation, canonical queue and frozen-input owner before changing any
  ordinary CodeSpaces publisher. Admit only its canonical installed payload
  and default legacy root; preserve its snapshot-default declaration,
  required context, staging, dependency closure and own publication/no-self/
  pinned controls. Use its actual native options, not Vault's service flags.
  Qualify config-derived Connection Owner enabled/disabled/unknown behavior
  in isolated native fixtures without modifying host registrations or
  connecting to a CodeSpace. Prove actual materialized stamp/construction,
  installed command/module/distribution identity, failed-candidate selection
  preservation, caller loss and no-write refusals on Windows/Linux.
  Selected-snapshot acceptance, compound stamp/provision, command/bootstrap/
  explicit-installer adoption, cells/custom roots, live service deployment and
  the existing #6909 snapshot-qualification ownership remain separate.
- [x] **Agent-recommended bounded bootstrap adoption:** route CodeSpaces'
  legacy session bootstrap's first stamp and installed-payload drift provisioning
  through its qualified fixed native actions and the same canonical queue.
  Package the self-contained foundation through the existing canonical source
  materializer; do not assume recipe fixtures already made it part of this
  payload. Preserve exact source/root/project, required context and native
  stamp differences, publication and no-self/pinned controls, killswitch,
  healthy no-op and independent provider/guidance hooks. Remove its age/PID
  installer guard, with no foreground or nested fallback. Qualify the actual
  15-second hook, output-free launch acknowledgement, Python-free cold stamp,
  caller loss/concurrency, ordinary durable receipts/results, installed
  CLI/module/distribution identity and no-write refusals on Windows/Linux.
  Use isolated disabled-Owner configuration and native registration adapters,
  never host registration or a CodeSpace connection. Selected snapshots,
  primary/payload commands, explicit installers, cells/custom roots, live
  service deployment and #6909 remain separate.
- [x] **Agent-recommended bounded next-adopter prerequisite:** qualify Logger's
  fixed legacy `stamp` and `provision` recipes through the same native foundation,
  canonical queue and frozen-input owner before changing any ordinary Logger
  publisher. Admit only its canonical installed payload and default legacy root;
  retain its six-command required-context declaration, own controls, native
  staging, dependency closure and actual service-less provision options.
  Prove materialized stamp/construction, safe installed command/module/
  distribution identity, failed-candidate preservation, Python-free caller
  loss/concurrency and no-write source/publication/nested/unsupported refusal
  on Windows/Linux. The [bounded matrix](<journal/2026/10.24 logger-first-use.md>)
  preserves #7030 publication and #8370 diagnostics custody. Selected snapshots,
  command/bootstrap/explicit-installer publishers, task/timer registration,
  personal sync/prune, cells/custom roots and rollout remain separate.
- [x] **Agent-recommended bounded semantic prerequisite:** qualify Logger's
  fixed legacy `install` action through the same foundation, canonical queue
  and frozen-input owner before migrating its bootstrap's drift path.
  Preserve the unchanged six-command declaration, exact source/root/project,
  native staging and publication controls, and the actual task/timer
  reconciliation that distinguishes `install` from service-less `provision`.
  The [bounded plan](logger-install-action.md) requires isolated native
  Windows/Linux registration adapters, real materialized runtime/consumer
  checks, registration-failure outcomes and no personal sync/prune execution.
  No ordinary publisher, host registration, maintenance enrollment, selected
  snapshot, custom-root/cell or rollout is adopted by this prerequisite.
- [x] **Agent-recommended bounded bootstrap adoption:** route Logger's
  legacy bootstrap first stamp and drift `install` through its qualified
  fixed actions, packaged foundation and the same canonical queue. Preserve
  the six-command declaration, exact source/root/project, publication and
  no-self/pinned controls, killswitch and healthy no-op. Keep actual task/timer
  reconciliation distinct from service-less provision and retain independent
  provider/guidance hooks. The [bounded plan](logger-bootstrap.md) requires
  actual 15-second hook/output qualification, native caller loss/concurrency,
  retained lifecycle source and isolated registration effects without personal
  sync/prune. Remove its age/PID installer guard and foreground fallback;
  selected snapshots, command/explicit-installer publishers, cells/custom
  roots, live periodic workers and rollout remain separate.
- [x] **Agent-recommended bounded source handoff:** qualify Logger's exact
  selected legacy snapshot for the existing service-less `provision` recipe
  through the same native foundation, canonical staged-origin reader, queue
  and frozen-input owner before adopting ordinary command publishers.
  Preserve the unchanged six-command declaration, complete dependency graph,
  exact source/root/project, own controls and retained lifecycle source.
  The [bounded plan](logger-selected-snapshot.md) requires actual materialized
  Windows/Linux snapshot-only construction and safe installed consumers,
  failed-candidate preservation and no-write refusal of substituted,
  unselected, malformed or unavailable sources. Keep `stamp`, registration-
  reconciling `install` and the already-qualified bootstrap limited to their
  canonical installed source; do not infer adoption of command/explicit
  installer publishers, live sync/prune, custom roots/cells or rollout.
- [x] **Agent-recommended bounded publisher adoption:** route Logger's actual
  legacy primary, six payload targets and five auxiliary compatibility
  commands through its qualified single `provision` recipe and the same
  caller-independent queue. Preserve healthy read-only dispatch, exact
  selected-source/lifecycle ownership, publication controls and original
  arguments/exit outcomes; remove cold outer execution locks and arbitrary
  installed-payload fallback. The [bounded plan](logger-commands.md) requires
  materialized command qualification, Python-free caller loss/concurrency,
  source independence, failed-candidate preservation and no-write refusals
  on native Windows/Linux. Keep bootstrap `install` distinct, retain healthy
  cell dispatch and refuse unsupported cold cells/custom roots. Explicit
  installers, compound actions, live sync/prune, macOS and rollout remain
  separate; review this proposal before implementation.
- [x] **Agent-recommended bounded next-adopter prerequisite:** qualify Bridge's
  existing legacy `stamp` and `provision` actions through the same native
  foundation, canonical queue and frozen-input owner before changing ordinary
  Bridge publishers. Admit only its canonical installed payload and default
  legacy root; retain required context, own controls, exact source/root/project,
  native staging and complete dependency closure. The
  [bounded plan](bridge-first-use.md) preserves Bridge's actual first-install
  registration/start effects and its platform-specific prepared-runtime
  reconciliation rather than borrowing another plugin's service-less recipe.
  Qualify actual materialized construction, safe installed command/module/
  distribution identity, failed-candidate preservation, caller loss and
  no-write refusals on native Windows/Linux with isolated effect adapters.
  Snapshot acceptance, command/bootstrap/explicit-installer adoption, custom
  roots/cells, live service deployment and existing lifetime/publication/
  diagnostic custodians remain separate.
- [x] **Agent-recommended bounded semantic prerequisite:** qualify Bridge's
  fixed legacy `install` action through the same foundation, canonical queue
  and frozen-input owner before migrating its bootstrap drift path. Preserve
  its direct required-context declaration, exact source/root/project,
  own controls, immutable content admission and actual native registration/
  start semantics. Windows prepared `provision` only reconciles its task;
  do not substitute that action for bootstrap's `install`. The
  [bounded plan](bridge-install-action.md) requires actual materialized
  native Windows/Linux construction, installed consumers, healthy reuse,
  content-mismatch refusal, failed-candidate preservation, caller loss and
  isolated registration/start outcomes. Ordinary publishers, selected
  snapshots, cells/custom roots, live service deployment and rollout remain
  separate.
- [x] **Agent-recommended bounded bootstrap adoption:** route Bridge's
  legacy first stamp and drift `install` through the qualified fixed actions,
  packaged canonical foundation and same queue. Preserve actual selected/
  completed source-aware readiness, own controls/publication, originating
  project and independent guidance. Retain Windows bootstrap's existing
  unattended Bridge logon choice, not merely the shell's noninteractive flag;
  qualify its S4U/AtStartup task through isolated adapters without changing
  ordinary installer defaults. The [bounded plan](bridge-bootstrap.md)
  requires native Python-free hook deadline/output, caller loss/concurrency,
  actual materialized construction/installed consumers, immutable failure
  safety and no-write refusal. Remove age/PID reaping, reconcile-status
  execution custody and direct installer fallback. Commands, explicit
  installers, selected snapshots, cells/custom roots, live bridge sessions,
  service deployment, macOS and rollout remain separate.
- [x] **Agent-recommended bounded source prerequisite:** qualify Bridge's
  canonical source-qualified selected legacy snapshot for the existing single
  `provision` recipe through the same native foundation, staged-origin reader,
  queue and frozen-input owner before adopting ordinary commands. Preserve
  exact selected source/root/project, actual Bridge context and native
  provisioning effects, source-independent installed consumers, immutable
  failure safety and no-write refusal. The
  [bounded plan](bridge-selected-snapshot.md) retains canonical-installed-only
  `stamp`/`install`/bootstrap, ordinary unreceipted compatibility and all existing
  source/publication/lifetime custody. Command/explicit-installer adoption,
  cold cells/custom roots, live deployment and rollout remain separate.
- [ ] **Agent-recommended bounded publisher adoption:** route Bridge's actual
  legacy primary and payload cold commands through its qualified single
  `provision` recipe, packaged foundation and the same ordinary queue.
  Preserve exact selected-source ownership and ordinary unreceipted
  exact-publisher compatibility, actual Bridge context/native effects,
  healthy read-only dispatch and original argument/stdin/stdout/exit behavior.
  The [bounded proposal](bridge-commands.md) requires real native
  Windows/Linux command, caller-loss/concurrency, installed-consumer and
  no-write refusal proof before removing cold outer locks/status/direct
  installer execution. Keep bootstrap `install`, explicit installers,
  unsupported cold cells/custom roots, live service/session and rollout
  separate; review and actually merge this proposal before implementation.
- [ ] Establish the standalone execution foundation before plugin provisioning without requiring an existing Python runtime, Manager or another plugin.
- [ ] Migrate first-use publishers onto that foundation, retaining the canonical installer, publication admission, healthy dispatch and precise skew/recovery outcomes.
- [ ] Qualify foundation genesis, caller loss, concurrent cold callers, failed construction, upgrade and subsequent first-use requests on native supported platforms.
- [x] Preserve explicit supervised-debug execution under the same kernel-backed authority, standalone compatibility and existing installer custody.
- [ ] Qualify real-process concurrency, caller loss, interrupted-worker recovery, exact request identity and native headless operation without waiting on #6990 or #6928.
- [x] Qualify Windows caller-Job loss, contained controls, two-cycle headless operation, restart-evidence admission and actual queued CLI/JSON consumer contracts.
- [ ] Complete native Linux qualification when a supported isolated execution lane is available; do not substitute Windows or synthetic boot evidence.
- [x] Document the actual migrated entry points and remaining separately owned integrations; no partial slice is blanket installation-driver completion.

### Phase 5 - Prove assembled use and native Windows readiness
- [ ] Run the assembled runtime path in a fresh WSL-hosted clean-room container from a clean merged dev commit.
- [x] Qualify every declared installer adopter against that exact merged commit with frozen scenario inputs, successful runner exit and source-independent installed command evidence; retain each adopter's separate report.
- [ ] Exercise real commands and service interaction after source-checkout removal, including marketplace/runtime skew and non-deployment hooks.
- [ ] Prove native Windows source installation, generation selection and hook-deployment separation before a consented runtime-only pilot.
- [ ] Repair the native snapshot qualification fixtures and cell-slot legacy-staging boundary tracked in #7612; qualify the complete matrix before claiming native readiness.
- [ ] **Agent-recommended:** close the native PowerShell final-path long-path qualification gap in #7010 across supported hosts; shorter fixture roots do not qualify that separate boundary.
- [ ] **Agent-recommended:** resolve the network-backed PowerShell retained-source first-use test's bounded qualification gap in #5379 before claiming complete native SSH provisioning evidence.
- [ ] Update owner documentation and record actual serving-generation evidence, not only successful copies or install markers.
- [ ] Prove every installed service/tool in the adopter's declared inventory, including Manager, rather than treating a canary or all-adopter installer tests as the native transition.

### Completion and scope control
- [ ] Resolve all Plan and Validation Plan items; record or transfer remaining discrete work to a named tracked objective before marking Done.
- [x] Deferred to `#7031`: implement exclusive local-checkout sourcing of Copilot plugin content after the runtime-only campaign.
- [x] Deferred to `#7250`: **Agent-recommended follow-up, operator-approved tracking:** preserve legacy Python-free redirect sets through a separate native publication mechanism; do not add a bootstrap interpreter prerequisite.

<!-- backlog-reconciliation:7556:transfer:2822:to:efforts/active/source-managed-runtime-installs/README.md -->

Add this COMPLETE named family directly to the existing ROOT Plan AND
Validation/continuing Coordination, retaining the full incumbent document, Journal and
#5472/#7028/#7029/adopter/deployment owners. Do not create another updater or acquire an
implementation claim.

### Installed CLI health and known-good publication retained from #2822
- [ ] Credit #3671, #6598 and #6707's actual entrypoint/full-command loading, isolated
  installed origins, held frozen construction and health-before-completion progress.
  Reconcile this installed-output family against those real deliveries rather than
  restoring the superseded unsafe in-place premise.
- [ ] On the original supported Worktrees entrypoints/platform lanes, classify an
  install that completes inside its watchdog budget but leaves the real CLI broken as
  FAILED. Real installed candidate health must precede completion and promotion;
  exit/time/stamp/metadata alone is not CLI readiness. Preserve explicit timeout versus
  completed-with-error diagnostics and genuine dependency/probe failures.
- [ ] Preserve missing __main__/entrypoint, truncated installed files, lazy/nonimported
  code/resource and wrong/missing installed dependency/output cases. Verify the relevant
  installed consumer rather than only source fingerprints/direct_url/provenance. Retain
  appropriate output-integrity qualification without prescribing a new universal hash
  algorithm or unrelated every-file audit.
- [ ] Publish current and last-known-good only after the appropriate
  candidate/selected-runtime health gates. Preserve and retain a verified previous-good
  generation across failed construction, promotion or cutover; never replace it with the
  failed candidate or trust a corrupt current/LKG pointer as health evidence.
- [ ] Qualify corrupt/unavailable prior or LKG evidence, failed candidate/promotion,
  interruption and state-aware recovery/compatible rollback with explicit
  uncertain/blocked/failure outcomes. Repair a bad published identity through a distinct
  qualified generation, never in-place rewriting, broad cache deletion or a force
  shortcut.
- [ ] Preserve the accepted immutable source/admitted-input generation identity,
  current-selection identity inequality rather than version/hash ordering, and
  health-gated compatible forward/reverse handoff. Marketplace source, merged source,
  containing release, installed selection and native effect remain separate facts.

### Validation and continuing coordination for #2822
- [ ] Record original/current consumer and exact source/provider mapping, including the
  quick-broken-install negative, missing/truncated/lazy/resource/dependency output
  cases, candidate/selected CLI invocation, safe current/LKG advancement, healthy-prior
  preservation and corrupt-prior/LKG refusal/recovery. Historical POSIX/WSL and isolated
  Windows proof remain credited at their actual limits; complete the original supported
  native lanes only under their existing owners and separately authorized venue.
- [ ] Preserve unrelated source-managed/adopter/native/maintenance/driver and
  rollback-skew work. This finite family may be implemented/qualified without waiting
  for every unrelated adopter or the entire parent; conversely its intake is not parent
  completion, rollout or a new generic ACK ceremony.

Receiving gate: NEW normal reviewed ACTUAL merge and complete CURRENT ROOT
Plan/Validation/Coordination readback must deliver every row before administratively
superseding #2822. Existing effort creation, a child document or this proposal is not
receipt. If status, scope, custody or evidence tiers cannot be preserved, retain #2822.

Current best-effort handoff interpretation: references above to compatible
forward/reverse handoff do not reinstate zero-outage continuity, a compatibility veto,
or incoming successor readiness before every old-runtime retirement. Preserve the
accepted bounded, identity-checked cold-takeover route when handoff breaks, explicit
outage/unknown recovery, and client/child retry obligations. Actual candidate/output CLI
health still gates candidate promotion and completion, and a verified retained
previous-good generation must survive failed construction or cutover. Best-effort
continuity does not make an unhealthy candidate or corrupt current/LKG evidence
acceptable. This records the existing reviewed direction; it authorizes no live force,
service, runtime, or deployment operation.

<!-- backlog-reconciliation:7556:transfer:4010:to:efforts/active/source-managed-runtime-installs/README.md -->

### Direct legacy existing-slot version/freshness acceptance from #4010

#### Continuing Coordination

Keep the SAME Active ROOT Plan/Validation/Coordination, complete dated Journal and OPEN
#7028 with actual eligible bounded-source coordination. Preserve all2822
installed-output/LKG clauses and real global
Validation,5472/7029/source-selection/adopter/native/deployment/maintenance/7758
execution-driver holders and every unreleased source claim. Existing source-isolation,
artifact-admission, version-authority and installation owners remain separate; this
tracking intake neither assigns their work nor reads private installed configuration.
Closed12 and eventual closed4010 are historical provenance, NOT live ownership tokens.
Normal current reviewed Dev workflow remains mandatory; no new universal ACK.

#### Unchecked Plan

- [ ] Retain the original installX -> updateY -> SAME existing venv slot ->
  version-command reproduction and its real distribution metadata freshness.12's
  independent version-counter alignment explicitly excluded this case; do not reopen
  that completed scope or call this one complete by association.
- [ ] Reconcile expected installed-version output with the accepted independent
  marketplace payload/package/runtime-source/selected-generation contract. Prove a real
  intended runtime update refreshes actual executable/metadata output OR obtain an
  explicit reviewed supported-migration/disposition for the legacy reused-slot path.
  Source-qualified slots alone do not authorize silently weakening the old expectation.
- [ ] Keep actual CLI interpreter/module origin, package metadata, payload delivery,
  source/build/profile and serving-daemon identity distinguishable. A newer payload
  label cannot make an unchanged old runtime current; a CLI print is not proof which
  daemon serves. Intentionally independent selected-source behavior remains supported
  and explainable.
- [ ] Use the actual canonical installer/resolver/installed version-command consumers
  with precise success, stale/incomplete slot, metadata mismatch, failed update,
  unsupported legacy/migration and truthful error outcomes. Avoid source-only
  import/provenance checks masquerading as native installed freshness.
- [ ] Preserve current2822 health and failure safety: actual candidate/selected output
  CLI health before candidate completion/promotion, a VERIFIED retained previous-good
  generation, safe selectors/redirects, failed construction/cutover preservation and
  explicit corrupt/unknown/LKG recovery. Never rewrite a completed slot or merely
  display a healthy-looking newer label.
- [ ] Preserve accepted best-effort compatible handoff and bounded identity-checked
  old-owner shutdown/cold takeover when handoff is broken, refused or hung. Cold
  fallback may establish old-owner exclusion before incoming readiness. Temporary
  outage/stream cut-off, durable recovery and client/child retries remain explicit; no
  zero-outage, compatibility veto or incoming-ready-before-every-retirement rule is
  reinstated.
- [ ] Keep this finite legacy/version outcome independent of full
  runtime/adopter/maintenance/driver or every-host completion. Preserve the original
  supported evidence tiers and all separately owned parent obligations; no test,
  installer, runtime or deployment is authorized by this intake.

#### Unchecked Validation Plan

- [ ] Record original/current consumer mapping and actual X/Y existing-slot result, or
  the explicit owner-reviewed migration/disposition and supported replacement proof.
  Verify installed command/module/distribution identity and truthful selected source
  rather than comparing only two manifest strings.
- [ ] Cover intended runtime update versus payload-only movement/independent older
  runtime, same-package different-source identity, stale/incomplete metadata and
  failed/refused update without confusing CLI output with serving-daemon evidence.
- [ ] Preserve actual output health, correct current/LKG advancement, a verified healthy
  retained predecessor and corrupt/unavailable prior/LKG refusal/recovery. Best-effort
  handoff cannot waive unhealthy-candidate refusal, durable ownership or recoverable
  progress.
- [ ] Attribute source/contract/native-installed/serving/release evidence and unknown
  capacity separately through the current bounded portfolio and authorized native
  owners. Do not demand unrelated all-adopter/whole-parent completion, fabricate a
  passing repro or add a new blanket ACK.

Receiving gate: a NEW ordinary reviewed ACTUAL MERGE must deliver this COMPLETE direct
unchecked Plan AND Validation/continuing Coordination, including the actual global
Validation entry. FULL CURRENT Active ROOT Plan/Validation/Coordination AND the linked
dated Journal, OPEN7028 and real source holds must be read back BEFORE4010 supersession.
Old creation,7080 foundations,2822 intake, a title/link/child-only note, an unmerged
amendment or this proposal is not receipt. If any original limit, identity distinction,
owner or health/recovery gate cannot be preserved, RETAIN #4010. No product/version
command, tests/imports/collection/checkers, installer, runtime, service or
credential/configuration effect occurs here.

<!-- backlog-reconciliation:7556:transfer:4015:to:efforts/active/source-managed-runtime-installs/README.md -->

### Direct all-service payload-to-serving-runtime acceptance from #4015

Receive ALL original functional freshness and seven refined all-consumer requirements
PLUS all EIGHT direct #4058 Bridge rows DIRECTLY into the EXISTING Active ROOT Plan, its
own Validation, actual global Validation Plan and continuing Coordination/dated Journal.
Preserve the full incumbent Plan/Validation, #2822/#4010 health/LKG rows, current
source/adopter/native/maintenance/driver custodians and every real hold. No
implementation, rollout or parent completion is declared.

#### Plan

- [ ] P1: Produce an explicit per-applicable-service-family/owner TRIGGER matrix after
  successful plugin payload refresh: bare plugin update, next-session hook, first use,
  periodic/explicit updater and selected source-controlled rollout are DISTINCT
  observations. Include running long-lived service and deferred/disabled/non-applicable
  roles. Do not silently count a full updater success as bare-update behavior.
- [ ] P2: Retain functional runtime freshness while explicitly reconciling the old
  always-rebuild/restart wording through existing owner-approved pin/no-churn/selection
  policy. Preserve standalone defaults, intentionally pinned or independently sourced
  runtimes, exact installation/profile/ref/source and unselected engines/services; no
  auto-enablement or unnecessary unchanged-content churn.
- [ ] P3: Trace changed ELIGIBLE content through the owning immutable
  dependency/construction/health/completion/publication transaction to actual selected
  AND serving generation. A payload/version/skill/catalog label, completed slot,
  selector, available interpreter or release marker alone is not runtime readiness or
  service convergence.
- [ ] P4: Preserve bounded startup and independent non-deployment hooks,
  single-flight/admission and the one canonical installation execution/maintenance
  owner. Do not add a heavy foreground hook, second updater, generic timestamp-based
  reaping or claim expiry; precise stale/pending/failure diagnostics stay visible.
- [ ] P5: Preserve work-aware canonical activation, private
  credential/relay/session/source/config custody and exact process/generation identity.
  Keep listener health, actual accepted handoff, transport result, serving identity and
  native exit separate; uncertain consequential outcomes cannot be replayed.
- [ ] P6: Preserve current best-effort compatible handoff and bounded owning-authority
  cold recovery when contracts are incompatible/refused/hung, INCLUDING permitted old
  retirement before incoming ready. Do not reinstate universal
  zero-outage/ACK/compatibility vetoes. Actual candidate/output CLI health, VERIFIED
  retained previous-good generation and durable recoverable progress remain mandatory;
  failure uses safe recovery OR proven justified commit-forward, never success-shaped
  loss.
- [ ] P7: Make pending/disabled/deferred/stale/failure/current servicing truthful
  through real command/serving/native evidence and explicit supported skew/readiness
  outcomes. Installed code, available command, configured role and authorized running
  instance remain separate.
- [ ] P8: Retain ALL EIGHT Bridge/#4058 clauses directly: exact trigger;
  payload-through-immutable-to-serving identity; selected source/pin/role/no-churn;
  selected canonical servicing/recovery; observable truthful evidence tiers; bounded
  single-owner startup/privacy; actual
  changed/unchanged/pinned/long-lived/failure/native trigger-platform-caller matrix;
  unchanged accepted owner without narrowing the all-service scope.
- [ ] P9: Preserve ALL broader seven refined #4015 outcomes: per-family supported
  trigger; exact selected source; real eligible content lifecycle; work/custody-safe
  activation; bounded hook/single-owner behavior; actual installed/serving/native
  consumer qualification; explicit supported owner dispositions with no discarded
  functional freshness.
- [ ] P10: Keep #4477/#5750/#4022/#5225 Bridge deployment/fallback/native,
  #5472/#7028/#7029 source/slot/adopter, actual maintenance and #7758 driver holders
  intact. Keep #4028's independent diagnostic acceptance and custody separate. This
  intake closes no separate claim, native gate or independent leaf by association.

#### Validation

- [ ] V1: Qualify the supported family/platform/caller trigger matrix through ACTUAL
  updated payload -> admitted selected runtime -> serving generation -> relevant
  native-supervisor consumers. Cover long-lived service, changed eligible bytes,
  pinned/unchanged/disabled/non-applicable roles, failed construction/cutover,
  pending/deferred/skew and single-flight behavior. Preserve the original
  all-applicable-family scope without adding every physical host.
- [ ] V2: Verify candidate/output health, current/LKG advancement, VERIFIED retained
  good generation and explicit failed/unknown durable recovery/commit-forward under
  current best-effort policy. A construction test, newer label, health port, happy
  restart or two-product canary cannot qualify every trigger/family or native adoption.
- [ ] V3: Exercise Bridge's original inherited trigger/platform/caller and failure
  matrix separately from generic shared helper/other-plugin proof. Keep
  request/session/credential custody and actual native tracking; preserve existing
  5750/4022/5225 implementations and source-specific gates.
- [ ] V4: Attribute source merge, containing Main, actual selected
  command/module/distribution, serving identity and authorized adopter/native
  observations separately. The recent bounded Main Core/Manager repair remains credited
  at its stated scope, not universal Windows/Bridge/all-consumer evidence.
- [ ] V5: BEFORE #4015 administrative supersession, obtain a NEW normal reviewed ACTUAL
  merge containing this COMPLETE direct ROOT Plan/Validation/Coordination/dated Journal
  and actual global row, then read back CURRENT FULL receiving sections and live
  custody. Old creation/provider transactions, a child/title link, proposal or
  successful update is not this receipt. If any functional clause/owner cannot survive,
  retain #4015.

#### Continuing Coordination and dated Journal

- [ ] C1: Continue through SAME #7028/source/slot, adopter/native/deployment,
  maintenance and #7758 execution owners, this Active ROOT's reviewed Plan/actual global
  Validation/dated Journal and eligible OPEN bounded-source claiming. Keep Bridge's
  separate canonical/fallback/native lanes and #4028's independent diagnostic custody
  intact. No new source/resource transfer or blanket ACK is created.
- [ ] C2: Journal the actual new reviewed intake merge/current full readback, delivered
  Bridge/shared foundations, exact remaining per-family trigger/serving matrix and the
  next bounded Plan stretch. Preserve every incumbent row/history, keep the effort
  Active, and perform no update, installer, test, native experiment,
  service/process/credential or configuration action through this intake.

## Validation Plan

- [ ] **Wholesale same-installation cutover:** every inventoried global binstub and applicable serving process executes its selected immutable merged-Dev slot while retaining the Main installation's existing runtime data/configuration identity. Prove actual installed/serving code identity and continuity of representative existing state; a new cell, copied database, changed data root or synthetic fresh profile does not satisfy this gate. Qualify data-format compatibility and supported recovery before a rollback; do not assume that sharing data makes arbitrary older code compatible.

- [ ] G1: Resolve EVERY directly retained #4015 original/refined all-service and EIGHT
  #4058 Bridge Plan/Validation/continuing Coordination clause at its original finite
  family/platform/evidence tier, preserving trigger distinctions, pin/no-churn/role
  alternatives, actual CLI health, VERIFIED retained-good recovery, same custodians and
  every independent hold. Administrative coalescence is not bare-update integration,
  running-service adoption, rollout or unrelated parent completion.

- [ ] Resolve EVERY directly retained4010 Plan/Validation clause above at its original
  finite scope, preserving literal existing-slot acceptance or explicit reviewed
  migration, truthful identity, actual CLI health, VERIFIED retained-good recovery and
  all incumbent2822/parent/custodian gates. Administrative intake is not implementation,
  native acceptance or rollout.

- [ ] Resolve every directly retained #2822 Plan/Validation clause above at its original
  finite scope and evidence tier, preserving alternatives, same owners, holds, and
  current portfolio bounds. Administrative intake is not implementation, native
  acceptance, rollout, or completion of unrelated parent work.

- [ ] **Bidirectional selection:** forward, lower-version and same-package/different-source replacements use identity inequality; compatible handoff health-gates before retirement, while incompatible/refused/hung handoff allows bounded owned-scope shutdown or identity-checked force termination before cold-start readiness.
- [ ] **Cold recovery and retries:** breaking all predecessor handoff contracts does not veto selected Dev deployment. Qualify durable recovery after abrupt child death, consumer reconnect after stream/reply loss, safe retry/deduplication or unknown-outcome reconciliation, exclusive ownership and explicit failed-recovery reporting.
- [ ] **Operator repair authority:** explicit repair proceeds without historical cleanup or pre-launch evidence, including older records that cannot supply it. Audited pre-launch recovery is optional. Distinguish authorization from native settlement, preserve actual failure/pending history, refuse implicit repair in ordinary callers, and verify scoped takeover, actual selected readiness and unrelated-data preservation separately.
- [ ] **Source admission:** reject dirty, worktree-backed, unmerged or substituted source; preserve the exact resolved commit throughout a run.
- [ ] **Materialization:** actual installer imports the complete vendor closure from a non-editable install with no source-checkout dependency.
- [ ] **Identity:** identical inputs reuse unchanged completion bytes; different commits or assembly/dependency/profile inputs cannot overwrite a completed slot.
- [ ] **Selection:** installed binstubs and native resolvers execute the exact completed selected generation, including hash-qualified dev sources.
- [ ] **Failure:** incomplete/failed candidates remain unselected and preserve the previous healthy runtime and redirects.
- [ ] **Policy:** real hook and first-use paths do not publish runtime copies or redirects in managed mode; their non-deployment behavior still runs.
- [ ] **Skew:** newer-plugin/older-service and older-plugin/newer-service interactions work within declared capabilities or return precise readiness outcomes.
- [ ] **Source modes:** main tracking, stable/exact pins and dev tracking converge through one source-assembly path without moving midway through a run.
- [ ] **Maintenance:** the existing owner runs bounded, observable source health/sync/install cycles at the selected cadence; unchanged input is reused and source/candidate/overlap failures preserve healthy runtimes and edits.
- [ ] **Background ownership:** default-off enrollment accepts explicit user or trusted-setup consent, current-user Windows Autorun requires no UAC registration, the worker survives its setup caller without visible consoles, and only the selected owner performs suite updates.
- [ ] **Worker upgrade:** native replacement preserves durable in-flight request/operation identity across graceful or cold recovery, prevents concurrent scheduling or duplicate unsafe effects, and reports actual readiness or a recovery outcome when the successor fails.
- [ ] **Execution driver:** concurrent ordinary callers observe one durable operation, caller exit preserves it, distinct explicit selections stay distinct, interrupted execution remains explicit, and supervised debugging cannot overlap the same transaction authority.
- [ ] **Setup adoption:** approved consuming-harness setup finishes with verified enrollment or a precise activation/readiness blocker; unrelated repository discovery cannot enroll maintenance and external-controller ownership cannot overlap Manager.
- [ ] **Management continuity:** Manager and the selected service runtimes can use distinct clean merged-source selections; the management tools remain usable for update and supported recovery.
- [x] **Clean-room:** a structured Tier-P report proves fresh Linux installation and real service use; report the exact source and serving identities.
- [ ] **Windows:** native installer/resolver fixtures pass; any installed pilot has explicit change consent and attributable running-state evidence.
- [ ] **Agent-recommended - robustness:** test concurrent preparation/publication, unavailable-source retry and state-aware rollback through existing lifecycle primitives.
- [ ] **Agent-recommended - rollout evidence:** retain a concise qualification/soak record before marking a development snapshot stable; do not infer stability from a clean install alone.
- [ ] **Contribution gates:** targeted bounded tests, required install-contract checks, appropriate clean-room evidence and reviewed merged PRs cover the final implementation.

## Proposal

Keep deployment policy, source selection and serving-generation selection
separate. Reuse the canonical materializer, runtime construction/retention and
admission paths. Runtime-source selection does not modify marketplace
definitions, introduce a second supervisor, or claim release promotion.

**Agent-recommended:** keep the Manager's own recovery/update source on a
known-good selection while canarying service runtimes; treat this as an
independent configuration choice rather than forcing the Manager and suite
onto one branch.

## Journal

The [dated journal](journal.md) retains the complete implementation, qualification and review history. The Plan and Validation Plan above remain the completion gate.
