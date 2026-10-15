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
| Human-progress implementation owner | Shared timing/line rendering, actual output adapters and focused proofs; no Git, PR or host lifecycle writes | Coordinator's bounded code delegation |
| Bridge serving source implementer | Only #6826 in an independent managed worktree and source PR; no production update or recovery authority | Coordinator's bounded cooperating-session delegation |

## Coordination

- **Topology:** one reviewed proposal followed by independently reviewable,
  dependency-ordered implementation PRs. Only the coordinator publishes or
  mutates Git state in the integration worktree.
- **Host (owns integration/presentation PRs):** Coordinator. The Bridge
  source implementer owns only its isolated #6826 PR through the normal
  review/CI flow; that PR must not merge before #6869 is merged and the
  serving branch is synchronized with regenerated native declarations.
- **Delegates:** three non-mutating evidence scopes above. Implementation
  ownership was assigned after proposal review: the outcome/framing slice
  is merged, and the human-progress owner now owns formatter/clock helpers,
  directly dependent output adapters, generated consumers and focused tests.
  Bridge serving-state algorithms remain outside that assignment. The
  coordinator owns this effort's journal and publication from the shared
  integration worktree. Its code edits and Git mutations are serialized
  with any active delegate using that same worktree.
- **Independent source cooperation:** #6826 implementation/test/PR work is
  explicitly delegated separately from the shared integration worktree.
  The coordinator retains overall effort, production rollout, native
  recovery, seed and held-work custody. No resource lease or administrative,
  signer, main-branch or private-role authority is transferred.
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

- [x] Retain per-action exit status and timed start/end/error outcomes in the
  shell launch-maintenance path while preserving its fresh replanning.
- [x] Correct the supervisor JSON-framing defect before applying human line
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

Equivalent before/after measurements use the same source/payload identities,
policy, declared dependencies and initial installed-state fixture. Record
operation counts and measured durations separately; a change in network
availability or payload state is not an equivalent speed comparison.

| Scenario | Required observable result |
|----------|----------------------------|
| Unchanged admitted and healthy runtime | No runtime/package reconstruction; retain the required policy, ownership and health checks. |
| Admitted Bridge slot current, serving version stale | Use the existing owned health/cutover path; no rebuild or disabled-service activation. |
| Changed Codespaces payload | Attribute construction, package, completion and activation boundaries; do not mistake silence between lines for command execution time. |
| First touch with no completed runtime | Perform the required construction and admission work; report its real phases and failures rather than claiming no-op savings. |
| Failed or missing child command | Preserve its exact failure and continuation policy; do not render aggregate success or contaminate JSON stdout. |
| Payload-to-runtime dependency ordering | Retain fresh second-pass planning and runtime re-resolution; do not suppress work solely because an earlier child exited zero. |

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

### 2026-10-15 - Preserve in-place promotion and receipt-owned execution

- Current-head #6869 review reproduced a same-root promotion failure:
  rewriting an installer invalidated the original declaration before its
  validation. Materialization now validates all original contracts before
  any owner mutates the tree, then regenerates digests after all owners
  finish. Separate-tree and same-root whole-payload tests cover all nine
  producers; invalid original script, declaration and helper inputs fail
  before any rewrite.
- Reconciled the newer process-tree/receipt installer owner rather than
  restoring exit-code-only execution. Its live output descriptor retains
  the existing kill-on-close job/session, deadline and receipt judgment.
  Native PowerShell `-File` arguments, policy refusal and exit behavior
  are exercised with both raw and receipt-owned execution.
- Windows focused validation: 282 passed, six platform skips across the
  six Agent Worktrees files; 110 materializer/generator tests; 46 Manager
  update tests; 93 headless-guard tests. Install contract, actual headless
  adopter scan and PR-scoped module-size guard pass without changing caps.
  The exact committed native Linux candidate passed 264 Agent Worktrees
  tests (24 platform skips), 46 Manager tests, 93 headless-guard tests and
  110 materializer/generator tests. Both OS runs retain the original
  item/suite limits; real native tests opt out of the subprocess stand-in,
  while fake installer tests forward the same output-descriptor argument.
  Current-head review and CI remain merge gates.
- The separately owned Bridge serving-state slice is held draft #6877.
  Its merge remains dependent on #6869's actual merge, synchronization,
  declaration regeneration and its own resulting-head review and CI.
  This transfers no production rollout or recovery responsibility.

### 2026-10-15 - Fix the review's privacy and completion fault class

- Current-head review of #6869 identified multiline credential exposure
  caused by splitting before redaction, and a failed phase's transient
  completion-write error being retried as `done` during cleanup.
  Reproduced both before editing production logic; the same false-success
  cleanup also occurred in Manager after two transient writes.
