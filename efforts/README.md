# Efforts — copilot-extensions

This tree holds active implementation campaigns for copilot-extensions. An
effort connects standing vision intent and GitHub issues to a phased plan,
participants, validation, and an append-only journal. The canonical lifecycle
and schema come from the `efforts:planning-efforts` skill; this page only binds
that pattern to this repository.

## Active effort index

| Effort | Status | Coordination |
|--------|--------|--------------|
| [Machine Alias Compatibility and Locality](active/machine-alias-compatibility-and-locality/README.md) | Active; retroactive capture, alias matrix and installed Windows/WSL acceptance open | #5674; original identity repair retained, wider transport convergence remains separately owned |
| [Bridge Workspace Owner Boundary](active/bridge-workspace-owner-boundary/README.md) | Active; local repair, coordinator review/publication and released-source proof pending | #6757 |
| [Manual Task Session Resume](active/manual-task-session-resume/README.md) | Draft; manual exit pause and explicit local CLI/mux resume, planning only | #6677; existing lifecycle/recovery owners retained |
| [Artifact Publication Boundary Hardening](active/artifact-publication-boundary-hardening/README.md) | Draft; follow-up gated on primary repairs and hosted publication | #6639; #6535/#6632 owners retained |
| [Continuous Worktree Reclamation](active/continuous-worktree-reclamation/README.md) | Draft; vision and implementation/performance plan only, execution separately authorized | #6620; preserves #1488/#1918/#3114, #6060 and #6471 |
| [Seasoned Dependencies and Transactional Installs](active/seasoned-dependencies-and-transactional-installs/README.md) | Draft; seven-day declarations/resolution and failed-build cleanup | #6476 |
| [Dispatch Fleet Reliability and Recovery](active/dispatch-fleet-reliability-recovery/README.md) | Active; maintenance categories, bounded supervision and registry-rooted recovery | #6466, #6467, #6470, #6471; existing #6407/#6421 repair owner retained |
| [Runtime Operational Contracts](active/review-pipeline-operational-contracts/README.md) | Draft; proposal review before implementation | #6274; existing Windows regression #6266 |
| [Configuration Backstage and Worker Instances](active/configuration-backstage-and-worker-instances/README.md) | Active; proposal reviewed, section and instance contracts | #6379 |
| [Main-Owned Workflow Control](active/main-owned-workflow-control/README.md) | Active; first wave live, validation consolidation open | #6237; run/check consolidation primary, ownership enabling |
| [Fleet Control V2](active/fleet-control-v2/README.md) | Active; isolated runtime identity verifier in review; policy/integration/lifecycle open | #6236, #6240, #6241, #6443, #5862; existing #3761/#352/#5789/#6030/#6424 owners retained |
| [Lean Runtime Dependencies](active/lean-runtime-dependencies/README.md) | Active; proposal reviewed, dependency reduction | #6234 |
| [Resilient Update Sequencing](active/resilient-update-sequencing/README.md) | Draft; historical command migration and cheap finishing first | #6820; complementary to #6812 |
| [Process Slot Ownership](active/process-slot-ownership/README.md) | Active; reader-lifetime continuation | #3963; registry presentation separately #5559 |
| [Process Spawn Reduction](active/process-spawn-reduction/README.md) | Active | #6086 |
| [Private PR Source Attribution](active/private-pr-source-attribution/README.md) | Active; originating identity slice | #6068 |
| [Remote Picker Seed Parity](active/remote-picker-seed-parity/README.md) | Active | #6057 |
| [Worktree Conclusion Tidier](active/worktree-conclusion-tidier/README.md) | Draft; audit capability released/installed, live pilot gated | #6060 |
| [Native Context Handoff Compatibility](active/native-context-handoff-compatibility/README.md) | Active; capability-contract investigation | #6028 |
| [Linux Role Fleet](active/linux-role-fleet/README.md) | Active; reviewed Komodo-first plan and isolated adoption proof | #6030, #5861 |
| [Durable PR-Watch Delegation](active/durable-pr-watch-delegation/README.md) | Active; proposal reviewed | #6007 |
| [Versioned Singleton Manager](active/versioned-singleton-manager/README.md) | Active | #5655 |
| [Status-monitor singleton correctness](active/status-monitor-singleton-correctness/README.md) | Draft; planning only, cold-start prerequisite satisfied | #5512 (restart), then #5453 (replacement and promotion/cutover) |
| [Adopter Material Refresh](active/adopter-material-refresh/README.md) | Active; primary documentation slice complete, preview/reference work unassigned | #5799 |
| [Machine Fleet Routing Foundation](active/machine-fleet-routing-foundation/README.md) | Active; contract implementation | #5789 |
| [Mutable dev slot](active/mutable-dev-slot/README.md) | Active | #5472 |
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
| [Compatibility-Root Decoupling](active/compatibility-root-decoupling/README.md) | Draft | #5293 |
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
| [Dev/Main Release Pipeline](active/dev-branch-release-pipeline/README.md) | Active (Phases 7 and 8) | #3336 |
| [Promotion-Failure Reactive Fix Agent](active/promotion-failure-reactive-fix-agent/README.md) | Active | _TBD_ |
| [Agent-Process Self-Report Registry](active/agent-process-self-report-registry/README.md) | Draft | #5559 |
| [Full-Harness Startup Reliability](active/full-harness-startup-reliability/README.md) | Active | #3303 |
| [Unified Skill Review](active/unified-skill-review/README.md) | Draft | #2847 |
| [Handoff Cutover Lifecycle Journal](active/handoff-cutover-lifecycle-journal/README.md) | Draft | #2457 |
| [Handoff Live Cutover](active/handoff-live-cutover/README.md) | Active | #2249† |
| [Handoff Cutover Reload Robustness](active/handoff-cutover-reload-robustness/README.md) | Active | #5250† |
| [Context-Handoff Overhaul](active/context-handoff-overhaul/README.md) | Draft | #2594 |
| [Custom Context Aggregator Retirement](active/custom-context-aggregator-retirement/README.md) | Draft | #2173 |
| [SessionStart Static/Dynamic Content Conformance](active/sessionstart-static-dynamic-conformance/README.md) | Active | #2256 |
| [agent-logger Aggregate Configuration](active/agent-logger-aggregate-configuration/README.md) | Active | #1817 |
| [Balanced Profile Assignment](active/balanced-profile-assignment/README.md) | Active | #1564 |
| [Budget-Aware Model Routing](active/budget-aware-model-routing/README.md) | Active; posture, session delivery, routing, and personal provider reports implemented; entitlement/account proof and adoption open | #2137 |
| [Evidence-Calibrated Model Routing](active/evidence-calibrated-model-routing/README.md) | Active | #2014 |
| [agent-bridge Contract Evolution](active/agent-bridge-contract-evolution/README.md) | Draft | #1460 |
| [agent-bridge AHP Convergence](active/agent-bridge-ahp-convergence/README.md) | Draft | #1266, #1308 |
| [agent-bridge Delegation Convergence](active/agent-bridge-delegation-convergence/README.md) | Active | #1448 |
| [agent-bridge Session Discovery](active/agent-bridge-session-discovery/README.md) | Draft | #2530 |
| [agent-bridge CLI-Mode Sessions](active/agent-bridge-cli-mode-sessions/README.md) | Active | See effort |
| [Proposed Alignment/Convergence of Observable agent-bridge CLI Sessions](active/agent-bridge-cli-session-alignment/README.md) | Active | See effort |
| [CLI-Default Bridging](active/cli-default-bridging/README.md) | Active; Phase 6 research complete, SDK prompt fix merged to dev, arbitration next | #6647, #5930 |
| [Migration Intake](active/migration-intake/README.md) | Draft | See effort |
| [Account-Aware Operations](active/account-aware-operations/README.md) | Draft | See effort |
| [Agent Machines Declarative Control Plane](active/agent-machines-declarative-control-plane/README.md) | Active | #1418 (closed; historical, no live umbrella) |
| [Review Automation Reliability](active/review-automation-reliability/README.md) | Draft | See effort |
| [Marketplace-Scoped Installations](active/marketplace-scoped-installations/README.md) | Active | #1096 |
| [Native-Construct Convergence](active/native-construct-convergence/README.md) | Active | #985 |
| [Tiered Payload Provisioning](active/tiered-payload-provisioning/README.md) | Draft | See effort |
| [Governed Python Artifact Promotion](active/governed-python-artifact-promotion/README.md) | Active | #4876 |
| [Progressive Context Disclosure](active/progressive-context-disclosure/README.md) | Active | #1612 |
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
| [Coverage-Guided CI Test Selection](active/coverage-guided-ci/README.md) | Draft | #4453 |
| [Launch-Time Model/Effort/Context Preference Flags](active/launch-time-model-preference-flags/README.md) | Done; pending archive | #4776 |
| [Picker New-Session Prompt + Registered-Pivot Composer](active/picker-new-session-prompt-and-composer/README.md) | Done; pending archive | See effort |
| [Per-Worktree Activity State](active/per-worktree-activity-state/README.md) | Active | #5664 |
| [Update Flow Observability](active/update-flow-observability/README.md) | Active; reviewed audit, accurate outcomes and machine-result framing next | #6812; #6826, #6827; existing installer repair owners retained |


## Completed effort index

| Effort | Outcome | Coordination |
|--------|---------|--------------|
| [Workspace Acquisition Continuity](2026/10/13%20workspace-acquisition-continuity/README.md) | Done; runtime merged to dev, main publication unconfirmed, no host rollout | #6440; automatic promotion remains #3336 |
| [agent-bridge Attention Waits](2026/09/03%20agent-bridge-attention-waits/README.md) | Done; archived | #1450 |
| [Plugin Process Hygiene](2026/09/13%20plugin-process-hygiene/README.md) | Done; archived | #736 |
| [agent-dispatch Workers Configuration Section](2026/10/12%20agent-dispatch-workers-config-section/README.md) | Done; local controls and native override proof landed | #6282; remote UI/validation transferred to #6283 |
| [PR Recovery Break Glass](2026/10/12%20pr-recovery-break-glass/README.md) | Done; released recovery demonstrated | #5817 |
| [Mux Child Window Suppression](2026/10/12%20mux-child-window-suppression/README.md) | Done; published repair, verified reachable rollout, remaining adopter verification transferred | #6040 |
| [Vision Backport Sweep](2026/10/09%20vision-backport-sweep/README.md) | Done; 35-vision source reconciliation, implementation owners retained | #5456 |

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
