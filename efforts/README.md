# Efforts — copilot-extensions

This tree holds active implementation campaigns for copilot-extensions. An
effort connects standing vision intent and GitHub issues to a phased plan,
participants, validation, and an append-only journal. The canonical lifecycle
and schema come from the `efforts:planning-efforts` skill; this page only binds
that pattern to this repository.

## Active effort index

| Effort | Status | Coordination |
|--------|--------|--------------|
| [Native Anchor Custody](active/native-anchor-custody/README.md) | Draft; explicit current-custody claims proposal, no live repair | #4488 bounded claims slice; preserves contributor-owned #6009 and broader lineage/handoff scope |
| [Bounded Passive Git Observation](active/bounded-passive-git-observation/README.md) | Draft; reviewed R1 deadline/R2 passive fsmonitor proposal, implementation admission pending | #7944/#7945; preserves roster/publication, resident-control/storage and broader #2619/#6086 ownership; no rollout |
| [Leased Daemon Instances](active/leased-daemon-instances/README.md) | Draft; reviewed shared launch, finite test lifetime and install/update cleanup contract before implementation | #8403; production cutover and existing custody remain authoritative; no rollout |
| [Worktree Launch Attempt Journal](active/worktree-launch-attempt-journal/README.md) | Draft; reviewed intent before implementation | #8375; protected 90-day prompts, attempt/outcome correlation, selected-machine inspection; preserves #6057 |
| [Repository Agent Exposure Policy](active/repository-agent-exposure-policy/README.md) | Active; implementation authorized, standing-intent review first | #8332; repository-owned hosting veto, source-only workflows and external Code Review preserved |
| [Ready Resident Prompt Receipt](active/ready-resident-prompt-receipt/README.md) | Active; delivery/control source slice, qualified backend and subject delivery remain gated | #7942; preserves receipt/roster owners and prohibits helper/lifecycle fallbacks |
| [Bridge Retained Runtime Restoration](active/bridge-retained-runtime-restoration/README.md) | Active; user-owned consumer/recovery refinement reviewed; consumer/SDK integration and full Phase 1 open | #8063; specializes #7268 without substituting for Dispatch/adopter acknowledgement; no rollout |
| [Actions Dialog Isolation](active/actions-dialog-isolation/README.md) | Done (source scope); configuration and caller-refusal #8286 merged after approval and required CI | #7940 source complete; #7939 release/installed and #7935 native gates remain |
| [Producer Work Reuse](active/producer-work-reuse/README.md) | Draft; code-grounded producer caching/batching/indexing plan only; execution admission pending | #7948/#7949/#7950 and retained #2110 declaration/health acceptance; no rollout |
| [Reconstructible Runtime State](active/reconstructible-runtime-state/README.md) | Active; G1/G3 source slices merged; bounded compatibility consumer proposal, no historical runtime execution | #7947; preserves #1460 compatibility (including consolidated #1915) and #3761 ground-state ownership; no support floor, live rebuild or rollout |
| [Prompt Receipt Observation](active/prompt-receipt-observation/README.md) | Draft; bounded async non-provisioning first-prompt receipt plan before implementation | #7942; unchanged consumption authority; no installed rollout |
| [Performance Telemetry](active/performance-telemetry/README.md) | Active; producer and optional OTLP source merged; native, structured-signal and rollout acceptance retained | #7935 / #5900; preserves #7938/#7943 production owners and #5215 readiness |
| [Namespace Discovery Latency](active/namespace-discovery-latency/README.md) | Active; preference slice merged, native latency still over budget; independent trusted-bootstrap design proposed, owner assignment and complete discovery/release/rollout open | #5761 latency slice; preserves #3963/#5559 owners and broader diagnostics |
| [Coordinated Launch Maintenance](active/coordinated-launch-maintenance/README.md) | Active; scheduling-only #7788 released; selected installed launcher evidence recorded, running acceptance open; broker redesign is separately owned | #7694; independent #6859/#7833; preserves #6812/#6820/#7667 owners |
| [SSH Reverse Candidate Forward](active/ssh-reverse-candidate-forward/README.md) | Active; reviewed intent merged; finite SSH owner candidate and lifecycle review corrections in progress | #7720; complements #7719 publication without a second selector; actual OpenSSH/adoption remain separate |
| [Backlog Reconciliation](active/backlog-reconciliation/README.md) | Active; oldest-first evidence and requirement-preserving consolidation, no implementation | #7556; 1,458 baseline, 729 guidance checkpoint |
| [Feed Construction Release Gate](active/feed-construction-release-gate/README.md) | Active; source-owner census, selected-extra constructor and structural result admission merged; authoritative native matrix and complete CI/promotion qualification open | #7529; preserves #3336/#6237 control and #7420/#7446/#7514 repair owners |
| [Acknowledged Execution Handoff](active/acknowledged-execution-handoff/README.md) | Active; source/test scope assigned, optional effect adapter construction first | #7268; coordinator integration and live adoption remain open |
| [Remote ACP Host Ownership](active/remote-acp-host-ownership/README.md) | Active; proposal and target-contribution prerequisite merged, qualified relationship claim integration next | #7237; existing session, claims and dispatch owners retained |
| [Agent Chat Graduation](active/agent-chat-graduation/README.md) | Active; canonical artifact/role construction merged; templated Chat container and Komodo amendment proposed; selected setup/native/lifecycle gates open | #7238; scoped Chat adapter changes, existing fleet/service/session-host owners retained |
| [Conventional Fleet Connectivity](active/conventional-fleet-connectivity/README.md) | Active; snapshot source merged; profile convergence, Bridge derivation and release/rollout acceptance remain | #7206 under #63; #7527 source complete; preserves identity, profile and listing owners |
| [Dispatch Extension Catalog](active/dispatch-extension-catalog/README.md) | Active; execution authorized, amended-plan review precedes templates, mapped sources and client semantic parity | #7091; preserves #6379/#4959/#6283 owners |
| [Trusted Linux CI Offload](active/trusted-linux-ci-offload/README.md) | Active; pure existing-row backend selector/tests implemented; CI integration after #7164 and qualification/activation pending | #7122; preserves #6237/#3336/#1303 owners |
| [Bridge Derived Execution Identity](active/bridge-derived-execution-identity/README.md) | Draft; reviewed derivation contract before implementation | #7069; preserves #6987/#5839/#7057 owners |
| [Venue Plugin Staging and Registration Parity](active/venue-plugin-staging-parity/README.md) | Active; source-preserving local CLI/ACP and owned startup gate merged; SSH/privileged, live-container and release acceptance open | #5286, #5072; sub-effort of #954 |
| [Source-Managed Runtime Installs](active/source-managed-runtime-installs/README.md) | Active; thirteen merged-source installers qualified; deployment policy/Manager integration open, recurring maintenance intent proposed | #7028; #3905, #7029, #7030; future plugin-source switch #7031 |
| [PR Lifecycle Conformance](active/pr-lifecycle-conformance/README.md) | Active; Phase 1 evidence and contract proposed, adapter implementation and acceptance open | #7051; existing #2357 lifecycle ownership retained |
| [Machine Alias Compatibility and Locality](active/machine-alias-compatibility-and-locality/README.md) | Active; shared identity readers merged; full producer implementation authorized with defaults/rollout gated; installed Windows/WSL acceptance open | #5674 · #6739; original identity repair retained, wider transport convergence remains separately owned |
| [Manual Task Session Resume](active/manual-task-session-resume/README.md) | Draft; manual exit pause and explicit local CLI/mux resume, planning only | #6677; existing lifecycle/recovery owners retained |
| [Artifact Publication Boundary Hardening](active/artifact-publication-boundary-hardening/README.md) | Blocked; proposal merged, containing primary repairs and attested hosted publication still required | #6639; #6535/#6632 owners retained |
| [Continuous Worktree Reclamation](active/continuous-worktree-reclamation/README.md) | Draft; vision and implementation/performance plan only, execution separately authorized | #6620; preserves #1488/#1918/#3114, #6060 and #6471 |
| [Installer Transaction Admission Repair](active/installer-transaction-admission-repair/README.md) | Done; actual reviewed repair and all four released protocols verified, pending archive; no host rollout | #6463 |
| [Seasoned Dependencies and Transactional Installs](active/seasoned-dependencies-and-transactional-installs/README.md) | Draft; seven-day declarations/resolution and failed-build cleanup | #6476 |
| [Dispatch Fleet Reliability and Recovery](active/dispatch-fleet-reliability-recovery/README.md) | Active; bounded supervision, exact recovery and retained maintenance acceptance | #6467, #6470, #6471 and Plan/Validation/Journal; #6466 is historical category work; existing #6407/#6421 repair owner retained |
| [Runtime Operational Contracts](active/review-pipeline-operational-contracts/README.md) | Active; proposal/prerequisites merged, activation/readiness/Windows contracts and adoption open | #6274; existing Windows regression #6266 |
| [Configuration Backstage and Worker Instances](active/configuration-backstage-and-worker-instances/README.md) | Active; proposal reviewed, section and instance contracts | #6379 |
| [Main-Owned Workflow Control](active/main-owned-workflow-control/README.md) | Active; shared consolidation roadmap and owner-only local-provider review rescue proposed, gated implementation open | #6237; canonical audit/coordination lead, existing release/runner/portfolio owners retained |
| [Fleet Control V2](active/fleet-control-v2/README.md) | Active; identity/admission/reader prerequisites merged; provider/enrollment/integration/lifecycle open | #6236, #6240, #6241, #6443, #5862; existing #3761/#352/#5789/#6030/#6424 owners retained |
| [Lean Runtime Dependencies](active/lean-runtime-dependencies/README.md) | Active; proposal reviewed, dependency reduction | #6234 |
| [Resilient Update Sequencing](active/resilient-update-sequencing/README.md) | Active; reviewed, historical command migration and cheap finishing first | #6820; complementary to #6812 |
| [Process Slot Ownership](active/process-slot-ownership/README.md) | Active; reader-lifetime continuation | #3963; registry presentation separately #5559 |
| [Process Spawn Reduction](active/process-spawn-reduction/README.md) | Active | #6086 |
| [Private PR Source Attribution](active/private-pr-source-attribution/README.md) | Active; originating identity slice | #6068 |
| [Remote Picker Seed Parity](active/remote-picker-seed-parity/README.md) | Active | #6057 |
| [Worktree Conclusion Tidier](active/worktree-conclusion-tidier/README.md) | Draft; audit capability released/installed, live pilot gated | #6060 |
| [Native Context Handoff Compatibility](active/native-context-handoff-compatibility/README.md) | Active; capability-contract investigation | #6028 |
| [Linux Role Fleet](active/linux-role-fleet/README.md) | Active; reviewed Komodo-first plan and isolated adoption proof | #6030, #5861 |
| [Gateway and SSH Fleet Onboarding](active/gateway-ssh-fleet-onboarding/README.md) | Active; bounded artifact-assembly source qualified and merged, proof/publication and ordered setup/adoption remain open | #6681 / #5861; closing source responsibility returns to tracked integration backlog |
| [Durable PR-Watch Delegation](active/durable-pr-watch-delegation/README.md) | Active; first-class PR service/fleet plan reviewed; runtime, owner and dispatch gates retained | #6007, #6286, #7188 |
| [Versioned Singleton Manager](active/versioned-singleton-manager/README.md) | Active | #5655 |
| [Status-monitor singleton correctness](active/status-monitor-singleton-correctness/README.md) | Draft; planning only, cold-start prerequisite satisfied | #5512 (restart), then #5453 (replacement and promotion/cutover) |
| [Adopter Material Refresh](active/adopter-material-refresh/README.md) | Active; primary documentation slice complete, preview/reference work unassigned | #5799 |
| [Machine Fleet Routing Foundation](active/machine-fleet-routing-foundation/README.md) | Active; contract implementation | #5789 |
| [Immutable Dev Slot](active/immutable-dev-slot/README.md) | Active; supersedes mutable/editable deployment; full admission/adopter and bidirectional-cutover acceptance retained | #5472; merged Dev deployment stays #7028/#7029 |
| [Machine Transport Convergence](active/machine-transport-convergence/README.md) | Active | #5737, #5738, #5740, #5741, #5972, #5973, #5974, #6039 |
| [agent-index Standalone Service](active/agent-index-standalone-service/README.md) | Active; fleet integration reviewed, container image slice | #5768, #5769, #6383 |
| [agent-dispatch Standalone + Container](active/agent-dispatch-standalone-container/README.md) | Active; Phase 1 landed | #6177 |
| [Dispatch Task Lifecycle and Conversations](active/agent-dispatch-monitor-and-confirmed-state/README.md) | Active; submission/output/delivery amendment proposed | #3681 |
| [Dispatch Tasks-Pane UX](active/agent-dispatch-tasks-pane-ux-overhaul/README.md) | Active; review/history/follow-up amendment proposed | #3681 (backend dependency) |
| [Lean Session Lifecycle](active/lean-session-lifecycle/README.md) | Draft | #5579, #5664, #2619 |
| [Session Intelligence and Accounting](active/session-intelligence-and-accounting/README.md) | Active | #5665 |
| [Operator Attention Contract ("what needs you")](active/operator-attention-contract/README.md) | Active (Phases 1-3 built; `bridge` + `pr` sources in review) | #5668 (core + `dispatch`, merged), #5813 (`bridge` + `pr` with their sibling commands); clients separately |
| [Supervised-Lane Process Discipline](active/supervised-lane-process-discipline/README.md) | Draft | #5301 |
| [Worker-Status Observability Hooks](active/worker-status-observability-hooks/README.md) | Draft | #5257 |
| [Compatibility-Root Decoupling](active/compatibility-root-decoupling/README.md) | Active; completed helper slices landed, remaining migration/census and full acceptance open | #5293 |
| [Retire Dead-Letter Status](active/retire-dead-letter-status/README.md) | Draft | #4744 |
| [ai-attribution Audience-Based Disclosure Policy](active/ai-attribution-audience-policy/README.md) | Active (Phase 1 done) | #2965 |
| [Devcontainer Test Isolation](active/devcontainer-test-isolation/README.md) | Done; pending archive | #5040 |
| [agent-dispatch Recipe Composability](active/agent-dispatch-recipe-composability/README.md) | Active | #4959 |
| [Configurable token-command sourcing](active/token-command-sourcing/README.md) | Draft | _pending_ |
| [CI Identifier Leak Guard](active/ci-identifier-leak-guard/README.md) | Active | #3923 |
| [CI Reliability & Flakiness Telemetry](active/ci-flakiness-telemetry-and-reliability/README.md) | Active | _pending_ |
| [Worktree/Effort Railroad Binding](active/worktree-effort-railroad-binding/README.md) | Draft | #3581 |
| [Worktree Head-Succession Hardening](active/worktree-head-succession-hardening/README.md) | Draft | #3584 |
| [Picker Creature Comforts](active/picker-creature-comforts/README.md) | Draft | #3586 |
| [Dev/Main Release Pipeline](active/dev-branch-release-pipeline/README.md) | Active (Phases 7 and 9); first contributor-regression cohort released: 322 units, natural 12m12s cycle | #3336 |
| [Promotion-Failure Reactive Fix Agent](active/promotion-failure-reactive-fix-agent/README.md) | Active | _TBD_ |
| [Agent-Process Self-Report Registry](active/agent-process-self-report-registry/README.md) | Draft | #5559 |
| [Full-Harness Startup Reliability](active/full-harness-startup-reliability/README.md) | Active | #3303 |
| [Unified Skill Review](active/unified-skill-review/README.md) | Draft | #2847 |
| [Handoff Cutover Lifecycle Journal](active/handoff-cutover-lifecycle-journal/README.md) | Active; complete retained acceptance, implementation/native gates open | Plan/Validation/Journal and open bounded-source issues; #2457 is source provenance after reviewed complete intake |
| [Handoff Live Cutover](active/handoff-live-cutover/README.md) | Active | #2249† |
| [Handoff Cutover Reload Robustness](active/handoff-cutover-reload-robustness/README.md) | Active | #5250† |
| [Context-Handoff Overhaul](active/context-handoff-overhaul/README.md) | Active; complete retained acceptance, implementation/native gates open | Plan/Validation/Journal and open bounded-source issues; #2594 is source provenance after reviewed complete intake |
| [Custom Context Aggregator Retirement](active/custom-context-aggregator-retirement/README.md) | Draft | #2173 |
| [SessionStart Static/Dynamic Content Conformance](active/sessionstart-static-dynamic-conformance/README.md) | Active | #2256 |
| [agent-logger Aggregate Configuration](active/agent-logger-aggregate-configuration/README.md) | Active; complete aggregate authorization and adoption acceptance retained | Plan/Validation/Journal plus open bounded source issues; #1817 is historical after complete reviewed intake |
| [Balanced Profile Assignment](active/balanced-profile-assignment/README.md) | Active; implementation merged, deployment acceptance unverified; default-off semantics retained | #1564; usage/deprecation decision remains #4871 |
| [Budget-Aware Model Routing](active/budget-aware-model-routing/README.md) | Active; posture, session delivery, routing, and personal provider reports implemented; entitlement/account proof and adoption open | Plan/Validation/Journal plus open bounded source issues; historical #2137 after reviewed intake |
| [Evidence-Calibrated Model Routing](active/evidence-calibrated-model-routing/README.md) | Active | Plan/Validation/Journal plus open bounded source issues; historical #2014 after reviewed intake |
| [agent-bridge Contract Evolution](active/agent-bridge-contract-evolution/README.md) | Draft | #1460 |
| [agent-bridge AHP Convergence](active/agent-bridge-ahp-convergence/README.md) | Draft | #1266, #1308 |
| [agent-bridge Delegation Convergence](active/agent-bridge-delegation-convergence/README.md) | Active | #1448 original; reviewed Plan/Validation/Journal with independent source claims and assigned #1266 preserved |
| [agent-bridge Session Discovery](active/agent-bridge-session-discovery/README.md) | Draft | #2530 |
| [agent-bridge CLI-Mode Sessions](active/agent-bridge-cli-mode-sessions/README.md) | Active | See effort |
| [Proposed Alignment/Convergence of Observable agent-bridge CLI Sessions](active/agent-bridge-cli-session-alignment/README.md) | Active | See effort |
| [CLI-Default Bridging](active/cli-default-bridging/README.md) | Active; Phase 6 research complete, SDK prompt fix merged to dev, arbitration next | #6647, #5930 |
| [Migration Intake](active/migration-intake/README.md) | Draft | See effort |
| [Account-Aware Operations](active/account-aware-operations/README.md) | Draft | See effort |
| [Agent Machines Declarative Control Plane](active/agent-machines-declarative-control-plane/README.md) | Active | #1418 historical; retained reset/prelaunch use reviewed Plan/Validation/Journal and open per-slice claims |
| [Review Automation Reliability](active/review-automation-reliability/README.md) | Draft | See effort |
| [Marketplace-Scoped Installations](active/marketplace-scoped-installations/README.md) | Active | #1096 |
| [Native-Construct Convergence](active/native-construct-convergence/README.md) | Active | #985 |
| [Tiered Payload Provisioning](active/tiered-payload-provisioning/README.md) | Draft | See effort |
| [Governed Python Artifact Promotion](active/governed-python-artifact-promotion/README.md) | Active | #4876 |
| [Progressive Context Disclosure](active/progressive-context-disclosure/README.md) | Active | #1612 original; reviewed Plan/Validation/Journal, unchanged lanes and open bounded-source claims |
| [Restricted Venue Targets](active/restricted-venue-targets/README.md) | Draft | #1188 |
| [Test Portfolio Rationalization](active/test-portfolio-rationalization/README.md) | Active | #1303 |
| [Terminal Worktree Reclamation](active/terminal-worktree-reclamation/README.md) | Draft | #1488 |
| [Venue Parity](active/venue-parity/README.md) | Active | #954 |
| [Windows Launch Hardening](active/windows-launch-hardening/README.md) | Active | #786 |
| [Worktree Manager Control Plane](active/worktree-manager-control-plane/README.md) | Active | #352 |
| [agent-index Engine Daemon](active/agent-index-engine-daemon/README.md) | Active | See effort |
| [Vendored Installer Engine](active/vendored-installer-engine/README.md) | Active | See effort |
| [Module Componentization Discipline](active/module-componentization-discipline/README.md) | Active | #2805 |
| [Componentization Campaign Auto-Worker](active/componentization-campaign-auto-worker/README.md) | Draft | #3372 |
| [Authoritative Write-Through Daemon](active/agent-worktrees-authoritative-daemon/README.md) | Active | #3761 |
| [agent-dispatch Worker Operating Procedures](active/agent-dispatch-worker-operating-procedures/README.md) | Done; pending archive | #3897 |
| [Mux Companion Manual Cutover Diagnostics](active/mux-companion-manual-cutover-diagnostics/README.md) | Active | #4369 |
| [Coverage-Guided CI Test Selection](active/coverage-guided-ci/README.md) | Draft; proposed Windows PR waves, complete native qualification remains open | #4453 |
| [Launch-Time Model/Effort/Context Preference Flags](active/launch-time-model-preference-flags/README.md) | Done; pending archive | #4776 |
| [Picker New-Session Prompt + Registered-Pivot Composer](active/picker-new-session-prompt-and-composer/README.md) | Done; pending archive | See effort |
| [Per-Worktree Activity State](active/per-worktree-activity-state/README.md) | Active | #5664 |
| [Persistent Installer Diagnostics](active/persistent-installer-diagnostics/README.md) | Draft; durable native attempt logs and non-blocking upstream coordination | #8370; independent diagnostics delivery, separately qualified #6859 continuation |
| [Update Flow Observability](active/update-flow-observability/README.md) | Active; reviewed audit, accurate outcomes and machine-result framing next | #6812; #6826, #6827; existing installer repair owners retained |
| [Native Target Preference Authority](active/native-target-preference-authority/README.md) | Active; proposal merged, native adapters and Windows/Linux execution proof before inheritance remain open | #5708; preserves #7069/#6987/#5839/#7057 owners |


