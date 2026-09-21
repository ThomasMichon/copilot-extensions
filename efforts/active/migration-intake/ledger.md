# Migration Intake — Candidate Ledger

Append-only. One row per candidate. See `README.md` § Intake Contract for the
disposition taxonomy and ownership rules. A candidate leaves `residual` only
once a later pass can resolve its disposition with evidence.

| # | Candidate (general-purpose statement) | Source (internal only, never published) | Disposition | Owner | Tracker outcome |
|---|----------------------------------------|-------------------------------------------|--------------|-------|------------------|
| 1 | Reconcile deferred bridge cutover work with the existing worktree-management control plane instead of duplicating that capability. | agent-bridge-ahp-convergence | superseded | worktree-manager-control-plane | already covered by Phase 3b Slice 1 -- no action |
| 2 | Decide whether historical dispatch-session records from before the new lifecycle should be backfilled or intentionally left unavailable. | agent-dispatch-session-worktree-history | residual | migration-intake | source effort is Done and deliberately leaves this open; no domain owner fits yet |
| 3 | Provide a manager/engine-backed cached worktree-status projection for external consumers (e.g. the Tasks-pane Worktree Status card). | agent-dispatch-tasks-pane-ux-overhaul | routed | worktree-manager-control-plane | accepted into Phase 8 |
| 4 | Resolve default accelerator-selection behavior so unsupported hardware does not cause avoidable runtime failures. | agent-index-engine-daemon | closed-obsolete | n/a | delivered in source's own Phase 7 since the initial scan |
| 5 | Define equivalent self-update scheduling behavior for non-Windows hosts. | agent-machines-self-update-watchdog | routed | agent-machines-declarative-control-plane | accepted into new Phase 5 |
| 6 | Add automatic generation self-retirement when a confirmed live successor is available. | agent-mcp-graceful-cutover | residual | migration-intake | still optional/out-of-scope at source; no active domain owns daemon self-retire semantics |
| 7 | Complete validation of the remaining session-guidance delivery paths before retiring the legacy aggregator. | custom-context-aggregator-retirement | rejected | n/a | blocked on facility-specific SSH/WSL transport proof, not a portable product gap |
| 8 | Attach declarative dispatch policy to a named worker identity and enforce the identity boundary at live call sites. | declarative-dispatch-engine-generalization | residual | migration-intake | agent-machines-declarative-control-plane is about machine/resource declarations, not dispatch worker identity -- no fitting owner found |
| 9 | Instrument owned-process lifecycle transitions so creation, transfer, completion, and abandonment are auditable. | handoff-cutover-lifecycle-journal | routed | worktree-finality-and-obligations | accepted into Phase 7 |
| 10 | Provide runtime-level bare-resume successor-spawn behavior that a plugin cannot safely implement itself. | handoff-cutover-reload-robustness | closed-obsolete | n/a | delivered upstream since the initial scan (2026-09-20); a different spawn-retry-loop issue remains open separately |
| 11 | Split an oversized coordinator module into independently testable components without changing behavior. | module-componentization-discipline | residual | migration-intake | next componentization candidate, not a reusable cross-domain capability |
| 12 | Implement delegated worktree creation once the native capability is available and stable. | native-construct-convergence | routed | native-construct-convergence | already covered by its own Phase C (#988) -- no action; corrected from initial mis-routing to worktree-manager-control-plane |
| 13 | Implement the remaining general-purpose PR-attribution phase after the initial hook-based design. | pr-attribution-codenames | closed-obsolete | n/a | effort is Done; the work landed since the initial scan |
| 14 | Support PR-guidance overlays and cross-PR relationship metadata once live caller-identity/network resolution exists. | pr-conduct-guidance-consolidation | routed | account-aware-operations | accepted into new Phase 5 |
| 15 | Make stale-branch/stale-PR reconciliation remote-aware after fork-mode publication. | role-aware-fork-pr-flow | routed | review-automation-reliability | accepted into new Phase 11 |
| 16 | Complete reclamation of terminal workspaces while preserving unresolved obligations and explicit abandonment semantics. | terminal-worktree-reclamation | routed | worktree-finality-and-obligations | accepted into Phase 7 |
| 17 | Unify stamp/provisioning self-provisioning semantics, including Windows stamp parity, for installed payloads. | tiered-payload-provisioning | routed | marketplace-scoped-installations | accepted into Phase 7 |
| 18 | Complete the final runtime-link retirement sweep while preserving the documented durable-runtime boundary. | uniform-runtime-resolution | closed-obsolete | n/a | source is Done; guard is strict-clean in CI |
| 19 | Audit and classify duplicated installer helpers before deciding which should be consolidated vs. kept as permanent exceptions. | vendored-installer-engine | routed | marketplace-scoped-installations | accepted into Phase 7 |

### Data-quality notes (not candidates)

- `session-context-aggregation` in the canonical domain-plans list resolves outside `efforts/active/` (under `efforts/2026/08/31 session-context-aggregation/`) -- the link is intentional (a non-active historical/completed reference), not broken, but its placement in the "canonical domain plans" list is worth a follow-up clarifying pass so it reads as a completed reference rather than an active plan.
- No canonical domain plan in the list resolves to a missing directory.
- `context-handoff-overhaul` and `budget-aware-model-routing` are the clearest candidates for "should this join the canonical domain-plans list" if either grows into a standing domain plan, but neither currently has enough evidence to add on this pass.

*(Initial pass, 2026-09-20: ~47 active effort directories skimmed for Journal/Plan deferral language; see README.md Journal for scan scope. Not exhaustive -- prioritized breadth over depth.)*

*(Phase 2 revalidation, 2026-09-20: all 19 raw candidates re-checked against current source/target effort text. 8 were stale on second read (4 already delivered since the initial scan -> `closed-obsolete`; 1 mis-routed owner corrected to its own already-covering effort -> `superseded`/`routed-already-covered`). 8 accepted `routed` candidates were placed into their target effort's own "Reconcile deferred backlog" phase (adding that phase where it did not yet exist: `agent-machines-declarative-control-plane` Phase 5, `account-aware-operations` Phase 5, `review-automation-reliability` Phase 11). 4 remain `residual` under `migration-intake` itself: no fitting canonical domain owner was found among the known domain plans. No public GitHub issues were created -- Phase 3's "create or update a public issue" step still requires domain-owner acceptance of each newly-added Plan item, which happens through that domain effort's own normal review, not this ledger.)*

