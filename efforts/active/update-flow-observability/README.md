# Update Flow Observability

- **Slug:** `update-flow-observability`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed plan and implementation slices
- **Created:** 2026-10-14
- **Status:** Active
- **Vision:** `visions/installer` Features/plugin-updating-and-alignment; `visions/plugin-services/runtime-assembly` verified immutable assembly
- **Umbrella issue:** #6812
- **Sub-issues:** #6826 (Bridge serving no-op), #6827 (supervisor result framing); existing #6600, #6086, #6046 and #6796 retain their owners and boundaries

## Guiding Intent

An operator should be able to account for every install/update operation:
what it does, why it is necessary, how long it takes, and what is currently
waiting. Consistent presentation must expose unnecessary work, not decorate
it or obscure failures. Speed improvements remove measured repeated work
while retaining installation, ownership, policy and health contracts.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Owns synthesis, shared contracts, integration, review and completion | The source worktree associated with #6812 |
| Orchestration evidence reader | Audits unified update and launch-maintenance ordering | Coordinator's bounded read-only delegation |
| Installer evidence reader | Audits runtime construction, activation and healthy no-op work | Coordinator's bounded read-only delegation |
| Output evidence reader | Audits existing output helpers, protocols and formatting ownership | Coordinator's bounded read-only delegation |
| Outcome/framing implementation owner | First two Phase 2 items and their focused regressions; no Git, PR or host lifecycle writes | Coordinator's bounded code delegation |

## Coordination

- **Topology:** one reviewed proposal followed by independently reviewable,
  dependency-ordered implementation PRs. Only the coordinator publishes or
  mutates Git state in the integration worktree.
- **Host (owns PRs):** Coordinator.
- **Delegates:** three non-mutating evidence scopes above. Implementation
  ownership was assigned after proposal review: the outcome/framing owner
  may edit shell action-result handling, the supervisor result boundary and
  directly related tests only. The coordinator does not edit those files
  or mutate Git concurrently.
- **Handoff:** keep this README and the operation audit authoritative. A
  completed PR is a slice, not completion of the effort.
- **Existing owners:** the vendored-installer-engine campaign owns wholesale
  installer-engine convergence; seasoned-dependencies-and-transactional-installs
  owns its dependency and transaction changes. Existing open repair PRs are
  not superseded. Coordinate any necessary overlapping change before editing.

## Context

- #6600 introduced persistent launch-phase accounting; this effort covers the
  larger install/update path and its mixed human-readable output.
- #6086 covers process-spawn reduction. Reuse its evidence and contracts rather
  than claiming that every subprocess can be removed.
- #6046 already owns the Windows seedless pane receipt defect; it is not a
  timing-formatter implementation task.
- #6796 owns the observed command-binding publication refusal.
- A real launch resolution completed before a long maintenance sequence.
  Bootstrap update failed, reconciliation continued, runtime construction and
  cutover took minutes, and the same plugin actions appeared in a second
  reconciliation pass. These observations motivate measurement; they do not
  prove which individual operation caused each gap.
- The [complete normalized line ledger and source operation map](audit.md)
  cover every reference-run output line without publishing operator state.
- `docs/install-contract.md` and `docs/patterns/README.md` remain binding:
  independent installers, attributable immutable runtimes, package and
  signature policy, publication ownership, health and rollback are preserved.
- Vision reconciliation: this closes the existing installer's observable,
  coherent update/alignment intent; it does not introduce another updater,
  service or policy authority.

## Request

The operator confirmed the slug `update-flow-observability` and requested:

> Audit every line of that update flow and identify why it's needed and figure out how we can speed things up. We likely should prefix every line of the install/update flow with the time, and post-fix with the duration, ideally using some consistent, elegant formatting. There's also mixes of formatting, messaging, and status reporting which need cleanup now that we report more overall update progress.

## Plan

The audit, timing, presentation and speed goals are operator-requested.
The sequencing, specific repair choices, formatter grammar and validation
matrix below are _(agent-recommended)_ implementation details.

### Phase 1 - Account for the flow and review the proposal

- [x] Audit every reached logical operation in unified update and normal
  launch maintenance, with source citations, necessity, conditions,
  repetitions, observed cost and remaining measurement gaps.
- [x] Identify existing formatter owners and machine-readable stream
  contracts; propose one coherent line and progress vocabulary.
- [x] Rank safe reductions by measured work avoided; explicitly distinguish
  code-proven causes from hypotheses.