- Redact whole logical messages before splitting or adding state labels,
  preserving canonical envelopes only through already-redacted text.
  Native streaming now retains incomplete credential prefixes according
  to the existing redactor so framing cannot separate a scheme from its
  following credential. Cover plain/state/completion, LF/CRLF, canonical
  records and real native pipe input.
- Propagate active completion exceptions into both phase and Manager
  cleanup, reset phase state before sink I/O, and retain explicit failure.
  Adopt the existing `agent_procutil` windowless flag helper rather than
  hand-rolling flags; no guard exemption is introduced.
- Targeted regressions passed after the fix. Full current-candidate
  acceptance and fresh review remain required; the prior approval is not
  treated as approval of this new head.
- Full acceptance of `a8e6332dbe54763567f1633765b2f3d71c4ded1d`
  then passed: canonical Windows engine/deadline coverage 264 cases
  (one platform skip), Manager 46, and headless guard contracts 93.
  An exact-source native Linux consumer passed 250 engine/deadline cases
  (15 platform skips), 46 Manager cases, 93 headless contracts and all
  38 generator cases. Original limits and all existing checks remain.
- Rebuilt the corrected stdlib wheel using the declared backend and
  seven-day cutoff. A new credential-free clean-room consumer verified
  its installed bytes and exercised both direct and real native-stream
  multiline redaction, Unicode output, timing and exit 23: three stages
  passed. That owned container was removed; the distinct source-feed TLS
  obstruction and full Windows bootstrap limitation remain documented.

### 2026-10-15 - Presentation review and bounded serving-source cooperation

- Opened #6869 for the bounded presentation slice. Its first required
  guard correctly rejected `update_runtime.py` at 1,051 lines against the
  unchanged 1,000-line cap.
- Extracted the cohesive installer process/deadline helpers into
  `update_installer_process.py`, retaining the runtime aliases and test
  seams. The three function bodies are AST-identical; the original module
  is now 968 lines. Canonical focused Windows coverage, including deadline
  and alias regressions, passed 248 cases (one platform skip). No cap,
  baseline, required check or behavior was waived.
- A cooperating controller requested the still-reserved #6826 source
  slice before allocation or edits. Authorized only its isolated source
  implementation, tests and PR, with the explicit dependency on #6869
  above. The reservation is recorded on #6826; #5754 and all production,
  native recovery, held-work and release-acceptance boundaries remain
  separate and unchanged.

### 2026-10-15 - Synchronized presentation candidate and fresh wheel proof

- Synchronized the candidate with the contribution branch and refreshed
  the three encoding declarations affected by merged installer changes.
  The synchronized code is bound by
  `7c947e4d970401db02913cc8f5fa573a618883c8`. Existing merged #6836
  owns the single final local-integration pass; #6862 owns refusal to
  mutate runtimes after required payload acquisition fails. Neither is
  reimplemented or claimed as this effort's new speedup.
- Canonical Windows preparation now succeeds after #6845. The complete
  four-file engine/library selection passed 220 cases (one platform
  skip), and canonical Manager preparation passed all 45 update cases.
  A fresh exact-source native Linux consumer passed all 38 generator
  cases, 207 engine/library cases (14 Windows-only skips) and 45 Manager
  cases, without deselections or changed limits.
- An agent-free Linux clean-room source-build attempt stopped at an
  external build-backend TLS `HandshakeFailure`; it is not reported as
  passing. The existing Docker engine is Linux, so a full fresh Windows
  PowerShell machine was unavailable; native PS5.1/7 contracts were
  exercised in contained fixtures.
- Separately built the exact pure-stdlib library wheel with its declared
  backend and an explicit seven-day dependency cutoff. A fresh,
  unauthenticated clean-room container installed that local wheel
  offline, verified its source/module hashes and installed-prefix origin,
  then preserved a real Unicode child's exit 23 and timed framing.
  All three packaging/renderer stages passed. This does not establish
  source-feed bootstrap, production artifact admission or host rollout.
- Both owned containers were removed. Native-shell/standalone timing,
  equivalent update-cost measurements, serving-state convergence and
  authorized promoted installation remain open.

### 2026-10-15 - Exact-candidate native-storage proof

- Bound the candidate as local commit
  `062bc917b2f785e90fa95332c47b0220cfda0d60`, tree
  `1d5addb17928a67eb6aa8bb854264dcf4b31ae9c`; no publication or
  installation was inferred from that commit.
- Independent Windows runs passed all four focused engine/library files
  (215 passes, one platform skip), the Manager update file (45 passes)
  and the complete generator file (38 passes). Whole-payload producer
  validation completed in 3.93s.