## Completed effort index

| Effort | Outcome | Coordination |
|--------|---------|--------------|
| [Native Mux Launch Contract](2026/10/23%20native-mux-launch-contract/README.md) | Done for bounded source delivery; terminal custody, connected status and selected-directory repairs merged in #8031 | #7962 retains human/native qualification, release and authorized rollout; no installed changes |
| [Generation-Bound Guidance](2026/10/23%20generation-bound-guidance/README.md) | Done for bounded source delivery; generation reuse, protected partial publication and late notices merged in #8045 | #7943 retains full native entry, cold-bootstrap, release and installed acceptance; no rollout |
| [Telemetry Measurement Contract](2026/10/22%20telemetry-measurement-contract/README.md) | Done; QoS/Engagement contract, 58-entry catalog and future instrumentation plan merged; no runtime adoption | #8102; existing Performance Telemetry and production owners retain implementation/rollout |
| [Picker Pending Binding](2026/10/22%20picker-pending-binding/README.md) | Done for the admitted source increments; consumer and config-dir producer merged, two scalar queries retained | #7938 remains open for full cold/native, remote, release and installed acceptance |
| [Dynamic Reverse Reservations](2026/10/22%20dynamic-reverse-reservations/README.md) | Done; bounded coordinator/finite-forward composition and isolated SSH consumers merged; no rollout | #7719; remaining production integration explicitly transferred to #7238, source custodians retained |
| [CodeSpaces Authentication CTA](2026/10/22%20codespaces-authentication-cta/README.md) | Done; reviewed provider/Manager source merged, no native authorization or rollout | #7793 retains native acceptance and rollout; #4180 remains separate |
| [RedirectionGuard Adoption](2026/10/20%20redirectionguard-adoption/README.md) | Done; shared interpreter and failed-front-door source repairs merged to Dev and qualified; no rollout | #7445; publication and installed-consumer recovery retained by #7305, construction by #7420 |
| [Gateway Native Qualification](2026/10/20%20gateway-native-qualification/README.md) | Done; explicit native selection and actual exact-source amd64/ARM64 assembled proofs merged, private evidence/images retained; no rollout | #7257 retains provider/live-edge gates; authentication, lifecycle and adoption owners unchanged |
| [ADO PR Stewardship](2026/10/20%20ado-pr-stewardship/README.md) | Done; central ADO evidence/configuration/owner integration released, installed worker policy proved, consumer tool forks retired | #7203; separate #3336/#6007/#7051 obligations retained |
| [Gateway Role Tunnels](2026/10/19%20gateway-role-tunnels/README.md) | Done; reusable runtime/image and generic ingress merged, native amd64 isolated proof and readiness record; no rollout | #7257 retains native ARM64/live edge/provider gates; broader #6236/#6030 remain open |
| [Remote ACP Provider Contributions](2026/10/20%20remote-acp-provider-contributions/README.md) | Done; generic target/scoped-auth source merged to dev, pending release; no rollout | #7218; host/adopter acceptance #7237, release #3336 |
| [Maintenance-Safe Module Opt-In](2026/10/19%20maintenance-safe-module-optin/README.md) | Done; strict default-off consent and bounded native fixtures merged to dev, pending release; no rollout | #7213; residual reliability #7247 |
| [Remote ACP Client Transport](2026/10/16%20remote-acp-client-transport/README.md) | Done; runtime merged to dev, pending release, no rollout | #6908; runtime #6997 |
| [Bridge Workspace Owner Boundary](2026/10/16%20bridge-workspace-owner-boundary/README.md) | Done; released owner-process repair and preserved managed-session resume verified | #6757; #6776 |
| [Workspace Acquisition Continuity](2026/10/13%20workspace-acquisition-continuity/README.md) | Done; runtime merged to dev, main publication unconfirmed, no host rollout | #6440; automatic promotion remains #3336 |
| [agent-bridge Attention Waits](2026/09/03%20agent-bridge-attention-waits/README.md) | Done; archived | #1450 |
| [Plugin Process Hygiene](2026/09/13%20plugin-process-hygiene/README.md) | Done; archived | #736 |
| [agent-dispatch Workers Configuration Section](2026/10/12%20agent-dispatch-workers-config-section/README.md) | Done; local controls and native override proof landed | #6282; remote UI/validation transferred to #6283 |
| [PR Recovery Break Glass](2026/10/12%20pr-recovery-break-glass/README.md) | Done; released recovery demonstrated | #5817 |
| [Mux Child Window Suppression](2026/10/12%20mux-child-window-suppression/README.md) | Done; published repair, verified reachable rollout, remaining adopter verification transferred | #6040 |
| [Vision Backport Sweep](2026/10/09%20vision-backport-sweep/README.md) | Done; 35-vision source reconciliation, implementation owners retained | #5456 |
| [Mutable dev slot](2026/10/22%20mutable-dev-slot/README.md) | Done; mutable/editable premise abandoned, not portfolio delivered | [Immutable Dev Slot](active/immutable-dev-slot/README.md), #5472; direct merged Dev deployment #7028/#7029 |

