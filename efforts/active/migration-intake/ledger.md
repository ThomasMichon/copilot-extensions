# Migration Intake — Candidate Ledger

Append-only. One row per candidate. See `README.md` § Intake Contract for the
disposition taxonomy and ownership rules. A candidate leaves `residual` only
once a later pass can resolve its disposition with evidence.

| # | Candidate (general-purpose statement) | Source (internal only, never published) | Disposition | Owner | Tracker outcome |
|---|----------------------------------------|-------------------------------------------|--------------|-------|------------------|
| 1 | Reconcile deferred bridge cutover work with the existing worktree-management control plane instead of duplicating that capability. | agent-bridge-ahp-convergence | superseded | worktree-manager-control-plane | pending Phase 3 |
| 2 | Decide whether historical dispatch-session records from before the new lifecycle should be backfilled or intentionally left unavailable. | agent-dispatch-session-worktree-history | residual | unclear (candidate: worktree-finality-and-obligations) | pending Phase 2 revalidation |
| 3 | Complete the remaining operator-facing task-pane validation and interaction refinements after the core behavior landed. | agent-dispatch-tasks-pane-ux-overhaul | routed | worktree-manager-control-plane | pending Phase 3 |
| 4 | Resolve default accelerator-selection behavior so unsupported hardware does not cause avoidable runtime failures. | agent-index-engine-daemon | rejected | n/a | tied to a specific machine/device environment, not portable |
| 5 | Define equivalent self-update behavior for non-Windows hosts. | agent-machines-self-update-watchdog | rejected | n/a | explicitly out of scope for its source slice; no portable owner established |
| 6 | Add automatic generation self-retirement when a confirmed live successor is available. | agent-mcp-graceful-cutover | routed | worktree-manager-control-plane | pending Phase 3 |
| 7 | Complete validation of the remaining session-guidance delivery paths before retiring the legacy aggregator. | custom-context-aggregator-retirement | rejected | n/a | evidence tied to a WSL/operator environment, not portable |
| 8 | Attach declarative dispatch policy to a named worker identity and enforce the identity boundary at live call sites. | declarative-dispatch-engine-generalization | routed | agent-machines-declarative-control-plane | pending Phase 3 |
| 9 | Instrument owned-process lifecycle transitions so creation, transfer, completion, and abandonment are auditable. | handoff-cutover-lifecycle-journal | routed | worktree-finality-and-obligations | pending Phase 3 |
| 10 | Provide runtime-level bare-resume successor-spawn behavior that a plugin cannot safely implement itself. | handoff-cutover-reload-robustness | routed | worktree-manager-control-plane | pending Phase 3 |
| 11 | Split an oversized coordinator module into independently testable components without changing behavior. | module-componentization-discipline | residual | unclear | general maintenance work, no canonical domain owner evident |
| 12 | Implement delegated worktree creation once the native capability is available and stable. | native-construct-convergence | routed | worktree-manager-control-plane | pending Phase 3 |
| 13 | Implement the remaining general-purpose PR-attribution phase after the initial hook-based design. | pr-attribution-codenames | residual | unclear | follow-up described only at a high level; ownership/novelty unresolved |
| 14 | Decide whether deferred review-guidance overlay and relationship metadata should be retained as a reusable capability. | pr-conduct-guidance-consolidation | residual | unclear | general-purpose but explicitly deferred without owner or acceptance criteria |
| 15 | Add stale-branch and stale-pull-request pruning after the role-aware merge flow. | role-aware-fork-pr-flow | routed | review-automation-reliability | pending Phase 3 |
| 16 | Complete reclamation of terminal workspaces while preserving unresolved obligations and explicit abandonment semantics. | terminal-worktree-reclamation | routed | worktree-finality-and-obligations | pending Phase 3 |
| 17 | Add deferred Windows self-provisioning and stamp handling for installed plugin payloads. | tiered-payload-provisioning | routed | marketplace-scoped-installations | pending Phase 3 |
| 18 | Complete the final runtime-link retirement sweep while preserving the documented durable-runtime boundary. | uniform-runtime-resolution | closed-obsolete | n/a | remaining work is cleanup around already-delivered scope, not a new capability |
| 19 | Audit and classify duplicated installer helpers before deciding which should be consolidated vs. kept as permanent exceptions. | vendored-installer-engine | residual | unclear | plan still at an unstarted audit stage; no canonical owner or publication boundary yet |

### Data-quality notes (not candidates)

- `session-context-aggregation` in the canonical domain-plans list resolves outside `efforts/active/` (under `efforts/2026/08/31 session-context-aggregation/`) -- the link is intentional (a non-active historical/completed reference), not broken, but its placement in the "canonical domain plans" list is worth a follow-up clarifying pass so it reads as a completed reference rather than an active plan.
- No canonical domain plan in the list resolves to a missing directory.
- `context-handoff-overhaul` and `budget-aware-model-routing` are the clearest candidates for "should this join the canonical domain-plans list" if either grows into a standing domain plan, but neither currently has enough evidence to add on this pass.

*(Initial pass, 2026-09-20: ~47 active effort directories skimmed for Journal/Plan deferral language; see README.md Journal for scan scope. Not exhaustive -- prioritized breadth over depth. Every row above is `pending Phase 2 revalidation` in spirit: none have been re-validated against current code/docs/issues yet, so no tracker entries have been created from this pass.)*
