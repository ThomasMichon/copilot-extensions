*The following contribution was assisted using Copilot.*

# Wave 1: lifecycle coverage

Back to the [stabilization roadmap](stabilization-roadmap.md), which owns cross-area sequencing and execution decisions.

This appendix accounts for **50 issues: 32 researched proposals and 18 provisional records**. Research status is not a finding that implementation is missing: the four source checks below distinguish concrete source defects from qualification and evidence work. The linked public issue remains the authority for its **whole** original, refined and inherited contract; these summaries do not replace acceptance clauses.

Vision and effort observations use source commit `307584006a70b5ca2de0ca82297fa71fc072f9b0`. The four code audits use `d77783f0a10b067a249dd39cee09f48f9dc6024b`; their inspected production/test blobs are unchanged between those pins, and standing visions/patterns did not change. Recorded effort statuses are source observations, not a fresh operational census.

**Planning boundaries:** a coherent proposed effort amendment can be researched without an accepted consolidation intake. Proposed homes below remain proposals, not ownership transfers. Context-only homes and genuine scope, policy or evidence gaps remain provisional. Existing source/resource owners, claims, handback conditions and independent acceptance survive.

Compatible handoff is preferred, not an unconditional no-outage guarantee. Authorized bounded cold takeover retains exact identity, one mutation authority, state-compatible rollback and explicit interrupted/uncertain outcomes. It does not silently satisfy a stronger finite survival, clean-drain or accepted-delivery requirement. No all-host, whole-parent or global execution gate is added.