## Local conventions

### Grouping and archive layout

- Active efforts use the flat layout `efforts/active/<slug>/`.
- Completed efforts archive to `efforts/<YYYY>/MM/DD <slug>/`.
- `efforts/TEMPLATE.md` is the local starting template.

### Participants and coordination

The `Participants` seam may name operating-system agents, worktrees, branches,
or other execution venues. Record only generic, public-safe identities and how
the participant is reached. Multi-agent efforts must name the branch topology,
the PR owner, slice ownership, and handoff rule in `## Coordination`.

### Issues and sources

- GitHub issues in `ThomasMichon/copilot-extensions` are the discrete tracking
  and public coordination tokens. Claim the relevant issue before beginning a
  stretch.
- Efforts are carved from vision-to-reality deltas, existing GitHub issues, and
  resumable plans under `docs/plans/`.
- Same-repository issue references may use `#NNN`; references to other
  repositories must be fully qualified.

### Cross-repo placement and sequencing

This repository hosts efforts for subjects implemented primarily in
copilot-extensions. A downstream or private driver may maintain a linked
elaboration, but the public effort remains generic and canonical for upstream
implementation. The equivalent cross-repo sequencing rule in `AGENTS.md`
requires reviewed upstream intent to land before any unreviewed downstream
publication; only completion markers follow implementation.

### Section set

Use the canonical template section set without renaming. Extract substantial
phase designs or inventories into sibling documents and link them from the
effort README rather than allowing the shared coordination document to sprawl.