- [x] Capture the audit in a linked operation inventory, complete this
  proposal, and land the reviewed plan before implementation.

### Phase 2 - Make timing and status reporting consistent

- [ ] Retain per-action exit status and timed start/end/error outcomes in the
  shell launch-maintenance path while preserving its fresh replanning.
- [ ] Correct the supervisor JSON-framing defect before applying human line
  formatting (#6827); preserve diagnostic stderr and identity-scoped retirement.
- [ ] Implement the smallest shared extension of the existing formatter
  ownership, covering aggregate progress, plugin installers, nested child
  output and persistent diagnostics.
- [ ] Prefix every human-readable install/update line with a wall-clock time;
  suffix it with honestly labeled monotonic elapsed time or completed-step
  duration. Do not represent arbitrary child-line arrival as execution cost.
- [ ] Consolidate banners, status names, nesting and error presentation;
  preserve useful detail without duplicate timestamps, repeated status
  prefixes or contradictory aggregate success.
- [ ] Preserve JSON/quiet/protocol output, redaction and Windows/POSIX parity.

### Phase 3 - Remove proven unnecessary work

- [ ] Reconcile Bridge serving-version drift through the admitted runtime's
  existing cutover path rather than returning a misleading local-content
  no-op or rebuilding an already current slot (#6826).
- [ ] Verify #6598's existing Codespaces admitted-construction reuse after
  promotion instead of reimplementing it; attribute any remaining
  package/completion/activation failure with the new phase outcomes.
- [ ] Implement the reviewed, evidence-backed reductions in repeated planning,
  probing, building or reconciliation, retaining all actual safety gates.
- [ ] Make unchanged healthy work a genuine no-op; retain fresh, changed,
  failed and uncertain-state behavior rather than adding success-shaped
  fallbacks.
- [ ] Measure before/after execution count and cost for the same scenarios.

### Phase 4 - Land, prove and finish

- [ ] Update source-owned documentation and per-plugin changefiles, then drive
  each implementation through required review, CI, merge and source-worktree
  finalization.
- [ ] Prove the installed promoted release through the authorized update
  owner; do not infer rollout from a dev merge or marketplace version.
- [ ] Resolve or explicitly transfer every plan and validation item to a
  named tracked objective, archive this effort, and release responsibility.

## Validation Plan

- [ ] Use deterministic clocks to cover start, progress, completion, no-op,
  failure, nesting, multiline output, empty output and clock movement.
- [ ] Prove clean JSON/stdout and diagnostic stderr at real subprocess
  boundaries, including a failed installer and preserved exit status.
- [ ] Cover Windows and POSIX installer entrypoints and synchronized vendored
  formatter copies; run the relevant install-contract and formatting guards.
- [ ] Exercise real unchanged, changed-version and first-touch consumer
  contracts in isolated fixtures, retaining original limits and safety gates.
- [ ] Compare operation counts and durations on equivalent before/after flows;
  show the cost of required work separately from eliminated repetition.
- [ ] Exercise clean-room provisioning when the changed boundary affects
  first installation; record an explicit reason for any unavailable lane.
- [ ] Verify a live authorized update and healthy no-op after promotion without
  duplicate installation ownership, service instances or prompt delivery.
- [ ] Record all validation tiers and rollout evidence with the corresponding
  implementation PRs.

## Proposal

The [audit](audit.md) contains the complete reference-run line ledger,
measured intervals, source map, concrete defects and proposed presentation.
The proposal cleared review and merged in #6823. Implementation proceeds
under the phase and validation gates below; the effort is not complete.

The requested timing suffix needs two distinct meanings: a progress line can
report elapsed time in its current operation, while a completion line can
report that operation's measured duration. Both must use monotonic clocks;
the timestamp prefix is for human wall-clock correlation.

Implementation proceeds in bounded slices: accurate action outcomes and
machine-result framing first; native in-process formatting and nested-output
convergence next; then measured work reduction and serving-state
convergence. No slice introduces a Python subprocess or durable telemetry
write per output line. A new library, service or lifecycle authority is not
part of the proposal.

Existing running/deployed state checks remain authoritative. Reconciliation
already replans between passes; caching an earlier plan or dropping a pass
because a process returned zero is explicitly rejected. Codespaces'
unattributed gaps remain an investigation item, not a promised savings
number.

The source advanced during the audit: #6598 already added Codespaces'
admitted-construction reuse. This effort verifies that improvement in the
consumer flow and does not claim or duplicate its implementation. The
Bridge serving-version gap and supervisor framing defect survived that
bounded source delta inspection.

## Journal

### 2026-10-15 - Retain framing coverage within the existing CI budget

- Opened #6846 and received an exact-head approving review. Its required
  Dispatch job then exceeded the unchanged 12-minute job deadline despite
  every test sub-suite passing; the required aggregate correctly failed.
  This is the passing-suite/job-budget class already tracked by #6052,
  not permission to bypass the gate or retry an unchanged candidate.
- Reduced the new framing matrix's redundant shell-host startups from
  22 to two. Each host still executes all 11 distinct Python producer
  subprocesses and validates their stream framing, exit status and
  diagnostics; shell cold startup itself is not the changed contract.
  No case, subprocess boundary, timeout or required check was removed.
- The same focused retirement selection now passes seven pytest items
  containing the original matrix: Windows fell from 68.02s to 30.15s;
  Linux fell from 130.74s to 58.48s. These are local measurements of
  equivalent test coverage, not claimed installer/update savings.
- The revised source still requires a fresh review and successful
  required CI. No merge, deadline increase or pipeline retry substitutes
  for that result.

### 2026-10-15 - First implementation slice prepared

- Retained native reconciliation exit codes, timed action outcomes and
  explicit continued-failure reporting in both shell launchers. Fresh
  replanning, streamed child output and environment restoration remain.
- Corrected #6827's complete-stdout JSON framing while preserving native
  stderr diagnostics, typed failure results and the existing scoped
  fallback. This removes JSON properties being expanded into warnings;
  it does not authorize broader process retirement.
- Independent Windows containment runs passed 27 retirement cases and
  10 maintenance/status cases (two POSIX-only cases skipped). The latter
  includes the real setup-log, quiet, interactive and stdio output helpers
  plus inherited native-error-preference restoration.
- The canonical Linux retirement lane passed 27 cases; isolated Linux
  maintenance passed two cases (10 Windows-only cases skipped). Touched
  Python lint, native PowerShell parsing and the install contract passed.
- Managed Windows dependency preparation failed before test collection,
  first on a temporary-file sharing violation and then during editable
  package construction. Windows code verification used an existing
  prepared interpreter under the same environment lease and containment
  APIs, with this worktree's source selected explicitly; preparation is
  not reported as passing.
- Fresh isolated child fixtures cover the changed process boundary.
  A full clean-room installation and live external rollout were not run:
  this slice changes result interpretation, not runtime construction or
  activation, and its promoted release does not yet exist.
- Bash currently reports whole-second native-counter durations. Clock
  adjustment and actual macOS Bash 3.2 proof remain open with the shared
  formatter phase; these tests do not prove monotonic clock behavior.
- No source PR or rollout is complete at this journal entry. Shared
  formatting, every-line time prefixes/suffixes, Bridge serving-state
  convergence and equivalent before/after measurements remain open.

### 2026-10-15 - Reviewed plan and implementation transition

- Merged #6823 as `657714e16ee08c957f55c35c5644daea83e860a3` after
  exact-head approval and successful required CI. This completed Phase 1,
  not the install/update implementation or rollout.
- Preserved the pre-sync local history, reconciled the merged PR through the
  native status flow, and completed native synchronization onto the current
  contribution branch.
- Coordinator continues with **Phase 2 - Make timing and status reporting
  consistent**, starting with per-action outcomes and supervisor
  machine-result framing before broad human-output normalization.

### 2026-10-14 - Kickoff

- Captured the operator's request verbatim and confirmed the effort slug.
- Claimed #6812 and established an isolated source worktree.
- Started three read-only evidence tracks before broad source inspection.
- The source worktree allocation completed, but its optional instruction
  projection reported an unavailable enabled payload. That warning is not
  treated as successful guidance publication or fixed by editing runtime files.
- Completed the three evidence tracks and a normalized ledger covering all
  249 reference-run log lines exactly once. Identified discarded action
  outcomes, Bridge's serving-version/no-op gap, unconditional post-venv
  package work, and the supervisor multiline-JSON framing defect.
- A later read-only reconcile plan still selected Bridge running drift and
  Codespaces version drift; Dispatch and Index were no longer selected.
  No further updater was launched to inspect that state.
- Revalidated the installer delta after #6598 merged: its Codespaces reuse
  short-circuit supersedes the baseline's unconditional package work.
  Preserved that implementation owner's scope and retained only verification
  and any remaining failure attribution here.