**Priority correction:** [#870](https://github.com/ThomasMichon/copilot-extensions/issues/870) is **P2, corrected from P1**: the original describes benign, fail-safe API hardening, and current source corroborates predecessor preservation rather than an outage.

## Current-source checks

### Birth-bound force fallback

**[#5006](https://github.com/ThomasMichon/copilot-extensions/issues/5006): `source-supported-defect`.** The ordinary/unbound `serving=None` deploy branch can reach the [retirement consumer](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-bridge/src/agent_bridge/venue_cli.py#L467-L477). Its [force fallback](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-bridge/src/agent_bridge/service_process_cli.py#L189-L211) still uses `os.kill(pid, _signal.SIGKILL)` when `safe_killpg` returns false.

- The [identity-bound POSIX primitive](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/libs/zdd/src/zdd/diagnostics.py#L299-L308) currently sends `SIGTERM`; substituting it unchanged would weaken force semantics. This is not a finding against every consumer: normal mux/Bridge identity-bound repairs and the published passive birth breadcrumb retain their credit.
- The [existing group-success test](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-bridge/tests/test_service_marker.py#L220-L243) covers successful group signaling, not the false-group fallback with matched versus stale/unknown birth identity.
- Conditional next change: extend/reuse the existing identity-bound signaling operation for the required force signal, carry the intended birth identity through the actual consumer, and refuse mismatches without bare-PID fallback. Preserve group behavior, default compatibility and existing replacement authorization; no new lifecycle controller.

### Source-to-serving fidelity

**[#3444](https://github.com/ThomasMichon/copilot-extensions/issues/3444): `design-or-evidence-gap`, not a blanket installer rewrite.** Current [shell import gates](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/scripts/install.ps1#L2019-L2033) and [shared source-wheel member checks](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/libs/installer-engine/installer_engine.py#L38-L50) are real foundations, not proof of the complete historical or installed chain.

- The [missing-content regression](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/tools/test_installer_source_build.py#L412-L432) and [backend-mutation controls](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/tools/test_installer_source_build.py#L435-L470) preserve source/constructor progress. Legacy shell and source-wheel construction profiles must not be conflated.
- [Coordinator readiness](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/src/agent_dispatch/installer_readiness.py#L27-L52) is a coordinator fact, not actual supervisor/lane identity. Three independently broken same-label wheels supersede a stale-cache-only theory, but do not identify the publisher/fetch cause.
- Next work is a finite source/payload/wheel/selected-runtime/actual coordinator-and-supervisor evidence matrix, or a named tracked uncertainty disposition through the existing owners. Preserve all eight #3419 and six #3594 clauses, separate rejected-candidate causation, previous-good data, publication/staging/native/redrive custody and corrected historical evidence.

### Ordinary early cutover results

**[#870](https://github.com/ThomasMichon/copilot-extensions/issues/870): `source-supported-defect`; P2.** [Port selection](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/libs/zdd/src/zdd/cutover.py#L476-L481) precedes normalization, and the [spawn handler](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/libs/zdd/src/zdd/cutover.py#L499-L513) explicitly re-raises when `conditional is None`. The [actual Bridge caller](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-bridge/src/agent_bridge/venue_cli.py#L427-L436) expects a result after a cleanup-only `finally`.

- The [conditional spawn-failure test](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/libs/zdd/tests/test_conditional_retirement.py#L236-L254) is genuine delivered coverage, but not the ordinary callback-throw contract.
- Add ordinary throwing-port and throwing-spawn controls: failed typed result, truthful failure evidence, unchanged predecessor, no routing flip/drain/demotion. Preserve partial-launch uncertainty rather than assuming an exception proves no effects.
- Reuse existing result/breadcrumb mechanisms and keep canonical/current shipped copies consistent through normal publication. This does not require full conditional retirement or native rollout, and does not establish an unsafe zero-daemon event.

### Completed-slot admission and worker lifetime

**[#5356](https://github.com/ThomasMichon/copilot-extensions/issues/5356): `qualification-only` for the audited completed-slot boundary.** The [construction adapter](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/scripts/install.ps1#L812-L826) obtains admission before the [outer reuse guard](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/scripts/install.ps1#L1733-L1740), which encloses **both venv and package mutation**, ending [before isolated health/activation](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/scripts/install.ps1#L1993-L2000).

- [Actual admission returns](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/scripts/versioned_runtime.py#L1747-L1761) reuse matching completed content and refuse conflicts. Build-before-cutover order or a retained `--reinstall-package` string alone is not current in-place-reinstall proof.
- Credit the [extracted reuse test](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/tests/test_installer_engine_adoption.py#L476-L489), [attempt/birth/staging repair](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/scripts/bootstrap-check.ps1#L224-L248) and [real-watchdog fixture](https://github.com/ThomasMichon/copilot-extensions/blob/d77783f0a10b067a249dd39cee09f48f9dc6024b/plugins/agent-dispatch/tests/test_windows_bootstrap_reconcile_lifecycle.py#L256-L278). Test text is not a new execution receipt.
- Qualify the actual normal Windows update with a matching completed live slot, conflicting content and genuinely owned incomplete/abandoned work. Do not port the guard again or use stop-first repair. Historical backend-stall and missing exit diagnostics remain separate evidence gaps; no whole-issue fixed verdict follows.

## Per-issue coverage

Each heading links the whole-contract authority. “Researched proposal” means a grounded planning result, not an accepted intake or a concrete missing-code verdict. Unless explicitly stated above, current implementation has not received a fresh source audit; release, installed and native outcomes remain unproved.

### [#356](https://github.com/ThomasMichon/copilot-extensions/issues/356) - P2; provisional
- **Outcome / rationale:** Guided local adoption OR remote clone into a chosen root OR skip, consented discovery and knowledge pairing remain distinct from shared acquisition already bounded to #8545.
- **Vision:** [visions/installer/README.md § first-harness-repo-adoption](../../../visions/installer/README.md#first-harness-repo-adoption): “Offers three paths for the initial harness repo”.
- **Home / owner:** [Installation-Independent Sessions](../installation-independent-sessions/README.md), Active, is context only; the original adoption/discovery owners remain. No whole owning home is verified.
- **Slice:** Compare one adoption command/UI path against all three choices and consented discovery, without taking #8545.
- **Exit:** Positive/negative choice controls and chosen-root/knowledge-pair readback; native UX is separately qualified.
- **Open:** Preserve discovery/UI and unrelated maintenance/Configurator custody; do not infer completion from shared setup delivery.

### [#609](https://github.com/ThomasMichon/copilot-extensions/issues/609) - P1; researched proposal
- **Outcome / rationale:** Disposable real-consumer coverage must preserve Vault warm TTL/no-reunlock, nonresumable work survival OR clean drain, and routing/retirement/interruption cases; mocks cannot establish these outcomes.
- **Vision:** [visions/clean-room-validation/README.md § fresh every time, host-safe](../../../visions/clean-room-validation/README.md#fresh-every-time-host-safe): “A run starts from a genuinely clean state and never mutates the host”.
- **Home / owner:** Proposed Phase 4/5 family in [Test Portfolio Rationalization](../test-portfolio-rationalization/README.md), Active; primary maintainer, rig/runtime and Windows security owners retain their scopes.
- **Slice:** Specify one disposable actual-work update witness with warm credential reuse.
- **Exit:** Identity-owned child/retirement controls, actual TTL/no-reunlock and survival OR deliberate clean drain; keep Windows DACL/security acceptance separate.
- **Open:** General permission for cold interruption is not a pass for this stronger finite survival/drain case; no new global no-outage guarantee.

### [#870](https://github.com/ThomasMichon/copilot-extensions/issues/870) - P2; researched proposal
- **Outcome / rationale:** Ordinary early port/spawn failures need failed `CutoverResult` normalization; the predecessor remains untouched. P1 is corrected to P2 for this explicitly fail-safe hardening.
- **Vision:** [visions/plugin-services/README.md § zero-downtime-cutover](../../../visions/plugin-services/README.md#zero-downtime-cutover): “Service handoffs are **best effort**.”
- **Home / owner:** Canonical `zdd` source contract; proposed Phase 1 controls in [Runtime Operational Contracts](../review-pipeline-operational-contracts/README.md), Active, do not transfer the library owner's scope.
- **Slice:** Normalize the two ordinary callback-failure paths identified in the source check above.
- **Exit:** Failed typed result, truthful breadcrumbs, unchanged serving predecessor and no flip/drain/demotion; preserve conditional behavior and canonical/shipped-copy consistency.
- **Open:** No outage or unsafe zero-daemon claim; partial-launch uncertainty and ordinary-versus-authorized-cold behavior remain distinct.

### [#918](https://github.com/ThomasMichon/copilot-extensions/issues/918) - P1; provisional
- **Outcome / rationale:** Authoritative ordinary roster/rendering needs installed load <2s and repaint <200ms plus keyboard/menu behavior; delivered foundations do not discharge that claim.
- **Vision:** [visions/plugins/agent-worktrees/README.md § schema-bounded-status-read](../../../visions/plugins/agent-worktrees/README.md#schema-bounded-status-read): “costs **O(state-schema)**”.
- **Home / owner:** [Process Slot Ownership](../process-slot-ownership/README.md), Active, is partial context; incumbent ordinary-roster/render custody is distinct from authority/feed, enrollment and churn owners.
- **Slice:** Join one bounded cached-render regression to the existing ordinary-roster claim.
- **Exit:** Representative installed load/repaint thresholds and keyboard/menu acceptance; score the unproved one-shot hang separately.
- **Open:** Exact whole effort home and installed evidence remain unverified; do not substitute a different writer/feed performance result.

### [#1012](https://github.com/ThomasMichon/copilot-extensions/issues/1012) - P2; provisional
- **Outcome / rationale:** Conditional form synchronization must follow actual visibility/events, not larger sleeps or weakened collected-value assertions.
- **Vision:** [visions/test-portfolio/README.md § cheapest faithful tier](../../../visions/test-portfolio/README.md#cheapest-faithful-tier): “A behavior is tested at the lowest-cost tier that can genuinely falsify it.”
- **Home / owner:** [Test Portfolio Rationalization](../test-portfolio-rationalization/README.md), Active, remains context only; the form/test owner and exact whole home are not reassigned.
- **Slice:** Replace one fixed wait with the real conditional-field boundary.
- **Exit:** Preserve collected values; slow-visible and never-visible cases have deterministic bounded outcomes.
- **Open:** This finite synchronization proposal was not promoted merely to increase researched coverage.

### [#1096](https://github.com/ThomasMichon/copilot-extensions/issues/1096) - P2; researched proposal
- **Outcome / rationale:** Preserve the assigned optional-coexistence/default-legacy keeper, including catalog/native tails, CMD dash handling, peer environments, remote source and simultaneous registration; optional cells must not block ordinary stabilization.
- **Vision:** [visions/plugin-services/installation-cells/README.md § coexistence-by-construction](../../../visions/plugin-services/installation-cells/README.md#coexistence-by-construction): “distinct by construction rather than by deployment convention”.
- **Home / owner:** [Marketplace-Scoped Installations](../marketplace-scoped-installations/README.md), Active; assigned cross-platform implementation/native custodians retain Phase 2/8 and inherited scope.
- **Slice:** Complete one already-owned attributable consumer-invocation tail.
- **Exit:** Intended installation wins simultaneous-registration, CMD and remote/cold controls; preserve runtime versus payload-only eligibility.
- **Open:** Native/all-copy/diagnostic and disposable-only activation holds remain; no default-legacy migration or ambient activation is authorized.

### [#1176](https://github.com/ThomasMichon/copilot-extensions/issues/1176) - P2; researched proposal
- **Outcome / rationale:** The complete required/optional runtime-plugin contract needs attributable structural enforcement, without making every #1096 behavior a prerequisite.
- **Vision:** [visions/plugin-services/README.md § uniform-deploy-contract](../../../visions/plugin-services/README.md#uniform-deploy-contract): “a single shared, vendored template realizes”.
- **Home / owner:** Proposed Phase 1/2 contract appendix in [Marketplace-Scoped Installations](../marketplace-scoped-installations/README.md), Active; the existing assigned runtime-contract owner remains.
- **Slice:** Reconcile the manifest/catalog/invocation/lifecycle inventory with one remaining conformance check.
- **Exit:** Fixture enforcement distinguishes required/optional surfaces, attributable declarations and payload-only exclusions.
- **Open:** Authoring delivery is credited but is not assignment release or proof of every installed consumer.

### [#1303](https://github.com/ThomasMichon/copilot-extensions/issues/1303) - P1; researched proposal
- **Outcome / rationale:** Retain the full effects/descendant/resource safety, static contract/provenance/cost/reliability/falsification census, every-run evidence and accountable periodic repair programme; a reported exact-node Windows timeout cannot justify lost assurance.
- **Vision:** [visions/test-portfolio/README.md § contract-complete portfolio](../../../visions/test-portfolio/README.md#contract-complete-portfolio): “Every critical behavior and invariant has deliberate positive, negative, and boundary coverage appropriate to its risk.”
- **Home / owner:** [Test Portfolio Rationalization](../test-portfolio-rationalization/README.md), Active; primary maintainer and existing audit/source/cohort/native/periodic/release owners retain every inherited family.
- **Slice:** Isolate repeated collection work under existing unit/portfolio owners, preserving admitted nodes and observers.
- **Exit:** Same node/source/hash closure, production-call coverage, phase/outcome evidence, zero prohibited effects and 300s cap.
- **Open:** Exact-function cohorts and observer refusals do not erase native/mock/order/history/unknown/companion/fixture obligations; general integration does not rejoin required release.

### [#1488](https://github.com/ThomasMichon/copilot-extensions/issues/1488) - P2; researched proposal
- **Outcome / rationale:** Allocator-owned terminal-worktree reclamation retains exact task/attempt/reservation identity, legitimate completion OR explicit abandonment and other-allocation holds; #9307 is bounded source credit.
- **Vision:** [visions/plugins/agent-dispatch/README.md § terminal-worktree-reclamation](../../../visions/plugins/agent-dispatch/README.md#terminal-worktree-reclamation): “owned obligation of the creating host and supervisor”.
- **Home / owner:** [Terminal Worktree Reclamation](../terminal-worktree-reclamation/README.md), Draft; Dispatch allocation and Worktrees finality maintainers retain their boundaries.
- **Slice:** Add the remaining two-tasks-one-worktree resolution/claim-release witness.
- **Exit:** One terminal task cannot release another allocation; stopped body and exact native identity precede ground-layer disposal.
- **Open:** Historical adoption, conservative cleanup and separate held-cleanup obligations remain; terminal metadata alone is not disposal authority.

### [#1742](https://github.com/ThomasMichon/copilot-extensions/issues/1742) - P2; provisional
- **Outcome / rationale:** Credit the Windows proxy-child/opaque-transport repair while preserving the contributor's unreleased recovery/no-wake/discovery/installer scope; the original is only a heading.
- **Vision:** [visions/session-hosting/README.md § capability-honest-control](../../../visions/session-hosting/README.md#capability-honest-control): “A provider advertises only the control it can guarantee.”
- **Home / owner:** [Windows Launch Hardening](../windows-launch-hardening/README.md), Active, is context, not a whole receiver; incumbent contributor/source custody remains.
- **Slice:** Recover the exact excluded scope from the existing attributable discussion before choosing a repair.
- **Exit:** Distinguish #2314's bounded delivery from still-owned recovery/discovery/installation obligations.
- **Open:** Missing original acceptance is not reconstructed from the title; no claimant expiry or new universal acknowledgement gate.

### [#1838](https://github.com/ThomasMichon/copilot-extensions/issues/1838) - P2; provisional
- **Outcome / rationale:** Recover the governed nested-build command/feed/claim boundary; an original containing only “Problem” cannot establish a fix specification.
- **Vision:** [visions/plugin-services/runtime-assembly/README.md § one-verified-acquisition-path](../../../visions/plugin-services/runtime-assembly/README.md#one-verified-acquisition-path): “one lookup, verification, and assembly path”.
- **Home / owner:** [Vendored Installer Engine](../vendored-installer-engine/README.md), Active, is context only; the existing source/claim owner must clarify the finite scope.
- **Slice:** Prepare an attributable reconstruction request, not an implementation.
- **Exit:** Finite command, selected build-isolation dependency sources and precedence are identified OR exact unavailable evidence is recorded.
- **Open:** No fabricated original, payload, accepted handoff or custody release.

### [#1886](https://github.com/ThomasMichon/copilot-extensions/issues/1886) - P1; provisional
- **Outcome / rationale:** Symmetric reference-safe uninstall needs declared stop/path/data/reference behavior, nonmutating planning and confirmed apply; stale selectors/wrappers can obstruct reconstruction.
- **Vision:** [visions/plugin-services/disposable-runtimes/README.md § uninstall-by-clear-storage-boundaries](../../../visions/plugin-services/disposable-runtimes/README.md#uninstall-by-clear-storage-boundaries): “Temporary state is distinguishable from durable authority”.
- **Home / owner:** [Installation-Independent Sessions](../installation-independent-sessions/README.md), Active, covers related prerequisites only; the independent uninstall/reference-accounting owner remains.
- **Slice:** Include current-version/last-known-good selectors and generated wrappers in the existing classification.
- **Exit:** Default plan makes no changes; confirmed supported uninstall de-registers owned tasks, normal update preserves registrations, and reconstruction retains configuration/data/connectivity.
- **Open:** Unknown/foreign references, partial failure, retry and reinstall remain protected; manual program removal is not supported-uninstall evidence.

### [#1943](https://github.com/ThomasMichon/copilot-extensions/issues/1943) - P2; researched proposal
- **Outcome / rationale:** Selected-cell cold launch must work with an empty inherited environment and direct headless ACP/stdio; the existing phased source claim is retained.
- **Vision:** [visions/plugin-services/installation-cells/README.md § cell-local-invocation](../../../visions/plugin-services/installation-cells/README.md#cell-local-invocation): “without depending on ownership of a bare, machine-global command name”.
- **Home / owner:** Proposed Phase 3 matrix in [Marketplace-Scoped Installations](../marketplace-scoped-installations/README.md), Active; incumbent cell-launch implementation/native owners remain.
- **Slice:** Qualify one minimal explicit cell/runtime/owned-command environment.
- **Exit:** Empty caller environment resolves exact cell paths, context and stdio, with platform-native evidence for operative use.
- **Open:** Reuse setup-owned Python; do not invent per-launch interpreter attestation, ambient command fallback or full-parent completion gates.

### [#2110](https://github.com/ThomasMichon/copilot-extensions/issues/2110) - P1; researched proposal
- **Outcome / rationale:** Aggregate normal-cadence overhead <=5% of one logical core AND independent real-event p95 <=2s are conjunctive; timer relaxation alone does not satisfy them.
- **Vision:** [visions/telemetry/README.md § bounded-fail-open-emission](../../../visions/telemetry/README.md#bounded-fail-open-emission): “Disabled emission is a cheap no-op.”
- **Home / owner:** [Producer Work Reuse](../producer-work-reuse/README.md) has a Draft header despite an Active index entry; the plan author owns planning, with execution left to existing source owners.
- **Slice:** Propose one source/configuration/lifetime-bound observation-reuse slice with comparable full-cadence measurement.
- **Exit:** Aggregate all retained lanes, preserve methods/cursors/unknown denominators, and meet BOTH CPU and real-event thresholds.
- **Open:** The plan-only reservation was released, not the functional outcome; index status is not implementation admission or a cadence waiver.

### [#2165](https://github.com/ThomasMichon/copilot-extensions/issues/2165) - P1; provisional
- **Outcome / rationale:** Write-time block OR flag must prevent checkout/dependency/toolchain/browser/raw-trace persistence while preserving small curated artifacts; sync exclusions alone are insufficient.
- **Vision:** Context only: [visions/plugins/agent-logger/README.md § fence-the-inputs-of-a-claimed-record](../../../visions/plugins/agent-logger/README.md#fence-the-inputs-of-a-claimed-record): “fences the exact session material it will record”.
- **Home / owner:** No exact whole effort home is verified; retain the original artifact-boundary owner rather than assigning it to chronicling by vocabulary.
- **Slice:** Define one prevention/flagging seam at the actual write boundary.
- **Exit:** Forbidden artifacts are blocked OR visibly flagged before replication; legitimate small curated artifacts survive.
- **Open:** The cited fencing feature does not wholly specify this policy. No claim that exclusions or later cleanup erased existing replicas.

### [#2423](https://github.com/ThomasMichon/copilot-extensions/issues/2423) - P1; researched proposal
- **Outcome / rationale:** Complete live state-machine integration needs actual launch accounting, transactional steer lease/application/ACK, uncertainty, terminal/card races and retained budget/review/dirty/health mappings.
- **Vision:** [visions/plugins/agent-dispatch/README.md § complete-means-done](../../../visions/plugins/agent-dispatch/README.md#complete-means-done): “not when a baton merely changed hands”.
- **Home / owner:** Proposed Phase 5/6/7 integration checklist in [Dispatch Task Lifecycle and Conversations](../agent-dispatch-monitor-and-confirmed-state/README.md), Active; backend, recipe and domain mapping owners remain distinct.
- **Slice:** Trace one existing steer-delivery race through lease, actual application, ACK and terminal/card concurrency.
- **Exit:** Resume hints do not count as launch attempts; ambiguous effects/lost ACK do not repeat work; deferred mappings remain explicit.
- **Open:** Independent confirmation and actual terminal resolution remain prerequisites only for their consumers, not a requirement to finish every state-machine feature first.

### [#2549](https://github.com/ThomasMichon/copilot-extensions/issues/2549) - P2; provisional
- **Outcome / rationale:** Raw SSH already works deliberately; the gap is a durable, scoped, flippable preference consumed consistently by launch/control/read.
- **Vision:** Contextual: [visions/plugins/agent-bridge/README.md § explicit-preference-authority-and-compatible-reuse](../../../visions/plugins/agent-bridge/README.md#explicit-preference-authority-and-compatible-reuse): “Requested, confirmed and unsupported choices remain distinguishable”.
- **Home / owner:** No exact whole effort home is verified; retain transport policy and authentication owners.
- **Slice:** Propose the smallest existing-settings preference/precedence amendment.
- **Exit:** A preference change affects the intended scope and all three consumers without bypassing authentication.
- **Open:** An execution-preference theme is not full transport-policy fit; uncertain carrier effects never justify parallel fallback/replay.

### [#2567](https://github.com/ThomasMichon/copilot-extensions/issues/2567) - P2; researched proposal
- **Outcome / rationale:** Evaluate shared core with hooks OR accepted separate removal executors; common terminology does not mandate consolidation.
- **Vision:** [visions/plugins/agent-worktrees/README.md § finalization-joins-durable-obligations](../../../visions/plugins/agent-worktrees/README.md#finalization-joins-durable-obligations): “Interactive process exit is neither necessary nor sufficient evidence of completion.”
- **Home / owner:** Proposed Phase 1/2 joint design annex in [Terminal Worktree Reclamation](../terminal-worktree-reclamation/README.md), Draft; Worktrees and Dispatch retain ordinary/managed/external responsibilities.
- **Slice:** Compare the existing executors against one explicit responsibility/finality contract.
- **Exit:** Either accepted alternative preserves late reacquisition, dirty/unmerged work, live processes, PR/effort/other-owner holds and failure/retry records.
- **Open:** Finality constrains an implementation, not parallel design research; no broad cleanup ownership is transferred.

### [#2612](https://github.com/ThomasMichon/copilot-extensions/issues/2612) - P2; provisional
- **Outcome / rationale:** Preserve current-mode plugin/service/engine/generation and source/controller mapping plus the #5768-specific custodian condition; the original is truncated.
- **Vision:** [visions/plugins/agent-index/README.md § released-version-controller-contract](../../../visions/plugins/agent-index/README.md#released-version-controller-contract): “Updates are serialized, verified and health-gated”.
- **Home / owner:** [agent-index Standalone Service](../agent-index-standalone-service/README.md), Active, is related context; original version/source and #5768 custody remain.
- **Slice:** Recover the finite retained mapping and exact existing acceptance condition.
- **Exit:** Supported current-mode identities are explicit OR missing evidence is named, without guessing the truncated original.
- **Open:** Do not replace the specific condition with a global owner-ACK requirement or invent a manifest repair from the title.

### [#2619](https://github.com/ThomasMichon/copilot-extensions/issues/2619) - P1; researched proposal
- **Outcome / rationale:** Mux/filesystem/lifecycle/direct-command events and transitive controller demand must drive affected-record incremental work, shared observations and a long correctness backstop.
- **Vision:** [visions/plugins/agent-worktrees/README.md § operation-triggered-recompute](../../../visions/plugins/agent-worktrees/README.md#operation-triggered-recompute): “signal the resident accelerator to recompute promptly”.
- **Home / owner:** [Lean Session Lifecycle](../lean-session-lifecycle/README.md), Draft; lifecycle and independently claimed producer/churn owners retain their slices.
- **Slice:** Implement or qualify one affected-record event path and shared cycle observation.
- **Exit:** One-of-1000 change avoids unrelated Git/history reconstruction; lost/reordered events and restart recover at the original evidence tier.
- **Open:** Reported timer changes delivered partial churn reduction, not whole scheduling. Do not invent an unconditional warm-reader wake cause or borrow another issue's SLO.

### [#2729](https://github.com/ThomasMichon/copilot-extensions/issues/2729) - P2; researched proposal
- **Outcome / rationale:** Named package diagnostics OR documented exclusions need actual interpreter/site/entrypoint provenance, safe editable detection and at least one automatic nonfatal detection path.
- **Vision:** [visions/installer/README.md § health-doctoring-and-validation](../../../visions/installer/README.md#health-doctoring-and-validation): “offers to repair”.
- **Home / owner:** Proposed Phase 2 diagnostic configuration point in [Vendored Installer Engine](../vendored-installer-engine/README.md), Active; engine/adopter owners retain scope.
- **Slice:** Add one automatic installer diagnostic route without modifying the environment.
- **Exit:** Named coverage OR exclusions, safe editables/shared interpreters, visible nonfatal detection and separately consented remediation.
- **Open:** Preserve intentional shared interpreters and package-specific exceptions; no per-launch signature/provenance service or implicit repair authority.

### [#2805](https://github.com/ThomasMichon/copilot-extensions/issues/2805) - P2; researched proposal
- **Outcome / rationale:** Retain the complete inherited #2730/#3382/#2614 programme: dedup-first coherent splits, ranked production/test/cross-language budgets, named paths and fair baseline/control attribution.
- **Vision:** [visions/harness-guidance/README.md § bounded-source-modules](../../../visions/harness-guidance/README.md#bounded-source-modules): “a module that has grown past a single cohesive responsibility”.
- **Home / owner:** [Module Componentization Discipline](../module-componentization-discipline/README.md), Active; effort and already-claimed module/consumer owners remain.
- **Slice:** Select one cohesive offender through the existing ranked, dedup-first process.
- **Exit:** Behavior and original qualified consumers survive; production, test and cross-language enforcement is attributable.
- **Open:** Guidance, mechanical file counts or a split alone do not prove enforcement or complete inherited scope.

### [#2877](https://github.com/ThomasMichon/copilot-extensions/issues/2877) - P2; researched proposal
- **Outcome / rationale:** Per-window/per-pool attempt/card/answer durations and churn rates must distinguish legitimate waits from repeated work.
- **Vision:** [visions/plugins/agent-dispatch/README.md § observable-lifecycle](../../../visions/plugins/agent-dispatch/README.md#observable-lifecycle): “state and structure only”.
- **Home / owner:** Proposed Phase 3 progress/telemetry amendment in [Dispatch Fleet Reliability and Recovery](../dispatch-fleet-reliability-recovery/README.md), Active; coordinator/supervision and telemetry owners remain.
- **Slice:** Add one optional content-free lifecycle rate/duration view through the existing seam.
- **Exit:** Known/unknown denominators, legitimate wait states and pool attribution are correct; sink failure remains bounded and fail-open.
- **Open:** Optional accounting never expires tasks, releases claims or becomes remediation authority.

### [#2965](https://github.com/ThomasMichon/copilot-extensions/issues/2965) - P2; provisional
- **Outcome / rationale:** Reconcile older audience-default effort text with newer per-contribution policy, retaining separate human chat/email acceptance instead of implementing stale policy.
- **Vision:** Context only: [visions/harness-guidance/README.md § portable-operator-policy](../../../visions/harness-guidance/README.md#portable-operator-policy): “without copying it into each repository”.
- **Home / owner:** [AI Attribution Audience Policy](../ai-attribution-audience-policy/README.md), Active; the existing policy driver retains the reconciliation.
- **Slice:** Align the remaining Plan/Validation with current contribution-aware rules and operator-only weakening.
- **Exit:** Opening/replying, human participation/bot-only replies, operator-only-host exceptions and human chat/email retain reviewed semantics.
- **Open:** This is below-altitude policy work with a real intent contradiction, not a dedicated vision or a completed behavior change.

### [#2974](https://github.com/ThomasMichon/copilot-extensions/issues/2974) - P3; provisional
- **Outcome / rationale:** Graceful exit versus interrupt depends on session state; whether an extension dispatches frontend slash commands is an independent capability question.
- **Vision:** [visions/session-hosting/README.md § capability-honest-control](../../../visions/session-hosting/README.md#capability-honest-control): “A provider advertises only the control it can guarantee.”
- **Home / owner:** No exact whole effort home is verified; retain session-exit and extension capability owners independently.
- **Slice:** Document one finite state-by-state comparison plus the separate slash-dispatch question.
- **Exit:** Idle/active states preserve fallback; additive-first OR deliberate replacement remains explicit.
- **Open:** Do not claim an unsupported frontend capability exists or execute an issue-supplied repro as planning.

### [#3303](https://github.com/ThomasMichon/copilot-extensions/issues/3303) - P1; researched proposal
- **Outcome / rationale:** Preserve manifest-derived default/all-plugin 20-successive-start functionality/latency, inherited PS5 first use and separate startup/hook/standalone-planning claims.
- **Vision:** [visions/harness-guidance/README.md § simple-local-static-delivery](../../../visions/harness-guidance/README.md#simple-local-static-delivery): “complete selected plugin set”.
- **Home / owner:** [Full-Harness Startup Reliability](../full-harness-startup-reliability/README.md), Active; PR-owning, clean-room and already-claimed hot-path owners retain scope.
- **Slice:** Continue one bounded existing hot-path slice, without taking the entire startup programme.
- **Exit:** Full-roster 20-start/functionality/latency and PS5 acceptance; standalone planning honors both opt-outs and background-owner OR remaining-deadline choice.
- **Open:** Distinguish complete-dispatch latency from model generation/TTFT; do not restore obsolete envelope admission or infer native proof from source.

### [#3336](https://github.com/ThomasMichon/copilot-extensions/issues/3336) - P1; researched proposal
- **Outcome / rationale:** Retain the whole generated Dev/Main pipeline, concurrent changefiles/no-churn/version clauses, frozen qualification/publication/tag/terminal lifecycle and throughput; release blockage has shared impact.
- **Vision:** [visions/release-pipeline/README.md § unit-only-release-test-gate](../../../visions/release-pipeline/README.md#unit-only-release-test-gate): “Promotion runs eligible unit tests and collects coverage once.”
- **Home / owner:** [Dev/Main Release Pipeline](../dev-branch-release-pipeline/README.md), Active Phases 7/9; existing release, regression-admission, helper and control owners remain.
- **Slice:** Continue one already-scoped exact-node collection or helper/source-coherence improvement.
- **Exit:** Preserve the eligible packet, coverage, 300s cap and exact evidence; separately verify source/artifact/publication identity and actual cadence.
- **Open:** The configured-feed construction/launch exception stays bounded; do not restore required general integration or require whole #1303 completion.

### [#3444](https://github.com/ThomasMichon/copilot-extensions/issues/3444) - P1; researched proposal
- **Outcome / rationale:** Preserve source/payload/three-wheel/selected-runtime/actual coordinator-supervisor fidelity and separate rejected-candidate causation; a healthy different process is insufficient.
- **Vision:** [visions/plugin-services/runtime-assembly/README.md § complete-or-rejected-generations](../../../visions/plugin-services/runtime-assembly/README.md#complete-or-rejected-generations): “An interrupted or partial candidate is never selected”.
- **Home / owner:** Independent #3444; proposed Phase 1/3 annex alongside [Immutable Dev Slot](../immutable-dev-slot/README.md), Active, retains publication/version, staging, construction/health, native and redrive custodians.
- **Slice:** Evidence/design work as audited above, not a blanket installer rewrite.
- **Exit:** Complete source-to-serving binding and all eight #3419/six #3594 clauses retain causal witness OR named tracked uncertainty, with previous-good state/data.
- **Open:** Corrected same-label three-wheel evidence does not establish exact publisher/fetch cause; current guards and unrelated partial fixes are not whole-case receipts.

### [#3654](https://github.com/ThomasMichon/copilot-extensions/issues/3654) - P1; provisional
- **Outcome / rationale:** Canonical-first source/version/fanout, uv authoring versus materialized deployment and local overrides must preserve root AND nested same-size/same-mtime freshness; archive credit does not settle cache semantics.
- **Vision:** [visions/release-pipeline/README.md § canonical-source-comparison](../../../visions/release-pipeline/README.md#canonical-source-comparison): “Producer and verifier share one declared canonical source representation.”
- **Home / owner:** [Source-Managed Runtime Installs](../source-managed-runtime-installs/README.md), Active, is context only; independent materialization/design and source-install/native owners remain.
- **Slice:** Qualify one actual root AND nested import-consumer freshness boundary without reviving retired pointer loaders.
- **Exit:** Current bytes OR explicitly reviewed ordinary-cache semantics; concurrent-reader safety and BOTH promotion and Manager/nested artifact filters remain.
- **Open:** Keep sanctioned mixed references/copies, intentional authoring editables and optional tiny dependency distinct; no cache-deletion shortcut or reopening archived Done rollout.

### [#3963](https://github.com/ThomasMichon/copilot-extensions/issues/3963) - P1; researched proposal
- **Outcome / rationale:** One logical role owner must retain actual coroutine/thread/native holders through cancellation/publication/provider epochs; cached rendering is partial credit.
- **Vision:** [visions/plugin-services/README.md § work-coalescing-singleton](../../../visions/plugin-services/README.md#work-coalescing-singleton): “Identical requests arriving close together **coalesce** into one execution”.
- **Home / owner:** [Process Slot Ownership](../process-slot-ownership/README.md), Active; proposed Continuations B/C/D refinement retains the process-ownership driver and separate registry/lifecycle owners.
- **Slice:** Qualify one exact-holder cancellation/publication race.
- **Exit:** One role/holder persists until actual completion; another waiter cannot release it; owner-end GC and separately authorized cold replacement remain distinct.
- **Open:** Work coalescing and live-claim protection are thematic fit, not generalized claim expiry, transfer of unrelated process cases or proof of installed adoption.

### [#4054](https://github.com/ThomasMichon/copilot-extensions/issues/4054) - P2; provisional
- **Outcome / rationale:** Watch OR explicit reload must update named-agent/default/alias configuration in the same running consumer; provider-catalog refresh is different.
- **Vision:** Contextual: [visions/plugins/agent-bridge/README.md § version-skew-safe-contract-evolution](../../../visions/plugins/agent-bridge/README.md#version-skew-safe-contract-evolution): “existing sessions keep the adapter and authority model they selected”.
- **Home / owner:** [Bridge Session Discovery](../agent-bridge-session-discovery/README.md), Draft, is context, not a verified whole reload home; configuration owners remain.
- **Slice:** Specify one watch OR explicit reload path through the existing configuration boundary.
- **Exit:** Add/change/remove reaches the same live consumer while sessions and last-known-valid state survive.
- **Open:** Discovery delivery does not satisfy static-configuration reload; exact whole effort mapping remains unverified.

### [#4057](https://github.com/ThomasMichon/copilot-extensions/issues/4057) - P1; researched proposal
- **Outcome / rationale:** Real harmless work and supported update races must expose duplicate-daemon/relay-conflict blast radius and uncertain effects; fake controls are not installed proof.
- **Vision:** [visions/test-portfolio/README.md § host-safe by default](../../../visions/test-portfolio/README.md#host-safe-by-default): “does not leave descendant processes, services, temporary state”.
- **Home / owner:** Proposed Phase 4/5 disposable/periodic family in [Test Portfolio Rationalization](../test-portfolio-rationalization/README.md), Active; existing rig and lifecycle owners retain execution/resources.
- **Slice:** Design one bounded actual-work concurrent-update witness.
- **Exit:** Preserve the original real-work/drain/uncertainty cases, measured conflict scope, identity-owned cleanup and recurring-tier enrollment.
- **Open:** Current cold-recovery intent does not authorize silent loss or falsely count interruption as a stronger case's pass; general integration remains outside required release.

### [#4190](https://github.com/ThomasMichon/copilot-extensions/issues/4190) - P3; provisional
- **Outcome / rationale:** Warning removal is already credited; retain only the original broadly-enough fixed-client deployment/owner condition.
- **Vision:** [visions/plugins/agent-bridge/README.md § version-skew-safe-contract-evolution](../../../visions/plugins/agent-bridge/README.md#version-skew-safe-contract-evolution): “retirement waits for evidence”.
- **Home / owner:** No exact whole effort home is verified; the original conditional-adoption owner remains.
- **Slice:** Reconcile the original finite deployment condition against existing authorized evidence.
- **Exit:** Establish original breadth/owner acceptance OR name the remaining missing evidence.
- **Open:** Do not restore the removed warning, launch another rollout campaign or invent an all-host gate.

### [#4195](https://github.com/ThomasMichon/copilot-extensions/issues/4195) - P1; researched proposal
- **Outcome / rationale:** Whole hook-family atomic helper admission, abandoned-owner recovery, caller-independent noninteractive lifetime and result publication exceed termination policy alone.
- **Vision:** [visions/plugin-services/README.md § self-provisioning-runtime](../../../visions/plugin-services/README.md#self-provisioning-runtime): “idempotent, version-keyed”.
- **Home / owner:** Proposed Phase 3/4b boundary checklist in [Source-Managed Runtime Installs](../source-managed-runtime-installs/README.md), Active; hook, installation, maintenance and execution owners remain.
- **Slice:** Add a bounded concurrent launch/publication/completion control to the actual hook path.
- **Exit:** One helper owner; exact birth/liveness-safe abandoned recovery; caller-independent windowless lifetime and explicit failure/completion.
- **Open:** Installer transaction locks do not themselves serialize helper creation; age, unreadable timestamp or a PID alone is not termination authority. No new controller umbrella.

### [#4231](https://github.com/ThomasMichon/copilot-extensions/issues/4231) - P2; provisional
- **Outcome / rationale:** A finite five-area planning ledger must cover process custody, turn continuity, cold diagnostics, crash isolation and durable connections, not implement every future area.
- **Vision:** [visions/agent-fabric/agentic-factory/worktree-stewardship/README.md § scoped-objectives-and-sequencing](../../../visions/agent-fabric/agentic-factory/worktree-stewardship/README.md#scoped-objectives-and-sequencing): “Related work names its task, effort, dependency and contribution relationships”.
- **Home / owner:** No exact whole effort home is verified; retain each area's established owner or explicit unresolved transfer.
- **Slice:** Reconcile the five retained areas against their statuses and direct evidence.
- **Exit:** Every area has status, evidence, dependency and owner OR a precisely named unresolved ownership question.
- **Open:** Planning order is not authority to assume all areas, replace the overview or release existing custody.

### [#4496](https://github.com/ThomasMichon/copilot-extensions/issues/4496) - P2; provisional
- **Outcome / rationale:** Production mux self-update/routing/adoption needs a finite rapid successive-update convergence witness; fake-daemon controls and archived delivery are not that witness.
- **Vision:** [visions/plugin-services/README.md § zero-downtime-cutover](../../../visions/plugin-services/README.md#zero-downtime-cutover): “Supersession is identity inequality”.
- **Home / owner:** [Versioned Singleton Manager](../versioned-singleton-manager/README.md), Active, is context only; incumbent mux/native owners retain the residual.
- **Slice:** Specify the real successive-update witness using production routing and adoption.
- **Exit:** Route/status/fallback, drain retirement and mappings converge across rapid updates under current graceful/cold policy.
- **Open:** Archived Done source delivery remains Done; exact whole home and current native qualification are not established.

### [#4547](https://github.com/ThomasMichon/copilot-extensions/issues/4547) - P1; provisional
- **Outcome / rationale:** Direct-slot/module-entrypoint, synchronous provisioning, partial-head/PR attribution, missing provider/entrypoint and forced-update variants need distinct causal dispositions.
- **Vision:** [visions/plugin-services/runtime-assembly/README.md § complete-or-rejected-generations](../../../visions/plugin-services/runtime-assembly/README.md#complete-or-rejected-generations): “An interrupted or partial candidate is never selected”.
- **Home / owner:** [Immutable Dev Slot](../immutable-dev-slot/README.md), Active, is context only; retain each original import/publication/construction owner.
- **Slice:** Select one already-observed variant and recover exact source/entrypoint/selected-slot identity.
- **Exit:** A causal witness OR named tracked uncertainty, with meaningful positive/negative control and immutable admission preserved.
- **Open:** No common truncation cause, force-update shortcut or whole-case completion from an unrelated admission fix.

### [#4783](https://github.com/ThomasMichon/copilot-extensions/issues/4783) - P2; researched proposal
- **Outcome / rationale:** Hosted permitted-command failure must distinguish allowlist denial from runtime refusal; local-lane success cannot establish the hosted boundary.
- **Vision:** [visions/ci-failure-remediation/README.md § bounded-fix-attempt](../../../visions/ci-failure-remediation/README.md#bounded-fix-attempt): “scoped to the diagnosed failure and nothing else”.
- **Home / owner:** [Promotion-Failure Reactive Fix Agent](../promotion-failure-reactive-fix-agent/README.md), Active Phase 2; driving and hosted-workflow owners retain separate fallback/guardrail duties.
- **Slice:** Classify the exact retained hosted command boundary and actual cause.
- **Exit:** A genuinely allowed hosted command succeeds OR the precise remaining platform limitation is attributed.
- **Open:** Nominal job success or a local substitute is insufficient; protected workflow/release guardrails are not weakened.

### [#5001](https://github.com/ThomasMichon/copilot-extensions/issues/5001) - P2; provisional
- **Outcome / rationale:** Gradual idle mux rotation needs per-client attribution OR bounded unknown compatibility, protected active/legacy routes, pinned requests and exact exit.
- **Vision:** [visions/plugin-services/README.md § interoperate-across-version-skew](../../../visions/plugin-services/README.md#interoperate-across-version-skew): “within their contract's declared tolerance”.
- **Home / owner:** [Versioned Singleton Manager](../versioned-singleton-manager/README.md), Active, is context, not a verified whole rotation home; design and implementation claims remain distinct.
- **Slice:** Complete the existing design decision for one eligible idle rotation with dry-run/explicit-apply semantics.
- **Exit:** Active/legacy clients, pinned requests, client mappings and actual retirement remain correct.
- **Open:** No PID/version-only retirement, new implementation consent or takeover of recoverable active work; current cold policy is not blanket reaping authority.

### [#5006](https://github.com/ThomasMichon/copilot-extensions/issues/5006) - P1; researched proposal
- **Outcome / rationale:** The independent shared birth-identity keeper still has the exact ordinary/unbound POSIX force fallback audited above; it is not every mux/Bridge consumer.
- **Vision:** [visions/plugin-services/README.md § attributed-daemon-instances](../../../visions/plugin-services/README.md#attributed-daemon-instances): “never guessed termination or deletion”.
- **Home / owner:** Proposed Phase C/D validation annex in [Versioned Singleton Manager](../versioned-singleton-manager/README.md), Active; shared keeper and individual consumer/native custodians remain.
- **Slice:** Supply force-capable identity-bound signaling through the actual false-group fallback, conditional on source-owner review.
- **Exit:** Matched birth requests the actual force signal; reused/mismatched/unknown birth refuses; group behavior, default compatibility and existing authority survive.
- **Open:** Credit normal caller and breadcrumb repairs; do not reopen them, silently use weaker SIGTERM or infer a current exploit/outage/native pass.

### [#5140](https://github.com/ThomasMichon/copilot-extensions/issues/5140) - P2; researched proposal
- **Outcome / rationale:** Retain all five original harness/termination cases: four corrected registry fixtures are credited, while the fifth actual matched-process case needs causal/validation disposition.
- **Vision:** [visions/test-portfolio/README.md § failures stay attributable](../../../visions/test-portfolio/README.md#failures-stay-attributable): “A failed test identifies the contract and tier that failed.”
- **Home / owner:** Proposed Phase 4 family accounting in [Test Portfolio Rationalization](../test-portfolio-rationalization/README.md), Active; fixture/source/platform owners remain.
- **Slice:** Isolate the fifth real matched-process assertion without reopening the corrected four.
- **Exit:** Five individually accounted cases preserve real-checkout/unknown assertions, original platform and causal result OR tracked uncertainty.
- **Open:** Current cause and native validation remain unproved; a source report or generic mismatch test does not settle the real matched case.

### [#5215](https://github.com/ThomasMichon/copilot-extensions/issues/5215) - P2; researched proposal
- **Outcome / rationale:** Actual self-emitted readiness/launch/degraded signals must exist during idle/no-command periods; HTTP health, start journals and session signals are not substitute producers.
- **Vision:** [visions/telemetry/README.md § continuous-executable-sink](../../../visions/telemetry/README.md#continuous-executable-sink): “continuous stream of structured JSON events through standard input”.
- **Home / owner:** Proposed Phase 6/7 producer amendment in [Performance Telemetry](../performance-telemetry/README.md), Active; the original readiness owner and bounded-signal/adoption owners remain.
- **Slice:** Add one optional bounded readiness producer through the existing telemetry seam.
- **Exit:** Idle and launch/degraded transitions are attributable, cheap and fail-open.
- **Open:** No per-event reporter process, mandatory sink, restart authority or source-as-installed acceptance.

### [#5319](https://github.com/ThomasMichon/copilot-extensions/issues/5319) - P1; researched proposal
- **Outcome / rationale:** The webhook producer owns a shared listener: readiness/routing/admission/drain and accepted-delivery custody cannot use an endpoint-free exemption or assume forge redelivery.
- **Vision:** [visions/plugin-services/README.md § zero-downtime-cutover](../../../visions/plugin-services/README.md#zero-downtime-cutover): “Service handoffs are **best effort**.”
- **Home / owner:** Proposed finite listener subsection in [Runtime Operational Contracts](../review-pipeline-operational-contracts/README.md), Active; the existing webhook producer owner retains implementation.
- **Slice:** Qualify one accepted delivery across the actual graceful transition boundary.
- **Exit:** Preserve explicit transfer/drain, no silent loss/duplicate admission and the original no-assumed-redelivery case.
- **Open:** Ordinary shutdown is not cross-generation proof. Reconcile this stronger finite case with current explicit cold recovery, not a universal no-interruption promise or silent waiver.

### [#5353](https://github.com/ThomasMichon/copilot-extensions/issues/5353) - P1; researched proposal
- **Outcome / rationale:** Nested create, binstub readiness and background reconcile can combine into unintended replacement; healthy exact-runtime shortcuts must remain.
- **Vision:** [visions/installer/README.md § single-installation-execution-owner](../../../visions/installer/README.md#single-installation-execution-owner): “one background execution owner”.
- **Home / owner:** Proposed Phase 4b case in [Source-Managed Runtime Installs](../source-managed-runtime-installs/README.md), Active; #7758 execution, nested-workspace and routing/adoption owners remain separate.
- **Slice:** Construct the finite combined trigger-admission race under existing custody.
- **Exit:** One legitimate acquisition owner, coherent routing/adoption and no unintended replacement; verify helper admission as well as installer serialization.
- **Open:** No live shared-daemon retry, disabled healthy shortcut or source-only result presented as native acceptance.

### [#5356](https://github.com/ThomasMichon/copilot-extensions/issues/5356) - P1; researched proposal
- **Outcome / rationale:** Qualification-only at the audited completed-slot boundary: the guard already encloses venv AND package mutation. Retain broader live-slot/backend-stall/liveness outcomes and released worker/attempt/watchdog credit.
- **Vision:** [visions/plugin-services/runtime-assembly/README.md § complete-or-rejected-generations](../../../visions/plugin-services/runtime-assembly/README.md#complete-or-rejected-generations): “An interrupted or partial candidate is never selected”.
- **Home / owner:** Proposed Phase 2 qualification ledger in [Vendored Installer Engine](../vendored-installer-engine/README.md), Active; bounded liveness, immutable-runtime and whole-installer anchoring owners retain their scopes.
- **Slice:** Qualify the actual normal Windows update with matching completed live content, conflicts and genuinely owned incomplete/abandoned work; do not port the guard again.
- **Exit:** Completed content is not mutated, conflicts refuse, worker outcomes are truthful and previous-good state/data survives.
- **Open:** No stop-first workaround or age-based retirement. Historical WMI/backend and missing exit diagnostics remain distinct; payload-loader friction is awareness-only, not a new fix mandate.

### [#5574](https://github.com/ThomasMichon/copilot-extensions/issues/5574) - P2; researched proposal
- **Outcome / rationale:** The selected Linux native-manager design deliberately uses exit 42 without child pre-spawn; actual restart tracking, not portable source resemblance, is the remaining proof.
- **Vision:** [visions/plugin-services/README.md § cutover-coherent-service-tracking](../../../visions/plugin-services/README.md#cutover-coherent-service-tracking): “reflects that handoff as **intentional**, never as a crash to retry”.
- **Home / owner:** [Versioned Singleton Manager](../versioned-singleton-manager/README.md), Active; shared implementation and Linux/native validation owners remain.
- **Slice:** Qualify the retained `Restart=on-failure` cycle.
- **Exit:** Real MainPID/restart accounting reaches the selected generation with actual exit-42 behavior.
- **Open:** Keep Windows/non-native behavior and #6137 attribution separate; a bare exit-code convention is not proof of overlapping-child tracking or installed adoption.

### [#6143](https://github.com/ThomasMichon/copilot-extensions/issues/6143) - P2; researched proposal
- **Outcome / rationale:** Preserve the optional exact-owner/configuration/deadline/drain/adoption/actual-exit protocol AND its separately scored ordinary Windows routing-lock bad-path case.
- **Vision:** [visions/plugin-services/README.md § zero-downtime-cutover](../../../visions/plugin-services/README.md#zero-downtime-cutover): “The whole lifecycle transition has **one mutation authority**”.
- **Home / owner:** Proposed Phase C/D adopter matrix in [Versioned Singleton Manager](../versioned-singleton-manager/README.md), Active; incumbent shared-cutover, #5655/#5754 and Worktrees snapshot owners remain.
- **Slice:** Qualify ordinary contention versus genuine bad-permission failure without reconstructing the missing original stack.
- **Exit:** Retry recognized contention only within one absolute acquisition deadline; arbitrary permission failures remain actionable; native configuration/identity/retirement adoption is separate.
- **Open:** No guessed byte-lock cause, larger transition budget or universal optional-protocol/first-install/reuse/cold-recovery veto; intentional state-compatible rollback is not version-order custody.

### [#6507](https://github.com/ThomasMichon/copilot-extensions/issues/6507) - P3; researched proposal
- **Outcome / rationale:** The resolver mechanism was retired; retain only the requested native full-suite evidence OR attributable refinement/disposition.
- **Vision:** [visions/test-portfolio/README.md § reductions preserve assurance](../../../visions/test-portfolio/README.md#reductions-preserve-assurance): “their integration tests need not block a release”.
- **Home / owner:** Proposed Phase 4/5 case disposition in [Test Portfolio Rationalization](../test-portfolio-rationalization/README.md), Active; original requester/source and bootstrap owners remain separate.
- **Slice:** Compare existing native evidence with the narrowed acceptance condition.
- **Exit:** Exact requested native evidence OR explicit attributed disposition at original scope.
- **Open:** Do not resurrect the resolver/subprocess mechanism or restore required native integration in release CI.

### [#6917](https://github.com/ThomasMichon/copilot-extensions/issues/6917) - P2; researched proposal
- **Outcome / rationale:** Prepared independent groups continue only ordinary assertion exit 1 with sticky failure; collection/usage/internal/interrupt/containment/resource errors must abort.
- **Vision:** [visions/test-portfolio/README.md § failures stay attributable](../../../visions/test-portfolio/README.md#failures-stay-attributable): “A failed test identifies the contract and tier that failed.”
- **Home / owner:** Proposed Phase 5 runner amendment in [Test Portfolio Rationalization](../test-portfolio-rationalization/README.md), Active; #6909 single-writer preparation custody remains.
- **Slice:** Wire existing group iteration to distinguish assertion failure from infrastructure/resource failure.
- **Exit:** Exit 1 continues independent groups with a final nonzero result; other named errors stop, within unchanged budgets.
- **Open:** This is the tiered-runner home, not a transfer of release-control ownership or a restoration of old mandatory native CI.

### [#6947](https://github.com/ThomasMichon/copilot-extensions/issues/6947) - P1; researched proposal
- **Outcome / rationale:** The normal Windows update must construct/cut over/read back BOTH Dispatch and required MCP with truthful partial outcomes; attribution/argv/PS5-stream repairs retain only their bounded credit.
- **Vision:** [visions/plugin-services/README.md § serving-state-reconciled-not-assumed](../../../visions/plugin-services/README.md#serving-state-reconciled-not-assumed): “what each authorized resident service is actually serving”.
- **Home / owner:** Proposed Phase 1 both-runtime case in [Runtime Operational Contracts](../review-pipeline-operational-contracts/README.md), Active; installed/native update owners and #7494 ARM64 construction remain separate.
- **Slice:** Expose the containing normal update's selected-versus-serving identities and actual readiness.
- **Exit:** Both required runtimes are ready at the qualified selected mode OR exact partial failure is reported.
- **Open:** No optional-MCP or feed-policy waiver, source-merge-as-rollout claim, or reassignment of the ARM64 construction cause.

## Interpretation and limits

The four audits establish two bounded source-supported defects, one qualification-only boundary and one design/evidence gap. They do not execute tests, inspect running installations or close whole issues. Public issue contracts, existing effort Plan/Validation obligations, finite alternatives and original source/resource owners remain authoritative.

This appendix neither selects a cross-area implementation sequence nor accepts any proposed home. The [stabilization roadmap](stabilization-roadmap.md) owns integration and review; the parent backlog and complete roadmap objectives remain open.