- Repeated the complete selection against an exact-HEAD/tree local-only
  shallow consumer on native Linux storage, with private native temporary
  state and current-source import assertions. All 38 generator cases
  passed, including the whole-payload/all-nine-producer case in 0.23s.
  The four engine/library files passed 202 cases (14 Windows-only skips);
  Manager update passed 45. There were no deselections.
- The formerly blocked two-process append case passed in 0.59s. This
  resolves the storage-dependent validation obstruction without changing
  source ownership checks or the existing 30-second item / 300-second
  suite limits. The consumer source remained clean.
- These measurements explain validation filesystem cost, not installer
  speedup. This remains a bounded Python-owned presentation slice:
  standalone/native-shell clock coverage, real work reductions and
  promoted installed proof remain outstanding.

### 2026-10-15 - Human-output implementation started

- Assigned the next bounded implementation to the existing update phase,
  telemetry and output ownership, including actual launch-maintenance and
  nested child streams. There is no separately verified central human
  update renderer to preserve by name; structured update status remains
  authoritative, and launch preparation is a distinct progress owner.
- The implementation must establish an honest portable clock strategy,
  not assume Bash 3.2 has PowerShell's Stopwatch equivalent. One admitted
  existing interpreter/stream owner per outer operation may be considered;
  a reporting process, telemetry write or lock per line remains forbidden.
- Added explicit equivalent-state measurement scenarios above. These are
  validation oracles, not measured update savings or installed proof.
- Release inspection found normal promotion run `38722957706` blocked in
  metadata-validator tooling preparation by the literal refusal
  `dependency-seasoning: canonical local graph lacks library attribution`.
  This is the existing materialized-release admission portfolio tracked by
  #6806 and #6844, not a new framing failure or a reason to bypass policy.
  The normal queued promoter retains release ownership; no competing
  repair, pipeline retry, installed-copy patch or local updater was started.
  A dev merge is still not promoted or installed proof for this effort.
- The first contained formatter run exercised 189 engine/library cases
  (one platform skip) and 43 Manager cases successfully. A subsequent
  native Unicode regression failed on both Windows PowerShell hosts:
  redirected producers used IBM437, losing unrepresentable characters
  before the UTF-8 relay could decode them. The candidate is not green
  or published; changing the decoder alone cannot repair that loss.
- The next repair must initialize encoding inside the captured producer
  process, preserving native invocation, argument, exit, trust and
  packaging contracts. It must not change a shared console, machine
  locale, execution policy or global environment to make output pass.
- Producer-owned initialization now replaces only redirected text writers;
  native-command decoding and pipeline stdin retain their original
  encodings. Nine generated producers declare this contract. Native host
  invocation, arguments and exit handling remain unchanged; unsupported
  producers keep inherited streams with one scoped presentation notice.
- Worker coverage reached 305 Windows passes (one platform skip) and
  290 POSIX passes (14 Windows-only skips). The POSIX run is not fully
  green: an unchanged bulk-append stress case exhausted the broader
  selection, and the whole-payload/all-nine-producer proof hit its existing
  30-second limit on DrvFS. No deadline or ownership check was weakened.
  Re-execute the complete selection on native Linux storage before
  classifying this as a code defect or filesystem-specific cost.
- Coordinator review also corrected completion timing across multiline
  output and failure reporting when update finalization raises. Two
  independent contained regressions passed; sink latency must not inflate
  a completed operation's duration or turn an exception into `done`.
- A local candidate commit will bind the exact input for a disposable,
  native-Linux validation consumer. This is not publication, a new work
  venue, a release or installed proof; no PR proceeds on the blocked
  selection alone.

### 2026-10-15 - First implementation merged; formatting continues

- Merged #6846 as `6abfb1d7d566474f653417f83c2a7ad8680074d3`
  after a fresh exact-head approval with zero findings and successful
  required CI. Dispatch completed in 11m8s under its unchanged 12-minute
  deadline; the required aggregate passed.
- This resolves the first two Phase 2 items. It does not resolve the
  monotonic POSIX formatter-clock obligation, the remaining presentation
  items, serving-state convergence or installed rollout.
- Preserved the pre-sync source history, reconciled the merged PR through
  the native worktree flow and synchronized onto the merged contribution
  branch. The effort remains Active in its existing worktree.
- The next slice must extend the actual aggregate **update** presentation
  owner, not mistake launch preparation progress for that owner. Confirm
  the native POSIX clock/streaming seam before committing a formatter
  design; no per-line reporting process or telemetry write is permitted.

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
