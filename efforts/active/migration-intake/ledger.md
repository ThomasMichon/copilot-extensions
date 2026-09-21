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

## Phase 2 correction (2026-09-20, post-merge)

A post-merge self-audit found that candidates **#17** and **#19** were
mis-routed: both were sent to `marketplace-scoped-installations`' Phase 7
as new backlog items, but each already has its own pre-existing, more
detailed **Draft** effort covering the identical scope --
`tiered-payload-provisioning` (Windows `stamp` action, Plan line ~260) for
#17, and `vendored-installer-engine` (Phase 0 — Audit the real shared
surface) for #19. Routing them into `marketplace-scoped-installations` too
would have created the exact two-owner situation the Intake Contract's
single-primary-owner rule forbids.

| # | Corrected disposition | Corrected owner | Outcome | What changed vs. Phase 2 revalidation |
|---|--------------------------|--------------------|---------|-------------------------------|
| 17 | superseded | tiered-payload-provisioning | already owned by its own Draft effort's Plan; no new item needed | corrected from `routed`/`marketplace-scoped-installations` -- removed the duplicate Phase 7 bullet added in the Phase 2 pass |
| 19 | superseded | vendored-installer-engine | already owned by its own Draft effort's Phase 0; no new item needed | corrected from `routed`/`marketplace-scoped-installations` -- removed the duplicate Phase 7 bullet added in the Phase 2 pass |

Lesson for future passes: before routing a candidate to a *large, active*
domain effort's generic backlog phase, first check whether a smaller,
dedicated (possibly still-Draft) effort already exists for that exact
scope -- a Draft effort is still a real owner, not an absence of one.

## Phase 3 publication (2026-09-20)

Creates a public tracker issue for each remaining `routed` candidate's
accepted domain-plan item, per Phase 3's own steps (domain-plan acceptance,
already confirmed merged to `main`, plus owner/novelty/portability). Every
row below keeps disposition `routed` -- domain-plan acceptance already
happened and this pass does not change disposition. What this pass adds is
purely **tracker metadata**, tracked separately from disposition: whether a
public issue exists yet, and whether the owning effort currently has a live
umbrella to attach it under. A missing or closed umbrella is not itself a
disposition and is not defined as an acceptance gate anywhere in the Intake
Contract; it is recorded here only as an operational fact about *where* the
already-`routed` item's public issue can be filed today.

| # | Disposition | Owner | Tracker outcome | What changed vs. prior pass |
|---|-------------|-------|------------------|-------------------------------|
| 5 | routed | agent-machines-declarative-control-plane | issue [#3115](https://github.com/ThomasMichon/copilot-extensions/issues/3115) created, standalone (no umbrella) -- the effort's own umbrella (#1418) and all sub-issues are closed | issue created this pass; stands alone until the effort opens a live umbrella to attach it under |
| 9 | routed | worktree-finality-and-obligations | issue [#3113](https://github.com/ThomasMichon/copilot-extensions/issues/3113) created under live umbrella #1312 | issue created this pass |
| 14 | routed | account-aware-operations | no issue created yet -- owning effort is `Draft` with no umbrella issue | unchanged; no issue created this pass, since the effort is not yet `Active` and has no umbrella to attach one under |
| 15 | routed | review-automation-reliability | no issue created yet -- owning effort is `Draft` with no umbrella issue | unchanged; no issue created this pass, since the effort is not yet `Active` and has no umbrella to attach one under |
| 16 | routed | worktree-finality-and-obligations | issue [#3114](https://github.com/ThomasMichon/copilot-extensions/issues/3114) created under live umbrella #1312 | issue created this pass |
| 3 | routed | worktree-manager-control-plane | no new issue needed -- already addressed by open, not-yet-merged PR [#3102](https://github.com/ThomasMichon/copilot-extensions/pull/3102) | annotated with a pointer to #3102; no separate public issue while that PR covers the same scope |


*(Phase 3 publication, 2026-09-20: of the 6 routed candidates not already
resolved by an earlier ledger pass, all 6 keep disposition `routed`
(domain-plan acceptance was already completed in an earlier pass). 3 new
public issues were created (#3113, #3114 under
`worktree-finality-and-obligations`'s live umbrella #1312; #3115 standalone,
since `agent-machines-declarative-control-plane`'s own umbrella #1418 and all
seven listed sub-issues are closed), 1 needed no new issue because an
in-flight, not-yet-merged PR under a different effort already covers the
same scope (#3, disposition stays `routed`), and 2 have no public issue yet
because their owning effort is still `Draft` with no umbrella issue to
attach one under (#14, #15) -- disposition stays `routed` for both; this is
a tracker-metadata gap, not a taxonomy state, and the ledger will record a
new issue for each once its owning effort has a live umbrella. Each created
issue's body is a self-contained, repository-neutral restatement of the
routed Plan bullet with no reference to this ledger's originating session
context.)*

