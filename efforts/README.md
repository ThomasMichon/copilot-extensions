# Efforts — copilot-extensions

This tree holds active implementation campaigns for copilot-extensions. An
effort connects standing vision intent and GitHub issues to a phased plan,
participants, validation, and an append-only journal. The canonical lifecycle
and schema come from the `efforts:planning-efforts` skill; this page only binds
that pattern to this repository.

## Active effort index

| Effort | Status | Coordination |
|--------|--------|--------------|
| [Worktree/Effort Railroad Binding](active/worktree-effort-railroad-binding/README.md) | Draft | #3581 |
| [Worktree Head-Succession Hardening](active/worktree-head-succession-hardening/README.md) | Draft | #3584 |
| [Picker Creature Comforts](active/picker-creature-comforts/README.md) | Draft | #3586 |
| [Dev/Main Release Pipeline](active/dev-branch-release-pipeline/README.md) | Draft | #3336 |
| [Promotion-Failure Reactive Fix Agent](active/promotion-failure-reactive-fix-agent/README.md) | Active | _TBD_ |
| [Vendored Doc Pointers](active/vendored-doc-pointers/README.md) | Done; pending archive | #3565 |
| [Vendor Pointer Generalization](active/vendor-pointer-generalization/README.md) | Draft | See effort |
| [Ambient Guidance Navigability](active/ambient-guidance-navigability/README.md) | Active | #3033 |
| [Unified Skill Review](active/unified-skill-review/README.md) | Draft | #2847 |
| [Handoff Cutover Lifecycle Journal](active/handoff-cutover-lifecycle-journal/README.md) | Draft | #2457 |
| [Handoff Live Cutover](active/handoff-live-cutover/README.md) | Active | #2249† |
| [Handoff Cutover Reload Robustness](active/handoff-cutover-reload-robustness/README.md) | Active | #5250† |
| [Context-Handoff Overhaul](active/context-handoff-overhaul/README.md) | Draft | #2594 |
| [Custom Context Aggregator Retirement](active/custom-context-aggregator-retirement/README.md) | Draft | #2173 |
| [Role-Aware Fork PR Flow](active/role-aware-fork-pr-flow/README.md) | Active | See effort |
| [SessionStart Static/Dynamic Content Conformance](active/sessionstart-static-dynamic-conformance/README.md) | Active | #2256 |
| [agent-logger Aggregate Configuration](active/agent-logger-aggregate-configuration/README.md) | Active | #1817 |
| [Balanced Profile Assignment](active/balanced-profile-assignment/README.md) | Active | #1564 |
| [Budget-Aware Model Routing](active/budget-aware-model-routing/README.md) | Draft | #2137 |
| [Evidence-Calibrated Model Routing](active/evidence-calibrated-model-routing/README.md) | Active | #2014 |
| [agent-bridge Contract Evolution](active/agent-bridge-contract-evolution/README.md) | Draft | #1460 |
| [agent-bridge Contract Baseline](active/agent-bridge-contract-baseline/README.md) | Draft | #1468 |
| [agent-bridge AHP Convergence](active/agent-bridge-ahp-convergence/README.md) | Draft | #1266, #1308 |
| [agent-bridge Delegation Convergence](active/agent-bridge-delegation-convergence/README.md) | Active | #1448 |
| [agent-bridge Attention Waits](active/agent-bridge-attention-waits/README.md) | Draft | #1450 |
| [agent-bridge Session Discovery](active/agent-bridge-session-discovery/README.md) | Draft | #2530 |
| [agent-bridge CLI-Mode Sessions](active/agent-bridge-cli-mode-sessions/README.md) | Active | See effort |
| [agent-bridge Delegation Contract](active/agent-bridge-delegation-contract/README.md) | Done; pending archive | #1449 |
| [Migration Intake](active/migration-intake/README.md) | Draft | See effort |
| [Account-Aware Operations](active/account-aware-operations/README.md) | Draft | See effort |
| [Agent Machines Declarative Control Plane](active/agent-machines-declarative-control-plane/README.md) | Active | #1418 (closed; historical, no live umbrella) |
| [Review Automation Reliability](active/review-automation-reliability/README.md) | Draft | See effort |
| [Worktree Finality and Obligations](active/worktree-finality-and-obligations/README.md) | Active | #1312 |
| [Marketplace-Scoped Installations](active/marketplace-scoped-installations/README.md) | Active | #1096 |
| [Native-Construct Convergence](active/native-construct-convergence/README.md) | Active | #985 |
| [Plugin Process Hygiene](active/plugin-process-hygiene/README.md) | Active | #736 |
| [Tiered Payload Provisioning](active/tiered-payload-provisioning/README.md) | Draft | See effort |
| [Progressive Context Disclosure](active/progressive-context-disclosure/README.md) | Active | #1612 |
| [Restricted Venue Targets](active/restricted-venue-targets/README.md) | Draft | #1188 |
| [Test Portfolio Rationalization](active/test-portfolio-rationalization/README.md) | Active | #1303 |
| [Terminal Worktree Reclamation](active/terminal-worktree-reclamation/README.md) | Draft | #1488 |
| [Venue Parity](active/venue-parity/README.md) | Active | #954 |
| [Windows Launch Hardening](active/windows-launch-hardening/README.md) | Active | #786 |
| [Worktree Manager Control Plane](active/worktree-manager-control-plane/README.md) | Active | #352 |
| [agent-index Engine Daemon](active/agent-index-engine-daemon/README.md) | Done; pending archive | See effort |
| [Uniform Runtime Resolution](active/uniform-runtime-resolution/README.md) | Done; pending archive | #765 |
| [Vendored Installer Engine](active/vendored-installer-engine/README.md) | Draft | See effort |
| [Session-Rescue Parity (Containers <-> CodeSpaces)](active/session-rescue-parity/README.md) | Done; pending archive | #3642 |
| [Pull-Request Capability](active/pull-request-capability/README.md) | Draft | #2691, #2699, #2700 |
| [Module Componentization Discipline](active/module-componentization-discipline/README.md) | Active | #2805 |
| [Componentization Campaign Auto-Worker](active/componentization-campaign-auto-worker/README.md) | Draft | #3372 |
| [PR Attribution Codenames](active/pr-attribution-codenames/README.md) | Done; pending archive | #2838 |
| [Codename Attribution By Default](active/codename-attribution-by-default/README.md) | Done; pending archive | #2977 |
| [AGENTS.md vs .github/instructions Split](active/agents-md-vs-instructions-split/README.md) | Done; pending archive | #2825 |
| [Claim Provider Pattern](active/claim-provider-pattern/README.md) | Done; pending archive | #3295, #3461 |
| [Authoritative Write-Through Daemon](active/agent-worktrees-authoritative-daemon/README.md) | Draft | #3761 |

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
