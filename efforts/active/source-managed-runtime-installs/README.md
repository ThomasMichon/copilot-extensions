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

The current Manager has self-update source configuration, but that is not a
suite-wide runtime-source policy. Plugin bootstrap paths still publish runtime
content and installed command redirects from the enabled payload.

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
- [ ] **Agent-recommended bounded slice:** qualify Bridge command-side first use before provision trace/status/root/lock writes, with actual standalone reachability, healthy managed invocation, helper skew and independently selected installation scope.
- [ ] **Agent-recommended bounded slice:** qualify Dispatch command-side first use through the canonical required-context dispatcher contract, preserving legacy defaults for unrelated version-1 manifests and actual selected-cell scope.
- [ ] Keep adapters thin and enforce drift-compatible service interaction without implicit runtime replacement.

### Phase 4 - Integrate Manager-owned runtime source selection
- [ ] **Agent-recommended alignment qualification:** prove forward/reverse current-generation replacement, including same-package/different-source selection, with preferred compatible handoff and bounded identity-checked cold takeover when contracts break or drain fails; use identity inequality, not version ordering.
- [ ] Maintain a separate installation source with main, stable/exact pin and opt-in merged-dev selection resolved once per run.
- [ ] Keep service-source selection independent of Manager self-update and Copilot plugin-content selection.
- [ ] Route the selected source through the same assembly, readiness, activation and observable update-outcome path.
- [ ] Preserve exact selection across retries and source failures; never fall back to an ambient checkout or marketplace payload.
- [ ] Qualify Manager's own clean-source, immutable non-editable installation and recovery independently of the selected service-runtime source.

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
- [ ] **Agent-recommended next publisher boundary:** trace Vault's remaining session bootstrap and explicit installer publication callers against the adopted command handoff, then qualify the next fixed request recipe under the same execution authority. Preserve the existing native staging, source/publication admission, healthy dispatch and explicit unsupported outcomes; do not let a bootstrap caller recursively submit behind its own custody or reintroduce a foreground fallback.
- [ ] **Agent-recommended bounded stamp prerequisite:** qualify Vault's fixed canonical stamp action through the existing native supervisor and ordinary queue before routing its session bootstrap. Preserve provision request identity, distinct/coalesced stamp identity, source/publication/no-self/pinned/nested refusal and real native publication without a Vault runtime or service. Recipe support is not bootstrap or explicit-installer adoption.
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

## Validation Plan

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
