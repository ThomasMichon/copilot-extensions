# Migration Intake — Candidate Ledger

Append-only. One row per candidate per pass. See `README.md` § Intake
Contract for the disposition taxonomy and ownership rules. A prior pass's
row is never edited in place -- a later pass appends its own dated section
recording the candidate's current disposition, so the full history from
initial scan to final outcome stays comparable and auditable.

## Initial scan (2026-09-20)

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

## Phase 2 revalidation (2026-09-20)

Re-checks the same 19 candidate numbers above against current source/target
effort text. This section records the *revalidated* disposition/owner/
outcome per candidate; it does not edit the Initial scan table above, so a
reader can compare what changed and why.

| # | Revalidated disposition | Revalidated owner | Outcome | What changed vs. initial scan |
|---|--------------------------|--------------------|---------|-------------------------------|
| 1 | superseded | worktree-manager-control-plane | already covered by Phase 3b Slice 1 -- no action | confirmed unchanged |
| 2 | residual | migration-intake | source effort is Done and deliberately leaves this open; no domain owner fits yet | owner narrowed from "candidate: worktree-finality-and-obligations" to confirmed-unclear (stays with intake) |
| 3 | routed | worktree-manager-control-plane | accepted into Phase 8 | candidate statement sharpened to the specific deferred item (cached worktree-status projection); Phase 3 action completed |
| 4 | closed-obsolete | n/a | delivered in source's own Phase 7 since the initial scan | corrected from `rejected` -- the work landed, so no disposition was even needed |
| 5 | routed | agent-machines-declarative-control-plane | accepted into new Phase 5 | corrected from `rejected` -- on re-read this is portable deferred scheduler-parity work, not an out-of-scope quirk |
| 6 | residual | migration-intake | still optional/out-of-scope at source; no active domain owns daemon self-retire semantics | confirmed unchanged |
| 7 | rejected | n/a | blocked on facility-specific SSH/WSL transport proof, not a portable product gap | confirmed unchanged |
| 8 | residual | migration-intake | agent-machines-declarative-control-plane is about machine/resource declarations, not dispatch worker identity -- no fitting owner found | corrected from `routed` -- the proposed owner does not actually fit |
| 9 | routed | worktree-finality-and-obligations | accepted into Phase 7 | confirmed; Phase 3 action completed |
| 10 | closed-obsolete | n/a | delivered upstream since the initial scan (2026-09-20); a different spawn-retry-loop issue remains open separately | corrected from `routed` -- the work landed |
| 11 | residual | migration-intake | next componentization candidate, not a reusable cross-domain capability | confirmed unchanged |
| 12 | superseded | native-construct-convergence | already covered by its own Phase C (#988) -- no action | corrected owner from the initial mis-routing guess (worktree-manager-control-plane) to the effort's own existing Phase C |
| 13 | closed-obsolete | n/a | effort is Done; the work landed since the initial scan | corrected from `residual` -- the work landed |
| 14 | routed | account-aware-operations | accepted into new Phase 5 | corrected owner from initial "unclear" -- the deferred overlay is blocked specifically on caller-identity/network resolution, which account-aware-operations owns |
| 15 | routed | review-automation-reliability | accepted into new Phase 11 | confirmed; Phase 3 action completed |
| 16 | routed | worktree-finality-and-obligations | accepted into Phase 7 | confirmed; Phase 3 action completed |
| 17 | routed | marketplace-scoped-installations | accepted into Phase 7 | confirmed; Phase 3 action completed |
| 18 | closed-obsolete | n/a | source is Done; guard is strict-clean in CI | confirmed unchanged |
| 19 | routed | marketplace-scoped-installations | accepted into Phase 7 | corrected owner from initial "unclear" -- the audit scope squarely belongs to the install-cell/install-contract surface |

*(Phase 2 revalidation, 2026-09-20: all 19 raw candidates re-checked against current source/target effort text. 8 were stale on second read (4 already delivered since the initial scan -> `closed-obsolete`; 1 mis-routed owner corrected to its own already-covering effort). 8 accepted `routed` candidates were placed into their target effort's own "Reconcile deferred backlog" phase (adding that phase where it did not yet exist: `agent-machines-declarative-control-plane` Phase 5, `account-aware-operations` Phase 5, `review-automation-reliability` Phase 11). 4 remain `residual` under `migration-intake` itself: no fitting canonical domain owner was found among the known domain plans. No public GitHub issues were created -- Phase 3's "create or update a public issue" step still requires domain-owner acceptance of each newly-added Plan item, which happens through that domain effort's own normal review, not this ledger.)*
